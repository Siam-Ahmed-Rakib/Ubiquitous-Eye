# Memory Index

- [Result cache architecture](result-cache-architecture.md) — why/how the Sentinel Hub response cache works; live against Supabase
- [Docker test workflow](docker-test-workflow.md) — rebuild+pytest steps; run `docker exec -w /app` via PowerShell, not Bash
- [.env secrets tracked](env-secrets-tracked.md) — .env was committed; now untracked; SH creds still in git history
- [Backend hosting constraints](backend-hosting-constraints.md) — HF/Koyeb/Render are dead ends; Azure for Students is the live option
- [Sentinel Hub credentials](sentinel-hub-account-expired.md) — was 403 since 2026-08-12; renewed 2026-08-26, live fetches work again
- [Where backend latency lives](where-backend-latency-lives.md) — measured: 98% network fetch, PNG encode is the only CPU cost, classify is 0.3s
