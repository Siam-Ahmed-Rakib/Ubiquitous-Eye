# The web UI this image serves is the Flutter client in mobile/, not the React
# app in Frontend/. Both exist; the Flutter one is the product. To go back to
# React, restore the node:20-alpine stage from git history and repoint the
# `COPY --from=frontend-builder` line at the runtime stage.
FROM ghcr.io/cirruslabs/flutter:stable AS frontend-builder
WORKDIR /app

# Resolve dependencies first so a source-only change reuses this layer.
COPY mobile/pubspec.yaml ./
RUN flutter pub get

COPY mobile/ ./

# Empty by default, and deliberately so: mobile/lib/config.dart falls back to
# the origin the app was served from, which is this same container. Baking a
# hostname here would pin the image to one deployment URL. Set it only to point
# the UI at a backend somewhere other than the one serving it.
ARG BACKEND_URL=
RUN flutter create . --platforms web
RUN flutter build web --release --dart-define=BACKEND_URL=${BACKEND_URL}

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIST=/app/frontend_dist

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt ./
# --retries/--timeout: this layer pulls ~2 GB of wheels, and the build has twice
# died partway through with "Name or service not known" *after* several large
# downloads had already succeeded -- a resolver giving out under load, not DNS
# being absent. pip's default is 5 retries with a 15 s timeout, which is not
# enough headroom for a 340 MB wheel on a slow link.
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

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
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --threads 4 --timeout 300 --preload api_server:app"]
