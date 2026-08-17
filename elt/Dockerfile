# Build context is the REPO ROOT (see docker-compose.yml), not elt/, because
# the image also needs packages/dxb-core. Hence the elt/-prefixed COPYs.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Shared schema package first: it changes far less often than ELT code, so it
# earns its own cache layer. Installing it here also satisfies the `dxb-core`
# requirement below — pip does not read uv's [tool.uv.sources].
COPY packages/dxb-core /opt/dxb-core
RUN pip install -e /opt/dxb-core

COPY elt/pyproject.toml elt/README.md ./
COPY elt/src ./src
COPY elt/tests ./tests
# editable install incl. dev deps: the compose volume mount of ./src stays live
# without rebuilds, and `docker compose run --rm elt python -m pytest -q` works
RUN pip install -e ".[dev]"

COPY elt/alembic.ini ./
COPY elt/alembic ./alembic

CMD ["dxb", "run-scheduler"]
