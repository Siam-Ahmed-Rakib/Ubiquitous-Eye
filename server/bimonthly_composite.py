"""
Bimonthly composite module — extracted from root bimonthly_composite.py.

Fetches S2 + Landsat data for a given AOI and half-month period,
builds a temporal median composite, resamples to 10 m, and returns
a DataFrame with Longitude, Latitude, band values, ClassID, ClassName.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

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

# Cloud-free true-colour mosaic, fetched at native resolution just for display.
# `isClear` drops exactly the SCL classes CLOUD_SCL_VALUES removes (cloud shadow 3,
# cloud medium 8, cloud high 9, thin cirrus 10) plus no-data 0 and saturated 1,
# then takes the per-pixel median of the remaining clear observations so a single
# cloudy pass can't bleed through. Reflectance is emitted raw (0..1-ish); the
# server applies its 2-98% stretch, so the exact scale here does not matter.
# Visible-band reflectance above this is almost certainly cloud, not ground
# (bright soil/roofs sit well below it). Catches haze/thin cloud the SCL layer
# misses, which is what leaves bright white blobs and darkens the stretch.
TRUE_COLOR_CLOUD_BRIGHTNESS = 0.35

EVALSCRIPT_TRUE_COLOR = """
//VERSION=3
function setup() {
    return {
        input: [{ bands: ["B02", "B03", "B04", "SCL", "dataMask"] }],
        output: [
            { id: "rgb",  bands: 3, sampleType: "FLOAT32" },
            { id: "mask", bands: 1, sampleType: "UINT8"   }
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

// Composite every pass in the month: for each pixel take the median of the clear
// (non-cloud, non-bright) observations. Where nothing is clear — persistent
// cloud — fall back to the darker quartile of whatever was seen, which dodges
// bright cloud tops, so the pixel is filled instead of left as a black hole.
function evaluatePixel(samples) {
    var cr = [], cg = [], cb = [];
    var all = [];
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

# ── Constants ─────────────────────────────────────────────────

BAND_NAMES = ["B01", "B02", "B03", "B04", "B08", "B11", "B12"]
CLOUD_SCL_VALUES = [3, 8, 9, 10]
# A clear observation must also contain real, unsaturated data. SCL 0/1 are not
# clouds, but counting either as clear would let an empty pixel pass the quality
# gate. Landsat QA bit 0 is the equivalent fill/no-data flag.
INVALID_S2_SCL_VALUES = [0, 1, *CLOUD_SCL_VALUES]
CLOUD_BQA_MASK = 1 | 2 | 4 | 8 | 16
RESOLUTION = 30  # metres

# Change detection starts with the selected calendar month, then grows the
# window evenly around it only when some pixels still lack enough clear looks.
MIN_CLEAR_OBSERVATIONS = 2
ADAPTIVE_EXPANSION_DAYS = 15
MAX_ADAPTIVE_WINDOW_DAYS = 60

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


def month_range(year: int, month: int) -> tuple[date, date]:
    """First to last day of the given month.

    The whole month yields ~6-8 Sentinel-2 passes instead of the ~2-3 in a half
    month, so the temporal-median composite has enough clear looks to actually
    remove clouds (the same approach as planet.py).
    """
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, end


def adaptive_windows(
    start: date,
    end: date,
    max_days: int = MAX_ADAPTIVE_WINDOW_DAYS,
    available_through: date | None = None,
) -> list[tuple[date, date]]:
    """Return the base window followed by balanced expansions up to ``max_days``.

    The first expansion adds 15 total days. The last reaches the cap exactly,
    avoiding an unnecessary one-day final attempt for February.
    """
    base_days = (end - start).days + 1
    if max_days < base_days:
        raise ValueError("max_days cannot be shorter than the base date window")

    targets = [base_days]
    if base_days < max_days:
        targets.append(min(base_days + ADAPTIVE_EXPANSION_DAYS, max_days))
    if targets[-1] < max_days:
        targets.append(max_days)

    windows = []
    for target_days in targets:
        extra = target_days - base_days
        before = extra // 2
        after = extra - before
        window_start = start - timedelta(days=before)
        window_end = end + timedelta(days=after)
        if available_through is not None and window_end > available_through:
            # There are no future acquisitions. Move that unavailable part to
            # the beginning so a current-month request still gets the intended
            # number of real calendar days rather than an empty future half.
            unavailable_days = (window_end - available_through).days
            window_start -= timedelta(days=unavailable_days)
            window_end = available_through
        windows.append((window_start, window_end))
    return windows


def search_dates(collection, bbox, start, end, catalog_cfg, max_cloud_coverage: float | None = None):
    """Return unique acquisition dates, optionally filtered by catalogue cloud cover.

    ``eo:cloud_cover`` is the Sentinel Hub catalogue scene percentage. It is
    evaluated for the scene footprint intersecting ``bbox``; it is not a
    per-pixel cloud calculation for an arbitrary polygon.
    """
    catalog = SentinelHubCatalog(config=catalog_cfg)
    results = list(catalog.search(
        collection, bbox=bbox,
        time=(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        fields={"include": ["id", "properties.datetime", "properties.eo:cloud_cover"], "exclude": []},
    ))
    accepted = []
    rejected = 0
    for item in results:
        properties = item.get("properties", {})
        cloud_cover = properties.get("eo:cloud_cover")
        if max_cloud_coverage is not None:
            try:
                if cloud_cover is None or float(cloud_cover) > max_cloud_coverage:
                    rejected += 1
                    continue
            except (TypeError, ValueError):
                rejected += 1
                continue
        accepted.append(properties["datetime"][:10])
    dates = sorted(set(accepted))
    if max_cloud_coverage is None:
        logger.info("  %s: %d scenes -> %d unique dates", collection.api_id, len(results), len(dates))
    else:
        logger.info(
            "  %s: %d scenes -> %d unique dates at cloud <= %.1f%% (%d scenes rejected)",
            collection.api_id, len(results), len(dates), max_cloud_coverage, rejected,
        )
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
    invalid = np.isin(scl, INVALID_S2_SCL_VALUES)
    return bands, invalid, scl


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


def _true_color_size(bbox, target_resolution, max_px):
    """Pixel size for a native-resolution true-colour request, capped at max_px."""
    native = bbox_to_dimensions(bbox, resolution=target_resolution)
    width, height = clamp_size(native[0], native[1])
    longest = max(width, height)
    if longest > max_px:
        scale = max_px / longest
        width, height = max(1, int(width * scale)), max(1, int(height * scale))
    return width, height


def fetch_true_color_base(
    polygon_coords: list[list[float]],
    year: int,
    month: int,
    client_id: str,
    client_secret: str,
    target_resolution: int = 10,
    max_px: int = 1536,
    whole_month: bool = True,
    date_window: tuple[date, date] | None = None,
):
    """Fetch a sharp, cloud-free Sentinel-2 true-colour scene for the AOI bbox.

    This is display-only: the classification still runs on the 30 m composite the
    models were trained on. Here we pull the RGB bands at their native 10 m and
    let Sentinel Hub mosaic the window, masking clouds per pixel via SCL
    (see EVALSCRIPT_TRUE_COLOR), so the backdrop under the mask is crisp and
    cloud-free rather than a blown-up 30 m composite.

    ``date_window`` takes precedence when the analysis adaptively expanded its
    month. Otherwise ``whole_month`` selects a calendar month or legacy days
    1-15. Keeping these aligned prevents the picture and model from reading
    different periods.

    Returns ``(rgb, valid)`` where ``rgb`` is H×W×3 float32 in [B04, B03, B02]
    order and ``valid`` is an H×W bool mask (False = cloud / no clear pixel).
    """
    lons = [pt[0] for pt in polygon_coords]
    lats = [pt[1] for pt in polygon_coords]
    bbox = BBox((min(lons), min(lats), max(lons), max(lats)), crs=CRS.WGS84)
    size = _true_color_size(bbox, target_resolution, max_px)

    if date_window is not None:
        start, end = date_window
    else:
        start, end = month_range(year, month) if whole_month else determine_half(year, month)
    logger.info("True-colour window: %s -> %s (whole_month=%s)", start, end, whole_month)
    cfg = build_s2_config(client_id, client_secret)

    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_TRUE_COLOR,
        input_data=[SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL2_L2A,
            time_interval=(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            mosaicking_order="leastCC",
        )],
        responses=[
            SentinelHubRequest.output_response("rgb", MimeType.TIFF),
            SentinelHubRequest.output_response("mask", MimeType.TIFF),
        ],
        bbox=bbox, size=size, config=cfg,
    )
    resp = req.get_data()[0]
    rgb = resp["rgb.tif"].astype(np.float32)
    mask = resp["mask.tif"]
    mask = mask[:, :, 0] if mask.ndim == 3 else mask
    valid = mask.astype(bool)
    logger.info("True-colour base: size=%s, clear=%.1f%%", size, 100.0 * np.mean(valid))
    return rgb, valid


def _extend_collection(
    start: date,
    end: date,
    s2_dates: list[str],
    ls_dates: list[str],
    bbox,
    aoi_size,
    s2_cfg,
    ls_cfg,
    band_stack: list[np.ndarray],
    scl_stack: list[np.ndarray],
    fetched_s2: set[str],
    fetched_ls: set[str],
) -> None:
    """Fetch newly eligible acquisition dates, without downloading any twice."""
    for ds in s2_dates:
        scene_date = date.fromisoformat(ds)
        if not start <= scene_date <= end or ds in fetched_s2:
            continue
        try:
            bands, invalid, scl = fetch_s2(ds, bbox, aoi_size, s2_cfg)
            masked = apply_cloud_mask(bands, invalid)
            cpct = 100 * np.mean(invalid)
            band_stack.append(masked)
            scl_stack.append(scl)
            fetched_s2.add(ds)
            logger.info("  S2 %s  cloud/no-data: %.1f%%", ds, cpct)
        except Exception as e:
            logger.warning("  S2 %s skipped: %s", ds, e)

    for ds in ls_dates:
        scene_date = date.fromisoformat(ds)
        if not start <= scene_date <= end or ds in fetched_ls:
            continue
        try:
            bands, cloud = fetch_landsat(ds, bbox, aoi_size, ls_cfg)
            masked = apply_cloud_mask(bands, cloud)
            cpct = 100 * np.mean(cloud)
            band_stack.append(masked)
            fetched_ls.add(ds)
            logger.info("  LS %s  cloud/no-data: %.1f%%", ds, cpct)
        except Exception as e:
            logger.warning("  LS %s skipped: %s", ds, e)


def collect_half(half_start, half_end, bbox, aoi_size, s2_cfg, ls_catalog_cfg, ls_cfg):
    """Fetch all S2 + Landsat images for a fixed date range and return stacks."""
    logger.info("Collecting images: %s -> %s", half_start, half_end)

    band_stack: list[np.ndarray] = []
    scl_stack: list[np.ndarray] = []
    s2_dates = search_dates(DataCollection.SENTINEL2_L2A, bbox, half_start, half_end, s2_cfg)
    ls_dates = search_dates(DataCollection.LANDSAT_OT_L2, bbox, half_start, half_end, ls_catalog_cfg)
    _extend_collection(
        half_start, half_end, s2_dates, ls_dates, bbox, aoi_size, s2_cfg, ls_cfg,
        band_stack, scl_stack, set(), set(),
    )

    if not band_stack:
        raise RuntimeError(f"No images collected for {half_start} -> {half_end}")

    logger.info("Total images: %d (S2 SCL scenes: %d)", len(band_stack), len(scl_stack))
    return band_stack, scl_stack


def clear_observation_count(band_stack: list[np.ndarray]) -> np.ndarray:
    """Number of fully valid seven-band observations contributing per pixel."""
    stacked = np.stack(band_stack, axis=0)
    return np.all(np.isfinite(stacked), axis=-1).sum(axis=0).astype(np.uint16)


def collect_adaptive(
    base_start,
    base_end,
    bbox,
    aoi_size,
    s2_cfg,
    ls_catalog_cfg,
    ls_cfg,
    min_clear_observations: int = MIN_CLEAR_OBSERVATIONS,
    max_window_days: int = MAX_ADAPTIVE_WINDOW_DAYS,
    available_through: date | None = None,
):
    """Collect a full month, expanding only until every pixel meets the gate."""
    windows = adaptive_windows(
        base_start,
        base_end,
        max_window_days,
        available_through=available_through,
    )
    search_start, search_end = windows[-1]

    # Catalog queries are cheap compared with raster downloads. Search the full
    # possible range once, then fetch only dates admitted by each expansion.
    s2_dates = search_dates(DataCollection.SENTINEL2_L2A, bbox, search_start, search_end, s2_cfg)
    ls_dates = search_dates(DataCollection.LANDSAT_OT_L2, bbox, search_start, search_end, ls_catalog_cfg)

    band_stack: list[np.ndarray] = []
    scl_stack: list[np.ndarray] = []
    fetched_s2: set[str] = set()
    fetched_ls: set[str] = set()
    clear_counts = None
    used_start, used_end = windows[0]

    for used_start, used_end in windows:
        logger.info("Adaptive composite attempt: %s -> %s", used_start, used_end)
        _extend_collection(
            used_start, used_end, s2_dates, ls_dates, bbox, aoi_size, s2_cfg, ls_cfg,
            band_stack, scl_stack, fetched_s2, fetched_ls,
        )
        if not band_stack:
            continue

        clear_counts = clear_observation_count(band_stack)
        meets_gate = clear_counts >= min_clear_observations
        coverage = 100.0 * float(np.mean(meets_gate))
        logger.info(
            "Clear-observation gate: %.1f%% pixels have >=%d looks (%d scenes)",
            coverage, min_clear_observations, len(band_stack),
        )
        if bool(np.all(meets_gate)):
            break

    if not band_stack or clear_counts is None:
        raise RuntimeError(f"No images collected for {search_start} -> {search_end}")

    return band_stack, scl_stack, clear_counts, used_start, used_end


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


def build_dataframe(composite, scl_stack, bbox, aoi_size, clear_counts=None):
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

    if clear_counts is not None:
        clear_resampled = np.repeat(np.repeat(clear_counts, 3, axis=0), 3, axis=1)
        data["ClearObservationCount"] = clear_resampled.flatten()

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
    whole_month: bool = False,
    min_clear_observations: int = 1,
    max_window_days: int | None = None,
) -> pd.DataFrame:
    """
    End-to-end pipeline: polygon + year/month -> resampled 10 m DataFrame.

    With ``whole_month`` it starts with every pass in the selected month.
    ``max_window_days`` enables balanced adaptive expansion when pixels do not
    reach ``min_clear_observations``. Otherwise the date range stays fixed.
    """
    lons = [pt[0] for pt in polygon_coords]
    lats = [pt[1] for pt in polygon_coords]
    bbox = BBox((min(lons), min(lats), max(lons), max(lats)), crs=CRS.WGS84)
    native_size = bbox_to_dimensions(bbox, resolution=RESOLUTION)
    aoi_size = clamp_size(native_size[0], native_size[1])

    logger.info("AOI bbox=(%s), size=%s, month=%d-%02d, whole_month=%s",
                bbox, aoi_size, year, month, whole_month)

    half_start, half_end = (
        month_range(year, month) if whole_month else determine_half(year, month)
    )
    today = datetime.now(timezone.utc).date()
    if half_start > today:
        raise ValueError("Cannot build a composite for a future month")
    half_end = min(half_end, today)

    s2_cfg = build_s2_config(client_id, client_secret)
    ls_cat_cfg = build_ls_catalog_config(client_id, client_secret)
    ls_cfg = build_ls_config(client_id, client_secret)

    if min_clear_observations < 1:
        raise ValueError("min_clear_observations must be at least 1")

    if max_window_days is not None:
        band_stack, scl_stack, clear_counts, used_start, used_end = collect_adaptive(
            half_start, half_end, bbox, aoi_size, s2_cfg, ls_cat_cfg, ls_cfg,
            min_clear_observations=min_clear_observations,
            max_window_days=max_window_days,
            available_through=today,
        )
    else:
        band_stack, scl_stack = collect_half(
            half_start, half_end, bbox, aoi_size,
            s2_cfg, ls_cat_cfg, ls_cfg,
        )
        clear_counts = clear_observation_count(band_stack)
        used_start, used_end = half_start, half_end

    composite = make_composite(band_stack)
    df = build_dataframe(composite, scl_stack, bbox, aoi_size, clear_counts)
    df.attrs.update({
        "window_start": used_start,
        "window_end": used_end,
        "minimum_clear_observations": min_clear_observations,
    })

    residual = 100 * np.mean(np.isnan(composite[:, :, 0]))
    logger.info("Composite residual NaN: %.1f%%, df shape: %s", residual, df.shape)
    return df
