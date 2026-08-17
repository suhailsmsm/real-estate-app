# Build context is the REPO ROOT (see docker-compose.yml), same as elt/ and
# api/, so the mcp/-prefixed COPYs are relative to it.
#
# Unlike the other two images this one does NOT install packages/dxb-core: the
# MCP server owns no schema and talks to the REST API over HTTP
# (docs/MCP_DESIGN.md §2). Not needing it here is the point.
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

COPY mcp/pyproject.toml ./
COPY mcp/src ./src
COPY mcp/tests ./tests
RUN pip install -e ".[dev]"

# Publicly reachable (via nginx), so it does not run as root.
RUN useradd --create-home --uid 10002 dxbmcp && chown -R dxbmcp:dxbmcp /app
USER dxbmcp

EXPOSE 8100

CMD ["python", "-m", "dxb_mcp"]
