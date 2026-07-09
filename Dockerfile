FROM node:20-alpine AS builder
WORKDIR /app

# Install dependencies first to maximize build cache reuse.
COPY Frontend/package*.json ./
RUN npm ci

# Build the static site.
COPY Frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runner
WORKDIR /usr/share/nginx/html

# Use a custom config so client-side routes are served by index.html.
COPY frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist ./

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
