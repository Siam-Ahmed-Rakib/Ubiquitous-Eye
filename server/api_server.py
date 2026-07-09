"""
Minimal backend API for polygon-based Sentinel Hub SCL retrieval
and full change-detection analysis pipeline.
"""

from __future__ import annotations

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

from bimonthly_composite import run_composite_pipeline
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
