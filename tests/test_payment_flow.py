"""Tests for the mandate-gated payment flow.

Every guard gets its own refusal, and every refusal gets its own test: before this
existed, ``process_test_payment`` accepted an amount and a currency and consulted no
mandate at all, so the AP2 signature the UI proudly displayed authorized nothing.
"""

import time

import pytest

from agentic_commerce.ap2.mandate import AP2Engine
from agentic_commerce.core.runtime import run_async
from agentic_commerce.payments.flow import (
    CaptureNotEnabledError,
    MandateExpiredError,
    MandateScopeError,
    MandateTamperedError,
    PaymentFlow,
)
from agentic_commerce.payments.ledger import MandateAlreadyUsedError
from agentic_commerce.payments.models import PaymentState

ENGINE = AP2Engine(secret_key="test_secret_123")


@pytest.fixture
def flow() -> PaymentFlow:
    return PaymentFlow(engine=ENGINE)


def _mandate(**overrides):
    mandate = ENGINE.create_payment_mandate(
        cart_id="gid://shopify/Cart/1",
        amount_cents=2400,
        currency="INR",
        merchant_domain="shop.example.com",
    )
    mandate.update(overrides)
    return mandate


def test_authorization_requires_a_valid_mandate_and_records_it(flow: PaymentFlow):
    mandate = _mandate()
    result = run_async(flow.authorize(mandate, session_id="s1"))

    assert result.state == PaymentState.AUTHORIZED.value
    assert result.mandate_id == mandate["mandate_id"]
    assert result.live is False


def test_tampered_mandate_is_refused_as_tampered(flow: PaymentFlow):
    mandate = _mandate()
    mandate["amount_cents"] = 1
    with pytest.raises(MandateTamperedError):
        run_async(flow.authorize(mandate, session_id="s1"))


def test_expired_mandate_is_refused_as_expired(flow: PaymentFlow):
    mandate = _mandate()
    mandate["expires_at"] = int(time.time()) - 10
    mandate["signature"] = ENGINE.sign(mandate)
    with pytest.raises(MandateExpiredError):
        run_async(flow.authorize(mandate, session_id="s1"))


def test_charge_above_the_spending_limit_is_refused(flow: PaymentFlow):
    with pytest.raises(MandateScopeError, match="spending limit"):
        run_async(flow.authorize(_mandate(), session_id="s1", amount_cents=99_999))


def test_charge_in_another_currency_is_refused(flow: PaymentFlow):
    with pytest.raises(MandateScopeError, match="authorizes INR"):
        run_async(flow.authorize(_mandate(), session_id="s1", currency="USD"))


def test_mandate_for_another_cart_is_refused(flow: PaymentFlow):
    with pytest.raises(MandateScopeError, match="this cart"):
        run_async(
            flow.authorize(
                _mandate(), session_id="s1", expected_cart_id="gid://shopify/Cart/999"
            )
        )


def test_mandate_for_another_merchant_is_refused(flow: PaymentFlow):
    """The merchant field used to be unsigned; now it is both signed and checked."""
    with pytest.raises(MandateScopeError, match="this merchant"):
        run_async(
            flow.authorize(
                _mandate(), session_id="s1", expected_merchant="attacker.example.com"
            )
        )


def test_a_mandate_cannot_authorize_two_different_payments(flow: PaymentFlow):
    mandate = _mandate()
    run_async(flow.authorize(mandate, session_id="s1"))
    with pytest.raises(MandateAlreadyUsedError):
        run_async(flow.authorize(mandate, session_id="s2"))


def test_replaying_one_authorization_returns_the_same_payment(flow: PaymentFlow):
    mandate = _mandate()
    first = run_async(flow.authorize(mandate, session_id="s1"))
    second = run_async(flow.authorize(mandate, session_id="s1"))
    assert second.reference_id == first.reference_id


def test_capture_is_implemented_and_refused(flow: PaymentFlow):
    """The transition exists; moving real money does not."""
    mandate = _mandate()
    result = run_async(flow.authorize(mandate, session_id="s1"))
    with pytest.raises(CaptureNotEnabledError, match="never moves money"):
        run_async(flow.capture(result.idempotency_key))
