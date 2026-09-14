#!/usr/bin/env python3
"""Batch historical land-cover inference pipeline for the Ubiquitous-Eye project.

Input format (one line per AOI):
    aoi_id|start_month|end_month|[[lon,lat],[lon,lat],...]

Example:
    sundarbans|2016-01|2026-12|[[89.8,22.1],[89.9,22.1],[89.9,22.2],[89.8,22.2]]

This script intentionally reuses the project's existing backend logic instead of
re-implementing the Sentinel Hub + preprocessing pipeline. The working pattern is:

    1. parse AOI and time range from input.txt
    2. generate a job for each month between start_month and end_month
    3. call run_composite_pipeline() from server.bimonthly_composite
    4. run the ensemble model from inference.inference
    5. save classified output + metadata
    6. store database-ready rows (metadata only; large raster files stay in object storage)

This is a batch-orchestration script, not a replacement for the app's API.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

import numpy as np
import pandas as pd


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("capstone.batch")

ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "server"

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")
else:
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

# Keep only the project root on the import path so `inference` stays a package.
for candidate in (str(ROOT),):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

try:
    import importlib.util

    composite_spec = importlib.util.spec_from_file_location(
        "ubiquitous_server_bimonthly_composite",
        ROOT / "server" / "bimonthly_composite.py",
    )
    composite_module = importlib.util.module_from_spec(composite_spec)
    assert composite_spec is not None and composite_spec.loader is not None
    composite_spec.loader.exec_module(composite_module)
    run_composite_pipeline = composite_module.run_composite_pipeline
    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - user-facing guidance
    run_composite_pipeline = None
    IMPORT_ERROR = exc

try:
    import importlib.util

    inference_spec = importlib.util.spec_from_file_location(
        "ubiquitous_inference_module",
        ROOT / "inference" / "inference.py",
    )
    inference_module = importlib.util.module_from_spec(inference_spec)
    assert inference_spec is not None and inference_spec.loader is not None
    inference_spec.loader.exec_module(inference_module)
    ensemble_predict = inference_module.ensemble_predict
    INFERENCE_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - user-facing guidance
    ensemble_predict = None
    INFERENCE_IMPORT_ERROR = exc

CLASS_MAP = {
    "Tree": 1,
    "Crop": 2,
    "Water": 3,
    "Soil": 4,
    "Building": 5,
    "Unknown": 0,
    "": 0,
}

# A 20 km tile keeps the Sentinel Hub requests and in-memory scene stacks at a
# manageable size. Larger AOIs are split before any satellite request is made.
MAX_TILE_SIDE_KM = 20.0


def parse_input_file(path: str | Path) -> List[dict]:
    """Parse the pipeline input file.

    Expected format:
        aoi_id|start_month|end_month|[[lon,lat],[lon,lat],...]
    """
    rows: List[dict] = []
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            raise ValueError(f"Line {line_number}: expected 4 pipe-separated parts: {raw_line}")

        aoi_id, start_month, end_month, polygon_text = parts
        polygon = parse_polygon(polygon_text)
        rows.append(
            {
                "aoi_id": aoi_id,
                "start_month": start_month,
                "end_month": end_month,
                "polygon": polygon,
                "source_line": line_number,
            }
        )
    return rows


def parse_polygon(polygon_text: str) -> List[List[float]]:
    """Parse a polygon string into a list of [lon, lat] floats."""
    polygon_text = polygon_text.strip()
    if not polygon_text:
        raise ValueError("Polygon text is empty")

    try:
        polygon = json.loads(polygon_text)
    except json.JSONDecodeError:
        raise ValueError(f"Invalid polygon JSON: {polygon_text!r}")

    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError("Polygon must be a list with at least 3 coordinates")

    normalized: List[List[float]] = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"Each polygon point must be [lon, lat], got: {point!r}")
        normalized.append([float(point[0]), float(point[1])])
    return normalized


def month_iter(start_month: str, end_month: str) -> Iterator[str]:
    """Yield YYYY-MM strings in ascending order between start_month and end_month."""
    start = datetime.strptime(start_month, "%Y-%m")
    end = datetime.strptime(end_month, "%Y-%m")

    current = start
    while current <= end:
        yield current.strftime("%Y-%m")
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)


def ensure_runtime_config() -> None:
    """Check that the runtime can access Sentinel Hub credentials."""
    client_id = os.environ.get("SH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing SH_CLIENT_ID / SH_CLIENT_SECRET in environment. "
            "Add them to the .env file or export them before running."
        )

    if run_composite_pipeline is None:
        raise RuntimeError(f"Unable to import run_composite_pipeline: {IMPORT_ERROR}")

    # This batch job is an imagery archive builder.  Models are deliberately
    # not loaded here; inference happens later from the downloaded GeoTIFFs.


def _build_output_dir(aoi_id: str, month_key: str) -> Path:
    output_root = ROOT / "outputs" / aoi_id / month_key
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root


def _to_classification_grid(df: pd.DataFrame) -> np.ndarray:
    """Create a simple 2D class grid from the inferred rows.

    This approximates the underlying raster grid without needing a full geospatial
    raster library. It is enough to persist a class matrix and later export a .tif
    if rasterio is installed.
    """
    lat_vals = sorted(df["Latitude"].dropna().unique().tolist())
    lon_vals = sorted(df["Longitude"].dropna().unique().tolist())

    if not lat_vals or not lon_vals:
        return np.zeros((0, 0), dtype=np.uint8)

    lat_to_row = {lat: i for i, lat in enumerate(lat_vals)}
    lon_to_col = {lon: j for j, lon in enumerate(lon_vals)}

    rows = len(lat_vals)
    cols = len(lon_vals)
    grid = np.zeros((rows, cols), dtype=np.uint8)

    # Avoid iterrows(): a large tile can contain hundreds of thousands of
    # cells, and a Python loop here makes saving take longer than inference.
    valid = df.dropna(subset=["Latitude", "Longitude"])
    row_indices = np.searchsorted(np.asarray(lat_vals), valid["Latitude"].to_numpy())
    col_indices = np.searchsorted(np.asarray(lon_vals), valid["Longitude"].to_numpy())
    class_values = (
        valid["classifier"].fillna("").astype(str).str.strip().map(CLASS_MAP).fillna(0).to_numpy(dtype=np.uint8)
    )
    grid[row_indices, col_indices] = class_values
    return grid


def save_classified_raster(df: pd.DataFrame, output_dir: Path) -> None:
    """Persist a classification raster if rasterio is available.

    Falls back to CSV + .npy if rasterio is not installed. The database should not
    store the raster bytes directly; this function writes a local file for the
    batch job, and the DB should keep a pointer/URI to it later.
    """
    grid = _to_classification_grid(df)
    if grid.size == 0:
        return

    csv_path = output_dir / "classification.csv"
    df[["Longitude", "Latitude", "classifier"]].to_csv(csv_path, index=False)

    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError:
        np.save(output_dir / "classification.npy", grid)
        return

    west = float(df["Longitude"].min())
    east = float(df["Longitude"].max())
    south = float(df["Latitude"].min())
    north = float(df["Latitude"].max())

    rows, cols = grid.shape
    transform = from_bounds(west, south, east, north, cols, rows)
    output_path = output_dir / "classification.tif"
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype=grid.dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(grid, 1)


def _native_30m_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one centre cell from each 3x3 10 m expansion.

    The composite's actual observations are 30 m. Classifying all nine copied
    10 m values wastes memory and model time without adding information.
    """
    rows10 = df["Latitude"].nunique()
    cols10 = df["Longitude"].nunique()
    if rows10 < 3 or cols10 < 3:
        return df
    row_idx = np.arange(1, rows10, 3)
    col_idx = np.arange(1, cols10, 3)
    flat_idx = (row_idx[:, None] * cols10 + col_idx[None, :]).ravel()
    return df.iloc[flat_idx].copy()


def _inside_polygon(df: pd.DataFrame, polygon: List[List[float]]) -> pd.DataFrame:
    """Clip a tile's point grid to the supplied lon/lat polygon."""
    x = df["Longitude"].to_numpy(dtype=float)
    y = df["Latitude"].to_numpy(dtype=float)
    px = np.asarray([point[0] for point in polygon], dtype=float)
    py = np.asarray([point[1] for point in polygon], dtype=float)
    inside = np.zeros(len(df), dtype=bool)
    previous = len(polygon) - 1
    for current in range(len(polygon)):
        intersects = ((py[current] > y) != (py[previous] > y)) & (
            x < (px[previous] - px[current]) * (y - py[current]) /
            (py[previous] - py[current] + 1e-15) + px[current]
        )
        inside ^= intersects
        previous = current
    return df.loc[inside].copy()


def iter_tiles(polygon: List[List[float]], max_side_km: float = MAX_TILE_SIDE_KM) -> Iterator[tuple[str, List[List[float]]]]:
    """Yield rectangular tiles covering the polygon's bounding box."""
    lons = [point[0] for point in polygon]
    lats = [point[1] for point in polygon]
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    mean_lat = (south + north) / 2.0
    lat_step = max_side_km / 110.574
    lon_step = max_side_km / (111.320 * max(math.cos(math.radians(mean_lat)), 0.1))
    row = 0
    tile_south = south
    while tile_south < north:
        tile_north = min(tile_south + lat_step, north)
        col = 0
        tile_west = west
        while tile_west < east:
            tile_east = min(tile_west + lon_step, east)
            yield (
                f"tile_r{row:03d}_c{col:03d}",
                [[tile_west, tile_south], [tile_east, tile_south], [tile_east, tile_north], [tile_west, tile_north]],
            )
            col += 1
            tile_west = tile_east
        row += 1
        tile_south = tile_north


def process_month(
    aoi_id: str,
    polygon: List[List[float]],
    year: int,
    month: int,
    output_root: Path,
    tile_id: str,
    clip_polygon: List[List[float]],
) -> dict:
    """Process one AOI-month using the project's existing backend functions."""
    client_id = os.environ["SH_CLIENT_ID"].strip()
    client_secret = os.environ["SH_CLIENT_SECRET"].strip()

    df = run_composite_pipeline(
        polygon,
        year,
        month,
        client_id,
        client_secret,
        whole_month=True,
        min_clear_observations=1,
    )

    if df.empty:
        raise RuntimeError(f"No composite rows produced for {aoi_id} {year}-{month:02d}")

    df = _native_30m_cells(df)
    df = _inside_polygon(df, clip_polygon)
    if df.empty:
        raise RuntimeError(f"Tile {tile_id} has no cells inside the AOI polygon")
    classified = ensemble_predict(df)
    classified = classified.rename(columns={"classifier": "classifier"})

    month_dir = output_root / tile_id
    month_dir.mkdir(parents=True, exist_ok=True)

    classified_path = month_dir / "classified.csv"
    classified.to_csv(classified_path, index=False)
    save_classified_raster(classified, month_dir)

    summary = {
        "aoi_id": aoi_id,
        "year": year,
        "month": month,
        "tile_id": tile_id,
        "rows": int(len(classified)),
        "class_counts": classified["classifier"].value_counts().to_dict(),
        "output_dir": str(month_dir),
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    summary_path = month_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_pipeline(input_path: str | Path) -> List[dict]:
    """Run the batch pipeline for all AOIs listed in the input file."""
    ensure_runtime_config()
    jobs = parse_input_file(input_path)

    result_rows: List[dict] = []
    for job in jobs:
        aoi_id = job["aoi_id"]
        polygon = job["polygon"]
        tiles = list(iter_tiles(polygon))
        logger.info("%s: %d tile(s), %s to %s", aoi_id, len(tiles), job["start_month"], job["end_month"])
        for month_key in month_iter(job["start_month"], job["end_month"]):
            year, month = map(int, month_key.split("-"))
            out_dir = _build_output_dir(aoi_id, month_key)
            for tile_id, tile_polygon in tiles:
                tile_summary_path = out_dir / tile_id / "summary.json"
                if tile_summary_path.exists():
                    try:
                        summary = json.loads(tile_summary_path.read_text(encoding="utf-8"))
                        result_rows.append(summary)
                        print(f"[SKIP] {aoi_id} {month_key} {tile_id}: already complete")
                        continue
                    except (OSError, json.JSONDecodeError):
                        logger.warning("Ignoring unreadable checkpoint: %s", tile_summary_path)
                try:
                    summary = process_month(
                        aoi_id, tile_polygon, year, month, out_dir, tile_id, polygon
                    )
                    result_rows.append(summary)
                    print(f"[OK] {aoi_id} {month_key} {tile_id}: {summary['rows']} rows")
                except Exception as exc:  # pragma: no cover - batch-run resilience
                    print(f"[ERROR] {aoi_id} {month_key} {tile_id}: {exc}")
                    result_rows.append(
                        {
                            "aoi_id": aoi_id,
                            "year": year,
                            "month": month,
                            "tile_id": tile_id,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
    return result_rows


def _tile_bbox(tile_polygon: List[List[float]]):
    """Build the Sentinel Hub bounding box for one rectangular archive tile."""
    return composite_module.BBox(
        (
            min(point[0] for point in tile_polygon),
            min(point[1] for point in tile_polygon),
            max(point[0] for point in tile_polygon),
            max(point[1] for point in tile_polygon),
        ),
        crs=composite_module.CRS.WGS84,
    )


def _polygon_mask(shape: tuple[int, int], bbox, polygon: List[List[float]]) -> np.ndarray:
    """Return True for pixel centres contained by the original AOI polygon."""
    height, width = shape
    xs = np.linspace(bbox.min_x, bbox.max_x, width, endpoint=False) + (bbox.max_x - bbox.min_x) / (2 * width)
    ys = np.linspace(bbox.max_y, bbox.min_y, height, endpoint=False) + (bbox.min_y - bbox.max_y) / (2 * height)
    x, y = np.meshgrid(xs, ys)
    px = np.asarray([point[0] for point in polygon], dtype=float)
    py = np.asarray([point[1] for point in polygon], dtype=float)
    inside = np.zeros(shape, dtype=bool)
    previous = len(polygon) - 1
    for current in range(len(polygon)):
        crosses = ((py[current] > y) != (py[previous] > y)) & (
            x < (px[previous] - px[current]) * (y - py[current]) /
            (py[previous] - py[current] + 1e-15) + px[current]
        )
        inside ^= crosses
        previous = current
    return inside


def save_raw_tiff(bands: np.ndarray, bbox, polygon: List[List[float]], output_path: Path) -> None:
    """Save one raw seven-band Sentinel-2 acquisition as a polygon-masked GeoTIFF."""
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError as exc:
        raise RuntimeError("rasterio is required to write GeoTIFF files") from exc

    height, width, band_count = bands.shape
    masked = bands.astype(np.float32, copy=True)
    masked[~_polygon_mask((height, width), bbox, polygon)] = np.nan
    transform = from_bounds(bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y, width, height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=band_count,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=np.nan,
        compress="deflate",
    ) as dst:
        for index in range(band_count):
            dst.write(masked[:, :, index], index + 1)
        dst.descriptions = tuple(composite_module.BAND_NAMES[:band_count])


def merge_date_tiles(tile_paths: List[Path], output_path: Path) -> None:
    """Mosaic all tiles from one acquisition date into one GeoTIFF."""
    try:
        import rasterio
        from rasterio.merge import merge
    except ImportError as exc:
        raise RuntimeError("rasterio is required to merge GeoTIFF tiles") from exc

    sources = [rasterio.open(path) for path in tile_paths]
    try:
        mosaic, transform = merge(sources, nodata=np.nan, method="first")
        profile = sources[0].profile.copy()
        # Tile-specific block sizes are invalid once a mosaic has a different
        # width/height. Let GDAL choose appropriate strips for the final file.
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
        profile.pop("tiled", None)
        profile.update(
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=transform,
            count=mosaic.shape[0],
            nodata=np.nan,
            compress="deflate",
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(mosaic)
            dst.descriptions = tuple(composite_module.BAND_NAMES[:mosaic.shape[0]])
    finally:
        for source in sources:
            source.close()


def run_pipeline(input_path: str | Path) -> List[dict]:
    """Download every date, tile it, then write one merged raw TIFF per date."""
    ensure_runtime_config()
    client_id = os.environ["SH_CLIENT_ID"].strip()
    client_secret = os.environ["SH_CLIENT_SECRET"].strip()
    s2_cfg = composite_module.build_s2_config(client_id, client_secret)
    jobs = parse_input_file(input_path)
    archive_root = ROOT / "tif_archive"
    work_root = archive_root / ".tiles"
    results: List[dict] = []

    for job in jobs:
        aoi_id = job["aoi_id"]
        original_polygon = job["polygon"]
        tiles = list(iter_tiles(original_polygon))
        full_bbox = _tile_bbox(original_polygon)
        logger.info("%s: archiving %d tile(s), max %.0f km per side", aoi_id, len(tiles), MAX_TILE_SIDE_KM)
        for month_key in month_iter(job["start_month"], job["end_month"]):
            year, month = map(int, month_key.split("-"))
            start, end = composite_module.month_range(year, month)
            try:
                # Discover the AOI's acquisition dates once.  Every one of
                # those dates is then requested for every tile, so a merged
                # file represents the entire original AOI rather than only
                # whichever tile happened to see that date in the catalogue.
                dates = composite_module.search_dates(
                    composite_module.DataCollection.SENTINEL2_L2A,
                    full_bbox,
                    start,
                    end,
                    s2_cfg,
                    max_cloud_coverage=1,
                )
            except Exception as exc:
                print(f"[ERROR] {aoi_id} {month_key}: catalogue search failed: {exc}")
                results.append({"aoi_id": aoi_id, "month": month_key, "status": "failed", "error": str(exc)})
                continue

            for date_key in dates:
                final_path = archive_root / aoi_id / f"{date_key}.tif"
                if final_path.exists():
                    print(f"[SKIP] {aoi_id} {date_key}: merged TIFF already exists")
                    continue
                tile_paths: List[Path] = []
                try:
                    for tile_id, tile_polygon in tiles:
                        bbox = _tile_bbox(tile_polygon)
                        tile_path = work_root / aoi_id / date_key / f"{tile_id}.tif"
                        tile_paths.append(tile_path)
                        if tile_path.exists():
                            print(f"[SKIP] {aoi_id} {date_key} {tile_id}: tile already exists")
                            continue
                        size = composite_module.clamp_size(
                            *composite_module.bbox_to_dimensions(
                                bbox, resolution=composite_module.RESOLUTION
                            )
                        )
                        bands, _invalid, _scl = composite_module.fetch_s2(date_key, bbox, size, s2_cfg)
                        save_raw_tiff(bands, bbox, original_polygon, tile_path)
                        print(f"[OK] {aoi_id} {date_key} {tile_id}")

                    merge_date_tiles(tile_paths, final_path)
                    # The final merged TIFF is the requested deliverable. The
                    # temporary tiles remain only while a date is incomplete,
                    # allowing an interrupted date to resume without repeats.
                    shutil.rmtree(work_root / aoi_id / date_key)
                    print(f"[MERGED] {aoi_id} {date_key}: {final_path}")
                    results.append({"aoi_id": aoi_id, "date": date_key, "path": str(final_path)})
                except Exception as exc:
                    print(f"[ERROR] {aoi_id} {date_key}: {exc}")
                    results.append({"aoi_id": aoi_id, "date": date_key, "status": "failed", "error": str(exc)})
    return results


def main() -> int:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "input.txt"
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    results = run_pipeline(input_path)
    summary_path = ROOT / "tif_archive" / "run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Pipeline finished. Summary written to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
