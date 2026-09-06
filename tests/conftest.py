"""Shared test wiring.

Sessions are durable now (``backend/session_store.py``), so tests are pinned to the
in-memory store: a suite run must not leave a SQLite file in the repository, and one
test's cart must not survive into the next.
"""

import pytest

from agentic_commerce.backend import session as session_module
from agentic_commerce.backend import session_store


@pytest.fixture(autouse=True)
def isolated_session_store() -> None:
    """Gives every test a private, non-persistent session store."""
    session_store.set_store(session_store.MemorySessionStore())
    session_module._SESSION_STORE.clear()
    yield
    session_store.set_store(None)
    session_module._SESSION_STORE.clear()
