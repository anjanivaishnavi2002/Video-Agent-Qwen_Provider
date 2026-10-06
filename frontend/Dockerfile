# syntax=docker/dockerfile:1.7
# Frontend for Cloud Run: static React build served by unprivileged nginx on $PORT. Contains NO secrets and NO API keys.

# ---------- build ----------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# The backend URL is NOT baked in: it is injected at container start (API_URL) through /config.json.
RUN npm run build

# ---------- serve ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine
USER root
RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/16-api-origin.envsh /docker-entrypoint.d/16-api-origin.envsh
COPY deploy/18-runtime-config.sh /docker-entrypoint.d/18-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/16-api-origin.envsh /docker-entrypoint.d/18-runtime-config.sh
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
RUN chown -R 101:101 /usr/share/nginx/html /etc/nginx/conf.d
USER 101

# Cloud Run sets PORT; the official entrypoint renders the nginx template with it (and with API_ORIGIN).
ENV PORT=8080 API_URL=""
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- "http://127.0.0.1:${PORT}/healthz" >/dev/null || exit 1
