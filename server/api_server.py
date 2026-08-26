"""
Minimal backend API for polygon-based Sentinel Hub SCL retrieval
and full change-detection analysis pipeline.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from PIL import Image
from scipy.ndimage import binary_dilation
from sentinelhub import (
    BBox,
    CRS,
    DataCollection,
    Geometry,
    MimeType,
    SHConfig,
    SentinelHubRequest,
    bbox_to_dimensions,
)

from bimonthly_composite import (
    determine_half,
    fetch_true_color_base,
    month_range,
    run_composite_pipeline,
)
from cache import cache_get, cache_get_containing, cache_put
from inference.inference import ensemble_predict, expand_class, get_mask


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("capstone.backend")

DEFAULT_LOOKBACK_DAYS = 7
LOOKBACK_FALLBACK_DAYS = [7, 14, 30, 60, 120, 365]
MAX_PROCESS_DIMENSION = 2500
MAX_MONTH_FALLBACK = 6  # Search up to 6 months backwards
MIN_VALID_DATA_PCT = 30.0  # At least 30% non-zero pixels required

# Cloud masking values (from planet.py — remove clouds during preprocessing)
CLOUD_SCL_VALUES = [3, 8, 9, 10]  # Cloud Shadow, Medium/High Probability, Thin Cirrus

# ── Land-use classification ───────────────────────────────────────────────
# The labels this product reports, and the RGB the overlay paints them.
# Colours are fully opaque here; the client blends the whole raster with its own
# opacity so the basemap stays readable underneath.
LAND_COVER_COLORS: dict[str, tuple[int, int, int]] = {
    "Tree": (46, 125, 50),
    "Crop": (156, 204, 101),
    "Water": (21, 101, 192),
    "Soil": (161, 136, 127),
}

# `expand_class` splits Sentinel-2's "Bare Soil" scene class in two: Building
# where NDBI > 0, Soil otherwise. Land-use classification doesn't call out
# built-up surfaces, so the split is folded back together here. Change detection
# still relies on both labels — see `get_mask` in inference/inference.py.
LAND_COVER_MERGE: dict[str, str] = {"Building": "Soil"}

# Bands `ensemble_predict` needs; a NaN in any of them means the composite had
# no cloud-free observation for that pixel, so it is left transparent.
CLASSIFY_BAND_COLS = ["B01", "B02", "B03", "B04", "B08", "B11", "B12"]

# Inference runs one row per cell, so cap how many cells a single request
# classifies. Larger areas are strided down (coarser ground resolution).
MAX_CLASSIFY_CELLS = 260_000

# Rasters are upscaled (nearest-neighbour) before transport so the client does
# not blur class edges when it stretches them to fill the viewport.
MIN_OVERLAY_PX = 512
MAX_OVERLAY_PX = 1536

# True-colour render of the composite the classifier actually saw, so the mask
# lines up with it pixel for pixel. A per-band 2–98% linear stretch handles the
# fact that raw surface reflectance is very dark; gamma lifts the midtones.
TRUE_COLOR_BANDS = ("B04", "B03", "B02")  # red, green, blue
TRUE_COLOR_PERCENTILES = (2.0, 98.0)
TRUE_COLOR_GAMMA = 0.8  # < 1 lifts midtones; brightens the cloud-free composite

# Before/after uses a different stretch to the land-use backdrop, over the
# *combined* reflectance of both dates and all three bands — see
# `_render_compare_true_color` for why per-band, per-image is wrong here.
TRUE_COLOR_COMPARE_PERCENTILES = (1.0, 98.0)

# Cap on how many reflectance values the shared stretch is measured from. Two
# 1536² scenes × 3 bands is ~14 M values; a strided sample of them lands on the
# same percentiles for a fraction of the memory.
STRETCH_SAMPLE_LIMIT = 400_000

# Change-mask overlay colours, matching `_deforestColor` / `_waterColor` in
# mobile/lib/screens/analysis/analysis_run_screen.dart. Keyed by the `mask`
# value `get_mask` emits.
CHANGE_COLORS: dict[int, tuple[int, int, int]] = {
    1: (229, 57, 53),   # forest / vegetation loss  #E53935
    2: (251, 140, 0),   # surface-water loss        #FB8C00
}

# ── Before/after land-cover class map ─────────────────────────────────────
# Change detection classifies both dates in full (`classify_df`) and then keeps
# only the pixels that differ. The class map returns that intermediate result as
# a picture: every cell of each date painted by what it is, so the two dates can
# be read as land cover rather than as raw reflectance.
#
# The five classifier labels fold into the three ground types the product
# actually reasons about. `Crop` joins `Tree` (both vegetation) and `Building`
# joins `Soil`, matching LAND_COVER_MERGE's precedent. Code 0 means the
# composite had no cloud-free observation there.
CLASS_MAP_CODES: dict[str, int] = {
    "Tree": 1,
    "Crop": 1,
    "Water": 2,
    "Soil": 3,
    "Building": 3,
}

CLASS_MAP_NAMES: dict[int, str] = {1: "Tree", 2: "Water", 3: "Soil"}

# Per-class dark -> mid -> light ramps. A flat fill per class throws away every
# bit of texture the sensor recorded and reads as three paper cut-outs, so hue
# carries the class while the scene's own brightness picks the point along the
# ramp. Canopy structure, water depth and soil relief all survive that way.
# The mid stop is the class's representative colour — it is what the legend
# shows, and what a cell with no brightness signal falls back to.
CLASS_MAP_RAMPS: dict[int, tuple[tuple[int, int, int], ...]] = {
    1: ((10, 58, 32), (45, 125, 50), (156, 214, 160)),    # Tree  — deep canopy -> new leaf
    2: ((7, 44, 80), (21, 101, 192), (147, 202, 249)),    # Water — deep -> shallow
    3: ((92, 62, 43), (176, 137, 104), (233, 214, 186)),  # Soil  — wet earth -> dry sand
}

# Brightness is stretched per class before it indexes the ramp. Raw reflectance
# occupies a narrow band within any one class, so without this every class comes
# out near one end of its ramp and the gradient is wasted.
CLASS_MAP_PERCENTILES = (4.0, 96.0)
CLASS_MAP_GAMMA = 0.85  # < 1 lifts midtones, matching the true-colour render

# ...but the stretched brightness is then held off the ends of the ramp. Letting
# it run the full 0-1 makes one class span so much lightness that a bright patch
# and a dark patch of the *same* class stop looking like the same thing — the
# categorical reading, which is the whole job, loses to the shading. Keeping to
# the middle of the ramp preserves the texture and keeps the hue in charge.
CLASS_MAP_SHADE_RANGE = (0.16, 0.86)

# Hairline drawn where two classes meet. Without it, adjacent patches of
# similar lightness bleed into one another and the class boundary — the thing
# the map exists to show — is the first thing lost.
CLASS_MAP_EDGE_RGB = (9, 11, 13)

# Class maps are flat banded colour, not photographs, and Pillow's `optimize`
# is the wrong tool for them -- see _encode_png. Level 3 is where the curve
# flattens: below it the file grows with no further time saved, above it the
# time climbs with no further shrink.
CLASS_MAP_PNG_COMPRESS_LEVEL = 3

# A changed pixel is one 10 m cell, which lands on ~1 px of a 1500 px-wide
# render — effectively invisible. The overlay grows each hit by this many
# pixels purely so it can be seen; the reported counts are never dilated.
CHANGE_DILATION_DIVISOR = 500

# Used only when the true-colour fetch fails and there is no base image whose
# shape the mask can borrow.
CHANGE_RASTER_PX = 768

# Part of the cache key. Entries written before before/after imagery existed
# carry no rasters, and serving one would silently drop the comparison view, so
# the suffix retires them. v3 retired v2, whose scenes were mosaicked over the
# whole month while the analysis reads only days 1-15 — the picture showed a
# different period than the numbers. v4 retires v3, whose two scenes were each
# stretched independently, so the same ground took a different colour on each
# side. v5 retires v4, which predates the per-date land-cover class maps — the
# before/after view now offers them as a toggle, and an entry without them would
# serve a view whose main control does nothing. Bump again whenever the response
# shape or the rendering changes.
ANALYZE_CACHE_KIND = "analyze_v5"


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", "/app/frontend_dist"))
TARGET_R20M_DIR = (
    BASE_DIR
    / "GRANULE"
    / "L2A_T45QYE_A007010_20260108T044510"
    / "IMG_DATA"
    / "R20m"
)


def build_config() -> SHConfig:
    """Load Sentinel Hub credentials from environment or local SH config."""
    cfg = SHConfig()
    has_id = bool(cfg.sh_client_id)
    has_secret = bool(cfg.sh_client_secret)
    logger.info("Sentinel Hub credential presence: client_id=%s, client_secret=%s", has_id, has_secret)
    if not cfg.sh_client_id or not cfg.sh_client_secret:
        raise RuntimeError(
            "Sentinel Hub credentials are missing. Configure SH_CLIENT_ID and SH_CLIENT_SECRET "
            "or set credentials in Sentinel Hub config."
        )
    return cfg


def scl_evalscript() -> str:
    """Evalscript returning only the scene classification layer."""
    return """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["SCL"] }],
    output: { bands: 1, sampleType: "UINT8" }
  };
}

function evaluatePixel(sample) {
  return [sample.SCL];
}
"""


def apply_cloud_mask(scl_array) -> tuple:
    """
    Remove cloud pixels from SCL array (from planet.py approach).
    Set cloud pixels to 0 (NoData) and return masked array + statistics.
    """
    scl_masked = scl_array.copy()
    cloud_pixels = np.isin(scl_masked, CLOUD_SCL_VALUES)
    original_count = scl_masked.size
    removed_count = np.sum(cloud_pixels)
    
    scl_masked[cloud_pixels] = 0  # Mark clouds as NoData
    
    cloud_pct = 100.0 * removed_count / original_count if original_count > 0 else 0
    logger.info("Cloud masking: removed %d pixels (%.1f%% of %d total)", 
                removed_count, cloud_pct, original_count)
    
    return scl_masked, removed_count, cloud_pct


def ring_from_polygon(polygon_coords: list[list[float]]) -> list[list[float]]:
    """Ensure polygon ring is closed and has minimum valid coordinates."""
    if len(polygon_coords) < 3:
        raise ValueError("Polygon must have at least 3 coordinate pairs")

    ring = [[float(lon), float(lat)] for lon, lat in polygon_coords]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def parse_iso_datetime(value: str) -> datetime:
    """Parse ISO datetime values, including trailing Z (UTC) format."""
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


def build_scl_request(
    bbox: BBox,
    geom: Geometry,
    size: tuple[int, int],
    start_dt: datetime,
    end_dt: datetime,
    cfg: SHConfig,
) -> SentinelHubRequest:
    """Build a Sentinel Hub request for latest SCL in a given time interval."""
    return SentinelHubRequest(
        evalscript=scl_evalscript(),
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=DataCollection.SENTINEL2_L2A,
                time_interval=(start_dt, end_dt),
                mosaicking_order="leastCC",
            )
        ],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        geometry=geom,
        size=size,
        config=cfg,
    )


def unique_target_file(end_dt: datetime) -> Path:
    """Generate a unique JP2 filename so each download is preserved."""
    stamp = end_dt.strftime("%Y%m%dT%H%M%SZ")
    suffix = str(uuid.uuid4())[:8]
    return TARGET_R20M_DIR / f"T45QYE_{stamp}_{suffix}_SCL_20m.jp2"


def clamp_request_size(width: int, height: int) -> tuple[tuple[int, int], float]:
    """Clamp request size to Sentinel Hub API limits while preserving aspect ratio."""
    max_dim = max(width, height)
    if max_dim <= MAX_PROCESS_DIMENSION:
        return (width, height), 1.0

    scale = MAX_PROCESS_DIMENSION / float(max_dim)
    clamped_width = max(1, int(math.floor(width * scale)))
    clamped_height = max(1, int(math.floor(height * scale)))
    return (clamped_width, clamped_height), scale


def _check_scl_quality(data) -> tuple[bool, float]:
    """Check if fetched SCL data has enough valid (non-zero) pixels.

    Returns (is_good, non_zero_pct).
    """
    if not data:
        return False, 0.0
    arr = data[0]
    if getattr(arr, "ndim", 0) == 3 and arr.shape[-1] == 1:
        arr = arr[:, :, 0]
    total = arr.size
    if total == 0:
        return False, 0.0
    non_zero = np.count_nonzero(arr)
    pct = 100.0 * non_zero / total
    return pct >= MIN_VALID_DATA_PCT, pct


def _month_range(year: int, month: int) -> tuple[datetime, datetime]:
    """Return (first-second-of-month, last-second-of-month) in UTC for a given year/month."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0, 0)
    end = datetime(year, month, last_day, 23, 59, 59)
    return start, end


def _try_fetch_with_fallback(bbox, geom, size, start_dt, end_dt, cfg, now):
    """Try fetching SCL for the given range. If data is empty/bad, go backwards
    month-by-month (-1, -2, -3 ...) until valid data is found.
    """
    # 1) Try the exact requested range
    logger.info("Sending Sentinel Hub request for SCL: %s to %s", start_dt.isoformat(), end_dt.isoformat())
    req = build_scl_request(bbox, geom, size, start_dt, end_dt, cfg)
    data = req.get_data(save_data=False)
    is_good, pct = _check_scl_quality(data)
    logger.info("Attempt [original]: non_zero=%.1f%% good=%s", pct, is_good)

    if is_good:
        return data, start_dt, end_dt

    # 2) Go backwards month by month from the start of the requested range
    # Determine the year/month of the original request midpoint
    mid_dt = start_dt + (end_dt - start_dt) / 2
    ref_year = mid_dt.year
    ref_month = mid_dt.month

    for offset in range(1, MAX_MONTH_FALLBACK + 1):
        # Go backwards: -1, -2, -3, ...
        m = ref_month - offset
        y = ref_year
        while m < 1:
            m += 12
            y -= 1
        attempt_start, attempt_end = _month_range(y, m)
        # Clamp future end
        if attempt_end > now:
            attempt_end = now
        if attempt_start >= attempt_end:
            continue

        logger.info("Fallback -%d month(s): trying %04d-%02d (%s to %s)",
                     offset, y, m, attempt_start.isoformat(), attempt_end.isoformat())
        try:
            req = build_scl_request(bbox, geom, size, attempt_start, attempt_end, cfg)
            attempt_data = req.get_data(save_data=False)
            is_good, pct = _check_scl_quality(attempt_data)
            logger.info("Fallback -%d month(s): non_zero=%.1f%% good=%s", offset, pct, is_good)
            if is_good:
                logger.info("Using data from %04d-%02d (-%d month(s) from requested)", y, m, offset)
                return attempt_data, attempt_start, attempt_end
        except Exception as exc:
            logger.warning("Fallback -%d month(s) failed: %s", offset, exc)

    # 3) If nothing found, return best-effort from original request if any data at all
    if data and pct > 0:
        logger.warning("No good fallback found, using original data with %.1f%% coverage", pct)
        return data, start_dt, end_dt

    raise RuntimeError(
        f"No valid Sentinel-2 data found for {ref_year}-{ref_month:02d} or "
        f"{MAX_MONTH_FALLBACK} months before. This area may have no coverage."
    )


def fetch_and_store_scl(
    polygon_coords: list[list[float]],
    start_date: str | None,
    end_date: str | None,
) -> Path:
    """Download SCL JP2 for the polygon and store with a unique output filename."""
    ring = ring_from_polygon(polygon_coords)
    logger.info("Polygon accepted with %d vertices", len(ring) - 1)

    lons = [pt[0] for pt in ring]
    lats = [pt[1] for pt in ring]

    bbox = BBox((min(lons), min(lats), max(lons), max(lats)), crs=CRS.WGS84)
    geom = Geometry({"type": "Polygon", "coordinates": [ring]}, crs=CRS.WGS84)

    now = datetime.utcnow()

    if end_date is None:
        end_dt = now
    else:
        end_dt = parse_iso_datetime(end_date)
        # Clamp future end dates to today
        if end_dt.replace(tzinfo=None) > now:
            logger.info("End date %s is in the future, clamping to now", end_dt.isoformat())
            end_dt = now

    if start_date is None:
        start_dt = end_dt - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    else:
        start_dt = parse_iso_datetime(start_date)

    if start_dt >= end_dt:
        raise ValueError("startDate must be earlier than endDate")

    native_size = bbox_to_dimensions(bbox, resolution=20)
    size, scale = clamp_request_size(native_size[0], native_size[1])
    logger.info(
        "Computed request bbox=(%.6f, %.6f, %.6f, %.6f), native_size=%s, request_size=%s, scale=%.4f, interval=%s to %s",
        min(lons),
        min(lats),
        max(lons),
        max(lats),
        native_size,
        size,
        scale,
        start_dt.isoformat(),
        end_dt.isoformat(),
    )
    cfg = build_config()

    data = None
    used_start = start_dt
    used_end = end_dt

    # If dates are not provided, progressively widen lookback to find latest available scene.
    if start_date is None and end_date is None:
        for lookback_days in LOOKBACK_FALLBACK_DAYS:
            attempt_start = end_dt - timedelta(days=lookback_days)
            logger.info("Trying latest scene lookup with lookback=%d days", lookback_days)
            req = build_scl_request(bbox, geom, size, attempt_start, end_dt, cfg)
            try:
                attempt_data = req.get_data(save_data=False)
                if attempt_data:
                    data = attempt_data
                    used_start = attempt_start
                    used_end = end_dt
                    logger.info("Scene found with lookback=%d days", lookback_days)
                    break
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Lookback=%d days failed: %s", lookback_days, exc)
        if data is None:
            raise RuntimeError("Could not retrieve latest available Sentinel-2 scene in fallback windows")
    else:
        # Try the exact requested range first, then go backwards month by month
        data, used_start, used_end = _try_fetch_with_fallback(
            bbox, geom, size, start_dt, end_dt, cfg, now
        )

    logger.info("Sentinel Hub request completed for interval=%s to %s", used_start.isoformat(), used_end.isoformat())

    if not data:
        raise RuntimeError("Sentinel Hub response did not return raster data")

    scl_array = data[0]
    if getattr(scl_array, "ndim", 0) == 3 and scl_array.shape[-1] == 1:
        scl_array = scl_array[:, :, 0]

    # Validate that the SCL data is not all zeros (no data)
    unique_vals = np.unique(scl_array)
    non_zero_pct = 100.0 * np.count_nonzero(scl_array) / scl_array.size if scl_array.size > 0 else 0
    logger.info("SCL data check: shape=%s, unique_values=%s, non_zero=%.1f%%", scl_array.shape, unique_vals, non_zero_pct)
    if len(unique_vals) == 1 and unique_vals[0] == 0:
        raise RuntimeError(
            f"No valid SCL data found for interval {used_start.isoformat()} to {used_end.isoformat()}. "
            f"Sentinel-2 may not have imagery for this area and date range. Try a different month."
        )

    # Apply cloud masking (from planet.py)
    scl_array, removed_count, cloud_pct = apply_cloud_mask(scl_array)
    logger.info("SCL cloud-masked: %d clouds removed (%.1f%%)", removed_count, cloud_pct)

    granule_root = BASE_DIR / "GRANULE"
    if granule_root.exists() and not granule_root.is_dir():
        raise RuntimeError("Output path conflict: /app/GRANULE exists as a file, not a directory")

    TARGET_R20M_DIR.mkdir(parents=True, exist_ok=True)
    target_file = unique_target_file(used_end)
    logger.info("Converting TIFF raster to JPEG2000 at: %s", target_file)
    img = Image.fromarray(scl_array.astype("uint8"), mode="L")
    img.save(target_file, format="JPEG2000")
    logger.info("SCL JP2 written to: %s", target_file)
    return target_file, used_start, used_end


app = Flask(__name__)
CORS(app)


@app.get("/api/health")
def health_check():
    """Simple health endpoint for deployment checks."""
    return jsonify({"status": "ok", "service": "capstone-backend"})


@app.get("/")
@app.get("/<path:path>")
def serve_frontend(path=""):
    """Serve the built web client for the root URL and SPA routes.

    That client is the Flutter build (see server/Dockerfile); the route itself
    is framework-agnostic -- it serves whatever static bundle is at
    FRONTEND_DIST and falls back to index.html so client-side routes resolve.
    """
    if path.startswith("api/"):
        return jsonify({"status": "error", "message": "Not found"}), 404

    frontend_root = FRONTEND_DIST.resolve()
    if frontend_root.exists() and (frontend_root / "index.html").exists():
        candidate = (frontend_root / path).resolve()
        try:
            candidate.relative_to(frontend_root)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid path"}), 400

        if path and candidate.exists() and candidate.is_file():
            return send_from_directory(frontend_root, path)

        return send_from_directory(frontend_root, "index.html")

    return health_check()


@app.post("/api/sentinel/scl")
def process_polygon_scl():
    """Process polygon request and save only the target SCL JP2 file."""
    request_id = str(uuid.uuid4())[:8]
    try:
        logger.info("[%s] Incoming /api/sentinel/scl request", request_id)
        payload = request.get_json(force=True, silent=False)
        logger.info("[%s] Payload keys: %s", request_id, sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
        polygon = payload.get("polygon")
        if not polygon or not isinstance(polygon, list):
            logger.warning("[%s] Invalid polygon payload", request_id)
            return jsonify({"status": "error", "message": "'polygon' is required"}), 400

        out_path, actual_start, actual_end = fetch_and_store_scl(
            polygon_coords=polygon,
            start_date=payload.get("startDate"),
            end_date=payload.get("endDate"),
        )

        logger.info("[%s] Completed successfully. Output: %s", request_id, out_path)
        return jsonify(
            {
                "status": "success",
                "message": "SCL JP2 downloaded, cloud-masked, and stored successfully",
                "outputPath": str(out_path),
                "fileName": out_path.name,
                "requestId": request_id,
                "cloudMasking": "enabled - cloud pixels removed",
                "actualDateRange": {
                    "start": actual_start.isoformat(),
                    "end": actual_end.isoformat(),
                },
            }
        )
    except ValueError as exc:
        logger.exception("[%s] Validation error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 400
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("[%s] Unexpected error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 500


@app.post("/api/sentinel/analyze")
def analyze_change():
    """Full pipeline: bimonthly composite -> inference -> change mask."""
    request_id = str(uuid.uuid4())[:8]
    try:
        logger.info("[%s] Incoming /api/sentinel/analyze request", request_id)
        payload = request.get_json(force=True, silent=False)

        polygon = payload.get("polygon")
        if not polygon or not isinstance(polygon, list) or len(polygon) < 3:
            return jsonify({"status": "error", "message": "'polygon' with >=3 coords required"}), 400

        old_year = int(payload.get("oldYear", 2024))
        old_month = int(payload.get("oldMonth", 1))
        new_year = int(payload.get("newYear", 2025))
        new_month = int(payload.get("newMonth", 1))

        logger.info("[%s] old=%d-%02d  new=%d-%02d  polygon=%d pts",
                     request_id, old_year, old_month, new_year, new_month, len(polygon))

        # Result cache: change detection composites two whole months, so a repeat
        # of the same area + date pair skips both Sentinel Hub fetches.
        now = datetime.utcnow()
        lons = [float(pt[0]) for pt in polygon]
        lats = [float(pt[1]) for pt in polygon]
        bbox_bounds = (min(lons), min(lats), max(lons), max(lats))
        date_key = f"{old_year}-{old_month:02d}_{new_year}-{new_month:02d}"
        is_current = (
            (old_year, old_month) == (now.year, now.month)
            or (new_year, new_month) == (now.year, now.month)
        )

        cached = cache_get(ANALYZE_CACHE_KIND, date_key, bbox_bounds, is_current)
        if cached is not None:
            logger.info("[%s] served change detection from cache", request_id)
            return jsonify(cached)

        # Phase 2: a smaller area fully inside a computed one is cropped from it.
        contained = cache_get_containing(ANALYZE_CACHE_KIND, date_key, bbox_bounds, is_current)
        if contained is not None:
            try:
                sub_result = _crop_analyze(contained, bbox_bounds)
                logger.info("[%s] served change detection from a cached larger area (subset)", request_id)
                return jsonify(sub_result)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[%s] subset crop failed (%s); computing fresh", request_id, exc)

        # Get Sentinel Hub credentials
        cfg = build_config()
        client_id = cfg.sh_client_id
        client_secret = cfg.sh_client_secret

        # ── Old date composite ──
        logger.info("[%s] Running composite pipeline for OLD date %d-%02d", request_id, old_year, old_month)
        old_df = run_composite_pipeline(polygon, old_year, old_month, client_id, client_secret)

        # ── New date composite ──
        logger.info("[%s] Running composite pipeline for NEW date %d-%02d", request_id, new_year, new_month)
        new_df = run_composite_pipeline(polygon, new_year, new_month, client_id, client_secret)

        # ── Classification ──
        logger.info("[%s] Running classification on old_df (%d rows) and new_df (%d rows)",
                     request_id, len(old_df), len(new_df))

        def classify_df(df):
            """Split by ClassID, run ensemble_predict on unclassified, expand_class on classified, merge."""
            classified_mask = df["ClassID"].isin([4, 5, 6])
            classified_df = df[classified_mask].copy()
            unclassified_df = df[~classified_mask].copy()

            results = []
            if len(unclassified_df) > 0:
                pred = ensemble_predict(unclassified_df)
                results.append(pred)
            if len(classified_df) > 0:
                # Compute indices needed by expand_class
                B04, B08, B11 = classified_df["B04"], classified_df["B08"], classified_df["B11"]
                denom_ndvi = B08 + B04
                classified_df["NDVI"] = np.where(denom_ndvi != 0, (B08 - B04) / denom_ndvi, 0.0)
                denom_ndbi = B11 + B08
                classified_df["NDBI"] = np.where(denom_ndbi != 0, (B11 - B08) / denom_ndbi, 0.0)
                expanded = expand_class(classified_df)
                results.append(expanded)

            if not results:
                return pd.DataFrame(columns=["Longitude", "Latitude", "classifier"])
            return pd.concat(results, ignore_index=True)

        prev_df = classify_df(old_df)
        curr_df = classify_df(new_df)

        logger.info("[%s] prev_df: %d rows, curr_df: %d rows", request_id, len(prev_df), len(curr_df))

        # ── Change mask ──
        # Align by coordinates: merge on Longitude/Latitude
        merged = prev_df.merge(
            curr_df,
            on=["Longitude", "Latitude"],
            suffixes=("_prev", "_curr"),
            how="inner",
        )
        logger.info("[%s] Merged on coords: %d matched pixels", request_id, len(merged))

        if len(merged) == 0:
            return jsonify({
                "status": "success",
                "message": "No overlapping pixels between old and new composites",
                "changes": [],
                "requestId": request_id,
            })

        # Build prev/curr DataFrames for get_mask
        prev_aligned = merged[["Longitude", "Latitude", "classifier_prev"]].rename(
            columns={"classifier_prev": "classifier"}
        )
        curr_aligned = merged[["Longitude", "Latitude", "classifier_curr"]].rename(
            columns={"classifier_curr": "classifier"}
        )

        mask_df = get_mask(prev_aligned, curr_aligned)

        # Filter to only changed pixels (mask != 0)
        changed = mask_df[mask_df["mask"] != 0]
        logger.info("[%s] Changed pixels: %d (mask=1: %d, mask=2: %d)",
                     request_id, len(changed),
                     int((changed["mask"] == 1).sum()),
                     int((changed["mask"] == 2).sum()))

        changes = changed[["Longitude", "Latitude", "mask"]].to_dict(orient="records")

        west, east = bbox_bounds[0], bbox_bounds[2]
        south, north = bbox_bounds[1], bbox_bounds[3]

        # ── Before/after imagery ──
        # A cloud-masked true-colour scene per date, rendered through one shared
        # stretch so the pair is comparable, covering the same days the
        # composites above read. Display-only: failure here must not lose an
        # otherwise-good analysis, so the client falls back to map points.
        old_image_b64 = new_image_b64 = None
        old_scene = new_scene = None
        base_w = base_h = 0
        try:
            (
                old_image_b64, new_image_b64, base_w, base_h, old_scene, new_scene,
            ) = _before_after_pngs(
                polygon, old_year, old_month, new_year, new_month,
                client_id, client_secret,
            )
            logger.info("[%s] before/after imagery: %dx%d", request_id, base_w, base_h)
        except Exception as exc:  # pragma: no cover - network/credentials dependent
            logger.warning(
                "[%s] before/after imagery unavailable (%s); returning points only",
                request_id, exc,
            )
            old_image_b64 = new_image_b64 = None
            old_scene = new_scene = None

        if base_w < 1 or base_h < 1:
            base_h, base_w = _change_raster_shape(west, south, east, north)

        # ── Land-cover class maps ──
        # `classify_df` already labelled every cell of both dates; only the cells
        # that differ survive into `changes`. Rendering the full labelling gives
        # the before/after view something to show besides raw reflectance — what
        # each date *is*, not just where it changed. Display-only, like the
        # imagery above, so a failure here must not lose the analysis.
        old_class_b64 = new_class_b64 = None
        old_classes: list[dict] = []
        new_classes: list[dict] = []
        try:
            grid_shape = _grid_shape(old_df)
            bbox = (west, south, east, north)
            old_class_b64, old_classes = _class_map_png(
                prev_df, grid_shape, bbox, old_scene, (base_h, base_w),
            )
            new_class_b64, new_classes = _class_map_png(
                curr_df, grid_shape, bbox, new_scene, (base_h, base_w),
            )
            logger.info(
                "[%s] class maps rendered from a %dx%d classifier grid",
                request_id, grid_shape[1], grid_shape[0],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[%s] class maps unavailable (%s)", request_id, exc)
            old_class_b64 = new_class_b64 = None

        deforestation_png_b64 = _encode_png(
            _render_change_overlay(changed, 1, west, south, east, north, base_h, base_w), 1,
        )
        water_loss_png_b64 = _encode_png(
            _render_change_overlay(changed, 2, west, south, east, north, base_h, base_w), 1,
        )

        result = {
            "status": "success",
            "message": f"Analysis complete: {len(changed)} changed pixels detected",
            "changes": changes,
            "oldImagePngBase64": old_image_b64,
            "newImagePngBase64": new_image_b64,
            "oldClassPngBase64": old_class_b64,
            "newClassPngBase64": new_class_b64,
            "deforestationPngBase64": deforestation_png_b64,
            "waterLossPngBase64": water_loss_png_b64,
            "imageWidth": base_w,
            "imageHeight": base_h,
            "bounds": {"north": north, "south": south, "east": east, "west": west},
            # Deliberately not "classes": the cache's derived-table writer keys
            # off a top-level `classes` *list* (see `_store_derived`), which is
            # classify's shape, not this per-date pair.
            "classBreakdown": {"old": old_classes, "new": new_classes},
            "stats": {
                "totalPixels": len(merged),
                "deforestation": int((changed["mask"] == 1).sum()),
                "waterLoss": int((changed["mask"] == 2).sum()),
                "oldDate": f"{old_year}-{old_month:02d}",
                "newDate": f"{new_year}-{new_month:02d}",
                # The days each composite actually covers — narrower than the
                # month the picker implies.
                "oldWindow": _window_label(old_year, old_month),
                "newWindow": _window_label(new_year, new_month),
            },
            "requestId": request_id,
        }
        cache_put(ANALYZE_CACHE_KIND, date_key, bbox_bounds, is_current, result)
        return jsonify(result)

    except ValueError as exc:
        logger.exception("[%s] Validation error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 400
    except Exception as exc:
        logger.exception("[%s] Unexpected error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 500


def _bbox_area_km2(west: float, south: float, east: float, north: float) -> float:
    """Approximate area of a lat/lon box, using the mean latitude for the scale."""
    lat_mid = math.radians((north + south) / 2.0)
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(lat_mid)
    return abs(north - south) * km_per_deg_lat * abs(east - west) * km_per_deg_lon


def _grid_shape(df: pd.DataFrame) -> tuple[int, int]:
    """Recover the (rows, cols) of the 10 m grid `build_dataframe` flattened.

    It flattens a meshgrid row-major, so the leading run of rows that share the
    first latitude is exactly one grid row. The values come from the same array,
    so exact float equality is safe here.
    """
    lat_vals = df["Latitude"].to_numpy()
    cols = int(np.argmax(lat_vals != lat_vals[0]))
    if cols == 0:  # every latitude identical — a single-row grid
        cols = len(df)
    return len(df) // cols, cols


def _classify_labels(sub: pd.DataFrame) -> np.ndarray:
    """Land-cover label per row of `sub`, or "" where the composite has no data.

    Mirrors the split in `/api/sentinel/analyze`: pixels the Sentinel-2 scene
    classification already resolved to Vegetation/Soil/Water (SCL 4/5/6) go
    through `expand_class`, everything else through the model ensemble.
    """
    labels = np.full(len(sub), "", dtype=object)
    valid = sub[CLASSIFY_BAND_COLS].notna().all(axis=1).to_numpy()
    if not valid.any():
        return labels

    valid_pos = np.flatnonzero(valid)
    work = sub.iloc[valid_pos].reset_index(drop=True)

    scl_resolved = work["ClassID"].isin([4, 5, 6])
    parts = []

    unresolved = work.loc[~scl_resolved].copy()
    if len(unresolved) > 0:
        # Seed the column so `expand_class` can return it even if the ensemble
        # predicts nothing in 4/5/6 for this tile.
        unresolved["classifier"] = pd.NA
        parts.append(ensemble_predict(unresolved))

    resolved = work.loc[scl_resolved].copy()
    if len(resolved) > 0:
        b04, b08, b11 = resolved["B04"], resolved["B08"], resolved["B11"]
        denom_ndvi = b08 + b04
        resolved["NDVI"] = np.where(denom_ndvi != 0, (b08 - b04) / denom_ndvi, 0.0)
        denom_ndbi = b11 + b08
        resolved["NDBI"] = np.where(denom_ndbi != 0, (b11 - b08) / denom_ndbi, 0.0)
        resolved["classifier"] = pd.NA
        parts.append(expand_class(resolved))

    # Both branches keep `work`'s positional index, so sorting realigns them.
    combined = pd.concat(parts).sort_index()
    values = combined["classifier"].to_numpy()
    values = np.where(pd.isna(values), "", values)
    for source, target in LAND_COVER_MERGE.items():
        values = np.where(values == source, target, values)

    labels[valid_pos] = values
    return labels


def _upscale_factor(width: int, height: int) -> int:
    """Integer nearest-neighbour factor that lands the raster in a sane size."""
    longest = max(width, height)
    return max(1, min(MAX_OVERLAY_PX // longest, math.ceil(MIN_OVERLAY_PX / longest)))


def _encode_png(
    img: Image.Image, scale: int, compress_level: int | None = None,
) -> str:
    """Upscale and base64-encode a raster. Nearest-neighbour keeps cells sharp.

    `optimize=True` is Pillow's most expensive setting: it forces maximum zlib
    effort and trials every row filter. On photographic content -- the stretched
    true-colour scenes -- that effort does buy a smaller file, so it stays the
    default. On the flat banded content of a class map it is actively
    counterproductive. Measured on a 1500x1500 class map: `optimize=True` took
    5.72 s for 5748 KB, `compress_level=3` took 0.47 s for 4956 KB. Twelve times
    faster *and* smaller, decoding to byte-identical pixels -- PNG is lossless
    either way, so this trades nothing.

    Callers opt in by passing `compress_level`; leaving it None keeps the
    photographic default untouched.
    """
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    options = (
        {"optimize": True} if compress_level is None
        else {"compress_level": compress_level}
    )
    img.save(buf, format="PNG", **options)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _render_overlay(grid: np.ndarray) -> Image.Image:
    """Paint a label grid into an RGBA image. Unlabelled cells stay transparent."""
    height, width = grid.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    for name, (red, green, blue) in LAND_COVER_COLORS.items():
        rgba[grid == name] = (red, green, blue, 255)
    return Image.fromarray(rgba, mode="RGBA")


def _stretch_true_color(bands: list[np.ndarray], valid: np.ndarray) -> Image.Image:
    """Percentile-stretch three RGB bands into an RGBA image.

    Cells outside `valid` (no data / cloud) come back fully transparent so they
    never tint the stretch or show through the mask.
    """
    height, width = valid.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if not valid.any():
        return Image.fromarray(rgba, mode="RGBA")

    for channel, band in enumerate(bands):
        observed = band[valid]
        low, high = np.percentile(observed, TRUE_COLOR_PERCENTILES)
        if high <= low:  # a flat band — fall back to its full range
            low, high = float(observed.min()), float(observed.max())
        if high <= low:
            high = low + 1e-6

        stretched = np.clip((band - low) / (high - low), 0.0, 1.0)
        stretched = np.where(valid, stretched, 0.0) ** TRUE_COLOR_GAMMA
        rgba[..., channel] = (stretched * 255.0).astype(np.uint8)

    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def _render_true_color(sub: pd.DataFrame, height: int, width: int) -> Image.Image:
    """Render the 30 m composite's RGB bands as a viewable RGBA image.

    This is the *same* composite the classifier consumed, sampled on the *same*
    grid, so it registers with the mask cell for cell. Used as the backdrop only
    when the sharper native-resolution fetch is unavailable.
    """
    bands = [
        sub[name].to_numpy(dtype=np.float64).reshape(height, width)
        for name in TRUE_COLOR_BANDS
    ]
    valid = np.ones((height, width), dtype=bool)
    for band in bands:
        valid &= np.isfinite(band)
    return _stretch_true_color(bands, valid)


def _window_label(year: int, month: int, whole_month: bool = False) -> str:
    """Human label for the days a composite actually covers, e.g. "1–15 Jan 2025".

    A month picker implies the whole month, but change detection only composites
    the first half of it. Saying so on screen stops the imagery looking like it
    is off-date when it is simply a narrower window than the label suggested.
    """
    start, end = month_range(year, month) if whole_month else determine_half(year, month)
    return f"{start.day}–{end.day} {start.strftime('%b')} {start.year}"


def _change_raster_shape(west: float, south: float, east: float, north: float) -> tuple[int, int]:
    """Fallback (height, width) for the change raster, from the bbox aspect.

    Only used when no base image was fetched; otherwise the mask borrows the
    base image's exact shape so the two register pixel for pixel.
    """
    lon_span = abs(east - west) or 1e-9
    lat_span = abs(north - south) or 1e-9
    if lon_span >= lat_span:
        width = CHANGE_RASTER_PX
        height = max(1, int(round(CHANGE_RASTER_PX * lat_span / lon_span)))
    else:
        height = CHANGE_RASTER_PX
        width = max(1, int(round(CHANGE_RASTER_PX * lon_span / lat_span)))
    return height, width


def _render_change_overlay(
    changed: pd.DataFrame,
    mask_value: int,
    west: float,
    south: float,
    east: float,
    north: float,
    height: int,
    width: int,
) -> Image.Image:
    """Paint one change class into an RGBA raster spanning the AOI bounding box.

    Each class gets its own raster so the client can toggle them independently —
    isolating deforestation is the common case. Sparse transparent PNGs compress
    to almost nothing, so two rasters cost far less than the imagery does.

    A pixel's lon/lat maps linearly into the box, which is how the client
    stretches the imagery too, so mask and scene stay aligned. Hits are dilated
    for visibility only — see CHANGE_DILATION_DIVISOR.
    """
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if len(changed) == 0 or width < 1 or height < 1:
        return Image.fromarray(rgba, mode="RGBA")

    selected = changed[changed["mask"] == mask_value]
    if len(selected) == 0:
        return Image.fromarray(rgba, mode="RGBA")

    lon_span = (east - west) or 1e-9
    lat_span = (north - south) or 1e-9
    lon = selected["Longitude"].to_numpy(dtype=np.float64)
    lat = selected["Latitude"].to_numpy(dtype=np.float64)

    cols = np.clip(((lon - west) / lon_span * width).astype(int), 0, width - 1)
    rows = np.clip(((north - lat) / lat_span * height).astype(int), 0, height - 1)

    hit = np.zeros((height, width), dtype=bool)
    hit[rows, cols] = True
    hit = binary_dilation(
        hit, iterations=max(1, max(height, width) // CHANGE_DILATION_DIVISOR),
    )
    rgba[hit] = (*CHANGE_COLORS[mask_value], 255)
    return Image.fromarray(rgba, mode="RGBA")


def _class_code_grid(
    df: pd.DataFrame,
    west: float,
    south: float,
    east: float,
    north: float,
    height: int,
    width: int,
) -> np.ndarray:
    """Scatter a classified lon/lat/label table into a class-code raster.

    `classify_df` concatenates two independently-classified subsets, so by the
    time we see the table its row order no longer matches the grid
    `build_dataframe` flattened — it cannot simply be reshaped. Placing every
    row by its own coordinate instead, through the same linear bbox mapping the
    change overlay uses, puts each cell back where it belongs whatever the order,
    and keeps the class map registered with the change mask and the scenes.

    Cells no row lands on stay 0 (no cloud-free observation).
    """
    codes = np.zeros((height, width), dtype=np.uint8)
    if len(df) == 0 or height < 1 or width < 1:
        return codes

    lon_span = (east - west) or 1e-9
    lat_span = (north - south) or 1e-9
    lon = df["Longitude"].to_numpy(dtype=np.float64)
    lat = df["Latitude"].to_numpy(dtype=np.float64)
    cols = np.clip(((lon - west) / lon_span * width).astype(int), 0, width - 1)
    rows = np.clip(((north - lat) / lat_span * height).astype(int), 0, height - 1)

    labels = df["classifier"].to_numpy()
    for name, code in CLASS_MAP_CODES.items():
        hit = labels == name
        if hit.any():
            codes[rows[hit], cols[hit]] = code
    return codes


def _class_edges(codes: np.ndarray) -> np.ndarray:
    """Hairline mask along the seams between differing classes.

    Only the cell on one side of each seam is marked — each axis is compared
    against the neighbour ahead of it — so a shared boundary comes out one pixel
    wide rather than two. Unclassified cells are excluded on both sides, so the
    outer edge of coverage is not outlined; that is a data limit, not a boundary
    between two things on the ground.
    """
    edge = np.zeros(codes.shape, dtype=bool)
    labelled = codes > 0
    edge[:-1, :] |= labelled[:-1, :] & labelled[1:, :] & (codes[:-1, :] != codes[1:, :])
    edge[:, :-1] |= labelled[:, :-1] & labelled[:, 1:] & (codes[:, :-1] != codes[:, 1:])
    return edge


def _shade_along_ramp(ramp: tuple[tuple[int, int, int], ...], t: np.ndarray) -> np.ndarray:
    """Evaluate a three-stop colour ramp at positions `t` in [0, 1].

    Returns an (N, 3) float array. Two linear segments (dark->mid, mid->light)
    give the curve a defined middle, which a straight dark->light interpolation
    does not: the mid stop is the class's recognisable colour, and it should land
    at mid brightness rather than wherever a two-point blend happens to put it.
    """
    stops = np.asarray(ramp, dtype=np.float64)
    lower = np.where(t < 0.5, 0, 1)
    frac = np.where(t < 0.5, t * 2.0, (t - 0.5) * 2.0)
    start = stops[lower]
    end = stops[lower + 1]
    return start + (end - start) * frac[:, None]


def _render_class_map(codes: np.ndarray, luma: np.ndarray | None) -> Image.Image:
    """Paint a class-code raster as a shaded, outlined land-cover map.

    `luma` is the scene's own brightness in [0, 1] on the same grid, and is what
    keeps this from looking like a paint-by-numbers: it picks each cell's point
    along its class ramp. It is stretched *within* each class, because a class
    occupies only a narrow slice of the scene's overall range — stretching
    globally would leave water permanently at the dark end of its ramp and soil
    permanently at the light end, wasting both.

    Pass `luma=None` (no imagery came back) and every cell takes its class's mid
    stop, i.e. the flat single-colour rendering.
    """
    height, width = codes.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if height < 1 or width < 1:
        return Image.fromarray(rgba, mode="RGBA")

    shade = (
        np.full(codes.shape, 0.5, dtype=np.float64)
        if luma is None
        else np.clip(np.asarray(luma, dtype=np.float64), 0.0, 1.0)
    )

    for code, ramp in CLASS_MAP_RAMPS.items():
        hit = codes == code
        if not hit.any():
            continue
        values = shade[hit]
        low, high = np.percentile(values, CLASS_MAP_PERCENTILES)
        if high <= low:  # a class of uniform brightness — sit it at the mid stop
            position = np.full(values.shape, 0.5)
        else:
            position = np.clip((values - low) / (high - low), 0.0, 1.0) ** CLASS_MAP_GAMMA
            floor, ceiling = CLASS_MAP_SHADE_RANGE
            position = floor + position * (ceiling - floor)
        rgba[hit, :3] = _shade_along_ramp(ramp, position).round().astype(np.uint8)
        rgba[hit, 3] = 255

    edges = _class_edges(codes)
    rgba[edges, :3] = CLASS_MAP_EDGE_RGB
    rgba[edges, 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def _luma_from_rgba(img: Image.Image) -> np.ndarray:
    """Perceived brightness in [0, 1] of a rendered RGBA scene.

    Taken from the *rendered* image rather than raw reflectance so both dates'
    class maps are shaded on the shared before/after stretch — the same ground
    then shades the same way on both sides, which is the whole point of that
    stretch existing.
    """
    arr = np.asarray(img.convert("RGBA"), dtype=np.float64)
    luma = (
        0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    ) / 255.0
    # Transparent cells carry no signal; mid keeps them off both ramp extremes.
    return np.where(arr[..., 3] > 0, luma, 0.5)


def _class_map_png(
    df: pd.DataFrame,
    grid_shape: tuple[int, int],
    bbox: tuple[float, float, float, float],
    scene: Image.Image | None,
    target: tuple[int, int],
) -> tuple[str, list[dict]]:
    """Render one date's classification as a PNG, plus its class breakdown.

    `grid_shape` is the classifier's own (rows, cols); `target` is the (height,
    width) of the scene imagery. The codes are built at the classifier's
    resolution — one cell per actual prediction, so the counts are exact — then
    nearest-neighbour resized to the scene's shape. Resizing the *codes* rather
    than the finished picture is what keeps the outlines a crisp single pixel at
    full size instead of a staircase scaled up with everything else.
    """
    west, south, east, north = bbox
    grid_h, grid_w = grid_shape
    codes = _class_code_grid(df, west, south, east, north, grid_h, grid_w)

    total = int((codes > 0).sum())
    classes = []
    for code, name in CLASS_MAP_NAMES.items():
        count = int((codes == code).sum())
        red, green, blue = CLASS_MAP_RAMPS[code][1]
        classes.append({
            "name": name,
            "color": f"#{red:02X}{green:02X}{blue:02X}",
            "pixels": count,
            "percent": round(100.0 * count / total, 2) if total else 0.0,
        })
    classes.sort(key=lambda c: c["pixels"], reverse=True)

    target_h, target_w = target
    if (target_h, target_w) != (grid_h, grid_w) and target_h > 0 and target_w > 0:
        codes = np.asarray(
            Image.fromarray(codes, mode="L").resize((target_w, target_h), Image.NEAREST)
        )

    luma = _luma_from_rgba(scene) if scene is not None else None
    if luma is not None and luma.shape != codes.shape:
        # Defensive: a scene of a different shape cannot shade this grid, and
        # guessing an alignment would put texture on the wrong ground.
        luma = None

    return (
        _encode_png(
            _render_class_map(codes, luma), 1,
            compress_level=CLASS_MAP_PNG_COMPRESS_LEVEL,
        ),
        classes,
    )


def _joint_stretch_bounds(scenes: list[tuple[np.ndarray, np.ndarray]]) -> tuple[float, float]:
    """One (low, high) reflectance range shared by every band of every scene."""
    pool = []
    for rgb, valid in scenes:
        arr = np.asarray(rgb, dtype=np.float64)
        ok = np.asarray(valid, dtype=bool)
        if not ok.any():
            continue
        for channel in range(arr.shape[-1]):
            band = arr[..., channel]
            values = band[ok & np.isfinite(band)]
            if values.size:
                pool.append(values)
    if not pool:
        return 0.0, 1.0

    combined = np.concatenate(pool)
    if combined.size > STRETCH_SAMPLE_LIMIT:
        combined = combined[:: math.ceil(combined.size / STRETCH_SAMPLE_LIMIT)]

    low, high = np.percentile(combined, TRUE_COLOR_COMPARE_PERCENTILES)
    if high <= low:
        high = low + 1e-6
    return float(low), float(high)


def _render_compare_true_color(
    rgb: np.ndarray, valid: np.ndarray, low: float, high: float,
) -> Image.Image:
    """Render a scene through a stretch shared with the date it is compared to.

    The land-use backdrop stretches each band to its own 2-98% range, per image.
    That is wrong for a before/after pair, twice over:

    * Per *image* means each date gets its own mapping, so identical ground
      renders as different colours across the two panes and a normalisation
      artefact reads as change.
    * Per *band* forces red, green and blue to each fill the range, which
      neutralises the real colour balance — vegetation over-saturates and water
      crushes to black. That is what makes the output look artificial.

    Applying one range to every band of both scenes keeps the colour relationships
    the sensor actually recorded, and makes the pair genuinely comparable.
    """
    arr = np.asarray(rgb, dtype=np.float64)
    ok = np.asarray(valid, dtype=bool)
    height, width = ok.shape
    out = np.zeros((height, width, 4), dtype=np.uint8)

    span = (high - low) or 1e-6
    finite = np.ones((height, width), dtype=bool)
    for channel in range(3):
        finite &= np.isfinite(arr[..., channel])
    ok = ok & finite

    for channel in range(3):
        stretched = np.clip((arr[..., channel] - low) / span, 0.0, 1.0)
        stretched = np.where(ok, stretched, 0.0) ** TRUE_COLOR_GAMMA
        out[..., channel] = (stretched * 255.0).astype(np.uint8)
    out[..., 3] = np.where(ok, 255, 0).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def _before_after_pngs(
    polygon: list,
    old_year: int,
    old_month: int,
    new_year: int,
    new_month: int,
    client_id: str,
    client_secret: str,
) -> tuple[str, str, int, int, Image.Image, Image.Image]:
    """Fetch both dates' scenes and render them through one shared stretch.

    ``whole_month=False`` matches the composites change detection reads (days
    1-15), so the picture covers the period the numbers came from.

    Returns ``(old_b64, new_b64, width, height, old_img, new_img)``. The rendered
    images come back alongside the encoded ones so the class maps can be shaded
    from the same stretch rather than re-deriving a second, inconsistent one.
    Raises if either fetch fails — the caller decides whether comparison imagery
    is optional.
    """
    old_rgb, old_valid = fetch_true_color_base(
        polygon, old_year, old_month, client_id, client_secret, whole_month=False,
    )
    new_rgb, new_valid = fetch_true_color_base(
        polygon, new_year, new_month, client_id, client_secret, whole_month=False,
    )

    low, high = _joint_stretch_bounds([(old_rgb, old_valid), (new_rgb, new_valid)])
    logger.info("Before/after shared stretch: reflectance %.4f-%.4f", low, high)

    old_img = _render_compare_true_color(old_rgb, old_valid, low, high)
    new_img = _render_compare_true_color(new_rgb, new_valid, low, high)
    return (
        _encode_png(old_img, 1),
        _encode_png(new_img, 1),
        min(old_img.width, new_img.width),
        min(old_img.height, new_img.height),
        old_img,
        new_img,
    )


def _render_base_true_color(rgb: np.ndarray, valid: np.ndarray) -> Image.Image:
    """Stretch a fetched native-resolution true-colour array into an RGBA image.

    `rgb` is H×W×3 in [B04, B03, B02] (red, green, blue) order, matching
    TRUE_COLOR_BANDS; `valid` is the cloud/no-data mask from the fetch.
    """
    rgb = np.asarray(rgb, dtype=np.float64)
    bands = [rgb[..., 0], rgb[..., 1], rgb[..., 2]]
    valid = np.asarray(valid, dtype=bool)
    for band in bands:
        valid = valid & np.isfinite(band)
    return _stretch_true_color(bands, valid)


# ── Cache subset serving (Phase 2) ─────────────────────────────────────────
# A request for a smaller area that sits fully inside a previously-computed one
# is served by cropping the cached result — no Sentinel Hub fetch. The land-cover
# label grid is stored (as compact int codes) alongside the response so the
# overlay can be re-rendered and class stats recomputed exactly for the sub-area.
_CODE_TO_LABEL = ["", "Tree", "Crop", "Water", "Soil"]
_LABEL_TO_CODE = {name: i for i, name in enumerate(_CODE_TO_LABEL)}


def _encode_grid(grid: np.ndarray) -> list[list[int]]:
    """Land-cover label grid -> nested int codes (compact, JSON-friendly)."""
    return [[int(_LABEL_TO_CODE.get(v, 0)) for v in row] for row in grid]


def _decode_grid(codes: list[list[int]]) -> np.ndarray:
    """Nested int codes -> object array of label strings."""
    return np.array(
        [[_CODE_TO_LABEL[int(c)] for c in row] for row in codes], dtype=object
    )


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _crop_png_b64(b64: str | None, cached: tuple, target: tuple) -> tuple[str | None, int, int]:
    """Crop a base64 PNG spanning ``cached`` bounds down to ``target`` bounds.

    Both are ``(west, south, east, north)``. Returns ``(base64, width, height)``,
    or ``(None, 0, 0)`` when there was nothing to crop.
    """
    if not b64:
        return None, 0, 0
    cw, cs, ce, cn = cached
    tw, ts, te, tn = target
    lon_span = (ce - cw) or 1e-9
    lat_span = (cn - cs) or 1e-9

    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
    width, height = img.size
    x0 = _clamp_int(int(round((tw - cw) / lon_span * width)), 0, width - 1)
    x1 = _clamp_int(int(round((te - cw) / lon_span * width)), x0 + 1, width)
    y0 = _clamp_int(int(round((cn - tn) / lat_span * height)), 0, height - 1)
    y1 = _clamp_int(int(round((cn - ts) / lat_span * height)), y0 + 1, height)

    cropped = img.crop((x0, y0, x1, y1))
    return _encode_png(cropped, 1), cropped.width, cropped.height


def _crop_classify(contained: dict, req_bbox: tuple) -> dict:
    """Build a fresh classify response for ``req_bbox`` from a cached larger area.

    ``contained`` is what ``cache_get_containing`` returns: the cached payload,
    the stored label grid (extras), and the cached bounds. We slice the grid to
    the requested sub-box, re-render the overlay, crop the stored backdrop PNG,
    and recompute the class stats for the sub-area.
    """
    payload = contained["payload"]
    extras = contained["extras"]
    cw, cs, ce, cn = contained["bounds"]
    rw, rs, re_, rn = req_bbox

    grid = _decode_grid(extras["labels"])
    grid_h, grid_w = grid.shape
    lon_span = (ce - cw) or 1e-9
    lat_span = (cn - cs) or 1e-9

    # Map the requested box to grid cell indices (lon W->E cols, lat N->S rows).
    c0 = _clamp_int(int(math.floor((rw - cw) / lon_span * grid_w)), 0, grid_w - 1)
    c1 = _clamp_int(int(math.ceil((re_ - cw) / lon_span * grid_w)), c0 + 1, grid_w)
    r0 = _clamp_int(int(math.floor((cn - rn) / lat_span * grid_h)), 0, grid_h - 1)
    r1 = _clamp_int(int(math.ceil((cn - rs) / lat_span * grid_h)), r0 + 1, grid_h)

    sub = grid[r0:r1, c0:c1]
    sub_h, sub_w = sub.shape

    # Cell-aligned bounds of the crop (a hair larger than requested — like snapping).
    west2 = cw + c0 / grid_w * lon_span
    east2 = cw + c1 / grid_w * lon_span
    north2 = cn - r0 / grid_h * lat_span
    south2 = cn - r1 / grid_h * lat_span

    scale = _upscale_factor(sub_w, sub_h)
    image_b64 = _encode_png(_render_overlay(sub), scale)
    out_w, out_h = sub_w * scale, sub_h * scale

    # Crop the backdrop from the stored PNG (which spans the full cached bounds).
    base_b64 = payload.get("baseImagePngBase64")
    if base_b64:
        base_img = Image.open(io.BytesIO(base64.b64decode(base_b64))).convert("RGBA")
        bw, bh = base_img.size
        bx0 = _clamp_int(int(round((west2 - cw) / lon_span * bw)), 0, bw - 1)
        bx1 = _clamp_int(int(round((east2 - cw) / lon_span * bw)), bx0 + 1, bw)
        by0 = _clamp_int(int(round((cn - north2) / lat_span * bh)), 0, bh - 1)
        by1 = _clamp_int(int(round((cn - south2) / lat_span * bh)), by0 + 1, bh)
        base_image_b64 = _encode_png(base_img.crop((bx0, by0, bx1, by1)), 1)
    else:
        base_image_b64 = None

    area_km2 = _bbox_area_km2(west2, south2, east2, north2)
    total_cells = sub_h * sub_w
    classes = []
    classified = 0
    for name, (red, green, blue) in LAND_COVER_COLORS.items():
        count = int((sub == name).sum())
        classified += count
        classes.append({
            "name": name,
            "color": f"#{red:02X}{green:02X}{blue:02X}",
            "pixels": count,
            "percent": round(100.0 * count / total_cells, 2) if total_cells else 0.0,
            "areaKm2": round(area_km2 * count / total_cells, 4) if total_cells else 0.0,
        })
    classes.sort(key=lambda c: c["pixels"], reverse=True)

    return {
        "status": "success",
        "message": f"Classified {classified} of {total_cells} cells",
        "imagePngBase64": image_b64,
        "baseImagePngBase64": base_image_b64,
        "imageWidth": out_w,
        "imageHeight": out_h,
        "bounds": {"north": north2, "south": south2, "east": east2, "west": west2},
        "classes": classes,
        "stats": {
            "gridWidth": sub_w,
            "gridHeight": sub_h,
            "totalCells": total_cells,
            "classifiedCells": classified,
            "resolutionMeters": int(extras.get("resM", 30)),
            "areaKm2": round(area_km2, 4),
            "date": payload.get("stats", {}).get("date"),
        },
        "requestId": payload.get("requestId"),
        "cached": True,
        "cachedSubset": True,
    }


def _crop_analyze(contained: dict, req_bbox: tuple) -> dict:
    """Build a change-detection response for ``req_bbox`` from a cached area.

    Change detection returns per-pixel changes with their own lon/lat, so we
    simply keep the changed pixels inside the sub-box and recompute the counts.
    ``totalPixels`` (the denominator, not part of the changes list) is scaled by
    the sub-area's share of the cached area.
    """
    payload = contained["payload"]
    cw, cs, ce, cn = contained["bounds"]
    rw, rs, re_, rn = req_bbox

    changes = [
        c for c in payload.get("changes", [])
        if rw <= c["Longitude"] <= re_ and rs <= c["Latitude"] <= rn
    ]
    deforestation = sum(1 for c in changes if c.get("mask") == 1)
    water_loss = sum(1 for c in changes if c.get("mask") == 2)

    full_stats = payload.get("stats", {})
    full_total = int(full_stats.get("totalPixels", 0))
    full_area = _bbox_area_km2(cw, cs, ce, cn)
    sub_area = _bbox_area_km2(rw, rs, re_, rn)
    ratio = (sub_area / full_area) if full_area > 0 else 0.0

    stats = dict(full_stats)
    stats["totalPixels"] = max(int(round(full_total * ratio)), len(changes))
    stats["deforestation"] = deforestation
    stats["waterLoss"] = water_loss

    # The stored rasters span the whole cached area, so crop all three to the
    # sub-box. They share bounds and shape, so one set of dimensions covers them.
    cached_bounds = (cw, cs, ce, cn)
    old_png, sub_w, sub_h = _crop_png_b64(payload.get("oldImagePngBase64"), cached_bounds, req_bbox)
    new_png, _, _ = _crop_png_b64(payload.get("newImagePngBase64"), cached_bounds, req_bbox)
    deforestation_png, mask_w, mask_h = _crop_png_b64(
        payload.get("deforestationPngBase64"), cached_bounds, req_bbox,
    )
    water_loss_png, _, _ = _crop_png_b64(
        payload.get("waterLossPngBase64"), cached_bounds, req_bbox,
    )
    # The class maps span the same bounds and shape as the scenes, so the same
    # crop lands on the same ground.
    old_class_png, _, _ = _crop_png_b64(
        payload.get("oldClassPngBase64"), cached_bounds, req_bbox,
    )
    new_class_png, _, _ = _crop_png_b64(
        payload.get("newClassPngBase64"), cached_bounds, req_bbox,
    )
    if sub_w < 1 or sub_h < 1:
        sub_w, sub_h = mask_w, mask_h

    return {
        "status": "success",
        "message": f"Analysis complete: {len(changes)} changed pixels detected",
        "changes": changes,
        "oldImagePngBase64": old_png,
        "newImagePngBase64": new_png,
        "oldClassPngBase64": old_class_png,
        "newClassPngBase64": new_class_png,
        "deforestationPngBase64": deforestation_png,
        "waterLossPngBase64": water_loss_png,
        "imageWidth": sub_w,
        "imageHeight": sub_h,
        "bounds": {"north": rn, "south": rs, "east": re_, "west": rw},
        # No `classBreakdown`: the shares are counted from the classifier's code
        # grid, which is not stored, and the gradient-shaded PNG cannot be
        # counted back. The map itself crops exactly; only the percentages are
        # unavailable for a subset, and the UI simply omits them.
        "stats": stats,
        "requestId": payload.get("requestId"),
        "cached": True,
        "cachedSubset": True,
    }


@app.post("/api/sentinel/classify")
def classify_land_use():
    """Classify one date's composite into land-cover types.

    Returns two pixel-aligned RGBA PNGs spanning the AOI's bounding box: a
    true-colour render of the Sentinel-2 composite, and the colour-mapped class
    labels to draw over it.
    """
    request_id = str(uuid.uuid4())[:8]
    try:
        logger.info("[%s] Incoming /api/sentinel/classify request", request_id)
        payload = request.get_json(force=True, silent=False)

        polygon = payload.get("polygon")
        if not polygon or not isinstance(polygon, list) or len(polygon) < 3:
            return jsonify({"status": "error", "message": "'polygon' with >=3 coords required"}), 400

        now = datetime.utcnow()
        year = int(payload.get("year", now.year))
        month = int(payload.get("month", now.month))
        if not 1 <= month <= 12:
            return jsonify({"status": "error", "message": "'month' must be 1-12"}), 400

        logger.info("[%s] date=%d-%02d  polygon=%d pts", request_id, year, month, len(polygon))

        # Result cache: the whole response is keyed by area + month, so an
        # identical (or ~100 m-close) re-run skips every Sentinel Hub fetch.
        lons = [float(pt[0]) for pt in polygon]
        lats = [float(pt[1]) for pt in polygon]
        bbox_bounds = (min(lons), min(lats), max(lons), max(lats))
        date_key = f"{year}-{month:02d}"
        is_current = (year, month) == (now.year, now.month)

        cached = cache_get("classify", date_key, bbox_bounds, is_current)
        if cached is not None:
            logger.info("[%s] served land-use classification from cache", request_id)
            return jsonify(cached)

        # Phase 2: a smaller area fully inside a computed one is cropped from it.
        contained = cache_get_containing("classify", date_key, bbox_bounds, is_current)
        if contained is not None and contained.get("extras"):
            try:
                sub_result = _crop_classify(contained, bbox_bounds)
                logger.info("[%s] served land-use from a cached larger area (subset)", request_id)
                return jsonify(sub_result)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[%s] subset crop failed (%s); computing fresh", request_id, exc)

        cfg = build_config()
        # whole_month=True: composite every pass in the month so the cloud-masked
        # median has enough clear looks to actually remove clouds (planet.py method).
        df = run_composite_pipeline(
            polygon, year, month, cfg.sh_client_id, cfg.sh_client_secret,
            whole_month=True,
        )

        rows10, cols10 = _grid_shape(df)
        rows30, cols30 = rows10 // 3, cols10 // 3
        if rows30 < 1 or cols30 < 1:
            raise RuntimeError(f"Composite grid too small to classify ({rows10}x{cols10})")

        # The 10 m grid is a nearest-neighbour ×3 expansion of the 30 m composite,
        # so sampling every 3rd cell (offset 1 = the centre) recovers the real
        # 30 m pixels without inventing detail. Stride further if the AOI is big.
        step = 1
        while (rows30 // step) * (cols30 // step) > MAX_CLASSIFY_CELLS:
            step += 1

        row_idx = np.arange(0, rows30, step) * 3 + 1
        col_idx = np.arange(0, cols30, step) * 3 + 1
        flat_idx = (row_idx[:, None] * cols10 + col_idx[None, :]).ravel()
        sub = df.iloc[flat_idx]
        height, width = len(row_idx), len(col_idx)
        logger.info(
            "[%s] grid 10m=%dx%d -> classifying %dx%d cells (step=%d, %d m/px)",
            request_id, rows10, cols10, height, width, step, 30 * step,
        )

        grid = _classify_labels(sub).reshape(height, width)

        scale = _upscale_factor(width, height)
        image_b64 = _encode_png(_render_overlay(grid), scale)
        out_w, out_h = width * scale, height * scale

        # Backdrop: a dedicated native-10 m, cloud-masked Sentinel-2 scene so the
        # imagery under the mask is sharp (the 30 m classification composite is
        # not). It spans the same bounding box, so the client still stacks the two
        # by filling one aspect box — no cell-for-cell size match required. If the
        # extra fetch fails (no creds/network), fall back to the composite render.
        try:
            rgb, valid = fetch_true_color_base(
                polygon, year, month, cfg.sh_client_id, cfg.sh_client_secret,
            )
            base_image_b64 = _encode_png(_render_base_true_color(rgb, valid), 1)
            logger.info("[%s] backdrop: native-resolution cloud-masked scene", request_id)
        except Exception as exc:  # pragma: no cover - network/credentials dependent
            logger.warning(
                "[%s] hi-res backdrop fetch failed (%s); using 30 m composite render",
                request_id, exc,
            )
            base_image_b64 = _encode_png(_render_true_color(sub, height, width), scale)

        lons = [float(pt[0]) for pt in polygon]
        lats = [float(pt[1]) for pt in polygon]
        west, east = min(lons), max(lons)
        south, north = min(lats), max(lats)
        area_km2 = _bbox_area_km2(west, south, east, north)

        total_cells = height * width
        classes = []
        classified = 0
        for name, (red, green, blue) in LAND_COVER_COLORS.items():
            count = int((grid == name).sum())
            classified += count
            classes.append({
                "name": name,
                "color": f"#{red:02X}{green:02X}{blue:02X}",
                "pixels": count,
                "percent": round(100.0 * count / total_cells, 2) if total_cells else 0.0,
                "areaKm2": round(area_km2 * count / total_cells, 4) if total_cells else 0.0,
            })
        classes.sort(key=lambda c: c["pixels"], reverse=True)

        logger.info(
            "[%s] classified %d/%d cells: %s",
            request_id, classified, total_cells,
            {c["name"]: c["pixels"] for c in classes},
        )

        result = {
            "status": "success",
            "message": f"Classified {classified} of {total_cells} cells",
            "imagePngBase64": image_b64,
            "baseImagePngBase64": base_image_b64,
            "imageWidth": out_w,
            "imageHeight": out_h,
            "bounds": {"north": north, "south": south, "east": east, "west": west},
            "classes": classes,
            "stats": {
                "gridWidth": width,
                "gridHeight": height,
                "totalCells": total_cells,
                "classifiedCells": classified,
                "resolutionMeters": 30 * step,
                "areaKm2": round(area_km2, 4),
                "date": f"{year}-{month:02d}",
            },
            "requestId": request_id,
        }
        extras = {
            "labels": _encode_grid(grid),
            "gridW": width,
            "gridH": height,
            "resM": 30 * step,
            # Stored with the grid so cache.py can decode it into per-location
            # rows without importing this module (and without a second copy of
            # the table drifting out of sync).
            "codeToLabel": _CODE_TO_LABEL,
        }
        cache_put("classify", date_key, bbox_bounds, is_current, result, extras=extras)
        return jsonify(result)

    except ValueError as exc:
        logger.exception("[%s] Validation error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 400
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("[%s] Unexpected error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
