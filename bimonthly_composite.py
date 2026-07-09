import numpy as np
import pandas as pd
import calendar
from datetime import datetime
from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection,
    SentinelHubRequest, MimeType, bbox_to_dimensions,
    SentinelHubCatalog
)

# ─────────────────────────────────────────
# 1. CONFIG
# ─────────────────────────────────────────
# S2  catalog + process → EU endpoint
s2_config = SHConfig(
    sh_client_id     = "464635bd-2342-4484-8866-4a607cdcb914",
    sh_client_secret = "FQrkZcXMUR9CYPuiA9MKm9DtXltv12I7",
    sh_base_url      = "https://services.sentinel-hub.com",
)

# Landsat catalog → US endpoint; process routing is automatic via collection.service_url
_creds = dict(
    sh_client_id     = "464635bd-2342-4484-8866-4a607cdcb914",
    sh_client_secret = "FQrkZcXMUR9CYPuiA9MKm9DtXltv12I7",
)
ls_catalog_config = SHConfig(**_creds, sh_base_url="https://services-uswest2.sentinel-hub.com")
ls_config         = SHConfig(**_creds)   # SDK routes process to US via collection.service_url

# ─────────────────────────────────────────
# 2. AREA, RESOLUTION & MONTH
# ─────────────────────────────────────────
aoi_bbox   = BBox(bbox=[90.3, 23.7, 90.5, 23.9], crs=CRS.WGS84)
resolution = 30                          # 30 m — works for both S2 and Landsat
aoi_size   = bbox_to_dimensions(aoi_bbox, resolution=resolution)

YEAR, MONTH = 2025, 10                   # ← change to your target month

last_day    = calendar.monthrange(YEAR, MONTH)[1]
half1_start = datetime(YEAR, MONTH, 1).date()
half1_end   = datetime(YEAR, MONTH, 15).date()
half2_start = datetime(YEAR, MONTH, 16).date()
half2_end   = datetime(YEAR, MONTH, last_day).date()

HALVES = [
    ("1st half", half1_start, half1_end),
    ("2nd half", half2_start, half2_end),
]

print(f"Month      : {YEAR}-{MONTH:02d}")
print(f"1st half   : {half1_start} → {half1_end}")
print(f"2nd half   : {half2_start} → {half2_end}")
print(f"Image size : {aoi_size} px\n")

# ─────────────────────────────────────────
# 3. EVALSCRIPTS
# ─────────────────────────────────────────

# Band layout (index): 0=Coastal  1=Blue  2=Green  3=Red  4=NIR  5=SWIR1  6=SWIR2
# Sentinel-2: 7 SR bands + SCL cloud mask
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

# Landsat 8/9: 7 SR bands + BQA cloud mask in two separate requests
# (Sentinel Hub LOTL2 does not allow mixing REFLECTANCE and DN units in one script)
# Band mapping: B01=Coastal  B02=Blue  B03=Green  B04=Red  B05=NIR  B06=SWIR1  B07=SWIR2
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

# ─────────────────────────────────────────
# 4. CATALOG SEARCH HELPERS
# ─────────────────────────────────────────
def search_dates(collection, bbox, start, end, catalog_cfg):
    catalog = SentinelHubCatalog(config=catalog_cfg)
    results = list(catalog.search(
        collection, bbox=bbox,
        time=(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        fields={"include": ["id", "properties.datetime", "properties.eo:cloud_cover"], "exclude": []}
    ))
    dates = sorted(set(item['properties']['datetime'][:10] for item in results))
    print(f"  {collection.api_id}: {len(results)} scenes → {len(dates)} unique dates")
    for item in results:
        print(f"    {item['properties']['datetime'][:10]}  "
              f"cloud: {item['properties'].get('eo:cloud_cover', 'N/A')}%")
    return dates

# ─────────────────────────────────────────
# 5. FETCH FUNCTIONS
# ─────────────────────────────────────────
CLOUD_SCL_VALUES = [3, 8, 9, 10]          # S2: cloud shadow, medium cloud, high cloud, cirrus
CLOUD_BQA_MASK   = (2 | 4 | 8 | 16)       # Landsat BQA bits: dilated cloud, cirrus, cloud, shadow


def fetch_s2(date_str, bbox, size, config):
    """Fetch one S2 date → (bands HxWx4, cloud_mask HxW bool)."""
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2,
        input_data=[SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL2_L2A,
            time_interval=(f"{date_str}T00:00:00Z", f"{date_str}T23:59:59Z"),
            mosaicking_order="leastCC",
        )],
        responses=[
            SentinelHubRequest.output_response("bands", MimeType.TIFF),
            SentinelHubRequest.output_response("scl",   MimeType.TIFF),
        ],
        bbox=bbox, size=size, config=config,
    )
    resp  = req.get_data()[0]
    bands = resp["bands.tif"].astype(np.float32)
    scl   = resp["scl.tif"]
    scl   = scl[:, :, 0] if scl.ndim == 3 else scl
    cloud = np.isin(scl, CLOUD_SCL_VALUES)
    return bands, cloud, scl


def fetch_landsat(date_str, bbox, size, config):
    """Fetch one Landsat date → (bands HxWx4, cloud_mask HxW bool)."""
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
    qa    = (qa_data["default.tif"] if isinstance(qa_data, dict) else qa_data).astype(np.uint16)
    qa    = qa[:, :, 0] if qa.ndim == 3 else qa
    cloud = (qa & CLOUD_BQA_MASK) != 0
    return bands, cloud


def apply_cloud_mask(bands, cloud):
    masked = bands.copy()
    masked[cloud, :] = np.nan
    return masked

# ─────────────────────────────────────────
# 6. COLLECT ALL IMAGES PER HALF
# ─────────────────────────────────────────
def collect_half(half_label, start, end):
    print(f"\n{'='*55}")
    print(f"  {half_label.upper()}: {start} → {end}")
    print(f"{'='*55}")

    # --- Sentinel-2 ---
    print("\n[Sentinel-2 catalog]")
    s2_dates = search_dates(DataCollection.SENTINEL2_L2A, aoi_bbox, start, end, s2_config)

    band_stack        = []
    cloud_pcts        = []
    labels            = []
    scl_stack         = []

    for date_str in s2_dates:
        print(f"  Fetching S2 {date_str} ...", end=" ")
        try:
            bands, cloud, scl = fetch_s2(date_str, aoi_bbox, aoi_size, s2_config)
            masked = apply_cloud_mask(bands, cloud)
            cpct   = 100 * np.mean(cloud)
            band_stack.append(masked)
            cloud_pcts.append(cpct)
            labels.append(f"S2 {date_str}\ncloud: {cpct:.0f}%")
            scl_stack.append(scl)
            print(f"ok  cloud: {cpct:.1f}%")
        except Exception as e:
            print(f"skipped: {e}")

    # --- Landsat ---
    print("\n[Landsat 8/9 catalog]")
    ls_dates = search_dates(DataCollection.LANDSAT_OT_L2, aoi_bbox, start, end, ls_catalog_config)

    for date_str in ls_dates:
        print(f"  Fetching LS {date_str} ...", end=" ")
        try:
            bands, cloud = fetch_landsat(date_str, aoi_bbox, aoi_size, ls_config)
            masked = apply_cloud_mask(bands, cloud)
            cpct   = 100 * np.mean(cloud)
            band_stack.append(masked)
            cloud_pcts.append(cpct)
            labels.append(f"LS {date_str}\ncloud: {cpct:.0f}%")
            print(f"ok  cloud: {cpct:.1f}%")
        except Exception as e:
            print(f"skipped: {e}")

    if len(band_stack) == 0:
        raise RuntimeError(f"No images collected for {half_label}.")

    print(f"\n  Total images collected: {len(band_stack)}")
    print(f"  S2 SCL scenes for composite: {len(scl_stack)}")
    return band_stack, cloud_pcts, labels, scl_stack

# ─────────────────────────────────────────
# 7. TEMPORAL MEDIAN COMPOSITE
# ─────────────────────────────────────────
def make_composite(band_stack):
    """Stack all cloud-masked images → nanmedian per pixel."""
    stacked   = np.stack(band_stack, axis=0)          # N x H x W x 7
    composite = np.nanmedian(stacked, axis=0)          # H x W x 7
    return composite

# ─────────────────────────────────────────
# 8. RUN BOTH HALVES
# ─────────────────────────────────────────
results = {}
for half_label, start, end in HALVES:
    band_stack, cloud_pcts, labels, scl_stack = collect_half(half_label, start, end)
    composite = make_composite(band_stack)
    residual  = 100 * np.mean(np.isnan(composite[:, :, 0]))
    avg_cloud = np.mean(cloud_pcts)
    print(f"\n  Composite residual cloud : {residual:.1f}%")
    print(f"  Mean cloud across images : {avg_cloud:.1f}%")
    results[half_label] = dict(
        composite=composite,
        scl_stack=scl_stack,
    )

# ─────────────────────────────────────────
# 14. RESAMPLE TO 10m & EXPORT CSV
# ─────────────────────────────────────────
# Band order in the composite array (index → name)
BAND_NAMES = ["B01", "B02", "B03", "B04", "B08", "B11", "B12"]

# SCL class mapping (same as resample.py)
SCL_CLASS_DICT = {
    0: "No Data",
    1: "Saturated",
    2: "Dark Area Pixels",
    3: "Cloud Shadow",
    4: "Vegetation",
    5: "Bare Soil",
    6: "Water",
    7: "Unclassified",
    8: "Cloud Medium Probability",
    9: "Cloud High Probability",
    10: "Thin Cirrus",
    11: "Snow / Ice"
}


def resample_30m_to_10m(composite, bbox, size_30m):
    """
    Nearest-neighbour resample: 30m composite (H×W×7) → 10m (3H×3W×7).
    Returns the upsampled array and 1-D lon/lat coordinate arrays for the 10m grid.
    Same approach as resample_20m_to_10m in resample.py (pixel-centre interleaving).
    """
    cols_30, rows_30 = size_30m          # sentinelhub returns (width, height)
    lon_min, lat_min = bbox.min_x, bbox.min_y
    lon_max, lat_max = bbox.max_x, bbox.max_y

    pix_lon_30 = (lon_max - lon_min) / cols_30
    pix_lat_30 = (lat_max - lat_min) / rows_30
    pix_lon_10 = pix_lon_30 / 3
    pix_lat_10 = pix_lat_30 / 3

    # 30m pixel centres
    lons_30 = lon_min + (np.arange(cols_30) + 0.5) * pix_lon_30
    lats_30 = lat_max - (np.arange(rows_30) + 0.5) * pix_lat_30   # north→south

    # 10m sub-pixel centres interleaved inside each 30m pixel
    lons_10 = np.empty(cols_30 * 3, dtype=np.float64)
    lons_10[0::3] = lons_30 - pix_lon_10   # west sub-pixel
    lons_10[1::3] = lons_30                 # centre
    lons_10[2::3] = lons_30 + pix_lon_10   # east sub-pixel

    lats_10 = np.empty(rows_30 * 3, dtype=np.float64)
    lats_10[0::3] = lats_30 + pix_lat_10   # north sub-pixel
    lats_10[1::3] = lats_30                 # centre
    lats_10[2::3] = lats_30 - pix_lat_10   # south sub-pixel

    # nearest-neighbour upsampling (repeat 3× along rows then cols)
    resampled = np.repeat(np.repeat(composite, 3, axis=0), 3, axis=1)  # 3H×3W×7
    return resampled, lons_10, lats_10


for half_label in ["1st half", "2nd half"]:
    composite_30m = results[half_label]["composite"]          # H×W×7
    scl_stack     = results[half_label]["scl_stack"]          # list of H×W uint8

    resampled_10m, lons_10, lats_10 = resample_30m_to_10m(
        composite_30m, aoi_bbox, aoi_size
    )

    lon_grid, lat_grid = np.meshgrid(lons_10, lats_10)

    data = {
        "Longitude": lon_grid.flatten(),
        "Latitude":  lat_grid.flatten(),
    }
    for i, bname in enumerate(BAND_NAMES):
        data[bname] = resampled_10m[:, :, i].flatten()

    # --- SCL composite: per-pixel mode across all S2 SCL scenes ---
    if scl_stack:
        N_SCL_CLASSES = 12
        H, W = scl_stack[0].shape
        counts = np.zeros((N_SCL_CLASSES, H, W), dtype=np.int32)
        for scl in scl_stack:
            for c in range(N_SCL_CLASSES):
                counts[c] += (scl == c)
        scl_mode = counts.argmax(axis=0).astype(np.uint8)     # H×W
        # upsample 3× (nearest-neighbour) to 10m
        scl_resampled = np.repeat(np.repeat(scl_mode, 3, axis=0), 3, axis=1)
        scl_flat = scl_resampled.flatten()
        data["ClassID"]   = scl_flat
        data["ClassName"] = pd.Series(scl_flat).map(SCL_CLASS_DICT).values
        print(f"  SCL: mode of {len(scl_stack)} S2 scene(s), "
              f"resampled {scl_mode.shape} (30m) → {scl_resampled.shape} (10m)")
    else:
        print(f"  Warning: no S2 SCL available for {half_label}, ClassID/ClassName omitted")

    df = pd.DataFrame(data)

    csv_fname = f"composite_{half_label.replace(' ', '_')}_10m.csv"
    df.to_csv(csv_fname, index=False)
    print(f"Saved → {csv_fname}  shape: {df.shape}  columns: {list(df.columns)}")
