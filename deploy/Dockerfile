# Retinue container image (§27.1 Docker path).
# Build context is the repository root:
#   docker build -f deploy/Dockerfile -t retinue .

# --- stage 1: frontend ----------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# --- stage 2: wheel ---------------------------------------------------------
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/ backend/
COPY --from=frontend /app/frontend/dist backend/src/retinue/static
RUN uv build backend --wheel -o /dist

# --- stage 3: runtime -------------------------------------------------------
# python:slim rather than distroless: the T1/T2 image favors debuggability
# (shell, pip) — a distroless variant is a release-pipeline follow-up (§21).
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 retinue \
    && mkdir -p /data \
    && chown retinue:retinue /data
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

USER retinue
ENV RETINUE_HOME_DIR=/data \
    RETINUE_LOG_FORMAT=json
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/healthz')" || exit 1

ENTRYPOINT ["retinue", "serve", "--host", "0.0.0.0", "--port", "8000"]
