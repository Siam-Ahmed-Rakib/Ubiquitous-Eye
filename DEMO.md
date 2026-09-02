# Demo cheat sheet — updated 2026-09-02

## Link

    https://ubiquitous-eye.redocean-c4117d93.malaysiawest.azurecontainerapps.io

Permanent. Nothing to keep open, no laptop involved — this is Azure Container Apps
with min-replicas 1, so it is always warm. Works on any phone or laptop, and the
same URL serves both the web app and the API.

Docker on the laptop is **not** required any more. The old cloudflared tunnel is
retired; if you find a `trycloudflare.com` link anywhere, it is dead.

## Draw these areas — they return in ~2 seconds

### Land classification (pick a month, single date) — still cached

| month | longitude | latitude | size |
|---|---|---|---|
| 2026-02 | 90.5806 – 90.6298 | 23.8365 – 23.8815 | 5.0 x 5.0 km |
| 2026-01 | 90.3158 – 90.3651 | 23.7510 – 23.7960 | 5.0 x 5.0 km |
| 2026-01 | 90.3562 – 90.4054 | 23.8139 – 23.8589 | 5.0 x 5.0 km |
| 2026-01 | 90.3295 – 90.3787 | 23.8757 – 23.9207 | 5.0 x 5.0 km |
| 2026-06 | 90.3158 – 90.3651 | 23.7510 – 23.7960 | 5.0 x 5.0 km |
| 2026-05 | 90.3800 – 90.4000 | 23.7800 – 23.8000 | 2.0 x 2.2 km |

### Change detection (pick two dates) — ⚠ cache reset on 2026-09-02

Commit `8dba210` moved the analyze cache from `analyze_v5` to `analyze_v7`, so these
are **cold until you run each one once**. Run all three after deploying and before
demoing; each first run takes minutes.

| from → to | longitude | latitude | size |
|---|---|---|---|
| 2020-10 → 2025-10 | 90.4652 – 90.5434 | 23.9017 – 23.9822 | 8.0 x 8.9 km |
| 2020-10 → 2025-10 | 90.5351 – 90.5727 | 23.8902 – 23.9226 | 3.8 x 3.6 km |
| 2024-06 → 2026-08 | 90.4063 – 90.4244 | 23.7581 – 23.7786 | 1.8 x 2.3 km |

The 8 x 9 km 2020→2025 one is the strongest change-detection story — largest area,
five-year span. It is also the slowest to warm, so start it first.

## If something goes wrong mid-demo

- **Request hangs for minutes** — the area drawn was not cached; it is running the
  real pipeline, not broken. Let it finish, or draw one from the tables above.
- **500 on classify/analyze** — a Sentinel Hub `403 Invalid or expired account` means
  the SH subscription lapsed again, not a code fault. Check with
  `az containerapp logs show -n ubiquitous-eye -g ubiquitous-eye-rg --tail 40`.
- **Everything is 502/503** — a revision failed to start. Check
  `az containerapp revision list -n ubiquitous-eye -g ubiquitous-eye-rg -o table`
  and roll back to the previous image tag.

## Honest framing for questions

- **Hosting is real**, not a tunnel to a laptop: Azure Container Apps, 0.5 vCPU /
  1 GiB, always-on, one container serving both the Flutter web client and the API
  from the same origin.
- Cached areas are instant. An uncached one is dominated by downloading a month of
  Sentinel-2 and Landsat scenes serially — ~98% of the wall time. The classification
  itself, a four-model ensemble over ~30,000 cells, takes **0.3 s**.
- Since `8dba210` a change is only reported where **both** dates had at least two
  clear satellite observations of that pixel. Anything cloudier is reported as
  uncertain rather than being forced into a land-cover transition. That is why the
  UI shows a reliable-pixel count next to the totals — it is a deliberate statement
  about what the data can and cannot support.
