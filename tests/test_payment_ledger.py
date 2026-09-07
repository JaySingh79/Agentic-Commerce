"""Tests for the payment ledger: idempotency, state transitions, single-use mandates.

These are the invariants that cannot live in application logic. Two coroutines checking
"has this mandate been used?" can both read "no"; a primary key cannot both accept the
same row twice.
"""

import pytest

from agentic_commerce.payments.ledger import (
    MandateAlreadyUsedError,
    PaymentLedger,
)
from agentic_commerce.payments.models import (
    IllegalTransitionError,
    PaymentResult,
    PaymentState,
)


@pytest.fixture
def ledger() -> PaymentLedger:
    book = PaymentLedger(":memory:")
    yield book
    book.close()


def _result(reference_id: str = "order_1") -> PaymentResult:
    return PaymentResult(
        provider="razorpay",
        status="created",
        reference_id=reference_id,
        amount_cents=2400,
        currency="INR",
        live=False,
    )


def test_reopening_a_key_reports_it_as_not_created(ledger: PaymentLedger):
    _, created_first = ledger.open_attempt("key-1", 2400, "INR", provider="razorpay")
    _, created_again = ledger.open_attempt("key-1", 2400, "INR", provider="razorpay")
    assert created_first is True
    assert created_again is False


def test_stored_result_round_trips_after_authorization(ledger: PaymentLedger):
    ledger.open_attempt("key-2", 2400, "INR", provider="razorpay")
    ledger.advance("key-2", PaymentState.AUTHORIZED, _result("order_2"))

    stored = ledger.stored_result("key-2")
    assert stored is not None
    assert stored.reference_id == "order_2"
    assert stored.state == PaymentState.AUTHORIZED.value
    assert stored.idempotency_key == "key-2"


def test_pending_attempt_has_no_stored_result(ledger: PaymentLedger):
    ledger.open_attempt("key-3", 2400, "INR", provider="razorpay")
    assert ledger.stored_result("key-3") is None


def test_illegal_state_transitions_are_refused(ledger: PaymentLedger):
    ledger.open_attempt("key-4", 2400, "INR", provider="razorpay")
    ledger.advance("key-4", PaymentState.FAILED)
    with pytest.raises(IllegalTransitionError):
        ledger.advance("key-4", PaymentState.AUTHORIZED)


def test_unknown_can_be_resolved_by_reconciliation(ledger: PaymentLedger):
    """An unanswered request is not terminal: a webhook or lookup can still settle it."""
    ledger.open_attempt("key-5", 2400, "INR", provider="razorpay")
    ledger.advance("key-5", PaymentState.UNKNOWN)
    row = ledger.advance("key-5", PaymentState.AUTHORIZED, _result("order_5"))
    assert row["state"] == PaymentState.AUTHORIZED.value


def test_a_mandate_authorizes_exactly_one_payment(ledger: PaymentLedger):
    ledger.claim_mandate("ap2_mandate_x", "key-6")
    with pytest.raises(MandateAlreadyUsedError):
        ledger.claim_mandate("ap2_mandate_x", "key-7")


def test_retrying_the_same_authorization_is_not_a_second_spend(ledger: PaymentLedger):
    """Same mandate *and* same idempotency key is one authorization being retried."""
    ledger.claim_mandate("ap2_mandate_y", "key-8")
    ledger.claim_mandate("ap2_mandate_y", "key-8")


def test_webhook_events_are_recorded_once(ledger: PaymentLedger):
    assert ledger.record_event("evt_1", "razorpay", {"event": "order.paid"}) is True
    assert ledger.record_event("evt_1", "razorpay", {"event": "order.paid"}) is False
