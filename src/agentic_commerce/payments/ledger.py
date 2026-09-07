"""Durable record of every payment attempt: idempotency, state, and audit trail.

Two invariants live here, and neither can be enforced anywhere else:

*Idempotency.* An attempt is written ``PENDING`` **before** the provider is called.
A second attempt with the same key never reaches the provider; it returns the stored
row. Razorpay exposes no ``Idempotency-Key`` header, so this table is the only thing
standing between a retried request and a duplicate order.

*Single-use mandates.* Claiming a mandate is an ``INSERT`` on a primary key, so two
concurrent authorizations of one AP2 mandate resolve to exactly one winner in the
database rather than in application logic that could interleave.

Storage mirrors ``backend/session_store.py``: SQLite, no service to provision, one
lock-guarded connection shared across the Gradio, uvicorn and tool-runtime threads.
Set ``AC_PAYMENTS_DB`` to relocate the file, or ``AC_PAYMENTS_PERSIST=0`` for a
memory-only database (the tests do this).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from agentic_commerce.payments.models import (
    PaymentError,
    PaymentResult,
    PaymentState,
    assert_transition,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_attempts (
    idempotency_key TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL DEFAULT '',
    mandate_id      TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    reference_id    TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    result          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mandate_claims (
    mandate_id      TEXT NOT NULL,
    purpose         TEXT NOT NULL DEFAULT 'authorization',
    idempotency_key TEXT NOT NULL,
    claimed_at      REAL NOT NULL,
    PRIMARY KEY (mandate_id, purpose)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id     TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    received_at  REAL NOT NULL,
    payload      TEXT NOT NULL
);
"""


class MandateAlreadyUsedError(PaymentError):
    """Raised when a mandate that already authorized a payment is presented again."""


def persistence_enabled() -> bool:
    """Whether the ledger is written to disk at all."""
    return os.getenv("AC_PAYMENTS_PERSIST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def default_db_path() -> Path:
    """Where the ledger lives unless ``AC_PAYMENTS_DB`` overrides it."""
    configured = os.getenv("AC_PAYMENTS_DB", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / ".agentic_commerce" / "payments.db"


class PaymentLedger:
    """Append-and-advance store for payment attempts, mandate claims, and events."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    # -- attempts ---------------------------------------------------------

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        """Returns the stored attempt for a key, or ``None`` when it is unknown."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM payment_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def open_attempt(
        self,
        idempotency_key: str,
        amount_cents: int,
        currency: str,
        provider: str,
        session_id: str = "",
        mandate_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Reserves an attempt, returning ``(attempt, created)``.

        ``created`` is ``False`` when the key was already known — the caller must
        then return the stored attempt instead of contacting the provider, which is
        what makes a retried request a no-op rather than a second order.
        """
        now = time.time()
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO payment_attempts (idempotency_key, session_id, "
                    "mandate_id, provider, state, amount_cents, currency, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        idempotency_key,
                        session_id,
                        mandate_id,
                        provider,
                        PaymentState.PENDING.value,
                        amount_cents,
                        currency,
                        now,
                        now,
                    ),
                )
                self._connection.commit()
                created = True
            except sqlite3.IntegrityError:
                created = False
            row = self._connection.execute(
                "SELECT * FROM payment_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _row_to_dict(row), created

    def advance(
        self,
        idempotency_key: str,
        state: PaymentState,
        result: PaymentResult | None = None,
    ) -> dict[str, Any]:
        """Moves an attempt to ``state``, refusing transitions outside the table."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM payment_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise PaymentError(f"unknown payment attempt {idempotency_key!r}")
            assert_transition(PaymentState(row["state"]), state)
            self._connection.execute(
                "UPDATE payment_attempts SET state = ?, updated_at = ?, "
                "provider = COALESCE(NULLIF(?, ''), provider), "
                "reference_id = COALESCE(NULLIF(?, ''), reference_id), "
                "result = COALESCE(NULLIF(?, ''), result) "
                "WHERE idempotency_key = ?",
                (
                    state.value,
                    time.time(),
                    result.provider if result else "",
                    result.reference_id if result else "",
                    json.dumps(result.as_dict()) if result else "",
                    idempotency_key,
                ),
            )
            self._connection.commit()
            updated = self._connection.execute(
                "SELECT * FROM payment_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _row_to_dict(updated)

    def stored_result(self, idempotency_key: str) -> PaymentResult | None:
        """Rebuilds the :class:`PaymentResult` recorded for a key, if any."""
        attempt = self.get(idempotency_key)
        if not attempt or not attempt.get("result"):
            return None
        payload = attempt["result"]
        return PaymentResult(
            provider=payload.get("provider", ""),
            status=payload.get("status", ""),
            reference_id=payload.get("reference_id", ""),
            amount_cents=int(payload.get("amount_cents", 0)),
            currency=payload.get("currency", ""),
            live=bool(payload.get("live", False)),
            receipt_url=payload.get("receipt_url"),
            state=payload.get("state", attempt["state"]),
            idempotency_key=idempotency_key,
            mandate_id=payload.get("mandate_id") or attempt["mandate_id"] or None,
        )

    # -- mandates ---------------------------------------------------------

    def claim_mandate(
        self, mandate_id: str, idempotency_key: str, purpose: str = "authorization"
    ) -> None:
        """Consumes a mandate exactly once *per purpose*.

        Authorizing and later settling the same mandate are two steps of one spend, so
        they claim under different purposes; two *authorizations* of one mandate are a
        double spend and the second raises :class:`MandateAlreadyUsedError`.

        Re-claiming with the *same* idempotency key is allowed: that is a retry of one
        authorization, not a second spend.
        """
        with self._lock:
            existing = self._connection.execute(
                "SELECT idempotency_key FROM mandate_claims "
                "WHERE mandate_id = ? AND purpose = ?",
                (mandate_id, purpose),
            ).fetchone()
            if existing is not None:
                if existing["idempotency_key"] == idempotency_key:
                    return
                raise MandateAlreadyUsedError(
                    f"mandate {mandate_id!r} has already been claimed for {purpose}"
                )
            self._connection.execute(
                "INSERT INTO mandate_claims "
                "(mandate_id, purpose, idempotency_key, claimed_at) VALUES (?, ?, ?, ?)",
                (mandate_id, purpose, idempotency_key, time.time()),
            )
            self._connection.commit()

    # -- webhooks ---------------------------------------------------------

    def record_event(self, event_id: str, provider: str, payload: dict[str, Any]) -> bool:
        """Stores a webhook event. Returns ``False`` when it was already seen."""
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO webhook_events (event_id, provider, received_at, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (event_id, provider, time.time(), json.dumps(payload)),
                )
                self._connection.commit()
            except sqlite3.IntegrityError:
                return False
        return True

    def close(self) -> None:
        """Closes the underlying connection."""
        with self._lock:
            self._connection.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converts a ledger row, parsing the stored result payload."""
    data = dict(row)
    raw = data.get("result") or ""
    data["result"] = json.loads(raw) if raw else None
    return data


_LEDGER: PaymentLedger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> PaymentLedger:
    """Returns the process-wide ledger, creating it on first use."""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            path = default_db_path() if persistence_enabled() else ":memory:"
            _LEDGER = PaymentLedger(path)
        return _LEDGER


def set_ledger(ledger: PaymentLedger | None) -> None:
    """Replaces the process-wide ledger (tests pin this to an in-memory instance)."""
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = ledger
