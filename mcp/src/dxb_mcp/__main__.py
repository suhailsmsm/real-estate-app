"""Entrypoint. Streamable HTTP by default; stdio only when switched on.

The stdio path is for local debugging against a desktop MCP client. It is off
unless `DXB_MCP_STDIO` is set, because a server process on a public host should
not expose a second command channel just because the code supports one
(docs/MCP_DESIGN.md §3).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from dxb_mcp.config import get_settings
from dxb_mcp.server import build_app, create_server


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        # stdio speaks JSON-RPC on stdout: a log line there corrupts the
        # protocol stream. Logs go to stderr always, not only in stdio mode,
        # so the two paths cannot diverge.
        stream=sys.stderr,
    )

    if settings.stdio_enabled:
        logging.getLogger(__name__).info("starting MCP server on stdio (debug)")
        asyncio.run(create_server(settings).run_stdio_async())
        return

    import uvicorn

    logging.getLogger(__name__).info(
        "starting MCP server on %s:%s%s (stateless)",
        settings.host,
        settings.port,
        settings.path,
    )
    uvicorn.run(
        build_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # Behind nginx: trust its X-Forwarded-* so client IPs and scheme are
        # the real ones in logs, not the compose bridge address.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
