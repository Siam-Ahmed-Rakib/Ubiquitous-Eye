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

from bimonthly_composite import fetch_true_color_base, run_composite_pipeline
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
    """Serve the built React app for the root URL and SPA routes."""
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

        return jsonify({
            "status": "success",
            "message": f"Analysis complete: {len(changed)} changed pixels detected",
            "changes": changes,
            "stats": {
                "totalPixels": len(merged),
                "deforestation": int((changed["mask"] == 1).sum()),
                "waterLoss": int((changed["mask"] == 2).sum()),
                "oldDate": f"{old_year}-{old_month:02d}",
                "newDate": f"{new_year}-{new_month:02d}",
            },
            "requestId": request_id,
        })

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


def _encode_png(img: Image.Image, scale: int) -> str:
    """Upscale and base64-encode a raster. Nearest-neighbour keeps cells sharp."""
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
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

        return jsonify({
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
        })

    except ValueError as exc:
        logger.exception("[%s] Validation error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 400
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("[%s] Unexpected error: %s", request_id, exc)
        return jsonify({"status": "error", "message": str(exc), "requestId": request_id}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
