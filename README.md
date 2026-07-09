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
