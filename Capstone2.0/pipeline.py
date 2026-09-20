#!/usr/bin/env python3
"""Historical land-cover ingestion pipeline for Ubiquitous Eye.

The program reads one or more AOI/month ranges from ``input.txt``, downloads
Sentinel-2 and Landsat observations through Sentinel Hub, builds the same
cloud-masked monthly median composite used by the backend, runs the copied
four-model ensemble, writes local object-store artifacts, and records searchable
metadata, summaries, per-cell history, and job state in SQL.

Input line format (pipe-delimited; end month is inclusive):

    aoi_id|YYYY-MM|YYYY-MM|[[lon,lat],[lon,lat],[lon,lat],[lon,lat]]

By default files go under ``object_store/`` beside this script and SQL goes to
``capstone.db`` (SQLite). Set DATABASE_URL to use PostgreSQL instead.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import io
import json
import logging
import math
import os
import re
import sys
import time
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

import joblib
import numpy as np
import pandas as pd
from PIL import Image
from scipy.stats import mode
from sentinelhub import (
    BBox,
    CRS,
    DataCollection,
    MimeType,
    SHConfig,
    SentinelHubCatalog,
    SentinelHubRequest,
    bbox_to_dimensions,
)
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

try:
    import rasterio
    from rasterio.transform import from_bounds
except ImportError:  # Reported with a focused message by check_runtime().
    rasterio = None
    from_bounds = None


SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_DIR = SCRIPT_DIR / "capstone_model_v2"
PIPELINE_VERSION = "2.0.0"

MODEL_NAMES = ("xgboost", "catboost", "lightgbm", "cart")
MODEL_FILES = (
    "scaler.joblib",
    "label_encoder.joblib",
    *(f"{name}.joblib" for name in MODEL_NAMES),
)

BAND_NAMES = ("B01", "B02", "B03", "B04", "B08", "B11", "B12")
CLASSIFY_BAND_COLUMNS = list(BAND_NAMES)
INVALID_S2_SCL_VALUES = (0, 1, 3, 8, 9, 10)
LANDSAT_BQA_MASK = 1 | 2 | 4 | 8 | 16
PROCESS_RESOLUTION_METRES = 30
MAX_PROCESS_DIMENSION = 2500
MAX_CLASSIFY_CELLS = 260_000
TRUE_COLOR_MAX_DIMENSION = 1536
TRUE_COLOR_CLOUD_BRIGHTNESS = 0.35
TRUE_COLOR_PERCENTILES = (2.0, 98.0)
TRUE_COLOR_GAMMA = 0.8
MIN_DISPLAY_DIMENSION = 512

CLASS_CODES = {"Tree": 1, "Crop": 2, "Water": 3, "Soil": 4}
CODE_TO_CLASS = {value: key for key, value in CLASS_CODES.items()}
CLASS_COLORS = {
    "Tree": (46, 125, 50),
    "Crop": (156, 204, 101),
    "Water": (21, 101, 192),
    "Soil": (161, 136, 127),
}

LOG = logging.getLogger("capstone2.pipeline")
_ARTIFACTS: dict | None = None


EVALSCRIPT_S2 = """
//VERSION=3
function setup() {
    return {
        input: [{
            bands: ["B01", "B02", "B03", "B04", "B08", "B11", "B12", "SCL"],
            units: "DN"
        }],
        output: [
            { id: "bands", bands: 7, sampleType: "FLOAT32" },
            { id: "scl",   bands: 1, sampleType: "UINT8" }
        ]
    };
}
function evaluatePixel(sample) {
    return {
        bands: [sample.B01 / 10000.0,
                sample.B02 / 10000.0,
                sample.B03 / 10000.0,
                sample.B04 / 10000.0,
                sample.B08 / 10000.0,
                sample.B11 / 10000.0,
                sample.B12 / 10000.0],
        scl: [sample.SCL]
    };
}
"""

EVALSCRIPT_LANDSAT_SR = """
//VERSION=3
function setup() {
    return {
        input: [{ bands: ["B01", "B02", "B03", "B04", "B05", "B06", "B07"], units: "REFLECTANCE" }],
        output: [{ id: "default", bands: 7, sampleType: "FLOAT32" }]
    };
}
function evaluatePixel(sample) {
    return [sample.B01, sample.B02, sample.B03, sample.B04,
            sample.B05, sample.B06, sample.B07];
}
"""

EVALSCRIPT_LANDSAT_BQA = """
//VERSION=3
function setup() {
    return {
        input: [{ bands: ["BQA"], units: "DN" }],
        output: [{ id: "default", bands: 1, sampleType: "UINT16" }]
    };
}
function evaluatePixel(sample) { return [sample.BQA]; }
"""

EVALSCRIPT_TRUE_COLOR = """
//VERSION=3
function setup() {
    return {
        input: [{ bands: ["B02", "B03", "B04", "SCL", "dataMask"] }],
        output: [
            { id: "rgb",  bands: 3, sampleType: "FLOAT32" },
            { id: "mask", bands: 1, sampleType: "UINT8" }
        ],
        mosaicking: "ORBIT"
    };
}
var CLOUD_BRIGHT = __CLOUD_BRIGHT__;
function isCloud(scl) { return scl == 3 || scl == 8 || scl == 9 || scl == 10; }
function isVoid(scl)  { return scl == 0 || scl == 1; }
function median(v) {
    v.sort(function (a, b) { return a - b; });
    var n = v.length, m = n >> 1;
    return (n % 2) ? v[m] : 0.5 * (v[m - 1] + v[m]);
}
function evaluatePixel(samples) {
    var cr = [], cg = [], cb = [], all = [];
    for (var i = 0; i < samples.length; i++) {
        var s = samples[i];
        if (s.dataMask != 1) continue;
        var bright = (s.B02 + s.B03 + s.B04) / 3.0;
        all.push({ b: bright, r: s.B04, g: s.B03, bl: s.B02 });
        if (!isVoid(s.SCL) && !isCloud(s.SCL) && bright < CLOUD_BRIGHT) {
            cr.push(s.B04); cg.push(s.B03); cb.push(s.B02);
        }
    }
    if (cr.length > 0) {
        return { rgb: [median(cr), median(cg), median(cb)], mask: [1] };
    }
    if (all.length > 0) {
        all.sort(function (x, y) { return x.b - y.b; });
        var p = all[Math.floor(all.length * 0.25)];
        return { rgb: [p.r, p.g, p.bl], mask: [1] };
    }
    return { rgb: [0, 0, 0], mask: [0] };
}
""".replace("__CLOUD_BRIGHT__", repr(TRUE_COLOR_CLOUD_BRIGHTNESS))


@dataclass(frozen=True)
class AOISpec:
    aoi_id: str
    start_month: date
    end_month: date
    polygon: tuple[tuple[float, float], ...]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        lons = [point[0] for point in self.polygon]
        lats = [point[1] for point in self.polygon]
        return min(lons), min(lats), max(lons), max(lats)


@dataclass
class SceneMetadata:
    collection: str
    scene_id: str
    acquired_at: datetime
    cloud_cover: float | None
    used: bool = False
    fetch_error: str | None = None

    @property
    def day(self) -> str:
        return self.acquired_at.date().isoformat()


@dataclass
class MonthResult:
    month: date
    window_start: date
    window_end: date
    bounds: tuple[float, float, float, float]
    composite: np.ndarray
    classification: np.ndarray
    clear_counts: np.ndarray
    resolution_metres: int
    sources: list[SceneMetadata]
    true_color: Image.Image
    class_overlay: Image.Image
    class_counts: dict[str, int]

    @property
    def height(self) -> int:
        return int(self.classification.shape[0])

    @property
    def width(self) -> int:
        return int(self.classification.shape[1])

    @property
    def total_cells(self) -> int:
        return self.width * self.height

    @property
    def classified_cells(self) -> int:
        return int(np.count_nonzero(self.classification))


@dataclass(frozen=True)
class AssetRecord:
    asset_type: str
    object_key: str
    uri: str
    sha256: str
    file_size: int
    image_format: str
    width: int
    height: int
    band_count: int
    dtype: str
    crs: str
    nodata: str | None


@dataclass
class Database:
    engine: Engine
    metadata: MetaData
    aoi: Table
    run: Table
    asset: Table
    land_class: Table
    summary: Table
    observation: Table
    source_scene: Table


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without adding another dependency."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def parse_month(value: str, line_number: int | None = None) -> date:
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m").date()
        return parsed.replace(day=1)
    except ValueError as exc:
        where = f" on input line {line_number}" if line_number else ""
        raise ValueError(f"Invalid month {value!r}{where}; expected YYYY-MM") from exc


def close_and_validate_polygon(raw: object, line_number: int) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError(f"Input line {line_number}: polygon needs at least 3 coordinate pairs")

    points: list[tuple[float, float]] = []
    for index, value in enumerate(raw, start=1):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(
                f"Input line {line_number}: coordinate {index} must be [longitude, latitude]"
            )
        try:
            lon, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Input line {line_number}: coordinate {index} is not numeric"
            ) from exc
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(
                f"Input line {line_number}: coordinate {index} is outside WGS84 bounds"
            )
        points.append((lon, lat))

    if len(set(points)) < 3:
        raise ValueError(f"Input line {line_number}: polygon needs 3 distinct points")
    if points[0] != points[-1]:
        points.append(points[0])

    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    if min(lons) == max(lons) or min(lats) == max(lats):
        raise ValueError(f"Input line {line_number}: polygon bounding box has zero area")
    return tuple(points)


def parse_input(path: Path) -> list[AOISpec]:
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    specs: list[AOISpec] = []
    id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("|", 3)
        if len(fields) != 4:
            raise ValueError(
                f"Input line {line_number}: expected "
                "aoi_id|YYYY-MM|YYYY-MM|[[lon,lat],...]"
            )
        aoi_id, start_text, end_text, polygon_text = (field.strip() for field in fields)
        if not id_pattern.fullmatch(aoi_id):
            raise ValueError(
                f"Input line {line_number}: AOI id must use letters, numbers, '.', '_' or '-'"
            )
        start_month = parse_month(start_text, line_number)
        end_month = parse_month(end_text, line_number)
        if end_month < start_month:
            raise ValueError(f"Input line {line_number}: end month precedes start month")
        if start_month > date.today().replace(day=1):
            raise ValueError(f"Input line {line_number}: start month is in the future")
        try:
            raw_polygon = json.loads(polygon_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Input line {line_number}: polygon is not valid JSON") from exc
        polygon = close_and_validate_polygon(raw_polygon, line_number)
        specs.append(AOISpec(aoi_id, start_month, end_month, polygon))

    if not specs:
        raise ValueError(f"No AOI records found in {path}")
    return specs


def iter_months(start: date, end: date) -> Iterator[date]:
    current = start.replace(day=1)
    end = end.replace(day=1)
    while current <= end:
        yield current
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)


def month_end(month: date) -> date:
    last = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    return min(last, date.today())


def canonical_polygon(polygon: Sequence[Sequence[float]]) -> list[list[float]]:
    return [[round(float(lon), 10), round(float(lat), 10)] for lon, lat in polygon]


def model_version() -> str:
    digest = hashlib.sha256()
    for name in MODEL_FILES:
        path = MODEL_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing model artifact: {path}")
        digest.update(name.encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return f"capstone_model_v2:{digest.hexdigest()[:16]}"


def check_runtime() -> None:
    missing = [name for name in MODEL_FILES if not (MODEL_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing model files in {MODEL_DIR}: {', '.join(missing)}")
    if rasterio is None:
        raise RuntimeError(
            "rasterio is required to write Cloud Optimized GeoTIFF files. "
            "Install the packages listed in README.md."
        )


def build_database(database_url: str) -> Database:
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg2://" + database_url[len("postgres://") :]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg2://" + database_url[len("postgresql://") :]

    engine_kwargs: dict = {"future": True, "pool_pre_ping": True}
    if database_url.startswith("sqlite:"):
        engine_kwargs["connect_args"] = {"timeout": 60}
    engine = create_engine(database_url, **engine_kwargs)

    if database_url.startswith("sqlite:"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    metadata = MetaData()
    integer_pk = BigInteger().with_variant(Integer, "sqlite")

    aoi = Table(
        "area_of_interest",
        metadata,
        Column("id", String(80), primary_key=True),
        Column("polygon", JSON, nullable=False),
        Column("west", Float, nullable=False),
        Column("south", Float, nullable=False),
        Column("east", Float, nullable=False),
        Column("north", Float, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    run = Table(
        "inference_run",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("aoi_id", String(80), ForeignKey("area_of_interest.id"), nullable=False),
        Column("requested_month", Date, nullable=False),
        Column("window_start", Date),
        Column("window_end", Date),
        Column("status", String(16), nullable=False),
        Column("attempt_count", Integer, nullable=False, default=0),
        Column("model_version", String(96), nullable=False),
        Column("pipeline_version", String(32), nullable=False),
        Column("resolution_metres", Integer),
        Column("grid_width", Integer),
        Column("grid_height", Integer),
        Column("total_cells", Integer),
        Column("classified_cells", Integer),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True)),
        Column("error", Text),
        UniqueConstraint(
            "aoi_id", "requested_month", "model_version", "pipeline_version",
            name="uq_inference_run_identity",
        ),
    )
    Index("idx_inference_run_aoi_month", run.c.aoi_id, run.c.requested_month)
    Index("idx_inference_run_status", run.c.status)

    asset = Table(
        "raster_asset",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), ForeignKey("inference_run.id", ondelete="CASCADE"), nullable=False),
        Column("asset_type", String(32), nullable=False),
        Column("object_key", Text, nullable=False),
        Column("uri", Text, nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("file_size", BigInteger, nullable=False),
        Column("format", String(24), nullable=False),
        Column("width", Integer, nullable=False),
        Column("height", Integer, nullable=False),
        Column("band_count", Integer, nullable=False),
        Column("dtype", String(32), nullable=False),
        Column("crs", String(32), nullable=False),
        Column("nodata", String(32)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("run_id", "asset_type", name="uq_raster_asset_run_type"),
    )

    land_class = Table(
        "land_cover_class",
        metadata,
        Column("id", SmallInteger, primary_key=True, autoincrement=False),
        Column("name", String(32), nullable=False, unique=True),
        Column("color", String(9), nullable=False),
    )

    summary = Table(
        "classification_summary",
        metadata,
        Column("id", integer_pk, primary_key=True, autoincrement=True),
        Column("run_id", String(36), ForeignKey("inference_run.id", ondelete="CASCADE"), nullable=False),
        Column("class_id", SmallInteger, ForeignKey("land_cover_class.id"), nullable=False),
        Column("pixel_count", Integer, nullable=False),
        Column("percent", Float, nullable=False),
        Column("area_km2", Float, nullable=False),
        UniqueConstraint("run_id", "class_id", name="uq_summary_run_class"),
    )
    Index("idx_summary_class", summary.c.class_id)

    observation = Table(
        "classification_observation",
        metadata,
        Column("id", integer_pk, primary_key=True, autoincrement=True),
        Column("run_id", String(36), ForeignKey("inference_run.id", ondelete="CASCADE"), nullable=False),
        Column("aoi_id", String(80), ForeignKey("area_of_interest.id"), nullable=False),
        Column("observed_month", Date, nullable=False),
        Column("row_index", Integer, nullable=False),
        Column("column_index", Integer, nullable=False),
        Column("longitude", Float, nullable=False),
        Column("latitude", Float, nullable=False),
        Column("class_id", SmallInteger, ForeignKey("land_cover_class.id"), nullable=False),
        Column("clear_observation_count", Integer, nullable=False),
        UniqueConstraint("run_id", "row_index", "column_index", name="uq_observation_cell"),
    )
    Index("idx_observation_aoi_month", observation.c.aoi_id, observation.c.observed_month)
    Index(
        "idx_observation_coordinate_month",
        observation.c.longitude,
        observation.c.latitude,
        observation.c.observed_month,
    )
    Index("idx_observation_class", observation.c.class_id)

    source_scene = Table(
        "source_scene",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), ForeignKey("inference_run.id", ondelete="CASCADE"), nullable=False),
        Column("collection", String(32), nullable=False),
        Column("scene_id", String(256), nullable=False),
        Column("acquired_at", DateTime(timezone=True), nullable=False),
        Column("cloud_cover", Float),
        Column("used", Boolean, nullable=False),
        Column("fetch_error", Text),
        UniqueConstraint("run_id", "collection", "scene_id", name="uq_source_scene"),
    )
    Index("idx_source_scene_run", source_scene.c.run_id)

    metadata.create_all(engine)
    db = Database(engine, metadata, aoi, run, asset, land_class, summary, observation, source_scene)
    seed_land_classes(db)
    return db


def seed_land_classes(db: Database) -> None:
    rows = [(0, "NoData", "#00000000"), *(
        (CLASS_CODES[name], name, "#%02X%02X%02X" % CLASS_COLORS[name])
        for name in CLASS_CODES
    )]
    with db.engine.begin() as connection:
        existing = set(connection.execute(select(db.land_class.c.id)).scalars())
        values = [
            {"id": class_id, "name": name, "color": color}
            for class_id, name, color in rows
            if class_id not in existing
        ]
        if values:
            connection.execute(insert(db.land_class), values)


def ensure_aoi(db: Database, spec: AOISpec) -> None:
    polygon = canonical_polygon(spec.polygon)
    west, south, east, north = spec.bounds
    with db.engine.begin() as connection:
        existing = connection.execute(
            select(db.aoi.c.polygon).where(db.aoi.c.id == spec.aoi_id)
        ).scalar_one_or_none()
        if existing is None:
            connection.execute(insert(db.aoi).values(
                id=spec.aoi_id,
                polygon=polygon,
                west=west,
                south=south,
                east=east,
                north=north,
                created_at=utcnow(),
            ))
            return
        if existing != polygon:
            raise ValueError(
                f"AOI {spec.aoi_id!r} already exists with different coordinates. "
                "Use a new AOI id to avoid mixing two locations."
            )


def prepare_run(
    db: Database,
    spec: AOISpec,
    month: date,
    model_id: str,
    force: bool,
) -> str | None:
    identity = (
        (db.run.c.aoi_id == spec.aoi_id)
        & (db.run.c.requested_month == month)
        & (db.run.c.model_version == model_id)
        & (db.run.c.pipeline_version == PIPELINE_VERSION)
    )
    with db.engine.begin() as connection:
        existing = connection.execute(
            select(db.run.c.id, db.run.c.status).where(identity)
        ).first()
        if existing and existing.status == "completed" and not force:
            return None

        if existing:
            run_id = existing.id
            for table in (db.observation, db.summary, db.asset, db.source_scene):
                connection.execute(delete(table).where(table.c.run_id == run_id))
            connection.execute(
                update(db.run).where(db.run.c.id == run_id).values(
                    status="running",
                    attempt_count=0,
                    started_at=utcnow(),
                    completed_at=None,
                    error=None,
                )
            )
        else:
            run_id = str(uuid.uuid4())
            connection.execute(insert(db.run).values(
                id=run_id,
                aoi_id=spec.aoi_id,
                requested_month=month,
                status="running",
                attempt_count=0,
                model_version=model_id,
                pipeline_version=PIPELINE_VERSION,
                started_at=utcnow(),
            ))
    return run_id


def record_attempt(db: Database, run_id: str, attempt: int) -> None:
    with db.engine.begin() as connection:
        connection.execute(
            update(db.run).where(db.run.c.id == run_id).values(attempt_count=attempt)
        )


def mark_failed(db: Database, run_id: str, exc: BaseException) -> None:
    message = f"{type(exc).__name__}: {exc}"[:8000]
    with db.engine.begin() as connection:
        connection.execute(
            update(db.run).where(db.run.c.id == run_id).values(
                status="failed", completed_at=utcnow(), error=message,
            )
        )


def bbox_area_km2(bounds: tuple[float, float, float, float]) -> float:
    west, south, east, north = bounds
    middle_latitude = math.radians((north + south) / 2.0)
    return (
        abs(north - south)
        * 110.574
        * abs(east - west)
        * 111.320
        * math.cos(middle_latitude)
    )


def complete_run(
    db: Database,
    run_id: str,
    spec: AOISpec,
    result: MonthResult,
    assets: list[AssetRecord],
) -> None:
    total = result.total_cells
    area = bbox_area_km2(result.bounds)
    west, south, east, north = result.bounds
    lon_step = (east - west) / result.width
    lat_step = (north - south) / result.height

    with db.engine.begin() as connection:
        now = utcnow()
        if assets:
            connection.execute(insert(db.asset), [
                {
                    "id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "asset_type": value.asset_type,
                    "object_key": value.object_key,
                    "uri": value.uri,
                    "sha256": value.sha256,
                    "file_size": value.file_size,
                    "format": value.image_format,
                    "width": value.width,
                    "height": value.height,
                    "band_count": value.band_count,
                    "dtype": value.dtype,
                    "crs": value.crs,
                    "nodata": value.nodata,
                    "created_at": now,
                }
                for value in assets
            ])

        summary_rows = []
        for name, class_id in CLASS_CODES.items():
            count = result.class_counts.get(name, 0)
            summary_rows.append({
                "run_id": run_id,
                "class_id": class_id,
                "pixel_count": count,
                "percent": 100.0 * count / total if total else 0.0,
                "area_km2": area * count / total if total else 0.0,
            })
        connection.execute(insert(db.summary), summary_rows)

        if result.sources:
            connection.execute(insert(db.source_scene), [
                {
                    "id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "collection": source.collection,
                    "scene_id": source.scene_id,
                    "acquired_at": source.acquired_at,
                    "cloud_cover": source.cloud_cover,
                    "used": source.used,
                    "fetch_error": source.fetch_error,
                }
                for source in result.sources
            ])

        chunk: list[dict] = []
        for row_index in range(result.height):
            latitude = north - (row_index + 0.5) * lat_step
            for column_index in range(result.width):
                longitude = west + (column_index + 0.5) * lon_step
                chunk.append({
                    "run_id": run_id,
                    "aoi_id": spec.aoi_id,
                    "observed_month": result.month,
                    "row_index": row_index,
                    "column_index": column_index,
                    "longitude": longitude,
                    "latitude": latitude,
                    "class_id": int(result.classification[row_index, column_index]),
                    "clear_observation_count": int(result.clear_counts[row_index, column_index]),
                })
                if len(chunk) >= 5000:
                    connection.execute(insert(db.observation), chunk)
                    chunk.clear()
        if chunk:
            connection.execute(insert(db.observation), chunk)

        connection.execute(
            update(db.run).where(db.run.c.id == run_id).values(
                window_start=result.window_start,
                window_end=result.window_end,
                status="completed",
                resolution_metres=result.resolution_metres,
                grid_width=result.width,
                grid_height=result.height,
                total_cells=result.total_cells,
                classified_cells=result.classified_cells,
                completed_at=now,
                error=None,
            )
        )


def build_s2_config(client_id: str, client_secret: str) -> SHConfig:
    return SHConfig(
        sh_client_id=client_id,
        sh_client_secret=client_secret,
        sh_base_url="https://services.sentinel-hub.com",
    )


def build_landsat_catalog_config(client_id: str, client_secret: str) -> SHConfig:
    return SHConfig(
        sh_client_id=client_id,
        sh_client_secret=client_secret,
        sh_base_url="https://services-uswest2.sentinel-hub.com",
    )


def build_landsat_process_config(client_id: str, client_secret: str) -> SHConfig:
    # This mirrors the existing backend, where the Landsat Process request uses
    # the default endpoint while its catalogue search uses us-west-2.
    return SHConfig(sh_client_id=client_id, sh_client_secret=client_secret)


def clamp_dimensions(width: int, height: int, maximum: int) -> tuple[int, int]:
    longest = max(width, height)
    if longest <= maximum:
        return max(1, width), max(1, height)
    scale = maximum / longest
    return max(1, int(width * scale)), max(1, int(height * scale))


def parse_catalog_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def search_scenes(
    collection: DataCollection,
    collection_name: str,
    bbox: BBox,
    start: date,
    end: date,
    config: SHConfig,
) -> tuple[list[str], list[SceneMetadata]]:
    catalog = SentinelHubCatalog(config=config)
    items = list(catalog.search(
        collection,
        bbox=bbox,
        time=(start.isoformat(), end.isoformat()),
        fields={
            "include": ["id", "properties.datetime", "properties.eo:cloud_cover"],
            "exclude": [],
        },
    ))
    sources: list[SceneMetadata] = []
    for item in items:
        properties = item.get("properties") or {}
        timestamp = properties.get("datetime")
        if not timestamp:
            continue
        cloud_value = properties.get("eo:cloud_cover")
        sources.append(SceneMetadata(
            collection=collection_name,
            scene_id=str(item.get("id") or f"{collection_name}:{timestamp}"),
            acquired_at=parse_catalog_datetime(str(timestamp)),
            cloud_cover=float(cloud_value) if cloud_value is not None else None,
        ))
    days = sorted({source.day for source in sources})
    LOG.info("%s catalogue: %d scenes on %d dates", collection_name, len(sources), len(days))
    return days, sources


def mark_sources(
    sources: list[SceneMetadata],
    collection: str,
    day: str,
    used: bool,
    error: str | None = None,
) -> None:
    for source in sources:
        if source.collection == collection and source.day == day:
            source.used = used
            source.fetch_error = error


def fetch_sentinel2(day: str, bbox: BBox, size: tuple[int, int], config: SHConfig):
    request = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2,
        input_data=[SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL2_L2A,
            time_interval=(f"{day}T00:00:00Z", f"{day}T23:59:59Z"),
            mosaicking_order="leastCC",
        )],
        responses=[
            SentinelHubRequest.output_response("bands", MimeType.TIFF),
            SentinelHubRequest.output_response("scl", MimeType.TIFF),
        ],
        bbox=bbox,
        size=size,
        config=config,
    )
    response = request.get_data()[0]
    bands = response["bands.tif"].astype(np.float32)
    scl = response["scl.tif"]
    if scl.ndim == 3:
        scl = scl[:, :, 0]
    invalid = np.isin(scl, INVALID_S2_SCL_VALUES)
    bands[invalid, :] = np.nan
    return bands, scl.astype(np.uint8, copy=False), invalid


def fetch_landsat(day: str, bbox: BBox, size: tuple[int, int], config: SHConfig):
    def request(evalscript: str):
        value = SentinelHubRequest(
            evalscript=evalscript,
            input_data=[SentinelHubRequest.input_data(
                data_collection=DataCollection.LANDSAT_OT_L2,
                time_interval=(f"{day}T00:00:00Z", f"{day}T23:59:59Z"),
                mosaicking_order="leastCC",
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox,
            size=size,
            config=config,
        ).get_data()[0]
        return value["default.tif"] if isinstance(value, dict) else value

    bands = request(EVALSCRIPT_LANDSAT_SR).astype(np.float32)
    qa = request(EVALSCRIPT_LANDSAT_BQA).astype(np.uint16)
    if qa.ndim == 3:
        qa = qa[:, :, 0]
    invalid = (qa & LANDSAT_BQA_MASK) != 0
    bands[invalid, :] = np.nan
    return bands, invalid


def composite_month(
    spec: AOISpec,
    month: date,
    client_id: str,
    client_secret: str,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    list[SceneMetadata],
    tuple[float, float, float, float],
    date,
    date,
]:
    start = month.replace(day=1)
    end = month_end(month)
    if start > end:
        raise ValueError(f"Cannot process future month {month:%Y-%m}")

    bounds = spec.bounds
    bbox = BBox(bounds, crs=CRS.WGS84)
    size = clamp_dimensions(*bbox_to_dimensions(bbox, resolution=PROCESS_RESOLUTION_METRES), MAX_PROCESS_DIMENSION)
    LOG.info("AOI=%s month=%s bbox=%s raster=%sx%s", spec.aoi_id, month, bounds, size[0], size[1])

    s2_config = build_s2_config(client_id, client_secret)
    landsat_catalog_config = build_landsat_catalog_config(client_id, client_secret)
    landsat_process_config = build_landsat_process_config(client_id, client_secret)

    s2_days, s2_sources = search_scenes(
        DataCollection.SENTINEL2_L2A, "sentinel-2-l2a", bbox, start, end, s2_config,
    )
    landsat_days, landsat_sources = search_scenes(
        DataCollection.LANDSAT_OT_L2, "landsat-ot-l2", bbox, start, end,
        landsat_catalog_config,
    )
    sources = s2_sources + landsat_sources

    band_stack: list[np.ndarray] = []
    scl_stack: list[np.ndarray] = []
    for day in s2_days:
        try:
            bands, scl, invalid = fetch_sentinel2(day, bbox, size, s2_config)
            band_stack.append(bands)
            scl_stack.append(scl)
            mark_sources(sources, "sentinel-2-l2a", day, True)
            LOG.info("S2 %s accepted; cloud/no-data %.1f%%", day, 100 * float(invalid.mean()))
        except Exception as exc:  # One bad acquisition must not lose the whole month.
            message = f"{type(exc).__name__}: {exc}"[:2000]
            mark_sources(sources, "sentinel-2-l2a", day, False, message)
            LOG.warning("S2 %s skipped: %s", day, exc)

    for day in landsat_days:
        try:
            bands, invalid = fetch_landsat(day, bbox, size, landsat_process_config)
            band_stack.append(bands)
            mark_sources(sources, "landsat-ot-l2", day, True)
            LOG.info("Landsat %s accepted; cloud/no-data %.1f%%", day, 100 * float(invalid.mean()))
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:2000]
            mark_sources(sources, "landsat-ot-l2", day, False, message)
            LOG.warning("Landsat %s skipped: %s", day, exc)

    if not band_stack:
        raise RuntimeError(f"No usable Sentinel-2 or Landsat imagery for {month:%Y-%m}")

    stacked = np.stack(band_stack, axis=0)
    clear_counts = np.all(np.isfinite(stacked), axis=-1).sum(axis=0).astype(np.uint16)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        composite = np.nanmedian(stacked, axis=0).astype(np.float32)

    if scl_stack:
        counts = np.zeros((12, size[1], size[0]), dtype=np.uint16)
        for scl in scl_stack:
            for code in range(12):
                counts[code] += scl == code
        scl_mode = counts.argmax(axis=0).astype(np.uint8)
    else:
        scl_mode = np.full((size[1], size[0]), 7, dtype=np.uint8)

    return composite, scl_mode, clear_counts, sources, bounds, start, end


def load_artifacts() -> dict:
    global _ARTIFACTS
    if _ARTIFACTS is None:
        _ARTIFACTS = {
            "scaler": joblib.load(MODEL_DIR / "scaler.joblib"),
            "label_encoder": joblib.load(MODEL_DIR / "label_encoder.joblib"),
            "models": [joblib.load(MODEL_DIR / f"{name}.joblib") for name in MODEL_NAMES],
        }
    return _ARTIFACTS


def expand_class(frame: pd.DataFrame) -> pd.DataFrame:
    vegetation = frame["ClassID"] == 4
    frame.loc[vegetation & (frame["NDVI"] > 0.6), "classifier"] = "Tree"
    frame.loc[vegetation & (frame["NDVI"] <= 0.6), "classifier"] = "Crop"

    soil = frame["ClassID"] == 5
    frame.loc[soil & (frame["NDBI"] > 0.0), "classifier"] = "Building"
    frame.loc[soil & (frame["NDBI"] <= 0.0), "classifier"] = "Soil"

    frame.loc[frame["ClassID"] == 6, "classifier"] = "Water"
    return frame[["Longitude", "Latitude", "classifier"]]


def ensemble_predict(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    b02, b03, b04, b08, b11 = (
        frame["B02"], frame["B03"], frame["B04"], frame["B08"], frame["B11"]
    )
    denominator = b08 + 6.0 * b04 - 7.5 * b02 + 1.0
    frame["EVI"] = np.where(denominator != 0, 2.5 * (b08 - b04) / denominator, 0.0)
    denominator = b11 + b08
    frame["NDBI"] = np.where(denominator != 0, (b11 - b08) / denominator, 0.0)
    denominator = b03 + b11
    frame["MNDWI"] = np.where(denominator != 0, (b03 - b11) / denominator, 0.0)
    denominator = b03 - b08
    frame["BSI"] = np.where(denominator != 0, (b03 + b08) / denominator, 0.0)
    denominator = b08 + b04
    frame["NDVI"] = np.where(denominator != 0, (b08 - b04) / denominator, 0.0)

    features = [
        "B04", "B03", "B08", "B02", "B01", "B12", "B11",
        "EVI", "NDBI", "MNDWI", "BSI", "NDVI",
    ]
    artifacts = load_artifacts()
    scaled = artifacts["scaler"].transform(frame[features].to_numpy())
    predictions = np.vstack([
        np.asarray(model.predict(scaled)).flatten() for model in artifacts["models"]
    ])
    voted = mode(predictions, axis=0, keepdims=False).mode
    frame["ClassID"] = artifacts["label_encoder"].inverse_transform(voted.astype(int))
    return expand_class(frame)


def classify_composite(
    composite: np.ndarray,
    scl_mode: np.ndarray,
    clear_counts: np.ndarray,
    bounds: tuple[float, float, float, float],
) -> tuple[np.ndarray, np.ndarray, int, np.ndarray]:
    source_height, source_width = composite.shape[:2]
    step = 1
    while math.ceil(source_height / step) * math.ceil(source_width / step) > MAX_CLASSIFY_CELLS:
        step += 1

    sampled = composite[::step, ::step, :]
    sampled_scl = scl_mode[::step, ::step]
    sampled_clear = clear_counts[::step, ::step]
    height, width = sampled.shape[:2]
    west, south, east, north = bounds
    lons = west + (np.arange(width) + 0.5) * (east - west) / width
    lats = north - (np.arange(height) + 0.5) * (north - south) / height
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    data = {
        "Longitude": lon_grid.ravel(),
        "Latitude": lat_grid.ravel(),
        "ClassID": sampled_scl.ravel(),
    }
    for band_index, name in enumerate(BAND_NAMES):
        data[name] = sampled[:, :, band_index].ravel()
    frame = pd.DataFrame(data)

    labels = np.full(len(frame), "", dtype=object)
    valid = frame[CLASSIFY_BAND_COLUMNS].notna().all(axis=1).to_numpy()
    if valid.any():
        positions = np.flatnonzero(valid)
        work = frame.iloc[positions].reset_index(drop=True)
        resolved_mask = work["ClassID"].isin([4, 5, 6])
        parts: list[pd.DataFrame] = []

        unresolved = work.loc[~resolved_mask].copy()
        if not unresolved.empty:
            unresolved["classifier"] = pd.NA
            parts.append(ensemble_predict(unresolved))

        resolved = work.loc[resolved_mask].copy()
        if not resolved.empty:
            denominator = resolved["B08"] + resolved["B04"]
            resolved["NDVI"] = np.where(
                denominator != 0,
                (resolved["B08"] - resolved["B04"]) / denominator,
                0.0,
            )
            denominator = resolved["B11"] + resolved["B08"]
            resolved["NDBI"] = np.where(
                denominator != 0,
                (resolved["B11"] - resolved["B08"]) / denominator,
                0.0,
            )
            resolved["classifier"] = pd.NA
            parts.append(expand_class(resolved))

        combined = pd.concat(parts).sort_index()
        values = combined["classifier"].fillna("").to_numpy(dtype=object)
        values = np.where(values == "Building", "Soil", values)
        labels[positions] = values

    label_grid = labels.reshape(height, width)
    codes = np.zeros((height, width), dtype=np.uint8)
    for name, code in CLASS_CODES.items():
        codes[label_grid == name] = code
    LOG.info(
        "Classified %d/%d cells at %d m/cell",
        int(np.count_nonzero(codes)), codes.size, PROCESS_RESOLUTION_METRES * step,
    )
    return codes, sampled_clear, PROCESS_RESOLUTION_METRES * step, sampled


def fetch_true_color(
    spec: AOISpec,
    start: date,
    end: date,
    client_id: str,
    client_secret: str,
) -> tuple[np.ndarray, np.ndarray]:
    bbox = BBox(spec.bounds, crs=CRS.WGS84)
    size = clamp_dimensions(*bbox_to_dimensions(bbox, resolution=10), TRUE_COLOR_MAX_DIMENSION)
    request = SentinelHubRequest(
        evalscript=EVALSCRIPT_TRUE_COLOR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL2_L2A,
            time_interval=(start.isoformat(), end.isoformat()),
            mosaicking_order="leastCC",
        )],
        responses=[
            SentinelHubRequest.output_response("rgb", MimeType.TIFF),
            SentinelHubRequest.output_response("mask", MimeType.TIFF),
        ],
        bbox=bbox,
        size=size,
        config=build_s2_config(client_id, client_secret),
    )
    response = request.get_data()[0]
    rgb = response["rgb.tif"].astype(np.float32)
    valid = response["mask.tif"]
    if valid.ndim == 3:
        valid = valid[:, :, 0]
    return rgb, valid.astype(bool)


def stretch_true_color(rgb: np.ndarray, valid: np.ndarray) -> Image.Image:
    rgb = np.asarray(rgb, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    valid &= np.isfinite(rgb).all(axis=-1)
    rgba = np.zeros((*valid.shape, 4), dtype=np.uint8)
    if not valid.any():
        return Image.fromarray(rgba, mode="RGBA")
    for channel in range(3):
        band = rgb[:, :, channel]
        observed = band[valid]
        low, high = np.percentile(observed, TRUE_COLOR_PERCENTILES)
        if high <= low:
            low, high = float(observed.min()), float(observed.max())
        if high <= low:
            high = low + 1e-6
        stretched = np.clip((band - low) / (high - low), 0, 1)
        stretched = np.where(valid, stretched, 0) ** TRUE_COLOR_GAMMA
        rgba[:, :, channel] = (stretched * 255).astype(np.uint8)
    rgba[:, :, 3] = np.where(valid, 255, 0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def render_composite_true_color(sampled: np.ndarray) -> Image.Image:
    # Backend band order for visible RGB is B04, B03, B02.
    rgb = sampled[:, :, [3, 2, 1]]
    valid = np.isfinite(rgb).all(axis=-1)
    return stretch_true_color(rgb, valid)


def render_classification(codes: np.ndarray) -> Image.Image:
    rgba = np.zeros((*codes.shape, 4), dtype=np.uint8)
    for name, code in CLASS_CODES.items():
        red, green, blue = CLASS_COLORS[name]
        rgba[codes == code] = (red, green, blue, 255)
    image = Image.fromarray(rgba, mode="RGBA")
    longest = max(image.size)
    scale = max(1, min(TRUE_COLOR_MAX_DIMENSION // longest, math.ceil(MIN_DISPLAY_DIMENSION / longest)))
    if scale > 1:
        image = image.resize(
            (image.width * scale, image.height * scale),
            resample=Image.Resampling.NEAREST,
        )
    return image


def process_month(
    spec: AOISpec,
    month: date,
    client_id: str,
    client_secret: str,
) -> MonthResult:
    composite, scl_mode, clear_counts, sources, bounds, start, end = composite_month(
        spec, month, client_id, client_secret,
    )
    codes, sampled_clear, resolution, sampled = classify_composite(
        composite, scl_mode, clear_counts, bounds,
    )
    overlay = render_classification(codes)

    try:
        rgb, valid = fetch_true_color(spec, start, end, client_id, client_secret)
        true_color = stretch_true_color(rgb, valid)
        if not valid.any():
            raise RuntimeError("true-colour request returned no valid pixels")
    except Exception as exc:
        LOG.warning("Native true-colour picture failed (%s); using model composite", exc)
        true_color = render_composite_true_color(sampled)

    counts = {name: int(np.count_nonzero(codes == code)) for name, code in CLASS_CODES.items()}
    return MonthResult(
        month=month,
        window_start=start,
        window_end=end,
        bounds=bounds,
        composite=composite,
        classification=codes,
        clear_counts=sampled_clear,
        resolution_metres=resolution,
        sources=sources,
        true_color=true_color,
        class_overlay=overlay,
        class_counts=counts,
    )


def atomic_png(image: Image.Image, destination: Path, compress_level: int = 6) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        image.save(temporary, format="PNG", compress_level=compress_level)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_cog(
    values: np.ndarray,
    destination: Path,
    bounds: tuple[float, float, float, float],
    nodata: int | float,
    descriptions: Sequence[str],
    categorical: bool,
) -> None:
    if rasterio is None or from_bounds is None:  # pragma: no cover - checked at startup
        raise RuntimeError("rasterio is unavailable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.tmp.tif")

    if values.ndim == 2:
        data = values[np.newaxis, :, :]
    elif values.ndim == 3:
        # In memory composites are height x width x bands; Rasterio is bands x height x width.
        data = np.moveaxis(values, -1, 0)
    else:
        raise ValueError(f"Cannot write raster with shape {values.shape}")

    count, height, width = data.shape
    west, south, east, north = bounds
    profile = {
        "driver": "COG",
        "height": height,
        "width": width,
        "count": count,
        "dtype": data.dtype,
        "crs": "EPSG:4326",
        "transform": from_bounds(west, south, east, north, width, height),
        "nodata": nodata,
        "compress": "DEFLATE",
        "blocksize": 512,
        "bigtiff": "IF_SAFER",
        "overview_resampling": "NEAREST" if categorical else "AVERAGE",
    }
    try:
        with rasterio.open(temporary, "w", **profile) as dataset:
            dataset.write(data)
            dataset.descriptions = tuple(descriptions)
            dataset.update_tags(
                pipeline="Ubiquitous Eye Capstone2.0",
                pipeline_version=PIPELINE_VERSION,
                categorical=str(categorical).lower(),
            )
            if categorical:
                color_map = {0: (0, 0, 0, 0)}
                color_map.update({
                    CLASS_CODES[name]: (*CLASS_COLORS[name], 255) for name in CLASS_CODES
                })
                dataset.write_colormap(1, color_map)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_record(
    asset_type: str,
    path: Path,
    object_store: Path,
    image_format: str,
    width: int,
    height: int,
    band_count: int,
    dtype: str,
    nodata: str | None,
) -> AssetRecord:
    resolved = path.resolve()
    return AssetRecord(
        asset_type=asset_type,
        object_key=resolved.relative_to(object_store.resolve()).as_posix(),
        uri=resolved.as_uri(),
        sha256=sha256_file(resolved),
        file_size=resolved.stat().st_size,
        image_format=image_format,
        width=width,
        height=height,
        band_count=band_count,
        dtype=dtype,
        crs="EPSG:4326",
        nodata=nodata,
    )


def store_assets(
    spec: AOISpec,
    result: MonthResult,
    object_store: Path,
    save_composite: bool,
) -> list[AssetRecord]:
    directory = object_store / spec.aoi_id / f"{result.month.year:04d}" / f"{result.month.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    records: list[AssetRecord] = []

    classification_path = directory / "classification.cog.tif"
    atomic_cog(
        result.classification,
        classification_path,
        result.bounds,
        nodata=0,
        descriptions=("land_cover_class",),
        categorical=True,
    )
    records.append(asset_record(
        "classification_cog", classification_path, object_store, "COG",
        result.width, result.height, 1, "uint8", "0",
    ))

    if save_composite:
        composite_path = directory / "composite.cog.tif"
        atomic_cog(
            result.composite,
            composite_path,
            result.bounds,
            nodata=float("nan"),
            descriptions=BAND_NAMES,
            categorical=False,
        )
        records.append(asset_record(
            "composite_cog", composite_path, object_store, "COG",
            result.composite.shape[1], result.composite.shape[0],
            result.composite.shape[2], "float32", "NaN",
        ))

    true_color_path = directory / "true-color.png"
    atomic_png(result.true_color, true_color_path, compress_level=6)
    records.append(asset_record(
        "true_color_png", true_color_path, object_store, "PNG",
        result.true_color.width, result.true_color.height, 4, "uint8", "alpha=0",
    ))

    overlay_path = directory / "classification.png"
    atomic_png(result.class_overlay, overlay_path, compress_level=3)
    records.append(asset_record(
        "classification_png", overlay_path, object_store, "PNG",
        result.class_overlay.width, result.class_overlay.height, 4, "uint8", "alpha=0",
    ))
    return records


def default_database_url() -> str:
    configured = os.environ.get("DATABASE_URL", "").strip()
    if configured:
        return configured
    return f"sqlite:///{(SCRIPT_DIR / 'capstone.db').as_posix()}"


def build_jobs(specs: list[AOISpec]) -> list[tuple[AOISpec, date]]:
    by_id: dict[str, tuple[tuple[float, float], ...]] = {}
    jobs: dict[tuple[str, date], tuple[AOISpec, date]] = {}
    for spec in specs:
        previous = by_id.setdefault(spec.aoi_id, spec.polygon)
        if previous != spec.polygon:
            raise ValueError(f"AOI {spec.aoi_id!r} appears with different polygons in input")
        for month in iter_months(spec.start_month, spec.end_month):
            jobs[(spec.aoi_id, month)] = (spec, month)
    return [jobs[key] for key in sorted(jobs)]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and store historical monthly land-cover classifications."
    )
    parser.add_argument(
        "--input", type=Path, default=SCRIPT_DIR / "input.txt",
        help="AOI input file (default: Capstone2.0/input.txt)",
    )
    parser.add_argument(
        "--object-store", type=Path, default=SCRIPT_DIR / "object_store",
        help="Local object-store root",
    )
    parser.add_argument(
        "--database-url", default=None,
        help="SQLAlchemy URL; defaults to DATABASE_URL or local capstone.db",
    )
    parser.add_argument(
        "--skip-composite", action="store_true",
        help="Do not retain the seven-band monthly composite COG",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Recompute jobs already marked completed",
    )
    parser.add_argument(
        "--retries", type=int, default=3,
        help="Attempts per failed AOI/month (default: 3)",
    )
    parser.add_argument(
        "--retry-delay", type=float, default=20.0,
        help="Initial retry delay in seconds; doubles each attempt",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate input and print jobs without network or database writes",
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO",
    )
    args = parser.parse_args(argv)
    if args.retries < 1:
        parser.error("--retries must be at least 1")
    if args.retry_delay < 0:
        parser.error("--retry-delay cannot be negative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    load_env_file(SCRIPT_DIR / ".env")
    if Path.cwd().resolve() != SCRIPT_DIR:
        load_env_file(Path.cwd() / ".env")

    try:
        specs = parse_input(args.input.resolve())
        jobs = build_jobs(specs)
    except Exception as exc:
        LOG.error("Input validation failed: %s", exc)
        return 2

    LOG.info("Validated %d AOI record(s), %d monthly job(s)", len(specs), len(jobs))
    if args.dry_run:
        for spec, month in jobs:
            print(f"{spec.aoi_id}|{month:%Y-%m}|bbox={spec.bounds}")
        return 0

    try:
        check_runtime()
        model_id = model_version()
        client_id = os.environ.get("SH_CLIENT_ID", "").strip()
        client_secret = os.environ.get("SH_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise RuntimeError(
                "SH_CLIENT_ID and SH_CLIENT_SECRET are required. Put them in "
                "Capstone2.0/.env or the process environment."
            )
        object_store = args.object_store.resolve()
        object_store.mkdir(parents=True, exist_ok=True)
        database_url = args.database_url or default_database_url()
        db = build_database(database_url)
        for spec in specs:
            ensure_aoi(db, spec)
    except Exception as exc:
        LOG.error("Startup failed: %s", exc)
        return 2

    completed = skipped = failed = 0
    for job_number, (spec, month) in enumerate(jobs, start=1):
        label = f"{spec.aoi_id}/{month:%Y-%m}"
        try:
            run_id = prepare_run(db, spec, month, model_id, args.force)
        except Exception as exc:
            LOG.error("[%d/%d] %s could not create job: %s", job_number, len(jobs), label, exc)
            failed += 1
            continue

        if run_id is None:
            LOG.info("[%d/%d] %s already completed; skipping", job_number, len(jobs), label)
            skipped += 1
            continue

        LOG.info("[%d/%d] Starting %s", job_number, len(jobs), label)
        last_error: BaseException | None = None
        for attempt in range(1, args.retries + 1):
            record_attempt(db, run_id, attempt)
            try:
                result = process_month(spec, month, client_id, client_secret)
                assets = store_assets(
                    spec, result, object_store, save_composite=not args.skip_composite,
                )
                complete_run(db, run_id, spec, result, assets)
                LOG.info(
                    "Completed %s: %d/%d cells, %d assets",
                    label, result.classified_cells, result.total_cells, len(assets),
                )
                completed += 1
                last_error = None
                break
            except KeyboardInterrupt:
                LOG.warning("Interrupted during %s; it will resume on the next run", label)
                return 130
            except Exception as exc:
                last_error = exc
                LOG.exception("%s attempt %d/%d failed", label, attempt, args.retries)
                if attempt < args.retries:
                    delay = args.retry_delay * (2 ** (attempt - 1))
                    LOG.info("Retrying %s in %.1f seconds", label, delay)
                    time.sleep(delay)

        if last_error is not None:
            try:
                mark_failed(db, run_id, last_error)
            except Exception as db_exc:
                LOG.error("Could not record failure for %s: %s", label, db_exc)
            failed += 1

    LOG.info(
        "Finished: completed=%d skipped=%d failed=%d total=%d",
        completed, skipped, failed, len(jobs),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
