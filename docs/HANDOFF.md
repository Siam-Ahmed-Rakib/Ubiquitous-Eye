# HANDOFF - read this first

Written 2026-08-26 on the user's **Windows** machine, for a session on the user's
**Ubuntu** machine. Updated 2026-09-02: the first deploy is done, so this is now a
*re*deploy guide.

---

## TL;DR - what to do

The backend is already live at

```
https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io
```

but it serves the code as of commit `5799969`. Commit `8dba210` ("cloud composite
fix") is **not deployed**. Push it out:

```bash
cd ~/Ubiquitous-Eye          # or wherever you cloned it
git pull
az account show              # must succeed; if not see "Azure login" below
bash deploy/azure-redeploy.sh
```

**A redeploy does not need `.env`.** The three secrets already live on the Container
App and `az containerapp update --image` leaves them alone. `.env` is only needed for
a from-scratch provision, or to run the stack locally. The "Secrets" section below is
kept for those cases.

Takes ~15-25 minutes, mostly the image build. It preflights the build context,
builds locally, pushes to the existing registry, rolls a revision, and then checks
that the *new* bundle is actually being served before declaring success.

**Use `azure-redeploy.sh`, never `azure-deploy.sh`.** The latter is create-only: it
picks a random registry name when `ACR_NAME` is unset and calls
`az containerapp create`, so re-running it builds a second, empty registry and then
points the app at an image that is not in it. It is kept only as the record of how
the app was first provisioned.

Then work through **Testing after deploy** near the bottom.

---

## What this project is

Satellite land-cover classification and change detection over Bangladesh.

- `server/` - Flask + gunicorn API. Pulls Sentinel-2 and Landsat scenes from Sentinel
  Hub, builds a cloud-masked monthly median composite, runs a 4-model ensemble
  (XGBoost / CatBoost / LightGBM / CART) per pixel, returns labelled cells plus PNG
  overlays.
- `mobile/` - Flutter client, web and Android. **This is the product.**
- `Frontend/` - older React client. Still in the repo, **not deployed**, ignore it.
- Supabase Postgres - caches whole API responses, so a repeated area is instant.

**One container serves everything.** `server/Dockerfile` has two stages: stage one runs
`flutter build web` over `mobile/`, stage two is the Python runtime and copies that
bundle to `/app/frontend_dist`. `api_server.py` serves the UI from `/` and the API from
`/api/*`. Same origin means no CORS, one URL, one thing to deploy.

---

## Where we stand

### Done and verified

| item | state |
|---|---|
| Image rebuilt from current source | done - it had been running 4-week-old code (`analyze_v4`, no class maps) |
| Flutter client baked into the backend image, React dropped | done - verified serving `/`, `main.dart.js`, `canvaskit.wasm` as `application/wasm`, `manifest.json`, SPA fallback, `/api/*` 404 guard |
| Web client is origin-relative | done - `mobile/lib/config.dart` reads `Uri.base.origin` on web, so one image works at any URL with no rebuild and no CORS |
| Class-map PNG latency fix | done - 12x faster, 14% smaller, byte-identical pixels |
| Renamed terrascope to Ubiquitous Eye | done - 15 files plus regenerated `android/`; `flutter analyze` clean |
| Release-APK INTERNET permission | done - Flutter grants it only to debug/profile builds |
| sklearn 1.6.1 vs 1.9.0 pickle warning | investigated, benign, do not change the pin |
| Database | live - 47 cache rows, 168,777 classification points, on the rotated password |
| Sentinel Hub credentials | renewed 2026-08-26, live fetches work |

### Not done

- **Redeploying `8dba210`** - this is your job. `bash deploy/azure-redeploy.sh`.
- **APK rebuild against the new backend.** An APK was built on Windows on 2026-08-26
  and works, but it predates `8dba210` and its `stats` parsing. The client tolerates
  the new fields being absent, not the reverse, so it will keep working - it just
  will not show the reliability figures.

---

## What commit 8dba210 changes, and what it costs

`8dba210` ("cloud composite fix", hamim-87) reworks how composites are built:
whole-month windows that adapt up to `MAX_ADAPTIVE_WINDOW_DAYS = 60` when cloud
forces it, and a reliability gate requiring `MIN_CLEAR_OBSERVATIONS = 2` clear looks
per pixel on both dates before a change is reported. Pixels that fail the gate are
counted as uncertain rather than being forced into a land-cover transition. The
response gains `eligiblePixels`, `uncertainPixels`, `minimumClearObservations` and
exact `oldWindowStart`/`oldWindowEnd`/`newWindowStart`/`newWindowEnd`.

Two consequences that will surprise you if you meet them during a demo:

- **Every cached analyze goes cold.** `ANALYZE_CACHE_KIND` moved `analyze_v5` ->
  `analyze_v7`, so none of the analyze areas listed below are instant any more.
  **Classify is unaffected** - its cache key is the literal string `"classify"` and
  did not change, so classify areas stay warm.
- **A cold analyze is slower than it used to be**, on top of that. The old code
  composited half a month; this composites a whole one and may widen to 60 days.
  Since ~98% of a cold request is serial Sentinel Hub round-trips, expect roughly 2x
  or worse. That is an estimate from the window size, not a measurement.

If analyze is being demoed, re-run the demo areas once after deploying to re-warm
the cache rather than discovering this in front of an audience.

It also changed the build in two ways worth knowing: `xgboost` -> `xgboost-cpu`
(same Python module, drops the large NVIDIA NCCL runtime) and a BuildKit pip cache
mount, so `# syntax=docker/dockerfile:1.7` means **BuildKit is now mandatory**.

---

## Azure login

Azure works on **Ubuntu** but not on **Windows**. On Windows `az login` fails with:

```
AADSTS530035 - Access has been blocked by security defaults
Device state: Unregistered
```

That is a BUET tenant Conditional Access policy requiring a registered device. It
blocks the Azure **Portal** as well, so it is not a CLI problem and not fixable without
a tenant admin. That is the whole reason this work moved to Ubuntu.

- Account: `2105158@ugrad.cse.buet.ac.bd`
- Tenant: `10d93f4f-3089-4c95-8cea-c56f9dea2aa7` ("Default Directory")
- Subscription: Azure for Students, $100/yr, no card attached - so it cannot overspend;
  Azure suspends the subscription when the credit runs out.

If `az account show` fails on Ubuntu, try plain `az login` first. If it lands in a
tenant with no subscriptions, retry with
`az login --tenant 10d93f4f-3089-4c95-8cea-c56f9dea2aa7`.

---

## Secrets

`.env` at the repo root is gitignored and does **not** come with a clone. It needs
exactly three lines:

```
SH_CLIENT_ID=...
SH_CLIENT_SECRET=...
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
```

The user has these on the Windows machine at `E:\Ubiquitous-Eye\.env`.

**Heredoc gotcha that already bit once:** the closing `EOF` must be alone on its own
line at column zero. Glued to the end of the `DATABASE_URL` line, the value silently
gets `EOF` appended and the shell hangs. Verify with `wc -l .env` (expect 3) and check
that `DATABASE_URL` ends in `/postgres`.

**Never print these values.** Confirm presence by length or hash only.

---

## What the scripts do

### `deploy/azure-redeploy.sh` - use this one

1. **Preflights the build context** for `mobile/pubspec.lock` and `mobile/web/index.html`
   and refuses to start without them. See the trap below.
2. Builds the image **locally** with BuildKit and tags it with the short commit SHA
   (`-dirty` appended if the tree is not clean).
3. `az acr login`, tags for `ubiquitouseye29146.azurecr.io`, pushes.
4. `az containerapp update --image ...` to roll a new revision.
5. Polls `/api/health`, then greps the served `main.dart.js` for a string that only
   exists in the new client, so "deployed" means the new bundle is really being
   served rather than just "a container is up".
6. Prints the live URL, the exact rollback command, and the APK build command.

**Why tag by SHA and not `:latest`** - Container Apps keys a new revision off the
image *reference*. Re-pushing `:latest` leaves the reference unchanged, so the app
can keep serving the old layers and the deploy silently does nothing.

### `deploy/azure-deploy.sh` - first provision only, do not re-run

Kept as the record of how the app was created: resource group `ubiquitous-eye-rg` in
**southeastasia**, ACR Basic with admin enabled, a Container Apps environment with
`--logs-destination none` to avoid a Log Analytics bill, and the app itself at
**0.5 vCPU / 1 GiB, min-replicas 1, max 3**, external ingress, target port 5000,
with the three secrets injected as Container Apps secrets rather than baked into the
image.

Its header still claims it uses `az acr build` and therefore needs no Docker. **That
is not true on this subscription** - `az acr build` is rejected with
`TasksOperationsNotAllowed`, which is why the image is built locally and pushed.
Re-running it would also mint a *new* random registry name and then
`az containerapp create` over an app that already exists.

**Why 0.5 vCPU and not more** - measured, please do not re-litigate: a cold classify is
98% serial Sentinel Hub round-trips. The 4-model ensemble over 31,659 cells takes
**0.30 s**. Container CPU peaked at 51% (never saturated a single core) and RAM at
266 MB. Nothing in the pipeline is parallel, so core count buys nothing. The 1 GiB is
headroom for large areas, not throughput.

**Cost:** roughly $15.55/mo for the app plus $5.07/mo for ACR in Southeast Asia, so
about 5 months of the $100 grant. ghcr.io plus GitHub Actions would be free and buy
back the ACR cost, but needs a workflow file - and the user pushes, not Claude.

---

## Testing after deploy

Substitute the URL the script printed.

```bash
U=https://<fqdn>

# 1. service up
curl -s $U/api/health

# 2. Flutter UI served from the same origin
curl -s $U/ | grep -o "<title>.*</title>"
curl -sI $U/canvaskit/canvaskit.wasm | grep -i content-type    # expect application/wasm

# 3. a real classification - this area IS cached, expect ~2 s
curl -s -X POST $U/api/sentinel/classify \
  -H "Content-Type: application/json" \
  -d @- <<'JSON' -o /tmp/c.json -w "%{http_code} %{size_download}B %{time_total}s\n"
{"polygon":[[90.5805528458285,23.8364742130499],[90.6298069680125,23.8364742130499],[90.6298069680125,23.881519258095],[90.5805528458285,23.881519258095]],"year":2026,"month":2}
JSON

python3 -c "import json;d=json.load(open('/tmp/c.json'));print(d['status'],d['cached'],d['message'])"
```

Expect `success True Classified 27880 of 27880 cells`.

**Then open the URL in a browser and actually use the app** - draw a box, run a
classification, watch the console. curl passing is not the same as the UI working.

### Areas already cached (respond in ~2 s)

Anything else runs the live pipeline: **~160 s for classify, ~170 s for analyze**. That
is not a bug - it is a month of satellite scenes being fetched one at a time.

Classify:

- `2026-02` lon 90.5806-90.6298, lat 23.8365-23.8815
- `2026-01` lon 90.3158-90.3651, lat 23.7510-23.7960
- `2026-01` lon 90.3562-90.4054, lat 23.8139-23.8589

Analyze - **all cold after `8dba210`**, because the cache kind moved `analyze_v5` ->
`analyze_v7`. These are the areas worth re-warming first; the old rows are still in
the table under the v5 key and are simply never read:

- `2020-10 -> 2025-10` lon 90.4652-90.5434, lat 23.9017-23.9822  (8 x 9 km, best demo)
- `2024-06 -> 2026-08` lon 90.4063-90.4244, lat 23.7581-23.7786

Full list in `DEMO.md` at the repo root.

---

## Then: the APK

Only after the URL exists.

```bash
cd mobile
flutter pub get
flutter build apk --release --dart-define=BACKEND_URL=https://<fqdn>
# -> build/app/outputs/flutter-apk/app-release.apk
```

**`--dart-define` is mandatory.** The origin fallback in `config.dart` is web-only; a
phone app has no serving origin and falls back to `http://localhost:5000`, which on a
phone means the phone itself. Without it the APK installs, launches, draws the map
shell, and then fails every request with no obvious cause.

If Gradle dies with "daemon disappeared unexpectedly", it is memory. Stop Docker first,
or add to `mobile/android/gradle.properties`:

```
org.gradle.jvmargs=-Xmx2048m
```

---

## Standing instructions from the user

- **Do not `git push`** unless told to in that specific message. The user pushes.
- **Ask before implementing anything ambiguous.** Do not settle a design question with
  your own choice.
- **Do not delete rows from `analysis_cache`.** Offered three times, declined each
  time. 32 rows in superseded formats hold ~64 MB. Leave them.
- **Never print secret values.**
- The user has explicitly declined parallelising the Sentinel Hub fetch loops - "that
  fetching wait is acceptable". Do not re-propose it unprompted.

---

## Traps that have already cost hours

- **Sentinel Hub `403 Invalid or expired account`** is an account problem, not a
  credential one. `sentinelhub` acquires its OAuth token *before* the catalog call, so
  a 403 at `catalog.search` with no token error means the subscription lapsed. Do not
  read a 500 from classify/analyze as a code regression before checking the log.
- **Supabase pauses free projects after 7 idle days**, and a paused project stops
  resolving in DNS entirely - which looks exactly like deletion. It is not. Restore it
  from the dashboard, then wait a few minutes for the connection pooler to re-register
  the tenant; until it does you get `tenant/user ... not found`.
- **Flutter grants `INTERNET` only in the `debug/` and `profile/` manifests.** Release
  builds use `main/`. Already fixed - do not let a regenerated `android/` drop it.
- **`docker-compose.yml` must not mount `./Frontend/dist` over `/app/frontend_dist`.**
  It shadowed the UI the image builds with an empty host directory, so the backend
  served a blank page locally while the image itself was fine. Already fixed.
- **Cloudflare quick tunnels enforce a ~100 s origin timeout (error 524).** An uncached
  analyze takes ~170 s and therefore always failed through the tunnel that was used as
  a stopgap. That is a tunnel limit, not an app bug, and hosting removes it.
- **On Windows, run `docker exec -w /app ...` through PowerShell, not Bash** - the Bash
  tool mangles `/app` into a Windows path. Irrelevant on Ubuntu.
- **`mobile/.gitignore` used to hide files the image build needs.** `8dba210` added
  `COPY mobile/pubspec.lock` and deleted `RUN flutter create . --platforms web`, while
  `pubspec.lock` and `web/` were both still ignored - so the build worked on the author's
  machine and could not work from a clone. Fixed on 2026-09-02 by tracking both. If a
  regenerated `mobile/` ever re-adds those two lines to `.gitignore`, the Flutter stage
  starts failing several minutes in with an error that does not name the cause;
  `azure-redeploy.sh` now preflights for exactly this. `android/` is still ignored, so
  the release-manifest `INTERNET` permission and the Gradle heap fix continue to live
  only on the Windows box.

---

## The PNG fix, in case it comes up

Commit 2583694 ("coloring map converted but latency increased") added per-date
land-cover class maps and slowed `/analyze` down. Cause: `_encode_png` used Pillow's
`optimize=True` everywhere, which forces maximum zlib effort and trials every row
filter. On flat banded class-map content that is pathological. Measured at 1500x1500:

| setting | time | b64 size |
|---|---|---|
| `optimize=True` | 5.72 s | 5748 KB |
| default (level 6) | 0.89 s | 5847 KB |
| `compress_level=3` | **0.47 s** | **4956 KB** |

12x faster **and** smaller, decoding to byte-identical pixels. `_class_map_png` runs
twice per analyze, so this is ~10.5 s off every change-detection request. Photographic
true-colour scenes deliberately keep `optimize=True`, where level 3 would be 2x faster
but 15% **larger**.

---

## More background

`docs/project-memory/` holds the accumulated findings - cache architecture and its
schema traps, where backend latency actually lives, the Sentinel Hub diagnosis, and the
hosting-options survey. Worth skimming if something surprises you.
