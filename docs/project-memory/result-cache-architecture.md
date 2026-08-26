---
name: result-cache-architecture
description: How and why the Sentinel Hub result cache works; current rollout state
metadata: 
  node_type: memory
  type: project
  originSessionId: 241d6d28-6325-4f26-9185-9cf014e2bdd1
  modified: 2026-07-28T22:14:03.608Z
---

The backend caches whole `/api/sentinel/classify` and `/api/sentinel/analyze` responses to skip the slow path.

**Why:** the cost is the *per-area* Sentinel Hub fetch (a whole-month composite downloads ~10-15 scenes), NOT the model — `ensemble_predict` on a few thousand rows is cheap. So per-pixel "only fetch the missing pixels" doesn't help (fetch is per-bbox); caching the whole result does.

**Design (user-approved 2026-07-22):** whole-response cache keyed by `analysis | date(s) | quantized bbox (round lat/lon to 3 dp ≈ 100 m)`, in `server/cache.py`. Backend `server/api_server.py` checks `cache_get` before the pipeline, `cache_put` after. Storage = any SQL DB via `DATABASE_URL` (Supabase Postgres in prod, SQLite in tests). **Graceful no-op** if `DATABASE_URL` unset/unreachable — cache can never break a request. Current-month results expire after `CURRENT_PERIOD_TTL_DAYS=5`; past months cached forever. Clients need no changes (identical body + extra `cached` flag).

**State (2026-07-22):** LIVE and verified end-to-end against Supabase Postgres (Singapore pooler `aws-0-ap-southeast-1.pooler.supabase.com:5432`, session mode). `DATABASE_URL` is in `.env` (untracked). Both phases done: Phase 1 (exact/same-area hits) AND Phase 2 (`cache_get_containing` + `_crop_classify`/`_crop_analyze` in `api_server.py` — a smaller area fully inside a computed one is cropped from it, zero fetches). classify stores the label grid in the `extras` column (int codes via `_encode_grid`) so the sub-area overlay re-renders and stats recompute exactly; the backdrop PNG is cropped. analyze filters its per-pixel `changes` and scales `totalPixels` by area. Side perf win: `inference/inference.py` loads the 4 joblib models once, not per call. All 21 backend tests green.

**Testing gotcha:** the container has a real `DATABASE_URL`, so `server/tests/conftest.py` has an autouse fixture disabling the cache for every test by default (test_cache opts back in with SQLite). Without it, endpoint tests pollute Supabase and contaminate each other.

**Schema (2026-07-29):** four tables — `analysis_cache` (saved responses), `land_cover_class` (4-row vocabulary: id/name/color), `classification_result` (per-class summary per search, FK CASCADE), and `classification_point`. The last is **not a search log**: one row per ~30 m location UNIQUE on `(longitude, latitude)`, upserted with `ON CONFLICT ... WHERE existing.observed_on < EXCLUDED.observed_on` so only a *newer observed period* overwrites. Its FK is **ON DELETE SET NULL**, not CASCADE — evicting a cached payload must not erase the map. Coordinates are snapped via `snap_point()` to a fixed 0.00027° grid; without that, cell centres derived from each search's own bbox never compare equal and the overwrite rule never fires (production: 9 distinct bboxes across 11 searches, and snapping merged 253,127 cells into 168,777 rows).

**Perf trap:** never write the point map with SQLAlchemy `executemany` — psycopg2 turns it into one round trip per row (67 s for 20,300 cells). Chunked multi-row INSERTs bring it to 5.8 s. Chunk size is dialect-aware: 1000 on PG, 300 on SQLite (999-parameter ceiling).

**Schema note (2026-07-29):** the supervisor requires **`id` as the primary key on every table**. Columns are now typed per dialect — SQLite must NOT be given a DATE column, as its NUMERIC affinity silently truncates an ISO date at the first dash (`'2024-03-01'` → `2024`). `date_key` deliberately stays a VARCHAR: it holds `"2024-03"` for classify but `"2024-01_2025-01"` for change detection, a month *pair* no DATE can represent; `period_start`/`period_end` hold the parsed form.

**Dead rows:** superseded analysis formats (`analyze`, `analyze_v2`, `analyze_v3`) are never read and never evicted — 12 of 43 rows as of 2026-07-29, each holding base64 PNGs. Cleanup was offered twice and never approved; ask before deleting.

Related: [[docker-test-workflow]], [[env-secrets-tracked]], [[backend-hosting-constraints]]
