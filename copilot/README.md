# dxb copilot

An LLM agent that answers questions about the Dubai real estate data **and**
reconfigures the UI to illustrate its answers. See
[../docs/UI_PLAN.md](../docs/UI_PLAN.md) §5 for the design.

## Why this is its own service

It needs an outbound model API key and a tool-calling loop, and it reaches data
through the MCP server — so it never needs database access at all. Keeping it
out of `api/` preserves the property that the API only reads the database and
does nothing else. Same reasoning that made `mcp/` separate.

```
ui/  ──SSE──▶  copilot/  ──tools──▶  mcp/  ──HTTP──▶  api/  ──▶  Postgres
```

## Configuration

### Model provider

`DXB_COPILOT_PROVIDER` selects which LLM answers questions — `anthropic`
(default), `openai`, or `ollama`. Each has its own `providers/*_provider.py`
implementation (`providers/base.py` is the shared, provider-neutral
interface `agent.py`'s loop talks to). Set the one credential the selected
provider needs:

| Provider | Env var | Default model |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY=sk-ant-...` | `claude-sonnet-5` |
| `openai` | `OPENAI_API_KEY=sk-...` | `gpt-4.1` |
| `ollama` | `DXB_COPILOT_OLLAMA_URL` (default `http://host.docker.internal:11434`, no key needed) | `glm-5.2:cloud` |

`DXB_COPILOT_MODEL` overrides the default for whichever provider is
selected. Without the selected provider's credential, the service still
boots and serves `/health` (reporting `"configured": false`), and every chat
request returns one clear explanation rather than a crash loop that looks
like a broken deployment.

**Ollama needs a tool-capable model.** Not every model supports function
calling — sending tool definitions to one that doesn't silently ignores them
rather than erroring, which would mean the copilot answers without ever
fetching real data. The provider checks `ollama.show(model)`'s reported
capabilities before the first request and fails with a clear error if the
configured model isn't tagged `tools` (e.g. `glm-5.2:cloud`, `llama3.1`,
`qwen2.5`, `mistral-nemo`, `firefunction-v2` are).

The default, `glm-5.2:cloud`, is an [Ollama cloud
model](https://ollama.com) — the local `ollama` daemon still fronts the
request (so `DXB_COPILOT_OLLAMA_URL` still points at it, not at Ollama's
cloud directly), it just executes remotely instead of on the Docker host's
own hardware. `ollama pull glm-5.2:cloud` once on the host before use.

### Everything else

| Variable | Default | What |
|---|---|---|
| `DXB_COPILOT_MCP_URL` | `http://mcp:8100/mcp` | The MCP server |
| `DXB_MCP_CLIENT_API_KEY` | — | Key this service presents to MCP |
| `DXB_COPILOT_CLIENT_API_KEYS` | — | Keys callers must present to *this* service |
| `DXB_COPILOT_MAX_TURNS` | `8` | Hard ceiling on tool round trips per question |

`DXB_COPILOT_CLIENT_API_KEYS` uses the same shape and hashing as the API's
`DXB_API_KEYS` and the MCP server's `DXB_MCP_CLIENT_API_KEYS`, so one
key-generation command works for all three:

```
python -c "import hashlib,secrets; k=secrets.token_urlsafe(32); print('key:',k); print('hash:',hashlib.sha256(k.encode()).hexdigest())"
```

**Auth fails closed.** With no keys configured every request is rejected. This
service spends money per request, so refusing is the only acceptable direction
to be wrong in. `DXB_COPILOT_AUTH_DISABLED=1` exists for local development and
must never be set anywhere else.

## Surface

- `GET /health` — public, static, reports whether a model key is present.
- `POST /chat` — authenticated. Takes `{messages, view_state}` and returns
  Server-Sent Events:

| Event | Payload | Meaning |
|---|---|---|
| `text` | `{text}` | Prose for the user |
| `tool` | `{name, input}` | A data tool is being called (drives a "thinking" indicator) |
| `view_state` | `{patch, explanation}` | Apply this ViewState patch |
| `done` | `{stop_reason}` | Turn finished |
| `error` | `{message}` | Something failed; the message is user-facing |

## Safety properties, and why each exists

- **Data only through MCP.** No SQL, no DB credentials. The model can call
  exactly the seven curated tools and nothing else.
- **UI changes only through a ViewState patch**, applied by the client's own
  reducer and validated by the same zod schema a user's click goes through.
  There is no privileged path, so nothing the copilot does is un-undoable.
- **Tool output is data, not instructions.** The system prompt says so
  explicitly, because tool results contain free-text database fields (property
  and project names) that an attacker could have influenced.
- **Bounded loop.** `max_turns` caps tool round trips; exceeding it returns an
  explanation rather than presenting a partial state as a finished answer.
- **Honesty rules are in the prompt**, not left to chance: always state sample
  size, never present a gross yield as achievable, always carry caveats
  through, never give investment advice.

## Development

```
uv sync --extra dev
uv run pytest -q
uv run ruff format . && uv run ruff check .
```

Tests fake the model provider (each of `tests/providers/test_*.py`) and MCP,
so the suite needs no API key, no local Ollama install, and no network.
