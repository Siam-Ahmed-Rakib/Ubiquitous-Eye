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
COPY server/inference/ ./inference/
COPY --from=frontend-builder /app/Frontend/dist /app/frontend_dist

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "300", "api_server:app"]
