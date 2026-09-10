"""Guards for observable durability and per-turn crew fan-out.

Covers two fixed defects: snapshot writes that failed silently (the turn kept
going in memory while durability was lost), and SSE crew replay driven by
shared list-identity + index bookkeeping (concurrent turns duplicated or
dropped each other's specialists).
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agentic_commerce.backend import session as session_module
from agentic_commerce.backend import session_store
from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.backend.session_store import MemorySessionStore, SqliteSessionStore
from agentic_commerce.core import telemetry


def restart_process() -> None:
    session_module._SESSION_STORE.clear()


def test_a_successful_save_reports_durable(tmp_path: Path):
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    session = get_or_create_session("sid_durable_ok")
    session.update_cart({"id": "cart_1"})
    assert session.save() is True
    assert session.last_save_ok is True
    assert session.last_save_error == ""


def test_a_failed_save_is_observable_not_silent():
    class BrokenStore(MemorySessionStore):
        def save(self, session_id: str, payload: dict) -> None:
            raise OSError("disk full")

    session_store.set_store(BrokenStore())
    restart_process()

    session = get_or_create_session("sid_durable_broken")
    assert session.save() is False
    assert session.last_save_ok is False
    assert "disk full" in session.last_save_error
    assert telemetry.get_session_save_failures("sid_durable_broken") >= 1


def test_durability_recovers_on_the_next_successful_save(tmp_path: Path):
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    session = get_or_create_session("sid_durable_flap")
    session.last_save_ok = False
    session.last_save_error = "stale"
    assert session.save() is True
    assert session.last_save_ok is True
    assert session.last_save_error == ""


def test_a_corrupt_snapshot_is_quarantined_not_reused(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "s.db")
    session_store.set_store(store)
    restart_process()
    with store._lock:
        store._connection.execute(
            "INSERT INTO sessions (session_id, updated_at, payload) VALUES (?, ?, ?)",
            ("sid_quarantine", 0.0, "{not json"),
        )
        store._connection.commit()

    assert get_or_create_session("sid_quarantine").active_cart is None
    assert list((tmp_path / "corrupt").glob("sid_quarantine.*.json")) != []
    with store._lock:
        row = store._connection.execute(
            "SELECT payload FROM sessions WHERE session_id = ?", ("sid_quarantine",)
        ).fetchone()
    assert row is None


def test_each_turn_receives_only_events_written_while_it_runs():
    session_store.set_store(MemorySessionStore())
    restart_process()

    session = get_or_create_session("sid_turn_scope")
    session.update_crew_events([{"agent": "CatalogScout", "status": "done", "detail": "old"}])

    first = session.subscribe_turn_events()
    try:
        session.update_crew_events(
            [{"agent": "CatalogScout", "status": "done", "detail": "new"}]
        )
        second = session.subscribe_turn_events()
        try:
            assert first.get_nowait()["detail"] == "new"
            assert second.empty()
        finally:
            session.unsubscribe_turn_events(second)
    finally:
        session.unsubscribe_turn_events(first)


def test_concurrent_turns_share_crew_events_without_losing_them():
    session_store.set_store(MemorySessionStore())
    restart_process()

    session = get_or_create_session("sid_turn_share")
    first = session.subscribe_turn_events()
    second = session.subscribe_turn_events()
    try:
        session.update_crew_events([{"agent": "WebScout", "status": "done"}])
        assert first.get_nowait()["agent"] == "WebScout"
        assert second.get_nowait()["agent"] == "WebScout"
    finally:
        session.unsubscribe_turn_events(first)
        session.unsubscribe_turn_events(second)


def test_unsubscribed_turns_stop_receiving_events():
    session_store.set_store(MemorySessionStore())
    restart_process()

    session = get_or_create_session("sid_turn_unsub")
    outbox = session.subscribe_turn_events()
    session.unsubscribe_turn_events(outbox)
    session.update_crew_events([{"agent": "CatalogScout", "status": "done"}])
    assert outbox.empty()
    # The stored history still records the latest run for the gallery surface.
    assert session.last_crew_events[0]["agent"] == "CatalogScout"


def test_snapshot_reports_whether_the_session_is_durable(tmp_path: Path):
    from agentic_commerce.api.server import session_snapshot

    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    get_or_create_session("sid_snapshot").update_cart({"id": "cart_9"})
    assert session_snapshot("sid_snapshot")["durable"] is True


def test_done_frame_reports_whether_the_turn_is_durable(
    tmp_path: Path, monkeypatch: Any
):
    import agentic_commerce.api.server as api

    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    class QuietAgent:
        def __init__(self, session_id: str = "", **_: Any) -> None:
            self.session_id = session_id

        def execute_stream(self, **_: Any):
            yield {"type": "content", "text": "hi"}

    monkeypatch.setattr(api, "CommerceAgent", QuietAgent)
    client = TestClient(api.app)
    body = client.post(
        "/api/chat/stream", json={"message": "hi", "session_id": "sid_done"}
    ).text
    done = [line for line in body.splitlines() if '"done"' in line]
    assert done and '"durable": true' in done[-1].lower()
