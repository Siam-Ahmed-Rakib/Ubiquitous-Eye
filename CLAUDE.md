# Ubiquitous Eye — working notes for Claude

Satellite land-cover classification and change detection. Flask/gunicorn backend
(`server/`), Flutter web+Android client (`mobile/`), legacy React client
(`Frontend/`, **not** deployed).

**If you are a fresh session picking this up: read `docs/HANDOFF.md` first.** It has
the current state, what is finished, what is not, and the exact next command.
`docs/project-memory/` holds accumulated findings worth not re-deriving.

## Standing instructions from the user

- **Never run `git push`.** Commit freely; the user pushes. This is explicit.
- **Ask before implementing anything ambiguous.** Do not resolve a design question
  with your own choice — raise it.
- **Never delete rows from `analysis_cache`.** Cleanup was offered three times and
  declined. 32 rows in superseded formats hold ~64 MB; leave them.
- **Never print secrets.** `.env` (untracked) holds `SH_CLIENT_ID`,
  `SH_CLIENT_SECRET`, `DATABASE_URL`. Verify presence by length/hash, never value.

## Architecture in one paragraph

`server/Dockerfile` is a two-stage build: stage one runs `flutter build web` over
`mobile/`, stage two is the Python runtime that copies that bundle to
`/app/frontend_dist`. `api_server.py` serves it from `/` and the API from `/api/*`,
so **one container is the whole product** — same origin, no CORS, one URL.
`mobile/lib/config.dart` derives its backend URL from `Uri.base.origin` on web, so
the same image works on localhost, a tunnel, or a hosted URL with no rebuild.

## Commands

```sh
docker compose --project-directory . up -d --build   # local stack -> :5001
docker compose --project-directory . down
bash deploy/azure-deploy.sh                          # deploy to Azure Container Apps
```

Run `docker exec -w /app ...` through **PowerShell**, not Bash — the Bash tool
mangles `/app` into a Windows path and the exec fails with "Cwd must be absolute".

## Traps that have already cost time

- **Sentinel Hub 403 "Invalid or expired account"** is an *account* problem, not a
  credential one. Do not read a 500 from classify/analyze as a code regression
  before grepping the log for it.
- **Supabase pauses free projects after 7 idle days.** A paused project stops
  resolving in DNS entirely, which looks exactly like deletion. It is not — restore
  it from the dashboard and wait a few minutes for the pooler to re-register.
- **Flutter's template grants `INTERNET` only in the debug and profile manifests.**
  Release APKs use `main/`. Already fixed; do not let a regenerated `android/` drop it.
- **`docker-compose.yml` must not mount `./Frontend/dist` over `/app/frontend_dist`.**
  It shadows the UI the image builds with an empty host directory.
