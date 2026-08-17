# `mcp/` — the MCP server

An MCP server exposing the Dubai real-estate analytics to any MCP-capable
agent. Full design and rationale: [docs/MCP_DESIGN.md](../docs/MCP_DESIGN.md).

**This package owns no SQL and no schema.** It is an HTTP client of `api/` and
nothing else — see `docs/MCP_DESIGN.md` §2 for why, and `pyproject.toml`'s
`banned-api` ruff rule for how that's enforced (`dxb_core`, `dxb_api`, and
`sqlalchemy` imports fail lint here on sight).

## Running it

Normally via `docker compose up -d` from the repo root, which builds this
image and puts it behind nginx at `https://localhost/mcp` — see the root
[README.md](../README.md) for base URLs and example calls, and
[Authentication](../README.md#authentication) for the two key variables this
service needs (`DXB_MCP_API_KEY` outbound, `DXB_MCP_CLIENT_API_KEYS` inbound).

For local tooling that can't trust nginx's self-signed cert (`claude mcp add`
and similar), compose also publishes this service directly on
`127.0.0.1:8100` — loopback-only, auth still enforced, just no TLS. See the
root README's "Connecting Claude Code directly".

Locally, from this directory: `uv run python -m dxb_mcp`. Defaults to
Streamable HTTP on `:8100`; `DXB_MCP_STDIO=1` switches to stdio instead, for
attaching a desktop MCP client directly — off by default, since a server
process on a public host should not expose a second command channel just
because the code supports one.

## Two things worth knowing before you're surprised by them

- **DNS-rebinding protection is on and will reject an unlisted `Host`.** The
  SDK validates the `Host`/`Origin` headers by default — it's what stops a
  page the user is browsing from driving an MCP server reachable from their
  machine. Behind nginx the `Host` is whatever the client sent, so the proxy's
  public hostname(s) must be in `DXB_MCP_ALLOWED_HOSTS` (comma-separated) or
  every proxied request 421s with `Invalid Host header`. Disabling the check
  is the tempting fix and the wrong one — extend the allow-list instead.
- **Client auth fails closed.** With `DXB_MCP_CLIENT_API_KEYS` empty and
  `DXB_MCP_AUTH_DISABLED` unset, every request gets `401` — logged as a
  startup warning so it's diagnosable, but rejected regardless. This is
  deliberate: the alternative (open by default) is the gap
  `docs/MCP_DESIGN.md` §4 exists to close.

## Tests

`uv run pytest -q` from this directory. No network, no real REST API: the
upstream is a mocked `httpx` transport (`tests/test_tools.py`), and the auth
tests drive the real ASGI app end to end (`build_app`) rather than calling
tool functions directly, because the auth middleware wraps the ASGI layer —
a test that bypassed HTTP would prove nothing about whether the check is
actually reachable.
