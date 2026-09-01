# syntax=docker/dockerfile:1.7

FROM ghcr.io/cirruslabs/flutter:stable@sha256:46691e311715845de03a3ba4753a475476936805b29431b1f00f1816981033f8 AS builder
WORKDIR /app

# Install dependencies first to maximize build cache reuse.
COPY mobile/pubspec.yaml mobile/pubspec.lock ./
RUN flutter pub get

# Build the static site. BACKEND_URL is baked into the JS bundle at build
# time -- see mobile/lib/config.dart -- so it must point at wherever the
# backend is reachable from the *browser*, not from inside this container.
COPY mobile/ ./
ARG BACKEND_URL=http://localhost:5000
RUN flutter build web --release --dart-define=BACKEND_URL=${BACKEND_URL}

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS runner
WORKDIR /usr/share/nginx/html

COPY mobile.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build/web ./

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
