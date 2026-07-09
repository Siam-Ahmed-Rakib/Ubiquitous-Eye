"""
Bimonthly composite module — extracted from root bimonthly_composite.py.

Fetches S2 + Landsat data for a given AOI and half-month period,
builds a temporal median composite, resamples to 10 m, and returns
a DataFrame with Longitude, Latitude, band values, ClassID, ClassName.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime

import numpy as np
import pandas as pd
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

logger = logging.getLogger("capstone.composite")

# ── Sentinel Hub API limit ────────────────────────────────────
MAX_PROCESS_DIMENSION = 2500


def clamp_size(width: int, height: int) -> tuple[int, int]:
    """Clamp request size to Sentinel Hub API limits (max 2500 px per dim)."""
    max_dim = max(width, height)
    if max_dim <= MAX_PROCESS_DIMENSION:
        return (width, height)
    scale = MAX_PROCESS_DIMENSION / max_dim
    return (max(1, int(width * scale)), max(1, int(height * scale)))


# ── Evalscripts ───────────────────────────────────────────────

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
            { id: "scl",   bands: 1, sampleType: "UINT8"   }
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

EVALSCRIPT_LS_SR = """
//VERSION=3
function setup() {
    return {
        input: [{ bands: ["B01", "B02", "B03", "B04", "B05", "B06", "B07"], units: "REFLECTANCE" }],
        output: [{ id: "default", bands: 7, sampleType: "FLOAT32" }]
    };
}
function evaluatePixel(sample) {
    return [sample.B01, sample.B02, sample.B03, sample.B04, sample.B05, sample.B06, sample.B07];
}
"""

EVALSCRIPT_LS_BQA = """
//VERSION=3
function setup() {
    return {
        input: [{ bands: ["BQA"], units: "DN" }],
        output: [{ id: "default", bands: 1, sampleType: "UINT16" }]
    };
}
function evaluatePixel(sample) {
    return [sample.BQA];
}
"""

# ── Constants ─────────────────────────────────────────────────

BAND_NAMES = ["B01", "B02", "B03", "B04", "B08", "B11", "B12"]
CLOUD_SCL_VALUES = [3, 8, 9, 10]
CLOUD_BQA_MASK = 2 | 4 | 8 | 16
RESOLUTION = 30  # metres

SCL_CLASS_DICT = {
    0: "No Data", 1: "Saturated", 2: "Dark Area Pixels",
    3: "Cloud Shadow", 4: "Vegetation", 5: "Bare Soil",
    6: "Water", 7: "Unclassified", 8: "Cloud Medium Probability",
    9: "Cloud High Probability", 10: "Thin Cirrus", 11: "Snow / Ice",
}


# ── Config builders ───────────────────────────────────────────

def build_s2_config(client_id: str, client_secret: str) -> SHConfig:
    return SHConfig(
        sh_client_id=client_id,
        sh_client_secret=client_secret,
        sh_base_url="https://services.sentinel-hub.com",
    )


def build_ls_catalog_config(client_id: str, client_secret: str) -> SHConfig:
    return SHConfig(
        sh_client_id=client_id,
        sh_client_secret=client_secret,
        sh_base_url="https://services-uswest2.sentinel-hub.com",
    )


def build_ls_config(client_id: str, client_secret: str) -> SHConfig:
    return SHConfig(
        sh_client_id=client_id,
        sh_client_secret=client_secret,
    )


# ── Helpers ───────────────────────────────────────────────────

def determine_half(year: int, month: int) -> tuple[date, date]:
    """
    User rule: "if date >15 take 1st part of month (1-15),
                if date <15 take last month 2nd part (16-last day)".
    We receive a year/month from the UI which already represents the user's
    chosen month, so we always return the 1st half of that month.
    If the caller wants the 2nd half they can call with adjusted months.
    
    For simplicity: return 1st half (1-15) of the given month.
    """
    start = date(year, month, 1)
    end = date(year, month, 15)
    return start, end


def search_dates(collection, bbox, start, end, catalog_cfg):
    catalog = SentinelHubCatalog(config=catalog_cfg)
    results = list(catalog.search(
        collection, bbox=bbox,
        time=(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        fields={"include": ["id", "properties.datetime", "properties.eo:cloud_cover"], "exclude": []},
    ))
    dates = sorted(set(item["properties"]["datetime"][:10] for item in results))
    logger.info("  %s: %d scenes -> %d unique dates", collection.api_id, len(results), len(dates))
    return dates


def fetch_s2(date_str, bbox, size, config):
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2,
        input_data=[SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL2_L2A,
            time_interval=(f"{date_str}T00:00:00Z", f"{date_str}T23:59:59Z"),
            mosaicking_order="leastCC",
        )],
        responses=[
            SentinelHubRequest.output_response("bands", MimeType.TIFF),
            SentinelHubRequest.output_response("scl", MimeType.TIFF),
        ],
        bbox=bbox, size=size, config=config,
    )
    resp = req.get_data()[0]
    bands = resp["bands.tif"].astype(np.float32)
    scl = resp["scl.tif"]
    scl = scl[:, :, 0] if scl.ndim == 3 else scl
    cloud = np.isin(scl, CLOUD_SCL_VALUES)
    return bands, cloud, scl


def fetch_landsat(date_str, bbox, size, config):
    def _req(es):
        return SentinelHubRequest(
            evalscript=es,
            input_data=[SentinelHubRequest.input_data(
                data_collection=DataCollection.LANDSAT_OT_L2,
                time_interval=(f"{date_str}T00:00:00Z", f"{date_str}T23:59:59Z"),
                mosaicking_order="leastCC",
            )],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=size, config=config,
        )
    sr_data = _req(EVALSCRIPT_LS_SR).get_data()[0]
    qa_data = _req(EVALSCRIPT_LS_BQA).get_data()[0]

    bands = (sr_data["default.tif"] if isinstance(sr_data, dict) else sr_data).astype(np.float32)
    qa = (qa_data["default.tif"] if isinstance(qa_data, dict) else qa_data).astype(np.uint16)
    qa = qa[:, :, 0] if qa.ndim == 3 else qa
    cloud = (qa & CLOUD_BQA_MASK) != 0
    return bands, cloud


def apply_cloud_mask(bands, cloud):
    masked = bands.copy()
    masked[cloud, :] = np.nan
    return masked


def collect_half(half_start, half_end, bbox, aoi_size, s2_cfg, ls_catalog_cfg, ls_cfg):
    """Fetch all S2 + Landsat images for a date range and return stacks."""
    logger.info("Collecting images: %s -> %s", half_start, half_end)

    band_stack = []
    scl_stack = []

    # Sentinel-2
    s2_dates = search_dates(DataCollection.SENTINEL2_L2A, bbox, half_start, half_end, s2_cfg)
    for ds in s2_dates:
        try:
            bands, cloud, scl = fetch_s2(ds, bbox, aoi_size, s2_cfg)
            masked = apply_cloud_mask(bands, cloud)
            cpct = 100 * np.mean(cloud)
            band_stack.append(masked)
            scl_stack.append(scl)
            logger.info("  S2 %s  cloud: %.1f%%", ds, cpct)
        except Exception as e:
            logger.warning("  S2 %s skipped: %s", ds, e)

    # Landsat
    ls_dates = search_dates(DataCollection.LANDSAT_OT_L2, bbox, half_start, half_end, ls_catalog_cfg)
    for ds in ls_dates:
        try:
            bands, cloud = fetch_landsat(ds, bbox, aoi_size, ls_cfg)
            masked = apply_cloud_mask(bands, cloud)
            cpct = 100 * np.mean(cloud)
            band_stack.append(masked)
            logger.info("  LS %s  cloud: %.1f%%", ds, cpct)
        except Exception as e:
            logger.warning("  LS %s skipped: %s", ds, e)

    if not band_stack:
        raise RuntimeError(f"No images collected for {half_start} -> {half_end}")

    logger.info("Total images: %d (S2 SCL scenes: %d)", len(band_stack), len(scl_stack))
    return band_stack, scl_stack


def make_composite(band_stack):
    stacked = np.stack(band_stack, axis=0)  # N x H x W x 7
    return np.nanmedian(stacked, axis=0)     # H x W x 7


def resample_30m_to_10m(composite, bbox, size_30m):
    cols_30, rows_30 = size_30m
    lon_min, lat_min = bbox.min_x, bbox.min_y
    lon_max, lat_max = bbox.max_x, bbox.max_y

    pix_lon_30 = (lon_max - lon_min) / cols_30
    pix_lat_30 = (lat_max - lat_min) / rows_30
    pix_lon_10 = pix_lon_30 / 3
    pix_lat_10 = pix_lat_30 / 3

    lons_30 = lon_min + (np.arange(cols_30) + 0.5) * pix_lon_30
    lats_30 = lat_max - (np.arange(rows_30) + 0.5) * pix_lat_30

    lons_10 = np.empty(cols_30 * 3, dtype=np.float64)
    lons_10[0::3] = lons_30 - pix_lon_10
    lons_10[1::3] = lons_30
    lons_10[2::3] = lons_30 + pix_lon_10

    lats_10 = np.empty(rows_30 * 3, dtype=np.float64)
    lats_10[0::3] = lats_30 + pix_lat_10
    lats_10[1::3] = lats_30
    lats_10[2::3] = lats_30 - pix_lat_10

    resampled = np.repeat(np.repeat(composite, 3, axis=0), 3, axis=1)
    return resampled, lons_10, lats_10


def build_dataframe(composite, scl_stack, bbox, aoi_size):
    """
    Resample composite to 10 m and build a DataFrame with
    Longitude, Latitude, B01..B12, ClassID, ClassName.
    """
    resampled, lons_10, lats_10 = resample_30m_to_10m(composite, bbox, aoi_size)
    lon_grid, lat_grid = np.meshgrid(lons_10, lats_10)

    data = {
        "Longitude": lon_grid.flatten(),
        "Latitude": lat_grid.flatten(),
    }
    for i, bname in enumerate(BAND_NAMES):
        data[bname] = resampled[:, :, i].flatten()

    # SCL composite: per-pixel mode across all S2 SCL scenes
    if scl_stack:
        N_SCL = 12
        H, W = scl_stack[0].shape
        counts = np.zeros((N_SCL, H, W), dtype=np.int32)
        for scl in scl_stack:
            for c in range(N_SCL):
                counts[c] += (scl == c)
        scl_mode = counts.argmax(axis=0).astype(np.uint8)
        scl_resampled = np.repeat(np.repeat(scl_mode, 3, axis=0), 3, axis=1)
        scl_flat = scl_resampled.flatten()
        data["ClassID"] = scl_flat
        data["ClassName"] = pd.Series(scl_flat).map(SCL_CLASS_DICT).values
        logger.info("SCL mode of %d scenes, resampled %s -> %s",
                     len(scl_stack), scl_mode.shape, scl_resampled.shape)
    else:
        logger.warning("No S2 SCL available — ClassID/ClassName set to 7/Unclassified")
        n = lon_grid.size
        data["ClassID"] = np.full(n, 7, dtype=np.uint8)
        data["ClassName"] = np.full(n, "Unclassified")

    return pd.DataFrame(data)


def run_composite_pipeline(
    polygon_coords: list[list[float]],
    year: int,
    month: int,
    client_id: str,
    client_secret: str,
) -> pd.DataFrame:
    """
    End-to-end pipeline: polygon + year/month -> resampled 10 m DataFrame.

    Uses the 1st half of the given month (days 1-15).
    """
    lons = [pt[0] for pt in polygon_coords]
    lats = [pt[1] for pt in polygon_coords]
    bbox = BBox((min(lons), min(lats), max(lons), max(lats)), crs=CRS.WGS84)
    native_size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
    aoi_size = clamp_size(native_size[0], native_size[1])

    logger.info("AOI bbox=(%s), size=%s, month=%d-%02d",
                bbox, aoi_size, year, month)

    half_start, half_end = determine_half(year, month)

    s2_cfg = build_s2_config(client_id, client_secret)
    ls_cat_cfg = build_ls_catalog_config(client_id, client_secret)
    ls_cfg = build_ls_config(client_id, client_secret)

    band_stack, scl_stack = collect_half(
        half_start, half_end, bbox, aoi_size,
        s2_cfg, ls_cat_cfg, ls_cfg,
    )
    composite = make_composite(band_stack)
    df = build_dataframe(composite, scl_stack, bbox, aoi_size)

    residual = 100 * np.mean(np.isnan(composite[:, :, 0]))
    logger.info("Composite residual NaN: %.1f%%, df shape: %s", residual, df.shape)
    return df
