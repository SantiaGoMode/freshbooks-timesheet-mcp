# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
# (FRESHBOOKS_TOKEN_PATH/BACKEND are config, not secrets — the key & creds come
#  from .env/secrets at runtime, never baked into the image.)
# Multi-stage: `test` runs the suite; `runtime` is the lean MCP server image.

FROM python:3.13-slim AS base
WORKDIR /app
COPY pyproject.toml README.md ./
COPY freshbooks_mcp ./freshbooks_mcp
COPY scripts ./scripts

# --- test stage: includes tests + dev deps ---
FROM base AS test
COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"
CMD ["pytest", "-q"]

# --- runtime stage: just the package + console scripts ---
FROM base AS runtime
RUN pip install --no-cache-dir .
RUN mkdir -p /data
# Containers can't reach the OS keychain — use the encrypted-file backend.
ENV FRESHBOOKS_TOKEN_BACKEND=file \
    FRESHBOOKS_TOKEN_PATH=/data/tokens.enc
# stdio MCP server by default; override the command for auth/smoke.
CMD ["freshbooks-mcp"]
