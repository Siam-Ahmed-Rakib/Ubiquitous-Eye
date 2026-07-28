FROM node:20-alpine AS frontend-builder
WORKDIR /app

COPY Frontend/package*.json ./Frontend/
RUN npm ci --prefix ./Frontend

COPY Frontend/ ./Frontend/
RUN npm run build --prefix ./Frontend

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIST=/app/frontend_dist

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/api_server.py ./
COPY server/bimonthly_composite.py ./
COPY server/cache.py ./
COPY server/inference/ ./inference/
COPY --from=frontend-builder /app/Frontend/dist /app/frontend_dist

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
