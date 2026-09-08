# Capstone Docker Setup

This project is fully dockerized with two services:

- frontend: React app served by Nginx on port 8080
- backend: Flask API (Gunicorn) on port 5000

A third, standalone **Flutter mobile app** (TerraScope) lives in
[`mobile/`](mobile/) — a responsive client that adapts across phones, tablets,
and wide web/desktop. It runs independently of the Docker stack; see
[`mobile/README.md`](mobile/README.md) to build and run it.

## 1) Configure environment

Create a root `.env` file from `.env.example`:

```bash
cp .env.example .env
```

Set:

- `SH_CLIENT_ID`
- `SH_CLIENT_SECRET`

## 2) Build and run

```bash
docker compose up --build
```

## 3) Access app

- Frontend: http://localhost:8080
- Backend: http://localhost:5000

The frontend uses `/api/*` and Nginx proxies it to the backend service.

## 4) Output data

Downloaded SCL JP2 output is persisted on host at:

- `server/GRANULE/L2A_T45QYE_A007010_20260108T044510/IMG_DATA/R20m/T45QYE_20260108T044151_SCL_20m.jp2`

## 5) Stop services

```bash
docker compose down
```








# 🛰️ Ubiquitous Eye

Satellite **land-cover classification** and **change detection** over Bangladesh.
Draw an area on the map, pick a month (or two months), and the backend fetches the
matching Sentinel-2 / Landsat scenes, builds a cloud-masked composite, runs a
four-model ensemble per pixel, and paints the result back over the imagery it was
computed from.

**Live app + API (same URL):**
<https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io>

> Azure Container Apps, min-replicas 1, always warm. No tunnel, no laptop involved.
> Any `trycloudflare.com` link in old notes is dead.

**Contents**

1. [Architecture](#-architecture) · [Repository layout](#-repository-layout)
2. [Full procedure, first to last](#-full-procedure-first-to-last) — Steps 0–6
3. [Satellite image analysis](#-satellite-image-analysis-how-a-pixel-becomes-a-label) — the imagery pipeline in detail
4. [Model training](#-model-training) — dataset build, per-class classifiers, the deployed ensemble
5. [Runtime behaviour](#-runtime-behaviour) · [API reference](#-api-reference)
6. [Local development](#-local-development-without-docker) · [Deployment](#️-deployment-azure-container-apps) · [APK](#-building-the-android-apk)
7. [Troubleshooting](#-troubleshooting) · [Security](#-security) · [Known gaps](#-known-gaps)

---

## 🏗️ Architecture

```
                    ┌────────────────────────────────────────────┐
   Browser ────────►│  ONE CONTAINER  (server/Dockerfile)        │
   or Android APK   │                                            │
                    │  stage 1: flutter build web  (mobile/)     │
                    │  stage 2: python:3.12-slim + gunicorn      │
                    │                                            │
                    │  Flask api_server.py                       │
                    │    /            → /app/frontend_dist (SPA) │
                    │    /api/*       → JSON API                 │
                    │  listens on $PORT (default 5000)           │
                    └───────┬─────────────────┬──────────────────┘
                            │                 │
              ┌─────────────▼──┐        ┌─────▼───────────────┐
              │ Sentinel Hub   │        │ inference/          │
              │ OAuth + Process│        │ capstone_model_v2/  │
              │ Sentinel-2 +   │        │ xgboost / catboost  │
              │ Landsat        │        │ lightgbm / cart     │
              └────────┬───────┘        │ + scaler + encoder  │
                       │                └─────┬───────────────┘
                       ▼                      ▼
        ┌──────────────────────────┐   ┌──────────────────────────┐
        │ ./server/GRANULE         │   │ Supabase Postgres        │
        │ (host bind mount)        │   │ result cache — optional  │
        │ downloaded SCL .jp2      │   │ DATABASE_URL, else no-op │
        └──────────────────────────┘   └──────────────────────────┘
```

`mobile/lib/config.dart` derives the backend URL from `Uri.base.origin` on web, so
the **same image works on localhost, on the Azure URL, or on a custom domain with no
rebuild**. Off web (the APK) there is no serving origin, so the URL must be baked in
with `--dart-define` — see [Building the APK](#-building-the-android-apk).

---

## 📁 Repository layout

```
.
├── mobile/                     # ⭐ Flutter client (web + Android) — THE PRODUCT
│   ├── lib/                    #    screens, widgets, services, models
│   ├── web/                    #    tracked — the image build COPYs it
│   ├── pubspec.lock            #    tracked — same reason
│   ├── test/                   #    flutter tests
│   └── README.md               #    detailed UI/feature documentation
├── server/                     # Flask + gunicorn backend
│   ├── api_server.py           #    all routes, compositing, rendering
│   ├── cache.py                #    Supabase/SQLAlchemy result cache
│   ├── bimonthly_composite.py  #    cloud-masked composite builder
│   ├── inference/              #    ensemble + capstone_model_v2/*.joblib
│   ├── tests/                  #    pytest suite
│   ├── requirements.txt        #    exactly pinned (pickle/version coupling)
│   ├── Dockerfile              #    ⭐ two-stage: Flutter → Python runtime
│   └── GRANULE/                #    persisted scene downloads (bind-mounted)
├── Frontend/                   # legacy React client — NOT deployed
├── deploy/
│   ├── azure-redeploy.sh       # ⭐ use this for every deploy
│   └── azure-deploy.sh         # first-provision only — do NOT re-run
├── docs/
│   ├── HANDOFF.md              # current state, what's done, what isn't
│   ├── REDEPLOY.md             # step-by-step redeploy runbook
│   └── project-memory/         # accumulated findings worth not re-deriving
├── docker-compose.yml          # canonical config (backend 5000)
├── docker-compose.override.yml # local-only: remaps to 5001 / 8081
├── Dockerfile                  # legacy React image
├── mobile.Dockerfile           # optional standalone Nginx Flutter image
├── .env.example
├── CLAUDE.md · DEMO.md · HOWTORUN.md
└── README.md
```

Training lives outside this repo — see [Model training](#-model-training)

---

## 🚀 Full procedure, first to last

### Step 0 — Prerequisites

| Need | Why |
|---|---|
| **Docker Engine 20.10+ with the Compose plugin** (`docker compose`) | the only supported run path |
| **BuildKit enabled** | `server/Dockerfile` starts with `# syntax=docker/dockerfile:1.7` and uses a cache mount. Not optional. Modern Docker has it on by default; if not, `export DOCKER_BUILDKIT=1` |
| **~15 GB free disk** where Docker stores data | Flutter builder base image is several GB, Python wheels ~2 GB, finished image ~1.5 GB |
| **~4 GB RAM** | |
| **Sentinel Hub account** with OAuth client credentials | fetching imagery |
| *(optional)* Supabase Postgres | result cache; without it every request hits Sentinel Hub |
| *(optional)* Flutter 3.27+ stable | only if running the client outside Docker or building the APK |
| *(optional)* Azure CLI on **Ubuntu** | only for deploying |

Check disk and Docker before spending 30 minutes on a build:

```bash
df -h /var/lib/docker
docker system df
docker info >/dev/null && echo "docker ok"
```

If Docker says *permission denied* rather than *daemon not running*:

```bash
sudo usermod -aG docker $USER
newgrp docker          # only affects the shell you run it in
```

---

### Step 1 — Download the project

```bash
git clone https://github.com/Siam-Ahmed-Rakib/Ubiquitous-Eye.git
cd Ubiquitous-Eye
```

If you already have a clone, check the tree **before** pulling:

```bash
git status --short        # you want this to print nothing
```

Two things can show up, both handled:

- **Modified tracked files** (usually `server/Dockerfile` or `deploy/azure-deploy.sh`,
  left over from hand-edits during the first deploy — those edits are already upstream):
  ```bash
  git checkout -- server/Dockerfile deploy/azure-deploy.sh
  ```
- **Untracked `mobile/web/` or `mobile/pubspec.lock`** — these are now *tracked*, so
  `git pull` refuses with *"untracked working tree files would be overwritten"*.
  Delete them; the pull brings back the correct versions:
  ```bash
  rm -rf mobile/web mobile/pubspec.lock
  ```

Then:

```bash
git pull
ls -l mobile/pubspec.lock mobile/web/index.html   # BOTH must exist
```

> **Why those two files matter.** `server/Dockerfile` COPYs `pubspec.lock` and no
> longer runs `flutter create . --platforms web`. If either is re-ignored, the image
> builds fine for whoever has them locally and fails for everyone else, several
> minutes in, with an error that does not name the cause. `azure-redeploy.sh`
> preflights for exactly this.

---

### Step 2 — Configure credentials

```bash
cp .env.example .env
```

`.env` lives at the repo root, is gitignored, and needs exactly three lines:

```dotenv
SH_CLIENT_ID=your-sentinel-hub-client-id
SH_CLIENT_SECRET=your-sentinel-hub-client-secret
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
```

- Get the Sentinel Hub pair from
  <https://apps.sentinel-hub.com/dashboard/> → **User settings** → **OAuth clients** →
  *Create new*. The secret is shown once — copy it immediately.
- `DATABASE_URL` is **optional**. Leave it blank and the cache degrades to a silent
  no-op; every endpoint behaves exactly as before, just slower on repeats.

**Heredoc gotcha that has already cost time:** if you write `.env` with a heredoc, the
closing `EOF` must be alone on its own line at column zero. Glued to the end of the
`DATABASE_URL` line, the value silently gets `EOF` appended and the shell hangs.
Verify with `wc -l .env` (expect 3) and check `DATABASE_URL` ends in `/postgres`.

Without credentials the app still loads — analysis returns *"Sentinel Hub credentials
are missing"*, and the **"Load sample result"** button on the analysis and land-use
screens shows the visualisation with clearly-labelled illustrative data.

---

### Step 3 — Build and run

From the repo root:

```bash
docker compose up --build
```

That is the whole thing. It builds the Flutter web bundle, builds the Python runtime,
and starts the single backend container which serves both.

First build: expect **20–40 minutes** on a slow link — it is nearly all network
(multi-GB Flutter base image, ~2 GB of Python wheels). Subsequent builds are much
shorter; the pip cache mount means an aborted download resumes rather than restarting.

Afterwards:

```bash
docker compose up -d          # start in background
docker compose logs -f        # follow logs
docker compose ps             # what's running
```

---

### Step 4 — Open the app

| What | URL |
|---|---|
| **App + API (default local run)** | <http://localhost:5001> |
| Health check | <http://localhost:5001/api/health> |

> **Why 5001 and not 5000.** `docker-compose.override.yml` is a **local-only** file
> that remaps the host port because 5000 was already taken on the author's machine.
> Compose picks it up automatically. Delete or rename it and you get the canonical
> port from `docker-compose.yml`: the app on **5000**.

Sanity check:

```bash
curl -s http://localhost:5001/api/health
# {"service":"capstone-backend","status":"ok"}
```

Then **actually use the app in a browser** — draw a box, run a classification. The
browser tab should read **"Ubiquitous Eye"**. A passing `curl` is not the same as a
working UI.

---

### Step 5 — Optional profiles

Neither is needed for normal use; the backend already serves the Flutter UI.

```bash
# Standalone Nginx-served Flutter frontend (compiles Flutter a second time)
docker compose --profile standalone-frontend up --build
#   → http://localhost:8081   (8080 without the override file)

# The legacy React client
docker compose --profile react up frontend-react
#   → http://localhost:8082
```

The standalone frontend bakes `BACKEND_URL` into the JS bundle **at build time**, so
it must be a host-reachable address, not the in-Docker one. Changing the backend's
host port means rebuilding it (`docker compose build frontend`), not just restarting.

---

### Step 6 — Stop

```bash
docker compose down          # stop and remove containers
docker compose down -v       # also drop named volumes
```

`./server/GRANULE` is a host bind mount, so downloaded scenes survive both.

---

## 🌍 Satellite image analysis: how a pixel becomes a label

Everything below happens between a user releasing the selection box and the coloured
mask appearing. It is the same sequence in training and at runtime — the only
difference is where the pixels come from (`.SAFE` products on disk during training,
Sentinel Hub Process API at runtime).

### 1. The source data

**Sentinel-2 Level-2A** (ESA/Copernicus) — bottom-of-atmosphere surface reflectance,
already atmospherically corrected by Sen2Cor. A product ships as a `.SAFE` directory:

```
S2B_MSIL2A_20260304T043659_N0512_R033_T45QYG_...SAFE/
├── MTD_MSIL2A.xml              ← product metadata: radiometric offset + quantisation
└── GRANULE/L2A_T45QYG_.../
    └── IMG_DATA/
        ├── R10m/  B02 B03 B04 B08          + TCI
        ├── R20m/  B01* B05 B06 B07 B8A B11 B12 + SCL
        └── R60m/  B09 ...
```

Bands actually used, and why each is there:

| Band | Wavelength | Native res | What it buys |
|---|---|---|---|
| `B01` | 443 nm coastal aerosol | 60/20 m | haze and shallow-water discrimination |
| `B02` `B03` `B04` | blue / green / red | 10 m | true colour, vegetation and water indices |
| `B05` `B06` `B07` | red edge | 20 m | vegetation vigour and stress |
| `B08` | 842 nm NIR | 10 m | the workhorse for vegetation and water |
| `B8A` | narrow NIR | 20 m | red-edge closure |
| `B09` | water vapour | 60 m | atmospheric context |
| `B11` `B12` | SWIR | 20 m | moisture, built-up, bare soil, burn |
| `SCL` | scene classification | 20 m | ESA's per-pixel cloud/shadow/water flags |

Change detection additionally pulls **Landsat** passes, so a month that Sentinel-2
covered badly still has clear looks available.

### 2. Radiometric correction — the step that silently breaks everything

From processing baseline **N0400** onward, ESA stores reflectance with an integer
offset. The raw DN is *not* reflectance:

```
ρ = (DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE      # typically (DN − 1000) / 10000
```

Both values are read **per product** from `MTD_MSIL2A.xml`; the constants above are
only fallbacks. Skip this and every index comes out shifted — NDVI over healthy crop
lands somewhere plausible-looking but wrong, and the classifier, trained on corrected
values, mislabels the whole scene. An all-NaN or all-one-class output is almost always
this.

### 3. Resampling to a common grid

The 20 m and 60 m bands are nearest-neighbour upsampled to the 10 m grid (×2 and ×6)
so every band, the SCL layer and the coordinate grid share one array shape and one
row-per-pixel table. Nearest-neighbour rather than bilinear on purpose: interpolation
would invent reflectance values that the sensor never measured and blur class
boundaries, which is exactly what a per-pixel classifier must not be fed.

### 4. Cloud masking via SCL

The Scene Classification Layer labels every 20 m pixel:

| Code | Class | Treatment |
|---|---|---|
| 0 | No data | dropped |
| 1 | Saturated / defective | dropped |
| 2 | Dark area | dropped |
| **3** | **Cloud shadow** | **masked** |
| **4** | **Vegetation** | **kept — a training label** |
| **5** | **Bare soil** | **kept — a training label** |
| **6** | **Water** | **kept — a training label** |
| 7 | Unclassified | dropped |
| **8** | **Cloud, medium probability** | **masked** |
| **9** | **Cloud, high probability** | **masked** |
| **10** | **Thin cirrus** | **masked** |
| 11 | Snow / ice | dropped (irrelevant over Bangladesh) |

At runtime `CLOUD_SCL_VALUES = [3, 8, 9, 10]` are removed before compositing. In
training, `KEEP_CLASSES = [4, 5, 6]` does double duty: it filters the scene *and*
supplies the ground-truth label, which is why the dataset builder can produce tens of
millions of labelled pixels with no manual annotation at all.

### 5. Spectral indices

Ten indices are computed from the corrected reflectances with NaN-safe division. Each
is in the feature set for a stated reason, not because it exists:

| Index | Formula | Why it is there |
|---|---|---|
| `NDVI` | (B08−B04)/(B08+B04) | vegetation vigour; saturates over dense canopy |
| `EVI` | 2.5(B08−B04)/(B08+6·B04−7.5·B02+1) | NDVI without the saturation, atmosphere-resistant |
| `SAVI` | 1.5(B08−B04)/(B08+B04+0.5) | soil-adjusted — separates sparse crop over bright soil where NDVI collapses toward the soil line |
| `NDBI` | (B11−B08)/(B11+B08) | built-up and dry bright surfaces |
| `NDMI` | (B08−B11)/(B08+B11) | canopy/soil **moisture**; separates wet bare soil and paddy from dry soil |
| `MNDWI` | (B03−B11)/(B03+B11) | water, robust on the turbid rivers Bangladesh is full of |
| `NDWI` | (B03−B08)/(B03+B08) | open water, a second opinion |
| `BSI` | ((B11+B04)−(B08+B02))/((B11+B04)+(B08+B02)) | bare soil, bounded to [−1, 1] |
| `BI` | brightness | sand and river chars are simply brighter |
| `AWEI` | automated water extraction (no-shadow variant) | robust water flag; the only feature using B12 |

> **`NDMI` is exactly `−NDBI`.** Both come from the same B08/B11 pair, so their
> correlation is −1.0000 and one is pure redundancy. The training notebooks detect
> this automatically (`|r| > 0.9995`) and drop one, which is why the water model has
> 21 features rather than 22. Keeping both doesn't hurt accuracy — trees split on
> either identically — it just splits the feature-importance score between two names
> and makes the model harder to read.

### 6. Compositing — why a single date is not enough

Over Bangladesh, a single-date scene during the monsoon is often more cloud than land.
So the runtime builds a **cloud-masked median composite** across a window:

1. Query every Sentinel-2 (and, for analyze, Landsat) pass in the target month.
2. Fetch each, apply the SCL mask, and stack.
3. Take the per-pixel **median** of the clear observations. Median rather than mean
   because a single missed cloud edge is an outlier, and the median ignores it.

If pixels still lack enough clear looks, the window **adapts**: first widened by 15
days, then up to `MAX_ADAPTIVE_WINDOW_DAYS = 60`. This is the trade the project makes
consciously — a wider window means better coverage and slightly worse temporal
precision.

`bimonthly_composite.py` is the standalone version of the same idea for offline use.

### 7. The reliability gate (change detection only)

A change is only reported where **both** dates had at least
`MIN_CLEAR_OBSERVATIONS = 2` clear looks at that pixel. Everything else is reported as
**cloud-uncertain** rather than being forced into a land-cover transition. This is a
deliberate statement about what the data supports: a pixel seen once through haze in
2020 and once in 2025 can produce a confident-looking "deforestation" that is really
two different atmospheres. The UI shows the reliable-pixel count next to the totals
for exactly this reason.

### 8. Rendering

True-colour output uses `B04`/`B03`/`B02` with a 2–98 percentile stretch and gamma
0.8. Two rendering paths exist and the difference matters:

- **Land-use backdrop** — stretches each band to its own 2–98% range *per image*.
  Fine for one scene.
- **Before/after pair** (`_render_compare_true_color`) — **one shared stretch across
  every band of both dates**. Per-image stretching would give each date its own colour
  mapping, so a normalisation artefact reads as change; per-band stretching would
  neutralise the real colour balance (vegetation over-saturates, water crushes to
  black). One range keeps the colours the sensor recorded and makes the pair
  comparable.

PNG encoding uses `compress_level=3` for class maps, not Pillow's `optimize=True`.
On flat banded class-map content `optimize=True` is pathological — measured at
1500×1500: 5.72 s and 5748 KB, versus **0.47 s and 4956 KB** at level 3, decoding to
byte-identical pixels. That is ~10.5 s off every change-detection request. Photographic
true-colour scenes deliberately keep `optimize=True`, where level 3 would be 2× faster
but 15% *larger*.

---

## 🧠 Model training

Training is **not** part of this repo — it runs in Kaggle notebooks against multi-tile
Sentinel-2 datasets. There are two distinct lineages and it is important not to
confuse them.

### Lineage A — the research classifiers (the notebooks)

Per-class **binary XGBoost** models, one per land-cover class, each trained on tens of
millions of pixels.

#### A1. Dataset construction — `refined-fullband.ipynb`

Turns raw `.SAFE` products into a labelled pixel table.

- **Inputs:** 12 Sentinel-2 L2A tiles covering Rangpur, Meherpur, Jessore, Pabna,
  Naogaon, Dinajpur, Bogura, Mymensingh, Dhaka and others, March 2026.
- **Per tile:** read the radiometric offset from `MTD_MSIL2A.xml` → read SCL first and
  build a keep-mask for classes 4/5/6 → **filter before reading the bands**, so the
  full-scene DataFrame never exists in RAM → read each band offset-corrected, resample
  to 10 m, mask → attach lon/lat → compute the ten indices.
- **Output:** one row per kept 10 m pixel, written as snappy Parquet, ~7.5 GB per tile.
  Pushed to Kaggle in six two-tile batches (`sentinel2-bd-part1…6`) because
  `/kaggle/working` is capped at 20 GB.
- **Column order is fixed** by `BAND_ORDER` + `INDEX_ORDER`. XGBoost keys features by
  position, so a reordered column list silently changes predictions.

The label comes free: SCL class 4/5/6 *is* the ground truth. That is the trick that
makes a 124 M-pixel training set possible with no annotation budget.

#### A2. What every classifier notebook does deliberately

These four decisions are why the reported numbers mean something. Do not "fix" them:

1. **`ClassID` and `ClassName` are dropped from the features.** The binary label is a
   deterministic function of `ClassID`. Leave either in and you get 100% accuracy and
   a useless model.
2. **`Longitude` / `Latitude` are dropped as features.** They are UTM metres. A tree
   given them memorises *where* the lakes are on this scene and learns nothing about
   spectra. They are used **only** to build the split.
3. **The split is spatial.** Satellite pixels are strongly autocorrelated — a 10 m
   pixel and its neighbour are nearly the same measurement. Hold out random *pixels*
   and most of the validation set has a near-twin in training: the score comes back at
   0.99 and the model falls over on the next scene. Instead the scene is tiled into
   1 km blocks, the block index is hashed through a splitmix64 finaliser (so adjacent
   blocks scatter instead of clumping), and **whole blocks** go to train / val / test
   at 70 / 15 / 15. The hash is a pure function of the block, so the assignment is
   identical on every shard and in every session, with no state to carry.
4. **The decision threshold is tuned on val and applied unchanged to test.** Tuning it
   on test is the quiet version of the same leak the spatial split exists to prevent.

#### A3. Engineering that makes it fit on a Kaggle P100

- `pd.read_parquet` on 14–16 GB does not survive a 29 GB session; decompressed to
  float64 it is well past 60 GB. Chaining `xgb_model=` across parts fits but biases the
  model toward whichever part was fed last. Neither is acceptable.
- Instead, Parquet row-groups stream through an `xgboost.DataIter` into a
  **`QuantileDMatrix`**, which bins each batch as it arrives and keeps only the
  quantised index on the GPU — roughly 1 byte per value at `max_bin=128`, so
  30 M × 21 ≈ 0.6 GB instead of ~2.5 GB of raw float32. The dense matrix is never
  materialised anywhere.
- Val/test matrices are built with `ref=dtrain` so they reuse the training bin edges.
  Building them independently would quantise them on their own quantiles and silently
  shift every threshold.
- **Rolling checkpoints with a wall-clock deadline.** Every N rounds the booster is
  written to a temp file and `os.replace`d onto `model_latest.json` — the rename is
  atomic, so a checkpoint can never be found half-written. At `DEADLINE_HOURS` (≤ 11,
  under Kaggle's 12 h cap) training saves and stops cleanly instead of dying.
  `ckpt_meta.json` carries `rounds_done`, the merged eval history, and a **fingerprint
  of the tree/loss settings** — changing capacity invalidates the checkpoint
  automatically, because boosting depth-24 trees on top of a depth-8 model gives you a
  blend that matches neither config.
- Known caveat, stated plainly: early stopping is stateless across a resume, so a
  resumed run can overshoot the true best iteration by up to `EARLY_STOP` rounds. That
  costs time, not accuracy — `best_iteration` is selected from the *merged* history.

#### A4. Results

| Model | Notebook | Label | Split | Threshold | Test F1 | Test IoU | PR-AUC | Accuracy |
|---|---|---|---|---|---|---|---|---|
| **Vegetation** | `tree-classifer__1_.ipynb` | `is_vegetation` | spatial, 1 km blocks | 0.4608 | **0.9589** | 0.9210 | 0.9930 | 0.9583 |
| **Water** | `water-classifier.ipynb` | `is_water` | spatial, 1 km blocks | 0.4436 | **0.9195** | 0.8509 | 0.9798 | 0.9198 |
| **Water (fork)** | `fork-of-water-classifier.ipynb` | `is_water` | spatial, 1 km blocks | 0.4445 | 0.9167 | 0.8463 | 0.9785 | 0.9172 |
| **Soil** | `soil-classifer__1_.ipynb` | `is_soil` | ⚠ random pixel holdout | 0.4559 | 0.9009 | 0.8196 | 0.9588 | 0.8972 |
| **Urban vs bare soil** | `model-train-urban.ipynb` | `soilClassifiedId` | ⚠ stratified random | 0.405 | 0.9875 | — | 0.9992 | 0.9850 |

Reading these honestly:

- **Vegetation and water are the trustworthy numbers.** Both used a spatial holdout,
  and both show a val→test delta under 0.002 — the model generalises across blocks it
  never saw, which is the thing that actually matters on a new tile.
- **The two water runs are the same experiment at different budgets.** The fork ran
  3000 rounds and peaked at round 2998; the main run peaked at **round 485** and scored
  slightly higher (F1 0.9195 vs 0.9167). More rounds bought nothing. Water sits at
  ~49.5% prevalence in the held-out blocks, so accuracy is a meaningful number here for
  once.
- **Soil and urban scores are optimistic and should not be quoted as generalisation.**
  The soil notebook uses a random 2% pixel holdout; the urban notebook prints its own
  warning — *"random split. Neighbouring pixels are near-duplicates, so scores here are
  optimistic. Set `SPLIT_STRATEGY='spatial_block'` for an honest estimate."* Both
  notebooks already contain the spatial-block code path. **Re-running them with it is
  the single highest-value outstanding piece of work in the modelling stack.** The
  urban model's 0.9875 F1 in particular is a random-split number and will drop.
- **Metrics are chosen for masks, not for leaderboards.** PR-AUC (threshold-free and
  honest under imbalance, where ROC-AUC flatters almost anything), IoU/Jaccard (the
  segmentation standard), Cohen's κ (the remote-sensing convention), and Brier score
  (calibration — only meaningful because `scale_pos_weight` was left at 1 rather than
  reweighting the classes).

#### A5. Artifacts

Each run writes a `model_card.json` next to the booster containing the **feature list
in order**, the **tuned threshold**, `best_iteration`, the full hyperparameters, the
split configuration (`block_m`, fractions, seed), what was dropped and why, and both
val and test metrics. The inference helper reads `features` and `threshold` from the
card rather than hardcoding them — because column order silently changes XGBoost
predictions and `0.5` is not the right threshold for any of these models.

`model-train-urban.ipynb` also auto-detects binary vs multi-class from the number of
distinct label values and switches objective, eval metrics, class weighting, decision
rule and scoring accordingly.

### Lineage B — the deployed ensemble (`capstone_model_v2`)

This is what actually runs in production, and it is **a different model from the
notebooks above**.

```
server/inference/capstone_model_v2/
├── scaler.joblib          # StandardScaler fitted on the training features
├── label_encoder.joblib   # ClassID ↔ contiguous integer
├── xgboost.joblib
├── catboost.joblib
├── lightgbm.joblib
└── cart.joblib
```

- **12 features, in this exact order:**
  `B04, B03, B08, B02, B01, B12, B11, EVI, NDBI, MNDWI, BSI, NDVI`.
  Any index missing from the input frame is computed on the fly by
  `ensemble_predict`, NaN-safe.
- **Hard majority vote** across the four models — no probability averaging, no
  per-model weights, no tuned threshold. `scipy.stats.mode` over the four predictions,
  then `label_encoder.inverse_transform` back to a ClassID.
- **`expand_class` then refines the coarse ClassID into the reported labels:**

| ClassID | Rule | Label |
|---|---|---|
| 4 (Vegetation) | NDVI > 0.6 | **Tree** |
| 4 (Vegetation) | NDVI ≤ 0.6 | **Crop** |
| 5 (Bare soil) | NDBI > 0.0 | **Building** |
| 5 (Bare soil) | NDBI ≤ 0.0 | **Soil** |
| 6 (Water) | — | **Water** |

  The land-use product folds **Building back into Soil** via `LAND_COVER_MERGE` in
  `api_server.py`. Change detection still uses both labels, so `inference.py` is
  untouched by that merge.
- **Artifacts load lazily and once.** `ensemble_predict` runs on every classified tile;
  re-reading ~6 MB of joblib files per call was pure overhead. Loading on first use
  keeps import cheap and order-independent — which is also what makes gunicorn's
  `--preload` safe.
- **Why four models rather than the best one.** Different inductive biases fail on
  different pixels: gradient boosting on ordered splits (XGBoost), ordered boosting
  with a different regularisation (CatBoost), leaf-wise growth (LightGBM), and a single
  interpretable tree (CART). A majority vote is only wrong where at least two of them
  agree on the same wrong answer. It costs almost nothing: the whole ensemble over
  **31,659 cells takes 0.30 s**.



### Reproducing a training run

```bash
# 1. Build the pixel dataset (Kaggle, one batch per session)
#    refined-fullband.ipynb — set BATCH = 1..6, run, verify, clear, repeat.
#    Output: sentinel2-bd-part{1..6} on Kaggle, ~7.5 GB per tile.

# 2. Train one classifier (Kaggle GPU, P100 or better)
#    Point DATA_DIR / CFG["INPUT"] at the combined parquet dataset.
#    Leave SPLIT_MODE = "spatial". Set SPLIT_STRATEGY = "spatial_block" in the
#    soil and urban notebooks — the defaults there are random and optimistic.
#    Keep DEADLINE_HOURS <= 11.

# 3. Resume across the 12 h wall
#    /kaggle/working survives a *commit*, not a timeout:
#      a. training stops itself at the deadline with a saved checkpoint
#      b. Save Version -> Save & Run All
#      c. next session: Add Input -> Your Work -> this notebook's output
#      d. re-run; find_checkpoint() picks up the highest rounds_done and continues,
#         and the shards come back too, so stage 1 is skipped

# 4. Collect artifacts from /kaggle/working/artifacts:
#      <name>_xgb.ubj, <name>_xgb.json, model_card.json, feature_importance.csv
```

`MAX_TRAIN_ROWS = 30_000_000` is a deliberate default, not a workaround: for a
22-feature binary problem the learning curve is flat long before 30 M rows, and the
last 90 M pixels are near-duplicates that buy ~nothing in F1 while costing ~4× the
time and disk. Set it to `None` once you have seen the baseline.

---

## ⚙️ Runtime behaviour

### Land-use classification — `POST /api/sentinel/classify`

1. Frontend sends the AOI polygon plus a `year` / `month`.
2. Cache lookup: the whole response is keyed by analysis kind + date + a bbox
   quantised to 3 decimals (~100 m at 23° N), so a near-identical re-run is instant.
   A smaller area fully inside a previously computed one is **cropped from it** rather
   than recomputed.
3. On a miss, every Sentinel-2 pass in the month is fetched and composited
   (see [Compositing](#6-compositing--why-a-single-date-is-not-enough)).
4. `ensemble_predict` computes missing indices, scales the 12 features, runs all four
   models, takes the majority vote, decodes to a ClassID.
5. `expand_class` refines it into the reported labels.
6. Two pixel-aligned RGBA PNGs come back, base64-encoded, sampled from the same grid
   at the same integer upscale so they register cell for cell:

| Field | What it is |
|---|---|
| `baseImagePngBase64` | true-colour render of the composite |
| `imagePngBase64` | the colour-mapped class labels |

| Class | Colour |
|---|---|
| Tree | green `#2E7D32` |
| Crop | light green `#9CCC65` |
| Water | blue `#1565C0` |
| Soil | brown `#A1887F` |

The palette is defined once per side — `LAND_COVER_COLORS` in `api_server.py` and
`kLandCoverPalette` in `land_use_result.dart` — and the backend sends its hex colours
with the response, so the legend cannot drift.

Classification runs at the composite's native 30 m. Large areas are strided coarser
(`MAX_CLASSIFY_CELLS = 260,000`; the panel shows the real m/px), which is why the
imagery looks pixellated — that is the true data resolution, not a scaling artefact.
Cells with no cloud-free observation stay transparent in **both** rasters, and the
panel reports what share of the area that was.

The mask is drawn on **the pixels it was derived from**, not on Esri's basemap. A
basemap is a different sensor on a different date, so any apparent misalignment there
would be an artefact of the backdrop rather than the model.

### Change detection — `POST /api/sentinel/analyze`

Runs the reliability-gated pipeline described in
[§7](#7-the-reliability-gate-change-detection-only) and returns four base64 PNGs, all
the same size and spanning the same box:

| Field | What it is |
|---|---|
| `oldImagePngBase64` | cloud-masked true-colour scene, older date |
| `newImagePngBase64` | cloud-masked true-colour scene, newer date |
| `deforestationPngBase64` | `mask 1` pixels, red `#E53935` |
| `waterLossPngBase64` | `mask 2` pixels, orange `#FB8C00` |

Each change class is a separate raster so it can be toggled alone — turning water loss
off to study deforestation is the common case. The mask is painted on the **AFTER**
scene only; BEFORE is deliberately left clear as the reference, since colouring it
would hide the very ground you are comparing against. Both panes share one
`TransformationController`, so zooming either moves the other identically.

The response also carries `eligiblePixels`, `uncertainPixels`,
`minimumClearObservations`, and exact `oldWindowStart`/`oldWindowEnd`/
`newWindowStart`/`newWindowEnd`.

> **Overlay vs counts.** One changed pixel is a single 10 m cell — about one pixel on a
> ~1500 px render, invisible. The backend dilates each hit
> (`CHANGE_DILATION_DIVISOR`) purely so it can be seen. **The counts in the stat cards
> are never dilated — they are exact.** Drop the opacity slider to 0 for the untouched
> scenes.

> **Response size.** Two full scenes make an analyze response several MB (a 25 km² AOI
> ≈ 0.9 MB of base64; the longest side is capped at 1536 px, so the ceiling is ~8 MB).
> Fine on Wi-Fi, noticeably slow on mobile data. The change masks are sparse and
> compress to ~1 KB each — the imagery is the entire cost.

### Where the time actually goes

Measured, so please don't re-litigate it: a cold request is **~98% serial Sentinel Hub
round-trips**. The four-model ensemble over 31,659 cells takes **0.30 s**. Cold
classify ≈ 160 s, cold analyze ≈ 170 s (more since the whole-month composite). Cached
areas return in ~2 s. Container CPU peaked at 51% — never saturated a single core — and
RAM at 266 MB. Nothing in the pipeline is parallel, so more cores buy nothing.

---

## 🔌 API reference

Base URL is the app's own origin (`http://localhost:5001` locally, the Azure URL live).

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness — `{"status":"ok","service":"capstone-backend"}` |
| `POST` | `/api/sentinel/scl` | download + cloud-mask a single SCL JP2 for a polygon |
| `POST` | `/api/sentinel/classify` | land-cover classification for one month |
| `POST` | `/api/sentinel/analyze` | change detection between two months |
| `GET` | `/` and `/<path>` | the Flutter SPA (any unmatched `api/…` path returns 404 JSON) |

**Classify:**

```bash
curl -s -X POST http://localhost:5001/api/sentinel/classify \
  -H "Content-Type: application/json" \
  -d '{
        "polygon": [[90.5806,23.8365],[90.6298,23.8365],
                    [90.6298,23.8815],[90.5806,23.8815]],
        "year": 2026,
        "month": 2
      }'
```

**Analyze:**

```bash
curl -s -X POST http://localhost:5001/api/sentinel/analyze \
  -H "Content-Type: application/json" \
  -d '{
        "polygon": [[90.4652,23.9017],[90.5434,23.9017],
                    [90.5434,23.9822],[90.4652,23.9822]],
        "oldYear": 2020, "oldMonth": 10,
        "newYear": 2025, "newMonth": 10
      }' --max-time 1200
```

`polygon` is `[[lon, lat], …]` and needs ≥ 3 points. `month` must be 1–12.

---

## 💻 Local development without Docker

**Backend:**

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SH_CLIENT_ID=... SH_CLIENT_SECRET=...
python api_server.py            # http://localhost:5000, CORS enabled
```

Versions in `requirements.txt` are pinned **exactly**, not floored. The model artifacts
are pickles and pickles are tied to the library version that wrote them — with `>=`
ranges the same commit can build a different image next week and the models may load
wrong, or silently mispredict, with nothing in the code to blame. (The
sklearn 1.6.1 → 1.9.0 pickle warning was investigated and is benign; do not change the
pin on account of it.) To upgrade, bump deliberately and re-run the tests.

**Flutter client:**

```bash
cd mobile
flutter pub get
flutter run -d chrome --dart-define=BACKEND_URL=http://localhost:5001
```

| Target | `BACKEND_URL` |
|---|---|
| Web / desktop, backend on the same machine | `http://localhost:5001` (or 5000) |
| Android **emulator** | `http://10.0.2.2:5001` — the emulator can't see the host as `localhost` |
| Physical device | your PC's LAN IP, e.g. `http://192.168.0.10:5001` |

`android/` and `ios/` are gitignored and generated locally. If you need them:

```bash
flutter create --platforms=android,ios --org com.ubiquitouseye .
```

Do that **without touching `lib/` or `pubspec.yaml`**, and note that a regenerated
`android/` will drop the release `INTERNET` permission — see the trap list.

**Tests:**

```bash
cd server && python -m pytest tests/      # backend
cd mobile && flutter test                 # client
```

The backend suite disables the result cache by default so it can never touch the
production Supabase database; `test_cache.py` opts back in with throwaway SQLite.

---

## ☁️ Deployment (Azure Container Apps)

**Always `deploy/azure-redeploy.sh`. Never `deploy/azure-deploy.sh`.** The latter is
create-only: with `ACR_NAME` unset it picks a *random* registry name and calls
`az containerapp create`, so re-running it mints a second empty registry and then
points the existing app at an image that isn't in it. It is kept only as the record of
how the app was first provisioned.

### The deploy

```bash
cd ~/Ubiquitous-Eye
git pull
az account show               # must succeed
bash deploy/azure-redeploy.sh
```

**A redeploy does not need `.env`.** The three secrets already live on the Container
App and `az containerapp update --image` leaves them alone. `.env` is only for a
from-scratch provision or a local run.

What the script does, in order:

| stage | expect |
|---|---|
| `preflight` | instant — both `mobile/pubspec.lock` and `mobile/web/index.html` print `present` |
| `checking login` | your subscription table |
| `building ubiquitous-eye:<sha>` | **the long part** — ~5 min CPU plus a multi-GB base image and ~2 GB of wheels. 20–40 min on a slow link |
| `pushing to ubiquitouseye29146.azurecr.io` | a few minutes |
| `rolling a new revision` | ~1 minute |
| `waiting for the new revision to serve` | polls `/api/health` up to ~7 min |
| `verifying the deployed bundle is the new build` | `new client confirmed live` |
| `verifying the app still has its secrets` | `database reachable, cached classify served` |

**Images are tagged by commit SHA, never `:latest`.** Container Apps keys a new
revision off the image *reference* — re-pushing `:latest` leaves the reference
unchanged, so the app can keep serving old layers and the deploy silently does
nothing. A distinct tag also makes rollback a one-liner.

**Trust the live URL over the script's verdict if they disagree.** On the 2026-09-02
run the script reported two failures against a completely healthy deploy: `curl | grep
-q` under `set -o pipefail` turns a *successful* match into a failed pipeline (grep
closes the pipe, curl exits 23), and a check read only the first 400 bytes of a 900 kB
response. Both fixed, but verify by hand before believing a red result.

### Verify

```bash
U=https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io

curl -s $U/api/health
curl -s $U/ | grep -o "<title>.*</title>"                    # "Ubiquitous Eye"
curl -sI $U/canvaskit/canvaskit.wasm | grep -i content-type  # application/wasm
```

Then open it in a browser and draw a box. Classification for `2026-02` over
lon 90.5806–90.6298 / lat 23.8365–23.8815 is cached and should return in ~2 s.

### Re-warm the cache after a cache-kind bump

`ANALYZE_CACHE_KIND` (currently `analyze_v7`) is versioned whenever the response shape
or detection semantics change. **Bumping it silently orphans every cached analyze** —
old rows stay in the table under the old key and are simply never read, so every demo
area goes cold at once. Re-warm before showing anyone:

```bash
U=https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io

warm() {
  echo "warming $1 ..."
  curl -s -X POST "$U/api/sentinel/analyze" \
    -H "Content-Type: application/json" -d "$2" \
    -o /dev/null -w "  HTTP %{http_code}  %{size_download} bytes  %{time_total}s\n" \
    --max-time 1200
}

warm "8x9km 2020-10 -> 2025-10" '{"polygon":[[90.4652,23.9017],[90.5434,23.9017],[90.5434,23.9822],[90.4652,23.9822]],"oldYear":2020,"oldMonth":10,"newYear":2025,"newMonth":10}'
warm "3.8x3.6km 2020-10 -> 2025-10" '{"polygon":[[90.5351,23.8902],[90.5727,23.8902],[90.5727,23.9226],[90.5351,23.9226]],"oldYear":2020,"oldMonth":10,"newYear":2025,"newMonth":10}'
warm "1.8x2.3km 2024-06 -> 2026-08" '{"polygon":[[90.4063,23.7581],[90.4244,23.7581],[90.4244,23.7786],[90.4063,23.7786]],"oldYear":2024,"oldMonth":6,"newYear":2026,"newMonth":8}'
```

Each takes minutes; the 8×9 km one is the slowest and the strongest demo, so start it
first. Classify is unaffected — its cache key is the literal string `"classify"`.

### Rollback

```bash
az containerapp revision list -n ubiquitous-eye -g ubiquitous-eye-rg -o table
az containerapp update -n ubiquitous-eye -g ubiquitous-eye-rg \
  --image ubiquitouseye29146.azurecr.io/ubiquitous-eye:<previous-sha>
```


## 📱 Building the Android APK

Only after the backend URL exists.

```bash
cd mobile
flutter pub get
flutter build apk --release --dart-define=BACKEND_URL=https://<fqdn>
# → build/app/outputs/flutter-apk/app-release.apk
```

**`--dart-define` is mandatory.** The origin fallback in `config.dart` is web-only; a
phone has no serving origin and falls back to `http://localhost:5000`, which on a
phone means the phone itself. Without it the APK installs, launches, draws the map
shell, and then fails every request with no obvious cause.

If Gradle dies with *"daemon disappeared unexpectedly"*, it is memory. Stop Docker
first, or add to `mobile/android/gradle.properties`:

```
org.gradle.jvmargs=-Xmx2048m
```

Client-side fixes reach **mobile web** only through a redeploy — the Flutter bundle is
baked into the container image.

---



## 🔐 Sehttps://github.com/Siam-Ahmed-Rakib/Ubiquitous-Eye/tree/mastercurity

`.env` is gitignored and no longer ships with a clone. **Outstanding items:**

- **The Sentinel Hub secret and the Supabase password were both pasted into a chat
  transcript and have not been rotated.** Do that.
- **`refined-fullband.ipynb` contains a hardcoded Kaggle API key** (`KAGGLE_USERNAME`
  / `KAGGLE_KEY` set as environment variables in a cell). The cell's own comment says
  the key is compromised and should be regenerated — that regeneration has not
  happened, and the notebook still carries the value. Revoke it at
  <https://www.kaggle.com/settings> → *API* → *Expire Token*, and replace the cell with
  a Kaggle Secrets lookup rather than a literal before sharing the notebook anywhere.
- If any older commit still carries a real `.env`, treat those credentials as
  compromised: revoke the OAuth client, create a new one, and strip the file from
  history — a deleting commit is not enough:
  ```bash
  git rm --cached .env
  git filter-repo --path .env --invert-paths     # or BFG Repo-Cleaner
  git push --force
  ```
- Secrets in production are **Container Apps secrets**, referenced as environment
  variables, never baked into an image layer. Keep it that way.
- **Never print secret values.** Confirm presence by length or hash.

Worth doing before this is used beyond a demo: rate-limit `analyze` and `classify`
(each call costs Sentinel Hub processing units), validate AOI size server-side, and
tighten CORS if the API is ever exposed on a different origin.

---


## 🤝 Working on this repo

Standing conventions, recorded so they don't get relitigated:

- **The user pushes.** Commit freely; don't `git push` unless told to in that message.
- **Ask before implementing anything ambiguous** — don't settle a design question with
  your own choice.
- **Don't delete rows from `analysis_cache`.** 32 rows in superseded formats hold
  ~64 MB. Leave them.
- **Never print secret values.**
- Parallelising the Sentinel Hub fetch loops has been explicitly declined — "that
  fetching wait is acceptable". Don't re-propose it unprompted.

Further reading in the repo: `docs/HANDOFF.md` (current state and next command),
`docs/REDEPLOY.md` (step-by-step runbook), `docs/project-memory/` (cache schema traps,
where latency lives, the Sentinel Hub diagnosis, hosting-options survey), `DEMO.md`
(cached demo areas), `mobile/README.md` (detailed UI and feature documentation).

---

## 🔗 Related

- **Sentinel Hub API docs:** <https://docs.sentinel-hub.com/api/latest/>
- **Sentinel-2 L2A product spec:** <https://sentinels.copernicus.eu/web/sentinel/user-guides/sentinel-2-msi>
- **Copernicus Data Space:** <https://dataspace.copernicus.eu/>
