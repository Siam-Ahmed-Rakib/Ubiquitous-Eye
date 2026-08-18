# How to run

From the repo root:

```sh
docker compose up --build
```

That is the whole thing — it builds the Flutter web app (`mobile/`, service
name `frontend`), builds the Flask backend, and starts both.

- **App** — <http://localhost:8081>
- **API health check** — <http://localhost:5001/api/health>

Stop it with `Ctrl-C`, or `docker compose down` from another shell.

## Before the first run

Copy `.env.example` to `.env` and fill in your Sentinel Hub credentials.
Without them the app still loads, but any analysis returns an error — the
backend has no imagery to fetch. `DATABASE_URL` is optional; leave it blank to
run without the result cache.

## Why 8081 and 5001, not 8080 and 5000

`docker-compose.override.yml` moves both host ports, because other projects on
this machine already hold 8080 and 5000. Compose picks that file up
automatically. Delete it and you get the canonical ports from
`docker-compose.yml` — the app on 8080, the API on 5000.

The Flutter web build bakes `BACKEND_URL` into the JS bundle at *build* time
(see `mobile/lib/config.dart`), so — unlike a server-side proxy — it has to be
the host-reachable address, not the in-Docker-network one. That's why
`docker-compose.yml` sets it to `http://localhost:5000` and the override
bumps it to `http://localhost:5001` to match the remapped backend port.
Changing the backend's host port means rebuilding `frontend`
(`docker compose build frontend`), not just restarting it.

## The React frontend

The original React app (`Frontend/`) still builds via the plain `Dockerfile`,
but it's no longer part of the default `docker compose up`. It sits behind a
compose profile so it doesn't build unless asked for:

```sh
docker compose --profile react up frontend-react
```

It's served on host port 8082 (`capstone-frontend-react`) so it can run
alongside the Flutter one.

## Running the Flutter app outside Docker

```sh
cd mobile && flutter run -d chrome --dart-define=BACKEND_URL=http://localhost:5001
```

On an Android emulator use `http://10.0.2.2:5001` instead — the emulator cannot
reach the host as `localhost`.
