# Build context is the REPO ROOT (see docker-compose.yml), same as elt/, api/
# and mcp/, so the copilot/-prefixed COPYs are relative to it.
#
# Like mcp/, this image does NOT install packages/dxb-core: the copilot owns no
# schema and holds no database credentials — it reaches data only through the
# MCP server (docs/UI_PLAN.md §5). Not needing it here is the point.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# curl for the compose healthcheck; python:3.12-slim ships neither curl nor
# wget, and ["CMD","curl",...] needs no shell so nothing has to be escaped.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY copilot/pyproject.toml ./
COPY copilot/src ./src
COPY copilot/tests ./tests
RUN pip install -e ".[dev]"

# Publicly reachable (via nginx), so it does not run as root.
RUN useradd --create-home --uid 10003 dxbcopilot && chown -R dxbcopilot:dxbcopilot /app
USER dxbcopilot

EXPOSE 8200

CMD ["python", "-m", "dxb_copilot"]
