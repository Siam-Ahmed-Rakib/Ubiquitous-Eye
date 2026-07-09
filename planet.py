import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone
from sentinelhub import (
    SHConfig, BBox, CRS, DataCollection,
    SentinelHubRequest, MimeType, bbox_to_dimensions,
    SentinelHubCatalog
)

# # ─────────────────────────────────────────
# # 1. CONFIG
# # ─────────────────────────────────────────
# config = SHConfig()
# config.sh_client_id     = "464635bd-2342-4484-8866-4a607cdcb914"
# config.sh_client_secret = "FQrkZcXMUR9CYPuiA9MKm9DtXltv12I7"
# config.sh_base_url      = "https://services.sentinel-hub.com"

# ─────────────────────────────────────────
# 2. AREA & DATE RANGE
# ─────────────────────────────────────────
aoi_bbox   = BBox(bbox=[90.3, 23.7, 90.5, 23.9], crs=CRS.WGS84)
resolution = 60
aoi_size   = bbox_to_dimensions(aoi_bbox, resolution=resolution)

YEAR, MONTH = 2025, 10          # ← change to your target month
import calendar
start_date = datetime(YEAR, MONTH, 1).date()
end_date   = datetime(YEAR, MONTH, calendar.monthrange(YEAR, MONTH)[1]).date()

print(f"Date range : {start_date} → {end_date}")
print(f"Image size : {aoi_size} px")

# ─────────────────────────────────────────
# 3. CHECK AVAILABLE SCENES VIA CATALOG
# ─────────────────────────────────────────
catalog = SentinelHubCatalog(config=config)

search_results = catalog.search(
    DataCollection.SENTINEL2_L2A,
    bbox=aoi_bbox,
    time=(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
    fields={
        "include": ["id", "properties.datetime", "properties.eo:cloud_cover"],
        "exclude": []
    }
)

available = list(search_results)
print(f"\nAvailable scenes: {len(available)}")
for item in available:
    print(
        " ", item['properties']['datetime'][:10],
        "  cloud cover:", item['properties'].get('eo:cloud_cover', 'N/A'), "%"
    )

if len(available) == 0:
    raise RuntimeError("No scenes found for this AOI and date range.")

available_dates = sorted(set([
    item['properties']['datetime'][:10] for item in available
]))
print(f"\nUnique dates to fetch: {available_dates}")

# ─────────────────────────────────────────
# 4. EVALSCRIPT — DN units, divide manually
# ─────────────────────────────────────────
evalscript = """
//VERSION=3
function setup() {
    return {
        input: [{
            bands: ["B01","B02", "B03", "B04", "B08","B11","B12", "SCL"],
            units: "DN"
        }],
        output: [
            { id: "bands", bands: 4, sampleType: "FLOAT32" },
            { id: "scl",   bands: 1, sampleType: "UINT8"   }
        ]
    };
}
function evaluatePixel(sample) {
    return {
        bands: [sample.B02 / 10000.0,
                sample.B03 / 10000.0,
                sample.B04 / 10000.0,
                sample.B08 / 10000.0],
        scl: [sample.SCL]
    };
}
"""

# ─────────────────────────────────────────
# 5. FETCH DATA
# ─────────────────────────────────────────
def fetch_day(date_str, bbox, size, config):
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=DataCollection.SENTINEL2_L2A,
                time_interval=(f"{date_str}T00:00:00Z", f"{date_str}T23:59:59Z"),
                mosaicking_order="leastCC",
            )
        ],
        responses=[
            SentinelHubRequest.output_response("bands", MimeType.TIFF),
            SentinelHubRequest.output_response("scl",   MimeType.TIFF),
        ],
        bbox=bbox,
        size=size,
        config=config,
    )

    data     = request.get_data()
    response = data[0]

    bands = response["bands.tif"].astype(np.float32)
    scl   = response["scl.tif"]
    scl   = scl[:, :, 0] if scl.ndim == 3 else scl

    # debug: print value range to confirm reflectance
    print(f"  → shape {bands.shape} | "
          f"min {np.nanmin(bands):.4f} "
          f"max {np.nanmax(bands):.4f} "
          f"mean {np.nanmean(bands):.4f}")

    return bands, scl


band_stack    = []
scl_stack     = []
fetched_dates = []

for date_str in available_dates:
    print(f"Fetching {date_str} ...")
    try:
        bands, scl = fetch_day(date_str, aoi_bbox, aoi_size, config)
        band_stack.append(bands)
        scl_stack.append(scl)
        fetched_dates.append(date_str)
    except Exception as e:
        print(f"  → skipped: {e}")

print(f"\nSuccessfully fetched {len(band_stack)} image(s)")

# ─────────────────────────────────────────
# 6. CLOUD MASKING
# ─────────────────────────────────────────
CLOUD_SCL_VALUES = [3, 8, 9, 10]

def apply_cloud_mask(bands, scl):
    masked = bands.copy()
    cloud_pixels = np.isin(scl, CLOUD_SCL_VALUES)
    masked[cloud_pixels, :] = np.nan
    return masked

masked_stack = []
for bands, scl in zip(band_stack, scl_stack):
    masked_stack.append(apply_cloud_mask(bands, scl))

# ─────────────────────────────────────────
# 7. TEMPORAL MEDIAN COMPOSITE
# ─────────────────────────────────────────
if len(masked_stack) == 0:
    raise RuntimeError("No images to composite.")

stack_array = np.stack(masked_stack, axis=0)
composite   = np.nanmedian(stack_array, axis=0)

cloud_free_pct = 100 * np.mean(~np.isnan(composite[:, :, 0]))
print(f"\nCloud-free pixels after composite: {cloud_free_pct:.1f}%")

# ─────────────────────────────────────────
# 8. RGB HELPER — auto stretch + auto DN detect
# ─────────────────────────────────────────
def to_rgb(arr, vmin=None, vmax=None):
    rgb = arr[:, :, [2, 1, 0]].copy()   # B04=R, B03=G, B02=B
    nan_mask = np.isnan(rgb[:, :, 0])   # remember residual cloud-gap pixels
    rgb = np.nan_to_num(rgb, nan=0.0)

    # auto-detect if still in DN range
    actual_max = np.nanmax(rgb)
    if actual_max > 1.0:
        rgb = rgb / 10000.0

    valid = rgb[rgb > 0]
    if len(valid) == 0:
        return rgb  # all black — nothing to stretch

    # percentile stretch for good contrast
    if vmin is None:
        vmin = np.percentile(valid, 2)
    if vmax is None:
        vmax = np.percentile(valid, 98)

    rgb = np.clip((rgb - vmin) / (vmax - vmin + 1e-9), 0, 1)
    # mark pixels that were cloudy in ALL dates as magenta
    rgb[nan_mask] = [1.0, 0.0, 1.0]
    return rgb

# ─────────────────────────────────────────
# 9. PLOT 1 — all individual masked images
# ─────────────────────────────────────────
n    = len(masked_stack)
cols = 4
rows = (n + cols - 1) // cols

fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 5 * rows))
axes = np.array(axes).flatten()

for i, (masked, date_str) in enumerate(zip(masked_stack, fetched_dates)):
    cloud_pct = 100 * np.mean(np.isnan(masked[:, :, 0]))
    axes[i].imshow(to_rgb(masked))
    axes[i].set_title(f"{date_str}\ncloud: {cloud_pct:.0f}%", fontsize=9)
    axes[i].axis("off")

for j in range(i + 1, len(axes)):
    axes[j].axis("off")

plt.suptitle("Individual Sentinel-2 images (cloud masked)", fontsize=13)
plt.tight_layout()
plt.savefig("individual_images.png", dpi=150, bbox_inches="tight")
plt.show()
print("Saved → individual_images.png")

# ─────────────────────────────────────────
# 10. PLOT 2 — best single date vs composite
# ─────────────────────────────────────────
cloud_pcts = [np.mean(np.isnan(m[:, :, 0])) for m in masked_stack]
best_idx   = int(np.argmin(cloud_pcts))
best_cloud_pct      = 100 * cloud_pcts[best_idx]
composite_cloud_pct = 100 * np.mean(np.isnan(composite[:, :, 0]))

fig, axes = plt.subplots(1, 3, figsize=(15, 5))

axes[0].imshow(to_rgb(band_stack[best_idx]))
axes[0].set_title(f"Best date — raw\n{fetched_dates[best_idx]}", fontsize=10)
axes[0].axis("off")

axes[1].imshow(to_rgb(masked_stack[best_idx]))
axes[1].set_title(f"Best date — cloud masked\n{fetched_dates[best_idx]}\nCloud: {best_cloud_pct:.1f}%", fontsize=10)
axes[1].axis("off")

axes[2].imshow(to_rgb(composite))
axes[2].set_title(
    f"Temporal median composite\n{fetched_dates[0]} → {fetched_dates[-1]}"
    f"\nResidual cloud: {composite_cloud_pct:.1f}%  (magenta = unfillable)",
    fontsize=10
)
axes[2].axis("off")

plt.suptitle("Sentinel-2 Cloud Removal — Temporal Median Composite", fontsize=13)
plt.tight_layout()
plt.savefig("composite_output.png", dpi=150, bbox_inches="tight")
plt.show()
print("Saved → composite_output.png")

# ─────────────────────────────────────────
# 11. PLOT 3 — valid observation count heatmap
# ─────────────────────────────────────────
valid_counts = np.sum(
    ~np.isnan(np.stack(masked_stack)[:, :, :, 0]), axis=0
)

fig, ax = plt.subplots(figsize=(6, 6))
im = ax.imshow(valid_counts, cmap='YlGn', vmin=0, vmax=n)
plt.colorbar(im, ax=ax, label='Number of cloud-free observations')
ax.set_title(f"Valid observation count per pixel\n(out of {n} dates)", fontsize=11)
ax.axis("off")
plt.tight_layout()
plt.savefig("valid_obs_count.png", dpi=150, bbox_inches="tight")
plt.show()
print("Saved → valid_obs_count.png")

# ─────────────────────────────────────────
# 12. SAVE COMPOSITE
# ─────────────────────────────────────────
np.save("s2_composite.npy", composite)
print("Saved → s2_composite.npy  shape:", composite.shape)