# Build the browser client from the committed npm lockfile.
FROM node:24.15.0-alpine3.22 AS web-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

# Locked API dependency closure; application runs without root.
FROM python:3.14.3-slim-bookworm AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ALPHA_ANALYSIS_ENGINE=mvp_offline \
    ALPHA_DEMO_MODE=0 \
    ALPHA_MVP_LOCAL_ACCESS=0 \
    ALPHA_MVP_ALLOW_LIVE=0 \
    ALPHA_MVP_ALLOW_CLIENT_DATA=0 \
    ALPHA_DB_PATH=/var/lib/alpha-mvp/runtime.sqlite3
WORKDIR /app
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
    && mkdir -p /var/lib/alpha-mvp /var/lib/alpha-news /inputs \
    && chown app:app /var/lib/alpha-mvp /var/lib/alpha-news
COPY deploy/runtime-requirements.lock /app/deploy/runtime-requirements.lock
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /app/deploy/runtime-requirements.lock
COPY backend /app/backend
COPY deploy/entrypoint.sh /app/deploy/entrypoint.sh
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]

# Unprivileged Nginx serves Vite output and proxies same-origin API requests.
FROM nginxinc/nginx-unprivileged:1.28-alpine AS web
COPY --from=web-build --chown=101:101 /src/frontend/dist/ /usr/share/nginx/html/
COPY --chown=101:101 deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
