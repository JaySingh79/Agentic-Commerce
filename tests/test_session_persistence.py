"""Tests for durable sessions.

The defect these cover: state lived only in a process-local dict keyed by Gradio's
``session_hash``, so a browser refresh orphaned the shopper's cart, mandate and
negotiation mid-checkout.
"""

from pathlib import Path

import pytest

from agentic_commerce.backend import session as session_module
from agentic_commerce.backend import session_store
from agentic_commerce.backend.session import (
    CommerceSession,
    forget_session,
    get_or_create_session,
    new_session_id,
)
from agentic_commerce.backend.session_store import (
    MemorySessionStore,
    SqliteSessionStore,
    default_db_path,
    persistence_enabled,
)


def restart_process() -> None:
    """Simulates a fresh process: memory is gone, the store is not."""
    session_module._SESSION_STORE.clear()


def test_a_cart_survives_losing_the_in_memory_session(tmp_path: Path):
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    get_or_create_session("sid_cart").update_cart({"id": "cart_1", "merchant_domain": "m.com"})
    restart_process()

    assert get_or_create_session("sid_cart").active_cart == {
        "id": "cart_1",
        "merchant_domain": "m.com",
    }


def test_every_commerce_surface_is_restored_not_just_the_cart(tmp_path: Path):
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    session = get_or_create_session("sid_all")
    session.update_search_results([{"id": "gid://1", "title": "Tee"}])
    session.update_web_results([{"title": "W", "url": "https://x.com", "source": "x.com"}])
    session.update_mandate({"mandate_id": "m1", "amount_cents": 500})
    session.update_negotiation({"agreed": True, "final_price_cents": 400})
    session.update_payment({"live": False, "provider": "simulated"})
    session.update_best_pick({"winner": {"product_id": "gid://1"}})
    session.update_crew_events([{"agent": "CatalogScout", "status": "done"}])
    session.update_active_product({"id": "gid://1"})
    session.update_checkout({"id": "co_1"})
    session.add_message("user", "hello")

    restart_process()
    restored = get_or_create_session("sid_all")

    assert restored.last_searched_products[0]["title"] == "Tee"
    assert restored.last_web_results[0]["url"] == "https://x.com"
    assert restored.active_mandate["mandate_id"] == "m1"
    assert restored.active_negotiation["agreed"] is True
    assert restored.active_payment["provider"] == "simulated"
    assert restored.best_pick["winner"]["product_id"] == "gid://1"
    assert restored.last_crew_events[0]["agent"] == "CatalogScout"
    assert restored.active_product["id"] == "gid://1"
    assert restored.active_checkout["id"] == "co_1"
    assert restored.history == [{"role": "user", "content": "hello"}]


def test_clearing_results_is_persisted_too(tmp_path: Path):
    """A cleared gallery that comes back after a refresh is a correctness bug."""
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    session = get_or_create_session("sid_clear")
    session.update_search_results([{"id": "gid://1"}])
    session.clear_search_results()
    restart_process()

    assert get_or_create_session("sid_clear").last_searched_products == []


def test_sessions_do_not_leak_into_each_other(tmp_path: Path):
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    get_or_create_session("sid_a").update_cart({"id": "cart_a"})
    assert get_or_create_session("sid_b").active_cart is None


def test_forgetting_a_session_removes_the_durable_copy(tmp_path: Path):
    session_store.set_store(SqliteSessionStore(tmp_path / "s.db"))
    restart_process()

    get_or_create_session("sid_gone").update_cart({"id": "cart_x"})
    forget_session("sid_gone")

    assert get_or_create_session("sid_gone").active_cart is None


def test_a_corrupt_snapshot_yields_a_fresh_session_rather_than_an_error(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "s.db")
    session_store.set_store(store)
    restart_process()
    with store._lock:
        store._connection.execute(
            "INSERT INTO sessions (session_id, updated_at, payload) VALUES (?, ?, ?)",
            ("sid_bad", 0.0, "{not json"),
        )
        store._connection.commit()

    assert get_or_create_session("sid_bad").active_cart is None


def test_an_unknown_field_in_a_snapshot_is_ignored():
    """Snapshots outlive code changes; a removed field must not break the restore."""
    restored = CommerceSession.from_dict({"active_cart": {"id": "c"}, "retired_field": 1})
    assert restored.active_cart == {"id": "c"}


def test_a_failing_store_does_not_break_the_turn(monkeypatch: pytest.MonkeyPatch):
    class BrokenStore(MemorySessionStore):
        def save(self, session_id: str, payload: dict) -> None:
            raise OSError("disk full")

    session_store.set_store(BrokenStore())
    restart_process()

    session = get_or_create_session("sid_broken")
    session.update_cart({"id": "cart_ok"})

    # Durability is lost; the shopper's cart is not.
    assert session.active_cart == {"id": "cart_ok"}


def test_session_ids_are_unguessable():
    ids = {new_session_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("s_") and len(i) > 20 for i in ids)


def test_persistence_can_be_switched_off_entirely(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AC_SESSION_PERSIST", "0")
    session_store.set_store(None)
    assert persistence_enabled() is False
    assert isinstance(session_store.get_store(), MemorySessionStore)


def test_the_db_path_is_configurable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AC_SESSION_DB", str(tmp_path / "custom.db"))
    assert default_db_path() == tmp_path / "custom.db"
