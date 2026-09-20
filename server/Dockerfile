# syntax=docker/dockerfile:1.7

# The web UI this image serves is the Flutter client in mobile/, not the React
# app in Frontend/. Both exist; the Flutter one is the product. To go back to
# React, restore the node:20-alpine stage from git history and repoint the
# `COPY --from=frontend-builder` line at the runtime stage.
FROM ghcr.io/cirruslabs/flutter:stable@sha256:46691e311715845de03a3ba4753a475476936805b29431b1f00f1816981033f8 AS frontend-builder
WORKDIR /app

# Resolve dependencies first so a source-only change reuses this layer.
COPY mobile/pubspec.yaml mobile/pubspec.lock ./
RUN flutter pub get

COPY mobile/ ./

# Empty by default, and deliberately so: mobile/lib/config.dart falls back to
# the origin the app was served from, which is this same container. Baking a
# hostname here would pin the image to one deployment URL. Set it only to point
# the UI at a backend somewhere other than the one serving it.
ARG BACKEND_URL=
# --pwa-strategy=none: do not register a service worker.
#
# Flutter's default PWA service worker caches main.dart.js and serves it from
# that cache on later loads, ignoring the `Cache-Control: no-cache` the backend
# sends. The effect is a UI that is half-updated after a redeploy: anything
# fetched from the API is current, while anything compiled into the bundle —
# colours, labels, layout — is whatever it was at the last cache fill. That is
# far more confusing than a slightly slower first paint, and this app is served
# from the same container as its API, so it has no offline story to protect.
RUN flutter build web --release --pwa-strategy=none \
    --dart-define=BACKEND_URL=${BACKEND_URL}

FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIST=/app/frontend_dist

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt ./
# Keep downloaded wheels in BuildKit's cache. If a slow or unreliable download
# aborts this step, the next build resumes from the packages already fetched.
# The cache is not copied into the runtime image.
RUN --mount=type=cache,id=ubiquitous-eye-pip,target=/root/.cache/pip,sharing=locked \
    pip install --retries 10 --timeout 120 -r requirements.txt

COPY server/api_server.py ./
COPY server/bimonthly_composite.py ./
COPY server/cache.py ./
COPY server/inference/ ./inference/
COPY --from=frontend-builder /app/build/web /app/frontend_dist

# Managed hosts tell the container which port to listen on via $PORT
# (Hugging Face Spaces, Cloud Run, Railway). Default keeps local compose at 5000.
ENV PORT=5000

EXPOSE 5000

# sh -c so ${PORT}/${WEB_CONCURRENCY} expand at runtime; exec so gunicorn
# replaces the shell as PID 1 and still receives SIGTERM for graceful shutdown.
#
# --preload imports the app once in the master before forking, so the ~225 MB of
# numpy/sklearn/xgboost/catboost is shared copy-on-write instead of paid per
# worker. This is only safe because cache.py builds its SQLAlchemy engine lazily,
# after the fork -- preloading an open connection pool corrupts shared sockets.
#
# --timeout is how long gunicorn lets a worker hold a request before killing it.
# A classify/analyze run is a catalogue search, a download of every clear
# acquisition in the window, a temporal median composite and then inference over
# every cell; a large area, or one whose adaptive compositor widens its window
# hunting for clear looks, takes far longer than the old 300 s. A killed worker
# surfaces to the browser as a connection error, so it reads like the server is
# down when it is really still working.
#
# Keep this >= kBackendRequestTimeout in mobile/lib/config.dart. The client must
# never outwait the server.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --threads 4 --timeout ${GUNICORN_TIMEOUT:-1800} --graceful-timeout 30 --preload api_server:app"]
