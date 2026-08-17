# nginx edge

TLS termination and rate limiting for both public surfaces. `api` and `mcp` do
not publish ports of their own — everything arrives through here.

```
                     ┌── /api/  → api:8000   (rest_zone: 10r/s, burst 20)
client ── 443 ── nginx ┤
                     └── /mcp   → mcp:8100   (mcp_zone:  5r/s, burst 30, SSE-aware)
```

## Certificates

Not in the repository — `nginx/certs/` is gitignored, because a committed
private key is a compromised one. Generate a self-signed pair for local
development:

```bash
openssl req -x509 -nodes -newkey rsa:2048 -days 825 -keyout nginx/certs/server.key -out nginx/certs/server.crt -subj "/CN=localhost/O=dubai-estate-dev" -addext "subjectAltName=DNS:localhost,DNS:api,DNS:mcp,IP:127.0.0.1"
```

Being self-signed, clients must be told to accept it — `curl -k`, or the
equivalent flag in whatever MCP client you are testing with. **In production,
replace both files with a real certificate** (Let's Encrypt or otherwise);
nothing in the config changes, only the two files.

## Why two rate-limit zones

The two clients have genuinely different traffic shapes, and one limit cannot
serve both honestly:

- A **browser** makes a handful of requests per view, spread out.
- An **agent** fires several tool calls in a burst while reasoning about a
  single question, then goes quiet for as long as the model is thinking.

Applying browser-shaped limits to agent traffic throttles entirely normal
behaviour, and the failure is misleading rather than obvious: from the agent's
side a 429 mid-reasoning looks like the data is unavailable, so it reports a
gap that does not exist. Hence the MCP zone has a lower sustained rate but a
much larger `burst` with `nodelay`.

## The one that will bite you

`proxy_buffering off` on the `/mcp` location is load-bearing. A Streamable HTTP
response may upgrade to SSE, and with buffering left on, nginx holds the event
stream until its buffer fills. Streaming silently stops working — every request
still returns 200, nothing errors, responses just arrive late or never. It
looks like an application bug and it is not one.

The long `proxy_read_timeout` matters for the same reason: an SSE stream is
idle between events by design, and the 60s default would cut a long analytics
run off mid-answer.
