"""Phase 4 tests: AP2 HMAC mandate lifecycle, tamper detection, settlement capture."""

import pytest

from agentic_commerce.ap2.mandate import AP2Engine, MandateConfigurationError
from agentic_commerce.ap2.settlement import MockSettlementGateway, SettlementError
from agentic_commerce.core.models import MandateStatus


def test_mandate_lifecycle_and_tamper():
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(
        cart_id="gid://shopify/Cart/1", amount_cents=4500, currency="USD"
    )
    assert mandate["mandate_id"].startswith("ap2_mandate_")
    assert mandate["status"] == MandateStatus.AUTHORIZED_PENDING_SETTLEMENT.value
    assert engine.verify_mandate(mandate) is True

    tampered = dict(mandate)
    tampered["amount_cents"] = 9999
    assert engine.verify_mandate(tampered) is False


def test_settlement_capture_marks_settled_and_ledgers():
    engine = AP2Engine(secret_key="test_secret_123")
    gateway = MockSettlementGateway(engine)
    mandate = engine.create_payment_mandate(cart_id="gid://shopify/Cart/1", amount_cents=2400)

    receipt = gateway.capture(mandate)
    assert receipt["status"] == MandateStatus.SETTLED.value
    assert receipt["amount_cents"] == 2400
    assert receipt["settlement_id"].startswith("stl_")
    assert len(gateway.get_ledger()) == 1


def test_settlement_rejects_tampered_mandate():
    engine = AP2Engine(secret_key="test_secret_123")
    gateway = MockSettlementGateway(engine)
    mandate = engine.create_payment_mandate(cart_id="gid://shopify/Cart/1", amount_cents=2400)
    mandate["amount_cents"] = 1  # tamper after signing

    with pytest.raises(SettlementError):
        gateway.capture(mandate)
    assert gateway.get_ledger() == []


def test_merchant_domain_is_signed_and_cannot_be_redirected():
    """Regression: the old 5-field canonical string left merchant_domain unsigned,
    so a valid mandate could be pointed at a different merchant and still verify."""
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(
        cart_id="gid://shopify/Cart/1", amount_cents=2400, merchant_domain="shop.example.com"
    )
    mandate["merchant_domain"] = "attacker.example.com"
    assert engine.verify_mandate(mandate) is False


def test_spending_limit_is_signed_and_cannot_be_raised():
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(cart_id="gid://shopify/Cart/1", amount_cents=2400)
    mandate["spending_limit_cents"] = 10_000_000
    assert engine.verify_mandate(mandate) is False


def test_buyer_id_is_signed():
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(cart_id="gid://shopify/Cart/1", amount_cents=2400)
    mandate["buyer_id"] = "someone_else"
    assert engine.verify_mandate(mandate) is False


def test_expired_mandate_does_not_verify():
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(
        cart_id="gid://shopify/Cart/1", amount_cents=2400, max_duration_seconds=-1
    )
    assert engine.verify_mandate(mandate) is False


def test_strict_mode_refuses_the_placeholder_signing_key(monkeypatch):
    """A key published in this repository is not a signature."""
    monkeypatch.delenv("CLIENT_SECRET", raising=False)
    monkeypatch.setenv("AC_PAYMENTS_STRICT", "1")
    with pytest.raises(MandateConfigurationError):
        AP2Engine()


def test_a_mandate_settles_only_once():
    engine = AP2Engine(secret_key="test_secret_123")
    gateway = MockSettlementGateway(engine)
    mandate = engine.create_payment_mandate(cart_id="gid://shopify/Cart/1", amount_cents=2400)

    gateway.capture(mandate)
    with pytest.raises(SettlementError, match="already"):
        gateway.capture(mandate)
