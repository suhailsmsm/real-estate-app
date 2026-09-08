"""Real Estate App New — Windows desktop launcher.

Same pattern as the old DubaiEstate desktop app (pywebview on Edge WebView2,
no bundled Chromium), adapted to this app's single-origin shell:

  1. Pick a free loopback port (from 8600).
  2. Build the shell (UI + /api + /mcp + /copilot on ONE origin).
  3. Serve it with uvicorn in a background thread.
  4. Open a pywebview window at it; fall back to the default browser when
     pywebview is unavailable (dev machines, exotic Linux setups).

Frozen by PyInstaller (packaging/dxb_desktop.spec, onedir — the data snapshot
is too big for onefile's temp-extraction-per-launch), wrapped by Inno Setup.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("dxb_desktop.launcher")

APP_TITLE = "Real Estate App New"


def app_root() -> Path:
    """Where bundled read-only assets live (frozen: PyInstaller bundle dir)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def exe_root() -> Path:
    """Where writable/user-adjacent files live (frozen: beside the exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def ui_dir() -> Path:
    for cand in (app_root() / "ui", app_root() / "_internal" / "ui"):
        if cand.is_dir():
            return cand
    return app_root() / "ui"


def db_path() -> Path:
    for cand in (
        exe_root() / "data" / "dxb.db",
        exe_root() / "_internal" / "data" / "dxb.db",
    ):
        if cand.exists():
            return cand
    return exe_root() / "data" / "dxb.db"


def find_free_port(start: int = 8600, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}..{start + tries - 1}")


def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    from dxb_desktop.db_engine import check_schema

    # Windows consoles default to cp1252, and PyInstaller windowed apps have no
    # usable console at all. Never let a unicode log line or a missing stream
    # crash the app — especially at close time (a UnicodeEncodeError on '✓'
    # after the window closes was a real bug in the sibling DubaiEstate app).
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except (OSError, ValueError, AttributeError):  # pragma: no cover
            pass

    db = db_path()
    try:
        check_schema(db)
    except (FileNotFoundError, RuntimeError) as exc:
        # A missing snapshot is a packaging error, not something to silently
        # paper over: show it in the window (or console) instead of a spin.
        log.error("%s", exc)

    port = find_free_port()

    import uvicorn

    from dxb_desktop.shell import build_shell

    app, _swap = build_shell(db, ui_dir(), port)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="dxb-server", daemon=True)
    thread.start()
    if not wait_for_port(port):
        log.error("server did not come up on port %s", port)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # type: ignore

        webview.create_window(
            APP_TITLE,
            url,
            width=1440,
            height=900,
            min_size=(1024, 640),
        )
        webview.start()
    except ImportError:
        log.warning("pywebview unavailable — opening the default browser")
        import webbrowser

        webbrowser.open(url)
        # Keep serving while the browser tab is open; Ctrl-C to stop.
        try:
            thread.join()
        except KeyboardInterrupt:  # pragma: no cover
            pass
        return
    except Exception:  # noqa: BLE001 - a window/runtime error must never
        # surface as an error dialog at close; log it and shut down cleanly.
        log.warning("window closed with an error", exc_info=True)

    # The window is closed: stop the embedded server so no background asyncio
    # thread is still mid-loop when the process exits. Without this, a close
    # can leave uvicorn logging against a torn-down event loop, which shows up
    # as an error right after the window disappears.
    try:
        server.should_exit = True
        thread.join(timeout=5.0)
    except Exception:  # noqa: BLE001 - shutdown must never raise on exit
        log.warning("server shutdown did not complete cleanly", exc_info=True)


if __name__ == "__main__":
    main()
