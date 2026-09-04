"""Integration tests for Shopify UCP Client, AP2 Engine, Commerce Tools, and Multi-turn Memory."""

from agentic_commerce.backend.agent import CommerceAgent
from agentic_commerce.backend.ap2 import AP2Engine
from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.backend.tools import ALL_COMMERCE_TOOLS

generate_ap2_mandate = {t.name: t for t in ALL_COMMERCE_TOOLS}["generate_ap2_mandate"]


def test_ap2_engine_mandate_lifecycle():
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(
        cart_id="gid://shopify/Cart/test_cart_001",
        amount_cents=4500,
        currency="USD",
        merchant_domain="store.example.com",
    )

    assert mandate["mandate_id"].startswith("ap2_mandate_")
    assert mandate["amount_cents"] == 4500
    assert mandate["currency"] == "USD"
    assert mandate["status"] == "AUTHORIZED_PENDING_SETTLEMENT"
    assert "signature" in mandate

    # Valid signature check
    assert engine.verify_mandate(mandate) is True

    # Tampered mandate check
    tampered = dict(mandate)
    tampered["amount_cents"] = 9999
    assert engine.verify_mandate(tampered) is False


def test_ap2_mandate_formatting():
    engine = AP2Engine(secret_key="test_secret_123")
    mandate = engine.create_payment_mandate(
        cart_id="gid://shopify/Cart/test_cart_001",
        amount_cents=2400,
        currency="USD",
    )
    rendered = engine.format_mandate_display(mandate)
    assert "$24.00" in rendered
    assert "Agent Payment Protocol" in rendered
    assert mandate["mandate_id"] in rendered


def test_tools_registry_contains_ucp_and_ap2():
    tool_names = [t.name for t in ALL_COMMERCE_TOOLS]
    assert "search_products" in tool_names
    assert "get_product_details" in tool_names
    assert "add_to_cart" in tool_names
    assert "checkout_cart" in tool_names
    assert "generate_ap2_mandate" in tool_names


def test_ap2_tool_invocation():
    result = generate_ap2_mandate.invoke({
        "cart_id": "gid://shopify/Cart/test_cart_999",
        "amount_cents": 12900,
        "currency": "USD",
        "merchant_domain": "brand.myshopify.com",
    })
    assert "$129.00" in result
    assert "brand.myshopify.com" in result


def test_commerce_agent_stream_execution():
    agent = CommerceAgent()
    events = list(agent.execute_stream("generate an AP2 mandate for order"))
    assert len(events) > 0
    types = [e.get("type") for e in events]
    assert "content" in types or "tool_call" in types


def test_continuous_multi_turn_history_preservation():
    session_id = "test_multi_turn_session"
    session = get_or_create_session(session_id)
    agent = CommerceAgent(session_id=session_id)

    # Turn 1: Search products
    turn1_events = list(agent.execute_stream("Search for running shoes", history=[]))
    assert len(turn1_events) > 0
    assert len(session.history) >= 2

    # Turn 2: Follow-up question referring to previous turn
    history_after_t1 = list(session.history)
    turn2_events = list(
        agent.execute_stream("Show details for the first one", history=history_after_t1)
    )
    assert len(turn2_events) > 0
    assert len(session.history) >= 4

    # Turn 3: User requests cart / purchase
    history_after_t2 = list(session.history)
    turn3_events = list(
        agent.execute_stream("Add it to my cart", history=history_after_t2)
    )
    assert len(turn3_events) > 0
    assert len(session.history) >= 6

    # Turn 4: Checkout active cart
    history_after_t3 = list(session.history)
    turn4_events = list(
        agent.execute_stream("checkout now", history=history_after_t3)
    )
    assert len(turn4_events) > 0
    assert len(session.history) >= 8
