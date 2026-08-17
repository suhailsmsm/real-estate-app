# dxb-api — read-only analytics API

FastAPI service over the star schema built by `elt/`. Design and the reasoning
behind it: [../docs/API_DESIGN.md](../docs/API_DESIGN.md).

Interactive docs once the stack is up: <http://localhost:8000/docs>

## Run

```bash
docker compose up -d api
```

## Develop

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

All code here is **async** and must never import from `elt/` — see the
sync/async rules in [../CLAUDE.md](../CLAUDE.md).

## Generating credentials

```bash
uv run python -c "from dxb_api.auth import hash_password; print(hash_password('yourpass'))"
uv run python -c "import secrets; from dxb_api.auth import hash_api_key; k=secrets.token_urlsafe(32); print(k, hash_api_key(k))"
```

Put the resulting hashes in `DXB_API_USERS` / `DXB_API_KEYS` (see
`.env.example`). Argon2 hashes contain `$`, which must be doubled to `$$` in
a file Docker Compose loads.
