"""Tests for the inline mandate/receipt tickets.

A mandate and a payment receipt are the two artifacts a turn produces that carry
an identity, an amount, an expiry and a signature. They are emitted as `artifact`
SSE events and rendered in the transcript as interactive tickets, so both halves
need cover: that the server emits them exactly once per authorization, and that
the rendered ticket never hides whether real money moved.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentic_commerce.api import server as api
from agentic_commerce.backend.session import get_or_create_session

HARNESS = Path(__file__).resolve().parent / "js" / "markdown_harness.mjs"


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.create_app(serve_frontend=False))


def sse_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def build_ticket(kind: str, data: dict[str, Any]) -> dict[str, str]:
    """Builds one ticket through the shipped frontend code."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["node", str(HARNESS)],
        input=json.dumps({"ticket": {"kind": kind, "data": data}}) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


# ------------------------------------------------------------------ the wire


def test_a_mandate_created_in_a_turn_arrives_as_an_artifact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class MandateAgent:
        def __init__(self, session_id: str = "", **_: Any) -> None:
            self.session_id = session_id

        def execute_stream(self, **_: Any):
            get_or_create_session(self.session_id).update_mandate(
                {"mandate_id": "ap2_1", "amount_cents": 4000, "currency": "USD"}
            )
            yield {"type": "content", "text": "Mandate ready."}

    monkeypatch.setattr(api, "CommerceAgent", MandateAgent)

    events = sse_events(
        client.post(
            "/api/chat/stream", json={"message": "authorize", "session_id": "tix_mandate"}
        ).text
    )
    artifacts = [e for e in events if e["type"] == "artifact"]

    assert [a["kind"] for a in artifacts] == ["mandate"]
    assert artifacts[0]["data"]["mandate_id"] == "ap2_1"


def test_a_payment_receipt_arrives_and_states_that_no_money_moved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class PayingAgent:
        def __init__(self, session_id: str = "", **_: Any) -> None:
            self.session_id = session_id

        def execute_stream(self, **_: Any):
            get_or_create_session(self.session_id).update_payment(
                {"provider": "simulated", "status": "captured", "live": False}
            )
            yield {"type": "content", "text": "Paid."}

    monkeypatch.setattr(api, "CommerceAgent", PayingAgent)

    events = sse_events(
        client.post("/api/chat/stream", json={"message": "pay", "session_id": "tix_pay"}).text
    )
    artifact = next(e for e in events if e["type"] == "artifact")

    assert artifact["kind"] == "payment"
    # `live` must survive the trip; the badge is rendered from it.
    assert artifact["data"]["live"] is False


def test_a_later_turn_does_not_re_emit_an_existing_mandate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The ticket marks the moment of authorization, not the fact one exists."""
    get_or_create_session("tix_once").update_mandate({"mandate_id": "ap2_old"})

    class QuietAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "content", "text": "Nothing to do."}

    monkeypatch.setattr(api, "CommerceAgent", QuietAgent)

    events = sse_events(
        client.post("/api/chat/stream", json={"message": "hi", "session_id": "tix_once"}).text
    )

    assert [e for e in events if e["type"] == "artifact"] == []


# --------------------------------------------------------------- verification


def test_verifying_a_real_mandate_confirms_it(client: TestClient):
    mandate = client.post(
        "/api/mandate", json={"cart_id": "cart_1", "amount_cents": 5000}
    ).json()

    result = client.post("/api/mandate/verify", json=mandate).json()

    assert result["valid"] is True
    assert result["seconds_remaining"] > 0


def test_a_tampered_amount_fails_verification_and_says_why(client: TestClient):
    mandate = client.post(
        "/api/mandate", json={"cart_id": "cart_1", "amount_cents": 5000}
    ).json()
    mandate["amount_cents"] = 50

    result = client.post("/api/mandate/verify", json=mandate).json()

    assert result["valid"] is False
    assert "signature" in result["reason"]


def test_an_expired_mandate_is_named_as_expired_not_as_tampered(client: TestClient):
    mandate = client.post(
        "/api/mandate", json={"cart_id": "cart_1", "amount_cents": 5000}
    ).json()
    mandate["expires_at"] = int(time.time()) - 10

    result = client.post("/api/mandate/verify", json=mandate).json()

    assert result["valid"] is False
    assert "expired" in result["reason"].lower()


# -------------------------------------------------------------- the rendering

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytestmark_node
def test_a_receipt_ticket_always_carries_a_mode_badge():
    out = build_ticket(
        "payment",
        {
            "provider": "simulated",
            "status": "captured",
            "reference_id": "pay_1",
            "amount_cents": 2400,
            "currency": "USD",
            "live": False,
        },
    )
    assert "TEST MODE" in out["text"]
    assert "24.00 USD" in out["text"]


@pytestmark_node
def test_a_live_receipt_is_visually_distinct_from_a_test_one():
    """If a live charge ever reaches this UI it must not look like the test badge."""
    out = build_ticket("payment", {"live": True, "amount_cents": 100, "currency": "USD"})
    assert "t-mode live" in out["html"]
    assert "LIVE" in out["text"]


@pytestmark_node
def test_a_mandate_ticket_shows_its_expiry_counting_down():
    out = build_ticket(
        "mandate",
        {
            "mandate_id": "ap2_1",
            "cart_id": "cart_1",
            "amount_cents": 5000,
            "currency": "USD",
            "expires_at": int(time.time()) + 600,
            "signature": "a" * 64,
            "status": "PENDING",
        },
    )
    assert "left" in out["text"]
    assert "Verify signature" in out["text"]


@pytestmark_node
def test_an_expired_mandate_ticket_says_so_on_arrival():
    out = build_ticket(
        "mandate", {"mandate_id": "ap2_1", "expires_at": int(time.time()) - 5}
    )
    assert "expired" in out["text"]


@pytestmark_node
def test_a_ticket_never_turns_its_payload_into_markup():
    """Mandate fields are server-signed, but the renderer must not assume that."""
    out = build_ticket(
        "mandate",
        {"mandate_id": "<img src=x onerror=alert(1)>", "merchant_domain": "</article><b>x"},
    )
    assert "<img" not in out["html"]
    assert "<b>" not in out["html"]


@pytestmark_node
def test_a_receipt_url_that_is_not_http_never_becomes_a_link():
    out = build_ticket(
        "payment", {"live": False, "receipt_url": "javascript:alert(1)", "amount_cents": 1}
    )
    assert "Open receipt" not in out["text"]
