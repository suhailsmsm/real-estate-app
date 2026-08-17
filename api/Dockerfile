# Build context is the REPO ROOT (see docker-compose.yml), not api/, because
# the image also needs packages/dxb-core. Hence the api/-prefixed COPYs.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# curl is for the compose healthcheck: python:3.12-slim ships neither curl nor
# wget, and `["CMD","curl",...]` needs no shell, so nothing has to be escaped
# inside YAML. It is also the obvious tool for debugging from inside the
# container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Shared schema package first — changes far less often than API code.
COPY packages/dxb-core /opt/dxb-core
RUN pip install -e /opt/dxb-core

COPY api/pyproject.toml api/README.md ./
COPY api/src ./src
COPY api/tests ./tests
RUN pip install -e ".[dev]"

# The API is the only service publishing a port, so it does not run as root.
RUN useradd --create-home --uid 10001 dxbapi && chown -R dxbapi:dxbapi /app
USER dxbapi

EXPOSE 8000

CMD ["uvicorn", "dxb_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
