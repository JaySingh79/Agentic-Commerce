"""Tests for the REST provider clients themselves, over ``httpx.MockTransport``.

These paths had no coverage at all: the request bodies, the ``>= 400`` branches and the
signature helpers were exercised only by hand. Everything here is offline — the
transport is a function, not a socket.
"""

import httpx
import pytest

from agentic_commerce.core.runtime import run_async
from agentic_commerce.payments.models import (
    ProviderRejectedError,
    ProviderTransientError,
    ProviderUnknownError,
)
from agentic_commerce.payments.providers.razorpay import (
    RazorpayProvider,
    verify_payment_signature,
    verify_webhook_signature,
)
from agentic_commerce.payments.providers.stripe import StripeProvider
from agentic_commerce.payments.settings import PaymentSettings, redact


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "supersecret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler):
    """Routes every ``httpx.AsyncClient`` in the payments layer to ``handler``."""
    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def test_razorpay_create_order_sends_the_receipt_and_maps_the_response(
    monkeypatch: pytest.MonkeyPatch,
):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "id": "order_R1",
                "amount": 4500,
                "currency": "INR",
                "status": "created",
                "receipt": "cart_1",
            },
        )

    _mock_client(monkeypatch, handler)
    result = run_async(RazorpayProvider().create_order(4500, "INR", "cart_1"))

    assert "cart_1" in seen["body"]
    assert result.reference_id == "order_R1"
    assert result.live is False
    assert result.idempotency_key == "cart_1"


def test_razorpay_4xx_is_a_rejection_and_redacts_the_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request for key rzp_test_abc / supersecret")

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderRejectedError) as excinfo:
        run_async(RazorpayProvider().create_order(4500, "INR", "cart_1"))

    assert "supersecret" not in str(excinfo.value)
    assert "rzp_test_abc" not in str(excinfo.value)


def test_razorpay_5xx_is_unknown_not_rejected(monkeypatch: pytest.MonkeyPatch):
    """A 502 may still have created the order, so it must not license a fallback."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderUnknownError):
        run_async(RazorpayProvider().create_order(4500, "INR", "cart_1"))


def test_connection_failure_is_transient(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderTransientError):
        run_async(RazorpayProvider().create_order(4500, "INR", "cart_1"))


def test_read_timeout_is_unknown(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer")

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderUnknownError):
        run_async(RazorpayProvider().create_order(4500, "INR", "cart_1"))


def test_razorpay_reconciliation_finds_an_existing_order(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["receipt"] == "cart_1"
        return httpx.Response(200, json={"items": [{"id": "order_R1", "amount": 4500}]})

    _mock_client(monkeypatch, handler)
    found = run_async(RazorpayProvider().find_by_receipt("cart_1"))
    assert found is not None
    assert found.reference_id == "order_R1"


def test_razorpay_reconciliation_reports_absence(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    _mock_client(monkeypatch, handler)
    assert run_async(RazorpayProvider().find_by_receipt("cart_1")) is None


def test_stripe_sends_an_idempotency_key(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["idempotency"] = request.headers.get("Idempotency-Key")
        return httpx.Response(
            200,
            json={
                "id": "pi_1",
                "amount": 4500,
                "currency": "usd",
                "status": "requires_payment_method",
                "livemode": False,
            },
        )

    _mock_client(monkeypatch, handler)
    result = run_async(StripeProvider().create_order(4500, "USD", "cart_1"))

    assert seen["idempotency"] == "cart_1"
    assert result.reference_id == "pi_1"
    assert result.currency == "USD"


def test_a_livemode_response_under_test_keys_is_refused(monkeypatch: pytest.MonkeyPatch):
    """Contradiction between our credentials and the provider is not silently resolved."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "pi_1", "amount": 4500, "currency": "usd", "livemode": True},
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(Exception, match="live transaction under test credentials"):
        run_async(StripeProvider().create_order(4500, "USD", "cart_1"))


def test_payment_signature_verification():
    secret = "supersecret"
    import hashlib
    import hmac

    signature = hmac.new(
        secret.encode(), b"order_1|pay_1", hashlib.sha256
    ).hexdigest()

    assert verify_payment_signature("order_1", "pay_1", signature, secret) is True
    assert verify_payment_signature("order_1", "pay_2", signature, secret) is False


def test_webhook_signature_verification():
    import hashlib
    import hmac

    body = b'{"event":"order.paid"}'
    secret = "whsec_test"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(body, signature, secret) is True
    assert verify_webhook_signature(b'{"event":"other"}', signature, secret) is False


def test_redaction_covers_keys_and_named_secrets():
    text = "auth failed for rzp_test_abc with sk_test_xyz and supersecret"
    cleaned = redact(text, "supersecret")
    assert "rzp_test_abc" not in cleaned
    assert "sk_test_xyz" not in cleaned
    assert "supersecret" not in cleaned


def test_settings_never_reports_live_under_the_guard():
    settings = PaymentSettings.load()
    assert settings.derive_live() is False
