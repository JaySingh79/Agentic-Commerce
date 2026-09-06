"""Durable backing store for :class:`~agentic_commerce.backend.session.CommerceSession`.

Session state used to live only in the process-local ``_SESSION_STORE`` dict, so a
browser refresh — which mints a new Gradio ``session_hash`` — orphaned the active
cart, mandate and negotiation. This module keeps the same dict as the hot path and
writes a JSON snapshot behind it, so a session survives a refresh, a reconnect, and
a server restart as long as the client presents the same id.

SQLite is used rather than Redis because it needs no service to be running: the
whole point is that a shopper mid-checkout does not lose their cart, and a
persistence layer that has to be provisioned first would not be on in practice.

Set ``AC_SESSION_DB`` to relocate the file, or ``AC_SESSION_PERSIST=0`` to run
fully in memory (the tests do this; so should anything that must not leave traces).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    updated_at REAL NOT NULL,
    payload    TEXT NOT NULL
)
"""


def persistence_enabled() -> bool:
    """Whether session snapshots are written at all."""
    return os.getenv("AC_SESSION_PERSIST", "1").strip().lower() not in {"0", "false", "no"}


def default_db_path() -> Path:
    """Where snapshots live unless ``AC_SESSION_DB`` overrides it."""
    configured = os.getenv("AC_SESSION_DB", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / ".agentic_commerce" / "sessions.db"


class SqliteSessionStore:
    """A tiny key/value store for session snapshots.

    One connection is shared across threads (``check_same_thread=False``) and guarded
    by a lock, because Gradio, uvicorn's threadpool and the tool runtime all touch
    sessions from different threads.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        with self._lock:
            self._connection.execute(_SCHEMA)
            self._connection.commit()

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Returns the stored snapshot, or ``None`` when the id is unknown.

        A snapshot that cannot be parsed is treated as absent rather than fatal: a
        corrupt row must not make the session unusable forever.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, session_id: str, payload: dict[str, Any]) -> None:
        """Writes a snapshot, replacing any previous one for this id."""
        blob = json.dumps(payload, default=str)
        with self._lock:
            self._connection.execute(
                "INSERT INTO sessions (session_id, updated_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at, "
                "payload = excluded.payload",
                (session_id, _now(), blob),
            )
            self._connection.commit()

    def delete(self, session_id: str) -> None:
        """Forgets a session entirely."""
        with self._lock:
            self._connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class MemorySessionStore:
    """Non-persistent store with the same surface, for tests and ``AC_SESSION_PERSIST=0``."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def load(self, session_id: str) -> dict[str, Any] | None:
        payload = self._rows.get(session_id)
        return json.loads(json.dumps(payload, default=str)) if payload is not None else None

    def save(self, session_id: str, payload: dict[str, Any]) -> None:
        self._rows[session_id] = payload

    def delete(self, session_id: str) -> None:
        self._rows.pop(session_id, None)

    def close(self) -> None:
        self._rows.clear()


def _now() -> float:
    import time

    return time.time()


_STORE: SqliteSessionStore | MemorySessionStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> SqliteSessionStore | MemorySessionStore:
    """The process-wide store, opened on first use.

    Falls back to the in-memory store when SQLite cannot be opened (read-only
    directory, unwritable path). Losing durability is a degradation; refusing to
    serve the shopper because a file could not be created is not acceptable, and the
    reason is surfaced on stderr rather than swallowed.
    """
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            if not persistence_enabled():
                _STORE = MemorySessionStore()
            else:
                try:
                    _STORE = SqliteSessionStore(default_db_path())
                except (sqlite3.Error, OSError) as exc:
                    import sys

                    print(
                        f"[session_store] falling back to in-memory sessions: {exc}",
                        file=sys.stderr,
                    )
                    _STORE = MemorySessionStore()
        return _STORE


def set_store(store: SqliteSessionStore | MemorySessionStore | None) -> None:
    """Replaces the process-wide store (tests, or an embedder wiring its own)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store
