"""Phase 4 tests: AP2 HMAC mandate lifecycle, tamper detection, settlement capture."""

import pytest

from agentic_commerce.ap2.mandate import AP2Engine
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
