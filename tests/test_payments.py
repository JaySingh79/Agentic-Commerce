"""Tests for payment authorization, provider selection, and the safety guards.

The production-key guard is a safety test: an autonomous agent must not be able to move
real money, so live keys are refused outright.

The fallback tests encode the rule that replaced "retry on any HTTPError": a provider is
only swapped out for a failure that happened *before* anything was transmitted. A
request that went out and was not answered is reconciled, never re-sent elsewhere.
"""

import httpx
import pytest

from agentic_commerce.backend import payments
from agentic_commerce.backend.payments import (
    PaymentConfigurationError,
    process_payment,
    select_provider,
)
from agentic_commerce.core.runtime import run_async
from agentic_commerce.payments.models import PaymentState
from agentic_commerce.payments.providers import RazorpayProvider, StripeProvider


def _stub_result(provider: str, amount_cents: int, currency: str):
    return payments.PaymentResult(
        provider=provider,
        status="created",
        reference_id=f"{provider}_ref_1",
        amount_cents=amount_cents,
        currency=currency,
        live=False,
    )


def test_defaults_to_simulated_without_credentials():
    assert select_provider() == "simulated"
    result = run_async(process_payment(2400, "USD"))
    assert result.provider == "simulated"
    assert result.live is False
    assert result.amount_cents == 2400
    assert result.reference_id.startswith("sim_")


def test_simulated_receipt_is_clearly_labelled_not_live():
    """A simulated capture must never read like a real one."""
    text = run_async(process_payment(5000, "USD")).format_display()
    assert "TEST / SIMULATED" in text
    assert "No real money moved" in text


def test_razorpay_is_preferred_when_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    assert select_provider() == "razorpay"


def test_stripe_used_when_only_stripe_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    assert select_provider() == "stripe"


def test_live_razorpay_key_is_refused(monkeypatch: pytest.MonkeyPatch):
    """Guard: an agent must not transact on production credentials."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_dangerous")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    with pytest.raises(PaymentConfigurationError, match="test key"):
        run_async(payments._charge_razorpay(1000, "INR", "r1"))


def test_live_stripe_key_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_dangerous")
    with pytest.raises(PaymentConfigurationError, match="test key"):
        run_async(payments._charge_stripe(1000, "USD", "r1"))


def test_live_key_degrades_to_simulated_rather_than_charging(monkeypatch: pytest.MonkeyPatch):
    """End to end, a live key must fall through to the safe gateway."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_dangerous")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    result = run_async(process_payment(2400, "INR"))
    assert result.provider == "simulated"
    assert result.live is False


def test_unreachable_razorpay_falls_back_to_stripe(monkeypatch: pytest.MonkeyPatch):
    """A connection that never opened cannot have created an order."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")

    async def _unreachable(self, amount_cents, currency, receipt):
        raise httpx.ConnectError("razorpay down")

    async def _stripe_ok(self, amount_cents, currency, receipt):
        return _stub_result("stripe", amount_cents, currency)

    monkeypatch.setattr(RazorpayProvider, "create_order", _unreachable)
    monkeypatch.setattr(StripeProvider, "create_order", _stripe_ok)

    result = run_async(process_payment(2400, "USD"))
    assert result.provider == "stripe"


def test_transmitted_request_without_an_answer_is_never_retried_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
):
    """The duplicate-charge case: a read timeout may already have created an order."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    stripe_calls: list[int] = []

    async def _timed_out(self, amount_cents, currency, receipt):
        raise httpx.ReadTimeout("no answer")

    async def _nothing_found(self, receipt):
        return None

    async def _stripe(self, amount_cents, currency, receipt):
        stripe_calls.append(amount_cents)
        return _stub_result("stripe", amount_cents, currency)

    monkeypatch.setattr(RazorpayProvider, "create_order", _timed_out)
    monkeypatch.setattr(RazorpayProvider, "find_by_receipt", _nothing_found)
    monkeypatch.setattr(StripeProvider, "create_order", _stripe)

    result = run_async(process_payment(2400, "INR", "rcpt-unknown"))
    assert stripe_calls == []
    assert result.state == PaymentState.UNKNOWN.value
    assert result.status == "unknown"


def test_unanswered_request_is_reconciled_when_the_order_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    """If the order did land, reconciliation adopts it instead of creating another."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")

    async def _timed_out(self, amount_cents, currency, receipt):
        raise httpx.ReadTimeout("no answer")

    async def _found(self, receipt):
        return _stub_result("razorpay", 2400, "INR")

    monkeypatch.setattr(RazorpayProvider, "create_order", _timed_out)
    monkeypatch.setattr(RazorpayProvider, "find_by_receipt", _found)

    result = run_async(process_payment(2400, "INR", "rcpt-reconcile"))
    assert result.provider == "razorpay"
    assert result.reference_id == "razorpay_ref_1"
    assert result.state == PaymentState.AUTHORIZED.value


def test_replaying_a_receipt_never_reaches_the_provider(monkeypatch: pytest.MonkeyPatch):
    """``receipt`` is the idempotency key, and the ledger enforces it."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    calls: list[str] = []

    async def _create(self, amount_cents, currency, receipt):
        calls.append(receipt)
        return _stub_result("razorpay", amount_cents, currency)

    monkeypatch.setattr(RazorpayProvider, "create_order", _create)

    first = run_async(process_payment(2400, "INR", "rcpt-once"))
    second = run_async(process_payment(2400, "INR", "rcpt-once"))

    assert calls == ["rcpt-once"]
    assert second.reference_id == first.reference_id


def test_both_providers_failing_still_yields_a_receipt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")

    async def _unreachable(self, amount_cents, currency, receipt):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(RazorpayProvider, "create_order", _unreachable)
    monkeypatch.setattr(StripeProvider, "create_order", _unreachable)

    assert run_async(process_payment(2400, "USD")).provider == "simulated"


def test_non_positive_amount_is_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        run_async(process_payment(0, "USD"))
    with pytest.raises(ValueError, match="must be positive"):
        run_async(process_payment(-100, "USD"))


def test_float_amounts_are_refused_rather_than_coerced():
    """Money stays an integer count of minor units all the way down."""
    with pytest.raises(ValueError, match="must be an int"):
        run_async(process_payment(24.00, "USD"))


def test_unknown_currency_is_rejected():
    with pytest.raises(ValueError, match="unsupported currency"):
        run_async(process_payment(2400, "XYZ"))


def test_amount_display_uses_major_units():
    result = run_async(process_payment(129900, "INR"))
    assert result.amount_display == "1299.00 INR"
