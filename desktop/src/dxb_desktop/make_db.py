"""Create an empty (schema-only) snapshot database.

Used by CI when no data snapshot release exists yet, and by tests as the
base they insert fixtures into. A schema-only db makes the app boot and
honestly report zero coverage — the same behaviour as an unloaded stack.

    python -m dxb_desktop.make_db path/to/dxb.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .schema import create_all


def make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        create_all(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/dxb.db")
    make_db(target)
    print(f"empty snapshot schema written to {target}")
