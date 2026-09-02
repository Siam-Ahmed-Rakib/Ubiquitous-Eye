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
bash deploy/azure-redeploy.sh                        # ship HEAD to Azure (build+push+revision)
# deploy/azure-deploy.sh is FIRST-PROVISION ONLY -- re-running it mints a second
# random-named registry and then `az containerapp create`s over the existing app.
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
- **`mobile/.gitignore` must keep tracking `pubspec.lock` and `web/`.** `server/Dockerfile`
  COPYs the lockfile and no longer runs `flutter create . --platforms web`, so if either
  is re-ignored the image builds fine for whoever has them locally and fails for everyone
  else. `deploy/azure-redeploy.sh` preflights for both.
- **Bumping `ANALYZE_CACHE_KIND` silently orphans every cached analyze.** Rows are never
  deleted (standing instruction), so the table keeps growing and every demo area goes
  cold at once. Re-warm the areas in `DEMO.md` after any bump.
- **`curl ... | grep -q` under `set -o pipefail` reports success as failure.** `grep -q`
  exits on its first match and closes the pipe; `curl` then dies with exit 23 and
  pipefail returns that for the whole pipeline. `curl ... | head -c` is the same bug
  plus `set -e` killing the script. Both shipped in `azure-redeploy.sh` and made a
  healthy deploy look like two failures. Write curl output to a file, then grep the file.
- **An overlay inside `FlutterMap` loses every touch drag unless you lower its slop.**
  flutter_map puts Horizontal/VerticalDragGestureRecognizers on a `GestureArenaTeam`
  that accept at *hit* slop (18 logical px, ~8 on Android) along one axis, while
  `GestureDetector.onPan*` accepts at *pan* slop -- double that. The map wins every
  straight finger drag. A mouse collapses both to 1-2 px and the deeper widget accepts
  first, so **desktop web looks fine while every touch device is broken**. Fix is a
  scoped `MediaQuery(gestureSettings: DeviceGestureSettings(touchSlop: 2))` around the
  overlay; see `mobile/lib/widgets/selection_overlay.dart`.
