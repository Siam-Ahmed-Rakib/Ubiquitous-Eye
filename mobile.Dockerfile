FROM ghcr.io/cirruslabs/flutter:stable AS builder
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

FROM nginx:1.27-alpine AS runner
WORKDIR /usr/share/nginx/html

COPY mobile.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build/web ./

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
