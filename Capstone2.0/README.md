# Capstone 2.0 historical inference pipeline

This folder contains a standalone, resumable pipeline for building monthly land-cover history on a PC server.

For every area and month, the pipeline:

1. Reads WGS84 coordinates and an inclusive month range from `input.txt`.
2. Searches Sentinel Hub for Sentinel-2 L2A and Landsat L2 acquisitions.
3. Downloads each available acquisition into memory as TIFF-backed NumPy arrays.
4. Masks cloud, shadow, cirrus, saturated, fill, and no-data pixels.
5. Builds a seven-band monthly median composite at 30 m.
6. Runs the copied XGBoost, CatBoost, LightGBM, and CART ensemble.
7. Writes a classified Cloud Optimized GeoTIFF (COG), an optional composite COG, and two PNG pictures.
8. Commits the run, file catalogue, source-scene provenance, summaries, and per-cell history to SQL.

Individual Sentinel Hub scene TIFF responses are temporary and are not retained. The monthly composite and classification are the retained products.

## Folder contents

```text
Capstone2.0/
├── pipeline.py
├── README.md
└── capstone_model_v2/
    ├── scaler.joblib
    ├── label_encoder.joblib
    ├── xgboost.joblib
    ├── catboost.joblib
    ├── lightgbm.joblib
    └── cart.joblib
```

These paths appear after the first successful execution:

```text
Capstone2.0/
├── .env                         # you create this; contains secrets
├── input.txt                    # you create this; contains AOIs and dates
├── capstone.db                  # local SQLite database
└── object_store/                # local filesystem object store
    └── <aoi_id>/<year>/<month>/
        ├── classification.cog.tif
        ├── composite.cog.tif
        ├── true-color.png
        └── classification.png
```

## Step 1: Prepare the PC

Use a 64-bit installation of Python 3.11 or newer. Make sure the PC has enough free disk space for the selected AOIs. Seven-band float composites are much larger than single-band classifications.

Create and activate a virtual environment.

Linux:

```bash
cd Capstone2.0
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Windows PowerShell:

```powershell
cd Capstone2.0
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install the runtime. The model-library versions match the backend versions that produced the copied joblib files.

```bash
python -m pip install numpy==2.5.1 pandas==3.0.3 scipy==1.18.0 pillow==12.3.0 joblib==1.5.3 scikit-learn==1.9.0 xgboost-cpu==3.3.0 catboost==1.2.10 lightgbm==4.7.0 sentinelhub==3.11.5 SQLAlchemy==2.0.51 psycopg2-binary==2.9.12 "rasterio>=1.4,<2"
```

If `xgboost-cpu` is unavailable for the operating system, install `xgboost==3.3.0` instead. Do not casually upgrade the model stack: joblib model files are sensitive to library-version changes.

## Step 2: Configure Sentinel Hub credentials

Create `Capstone2.0/.env`:

```dotenv
SH_CLIENT_ID=your-client-id
SH_CLIENT_SECRET=your-client-secret
```

Do not commit `.env` or print its values in logs.

The default database is local SQLite, so no database configuration is necessary. The script creates `capstone.db` automatically.

To use PostgreSQL instead, add a SQLAlchemy-compatible connection string:

```dotenv
DATABASE_URL=postgresql://username:password@localhost:5432/capstone
```

The schema is created automatically. PostgreSQL is preferable if multiple applications will query or write the archive concurrently. SQLite is appropriate for one pipeline process and a light local API.

## Step 3: Create `input.txt`

Each non-comment line has four pipe-separated fields:

```text
aoi_id|start_month|end_month|polygon_json
```

Example for 120 completed months, September 2016 through August 2026:

```text
# Coordinates are [longitude, latitude], not [latitude, longitude].
dhaka_01|2016-09|2026-08|[[90.5805528458,23.8364742130],[90.6298069680,23.8364742130],[90.6298069680,23.8815192581],[90.5805528458,23.8815192581]]
```

Multiple AOIs are allowed:

```text
dhaka_01|2025-01|2025-12|[[90.5805,23.8364],[90.6298,23.8364],[90.6298,23.8815],[90.5805,23.8815]]
gazipur_01|2025-01|2025-12|[[90.3500,24.0000],[90.4000,24.0000],[90.4000,24.0500],[90.3500,24.0500]]
```

Input rules:

- Coordinates use WGS84 longitude and latitude.
- Start and end use `YYYY-MM` and both months are included.
- The polygon needs at least three distinct points. The script closes it automatically.
- `aoi_id` may contain letters, numbers, `.`, `_`, and `-`.
- Keep an AOI's coordinates stable. If the coordinates change, use a new ID.
- A partial current month is allowed, but completed months are preferable for a permanent archive.
- The current processing implementation analyzes the polygon's bounding rectangle, matching the existing backend. It does not clip cells along an irregular polygon edge.

## Step 4: Validate before downloading

Run a dry run:

```bash
python pipeline.py --input input.txt --dry-run
```

This validates coordinates and month ranges and prints every AOI/month job. It performs no Sentinel Hub request and makes no database or object-store changes.

Start with one small AOI and one month. A successful trial proves that credentials, model libraries, Rasterio, object storage, and SQL all work before starting 120 months.

## Step 5: Run the pipeline

```bash
python pipeline.py --input input.txt
```

Useful options:

```text
--object-store PATH   Use a different local object-store directory.
--database-url URL    Override DATABASE_URL and the default SQLite database.
--skip-composite      Save disk by omitting the seven-band composite COG.
--retries 3           Number of attempts for a failed AOI/month.
--retry-delay 20      Initial retry delay; later delays double.
--force               Recompute completed jobs and replace their database rows.
--log-level DEBUG     Show more diagnostic output.
```

For example, save only classifications and PNGs:

```bash
python pipeline.py --input input.txt --skip-composite
```

The pipeline processes one month at a time. A failed month is recorded as `failed`, and later months continue. Running the same command again skips completed jobs and retries failed or interrupted jobs.

`Ctrl+C` leaves the active job as `running`. The next execution recognizes it as incomplete and safely starts that month again.

## Step 6: What happens to the coordinate

For each input polygon, the script calculates:

```text
west  = minimum longitude
east  = maximum longitude
south = minimum latitude
north = maximum latitude
```

That bounding box is sent to Sentinel Hub. Its approximate 30 m dimensions determine the processing grid. Dimensions are capped at 2,500 pixels per side to respect the Process API limit used by the existing backend.

For large large AOIs, classification is automatically sampled more coarsely until it contains at most 260,000 cells. The stored `resolution_metres` records the effective classification resolution.

## Step 7: Sentinel Hub collection and preprocessing

The pipeline searches two collections for the selected calendar month:

```text
Sentinel-2 L2A
Landsat OLI/TIRS L2
```

For each Sentinel-2 date it requests:

```text
B01, B02, B03, B04, B08, B11, B12 → float32 reflectance
SCL                                  → uint8 scene classification
```

Sentinel-2 digital numbers are divided by 10,000 in the evalscript. SCL values `0`, `1`, `3`, `8`, `9`, and `10` are masked because they represent no data, saturation, shadow, cloud, or cirrus.

For each Landsat date it requests seven surface-reflectance bands plus BQA. Pixels selected by the backend's BQA bit mask are removed.

The accepted acquisitions are stacked and reduced with a per-band, per-pixel median:

```text
all clear monthly observations
             ↓
height × width × 7 monthly composite
```

`ClearObservationCount` records how many fully valid observations contributed to each output location.

## Step 8: Classification

Cells with SCL vegetation, bare soil, or water are resolved using the backend's SCL, NDVI, and NDBI path. Other valid cells are passed to all four copied models after the backend's spectral indices and scaler are applied.

The models vote by hard majority. Public results use these class codes:

| Code | Class | Colour |
|---:|---|---|
| 0 | No data/unclassified | Transparent |
| 1 | Tree | `#2E7D32` |
| 2 | Crop | `#9CCC65` |
| 3 | Water | `#1565C0` |
| 4 | Soil | `#A1887F` |

The model's internal `Building` result is folded into `Soil`, matching the current backend and Flutter UI.

## Step 9: Files written to the local object store

One directory is created for each AOI/month:

```text
object_store/dhaka_01/2025/06/
├── classification.cog.tif
├── composite.cog.tif
├── true-color.png
└── classification.png
```

### `classification.cog.tif`

This is the authoritative classified raster:

- One `uint8` band
- Values `0` through `4` from the table above
- NoData value `0`
- EPSG:4326 georeferencing
- Lossless DEFLATE compression
- Categorical nearest-neighbour overviews
- Embedded colour map

### `composite.cog.tif`

This is the reusable model input:

- Seven `float32` bands in the order B01, B02, B03, B04, B08, B11, B12
- EPSG:4326 georeferencing
- Lossless DEFLATE compression
- Continuous-value average overviews
- NaN for missing pixels

Keep this file if future model versions should be able to run without downloading the month again. Use `--skip-composite` if disk cost is more important than reproducibility.

### `true-color.png`

This is the satellite picture shown below the Flutter classification. It is composed as red B04, green B03, and blue B02, with cloud/no-data pixels transparent and a percentile/gamma display stretch.

The script first requests the backend's sharper 10 m Sentinel-2 display composite. If that request fails but the model composite succeeded, it creates the picture from the 30 m composite instead.

### `classification.png`

This is the coloured RGBA classification overlay. No-data cells are transparent. It is enlarged with nearest-neighbour resampling so Flutter displays crisp class edges.

PNG files are display derivatives. The COG and SQL records remain the authoritative data.

## Step 10: Database tables

The pipeline creates these tables:

| Table | Purpose |
|---|---|
| `area_of_interest` | Stable AOI polygon and exact bounding box |
| `inference_run` | One resumable AOI/month/model execution |
| `raster_asset` | Paths, checksums, formats, dimensions, CRS, and NoData values |
| `land_cover_class` | Fixed class vocabulary and display colours |
| `classification_summary` | Per-month class counts, percentages, and area |
| `classification_observation` | Historical class and clear-look count for every grid cell |
| `source_scene` | Sentinel/Landsat catalogue items and whether their date was used |

A completed run and all its related rows are committed in one SQL transaction. The run is not marked `completed` if an observation, summary, provenance, or asset record fails to write.

The database stores object keys and `file://` URIs, not the TIFF/PNG binary bytes. Back up the database and `object_store` together.

## Step 11: Query the database

Open the local database with SQLite:

```bash
sqlite3 capstone.db
```

Show run status:

```sql
SELECT aoi_id, requested_month, status, classified_cells, total_cells, error
FROM inference_run
ORDER BY aoi_id, requested_month;
```

Show monthly class percentages:

```sql
SELECT
    r.aoi_id,
    r.requested_month,
    c.name,
    ROUND(s.percent, 2) AS percent,
    ROUND(s.area_km2, 4) AS area_km2
FROM classification_summary AS s
JOIN inference_run AS r ON r.id = s.run_id
JOIN land_cover_class AS c ON c.id = s.class_id
WHERE r.aoi_id = 'dhaka_01'
  AND r.status = 'completed'
ORDER BY r.requested_month, c.name;
```

Show all stored files for a month:

```sql
SELECT a.asset_type, a.object_key, a.sha256, a.file_size
FROM raster_asset AS a
JOIN inference_run AS r ON r.id = a.run_id
WHERE r.aoi_id = 'dhaka_01'
  AND r.requested_month = '2025-06-01';
```

Show the class history nearest to one coordinate:

```sql
WITH nearest_cell AS (
    SELECT row_index, column_index
    FROM classification_observation
    WHERE aoi_id = 'dhaka_01'
    ORDER BY
        (longitude - 90.6050) * (longitude - 90.6050) +
        (latitude  - 23.8580) * (latitude  - 23.8580)
    LIMIT 1
)
SELECT
    o.observed_month,
    c.name,
    o.longitude,
    o.latitude,
    o.clear_observation_count
FROM classification_observation AS o
JOIN nearest_cell AS n
  ON n.row_index = o.row_index
 AND n.column_index = o.column_index
JOIN land_cover_class AS c ON c.id = o.class_id
WHERE o.aoi_id = 'dhaka_01'
ORDER BY o.observed_month;
```

Show tree-cover change over time:

```sql
SELECT r.requested_month, s.percent, s.area_km2
FROM classification_summary AS s
JOIN inference_run AS r ON r.id = s.run_id
JOIN land_cover_class AS c ON c.id = s.class_id
WHERE r.aoi_id = 'dhaka_01'
  AND r.status = 'completed'
  AND c.name = 'Tree'
ORDER BY r.requested_month;
```

Show failed jobs:

```sql
SELECT aoi_id, requested_month, attempt_count, error
FROM inference_run
WHERE status = 'failed'
ORDER BY requested_month;
```

## Step 12: Serve stored pictures to Flutter

The existing Flutter application expects two Base64 fields:

```json
{
  "baseImagePngBase64": "<true-color.png encoded as Base64>",
  "imagePngBase64": "<classification.png encoded as Base64>"
}
```

A backend endpoint can locate the two `raster_asset` rows for the requested AOI/month, read their local PNG files, Base64-encode them, and return the existing response shape. Flutter can then continue working without modification.

For a future high-volume web service, returning file or tile URLs is more efficient than Base64. That is a separate API change and is not required by this pipeline.

## Step 13: Backup and operational guidance

Back up these together:

```text
Capstone2.0/capstone.db
Capstone2.0/object_store/
Capstone2.0/input.txt
```

Also keep the `capstone_model_v2` directory and the pipeline version used to produce the archive.

Operational recommendations:

- Run only one SQLite-writing pipeline process at a time.
- Do not launch all ten years concurrently; Sentinel Hub access and current preprocessing are intentionally sequential.
- Test one small month before starting the full range.
- Monitor disk usage, especially when retaining composite COGs.
- Keep the PC awake and disable automatic reboot during a long ingestion.
- Rerun the same command after network, power, or subscription failures; completed work is skipped.
- Do not use `--force` unless completed months genuinely need replacement.
- Copy the SQLite database only after stopping the writer, or use SQLite's online backup command.

## Complete command sequence

After Python and the input files are prepared:

```bash
cd Capstone2.0
source .venv/bin/activate
python pipeline.py --input input.txt --dry-run
python pipeline.py --input input.txt
```

To minimize disk usage:

```bash
python pipeline.py --input input.txt --skip-composite
```

To use PostgreSQL and a separate disk as the object store:

```bash
python pipeline.py \
  --input input.txt \
  --database-url postgresql://capstone:password@localhost:5432/capstone \
  --object-store /data/capstone-object-store
```
