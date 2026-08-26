# Demo cheat sheet — 2026-08-26

## Link

    https://advanced-ricky-granted-foam.trycloudflare.com

Works on any phone or laptop. **Keep the cloudflared terminal open** — closing it
kills the link. If it dies, rerun and the URL will be different:

    & "C:\Users\USER\cloudflared.exe" tunnel --url http://localhost:5001

Docker must also be running: `docker compose --project-directory . up -d`

## Draw these areas — they return in ~2 seconds

Cached results. Anything else runs the live pipeline: **~160 s**, because a whole
month of Sentinel-2 + Landsat scenes is fetched one at a time.

### Land classification (pick a month, single date)

| month | longitude | latitude | size |
|---|---|---|---|
| 2026-02 | 90.5806 – 90.6298 | 23.8365 – 23.8815 | 5.0 x 5.0 km |
| 2026-01 | 90.3158 – 90.3651 | 23.7510 – 23.7960 | 5.0 x 5.0 km |
| 2026-01 | 90.3562 – 90.4054 | 23.8139 – 23.8589 | 5.0 x 5.0 km |
| 2026-01 | 90.3295 – 90.3787 | 23.8757 – 23.9207 | 5.0 x 5.0 km |
| 2026-06 | 90.3158 – 90.3651 | 23.7510 – 23.7960 | 5.0 x 5.0 km |
| 2026-05 | 90.3800 – 90.4000 | 23.7800 – 23.8000 | 2.0 x 2.2 km |

### Change detection (pick two dates)

| from → to | longitude | latitude | size |
|---|---|---|---|
| 2020-10 → 2025-10 | 90.4652 – 90.5434 | 23.9017 – 23.9822 | 8.0 x 8.9 km |
| 2020-10 → 2025-10 | 90.5351 – 90.5727 | 23.8902 – 23.9226 | 3.8 x 3.6 km |
| 2024-06 → 2026-08 | 90.4063 – 90.4244 | 23.7581 – 23.7786 | 1.8 x 2.3 km |

The 8 x 9 km 2020→2025 one is the strongest change-detection story — largest area,
five-year span.

## If something goes wrong mid-demo

- **Blank page / spinner forever** — tunnel dropped. Check the cloudflared terminal.
- **Request hangs ~2-3 min** — the area drawn was not cached; it is running the real
  pipeline, not broken. Let it finish or draw one from the tables above.
- **500 on classify/analyze** — check `docker logs capstone-backend --tail 40`. A
  Sentinel Hub `403 Invalid or expired account` means the SH subscription lapsed
  again, not a code fault.

## Honest framing for questions

- The link is a tunnel to this laptop, not cloud hosting. Permanent hosting on Azure
  Container Apps is scripted and ready (`deploy/azure-deploy.sh`) but blocked by a
  BUET tenant device policy on the Windows machine; it deploys from the Ubuntu box.
- Cached areas are instant; uncached takes ~160 s, 98% of which is downloading a
  month of satellite scenes serially. The classification itself is 0.3 s.
