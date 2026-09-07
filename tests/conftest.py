"""Shared test wiring.

Sessions are durable now (``backend/session_store.py``), so tests are pinned to the
in-memory store: a suite run must not leave a SQLite file in the repository, and one
test's cart must not survive into the next.
"""

import pytest

from agentic_commerce.backend import session as session_module
from agentic_commerce.backend import session_store
from agentic_commerce.payments import ledger as payment_ledger

#: Credentials that would make a test talk to a real payment provider.
_PAYMENT_ENV = (
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_MCP_BRIDGE",
    "STRIPE_SECRET_KEY",
    "STRIPE_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_session_store() -> None:
    """Gives every test a private, non-persistent session store."""
    session_store.set_store(session_store.MemorySessionStore())
    session_module._SESSION_STORE.clear()
    yield
    session_store.set_store(None)
    session_module._SESSION_STORE.clear()


@pytest.fixture(autouse=True)
def isolated_payment_ledger() -> None:
    """Gives every test a private in-memory payment ledger.

    Without this the suite writes ``.agentic_commerce/payments.db`` into the repo and
    one test's mandate claim blocks the next test's identical mandate.
    """
    ledger = payment_ledger.PaymentLedger(":memory:")
    payment_ledger.set_ledger(ledger)
    yield
    payment_ledger.set_ledger(None)
    ledger.close()


@pytest.fixture(autouse=True)
def no_real_payment_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the suite off real payment APIs.

    A developer's ``.env`` is loaded into the process, so without this a test that
    exercises the payment path reaches api.razorpay.com for real. Tests that want a
    provider configure it themselves.
    """
    for name in _PAYMENT_ENV:
        monkeypatch.delenv(name, raising=False)
