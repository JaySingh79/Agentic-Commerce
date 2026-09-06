"""Tests for test-mode payment processing and provider selection.

The production-key guard is a safety test: an autonomous agent must not be able
to move real money, so live keys are refused outright.
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
from agentic_commerce.payments import gateway as payment_gateway

PAYMENT_ENV = (
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_payment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolates each test from any real credentials in the environment."""
    for name in PAYMENT_ENV:
        monkeypatch.delenv(name, raising=False)


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


def test_razorpay_failure_falls_back_to_stripe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")

    async def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("razorpay down")

    async def _stripe_ok(amount_cents, currency, receipt):
        return payments.PaymentResult(
            provider="stripe",
            status="requires_payment_method",
            reference_id="pi_test_1",
            amount_cents=amount_cents,
            currency=currency,
            live=False,
        )

    monkeypatch.setattr(payment_gateway, "_charge_razorpay", _boom)
    monkeypatch.setattr(payment_gateway, "_charge_stripe", _stripe_ok)

    result = run_async(process_payment(2400, "USD"))
    assert result.provider == "stripe"


def test_both_providers_failing_still_yields_a_receipt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")

    async def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(payment_gateway, "_charge_razorpay", _boom)
    monkeypatch.setattr(payment_gateway, "_charge_stripe", _boom)

    assert run_async(process_payment(2400, "USD")).provider == "simulated"


def test_non_positive_amount_is_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        run_async(process_payment(0, "USD"))
    with pytest.raises(ValueError, match="must be positive"):
        run_async(process_payment(-100, "USD"))


def test_amount_display_uses_major_units():
    result = run_async(process_payment(129900, "INR"))
    assert result.amount_display == "1299.00 INR"
