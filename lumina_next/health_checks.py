"""Small non-mutating checks: a missing drive must not crash /health."""
from __future__ import annotations

import sqlite3
from typing import Any


def memory_health(memory: Any) -> dict:
    try:
        return {"ok": True, "stats": memory.stats()}
    except (OSError, sqlite3.Error) as exc:
        # No replacement database, recovery write, or private memory contents.
        return {"ok": False, "stats": None, "error": type(exc).__name__,
                "detail": "memory storage unavailable; check runtime volume access"}
