# Hosted Why: dashboard + GitHub App webhook. Build the dashboard first (cd web && npm ci && npm run build).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .
# Demo data: a pre-built Larkspur repo so the hosted demo always has something to show.
COPY demo ./demo
RUN git config --global user.email demo@larkspur.example && git config --global user.name "Why demo" \
    && FAST=1 WHY_BIN=why PY=python bash demo/run_demo.sh --no-serve
ENV WHY_LOCAL_REPO=/app/demo/out/larkspur \
    WHY_WORKDIR=/data \
    WHY_DASHBOARD_WRITE=0
# Remote MCP at /mcp/ is read-only unless WHY_MCP_TOKEN is set at runtime.
EXPOSE 8080
CMD ["uvicorn", "why.server.app:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8080"]
