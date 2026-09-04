"""Autonomous Commerce Agent with dynamic conversational reasoning and UCP tool calling."""

import os
from collections.abc import Generator
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from agentic_commerce.backend.session import CommerceSession, get_or_create_session  # noqa: E402
from agentic_commerce.backend.tools import make_commerce_tools  # noqa: E402
from agentic_commerce.core.telemetry import (  # noqa: E402
    estimate_tokens,
    record_llm_usage,
    trace_llm_call,
    trace_turn,
)

COMMERCE_SYSTEM_PROMPT = """You are Agentic Commerce, an intelligent, dynamic personal
shopping assistant and autonomous commerce agent powered by Shopify's Universal Commerce
Protocol (UCP), Model Context Protocol (MCP), and Agent Payment Protocol (AP2).

Your goals:
1. Dynamic Conversation & Advice: Chat naturally, understand buyer tastes, style preferences,
budgets, and needs. Be helpful, friendly, and conversational.
2. Real-Time Product Discovery: When the user asks for products, clothes, footwear, electronics,
or recommendations (e.g., 'i want a black jacket', 'find running shoes'), call `search_products`
to pull live items from the Shopify Global Catalog. Present matches with clear titles and prices.
3. Web Fallback: The Shopify catalog only covers participating merchants. If
`search_products` returns no matches, or the shopper asks to look beyond it (e.g. 'search
the web', a niche brand), call `search_web_products`. Always say when results came from the
open web and note that those listings cannot be added to a Universal Cart.
4. Product Details: Call `get_product_details` when the user asks about specific details
or materials.
5. Cart & Checkout: Call `add_to_cart` when the user chooses an item, and offer to prepare checkout
with `checkout_cart` or generate an AP2 mandate with `generate_ap2_mandate`.
6. Price Negotiation (A2A): If the shopper says a price is too high, asks for a discount, or gives a
budget under the listed price, call `negotiate_price`. A buyer agent negotiates with the merchant's
sales agent and never exceeds the stated budget. Report the agreed price and the saving.
7. Payment: Call `process_test_payment` to capture payment for an authorized cart or mandate. This
is always test-mode (Razorpay, else Stripe, else a simulated gateway) — say so plainly, and never
imply real money moved.
8. Multi-turn Continuity: Always remember previously discussed items, active carts, and preferences.

You coordinate a crew: you talk to the shopper while specialist agents (CatalogScout, WebScout,
Negotiator, Cashier) work in the background. Mention what the crew is doing when it is useful.
"""


def extract_text_content(content: Any) -> str:
    """Safely extracts plain string text from LangChain message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
            elif hasattr(item, "text"):
                parts.append(str(item.text))
        return "".join(parts)
    return str(content) if content is not None else ""


def _product_media(product: dict[str, Any]) -> list[dict[str, str]]:
    """Normalizes product media to list of {url, alt_text, type} per spec."""
    media: list[dict[str, str]] = []
    for item in product.get("media", []) or []:
        if isinstance(item, dict) and item.get("url"):
            media.append({
                "url": str(item["url"]),
                "alt_text": str(item.get("alt_text") or product.get("title", "")),
                "type": str(item.get("type") or "image"),
            })
    if media:
        return media
    # Fallback to featured variant image
    variants = product.get("variants", []) or []
    if variants and isinstance(variants[0], dict):
        featured = variants[0]
        image = featured.get("image")
        if isinstance(image, dict) and image.get("url"):
            media.append({
                "url": str(image["url"]),
                "alt_text": str(image.get("alt_text") or product.get("title", "")),
                "type": "image",
            })
        for item in featured.get("media", []) or []:
            if isinstance(item, dict) and item.get("url"):
                media.append({
                    "url": str(item["url"]),
                    "alt_text": str(item.get("alt_text") or product.get("title", "")),
                    "type": str(item.get("type") or "image"),
                })
    return media


def _serialize_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lightweight product serializer preserving media for UI consumption."""
    out: list[dict[str, Any]] = []
    for p in products[:5]:
        price = p.get("price_range", {}).get("min", {}).get("amount")
        if price is None:
            variants = p.get("variants", []) or []
            if variants and isinstance(variants[0], dict):
                price = variants[0].get("price", {}).get("amount")
        out.append({
            "id": p.get("id", ""),
            "title": p.get("title", "Product"),
            "price_range": p.get("price_range"),
            "price_cents": price,
            "media": _product_media(p),
            "variants": p.get("variants", [])[:3] if isinstance(p.get("variants"), list) else [],
        })
    return out


class CommerceAgent:
    """Orchestrates LLM reasoning, continuous conversation memory, and UCP tool execution."""

    DEFAULT_MODEL = os.getenv("MODEL") or "gemini-2.5-flash"

    def __init__(
        self,
        model_name: str | None = None,
        temperature: float = 0.4,
        api_key: str | None = None,
        session_id: str = "default_user_session",
    ):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.temperature = temperature
        self.session_id = session_id
        self.session: CommerceSession = get_or_create_session(session_id)
        # Tools are bound to this session id, so results land in the right session
        # regardless of which worker thread Gradio runs a generator step on.
        self.tools = make_commerce_tools(session_id)
        self.tool_map = {t.name: t for t in self.tools}
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        self.llm = self._init_llm()

    def _init_llm(self):
        """Initializes the LangChain Google GenAI chat model bound to the commerce tools."""
        if not self.api_key:
            return None
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=self.model_name,
                temperature=self.temperature,
                google_api_key=self.api_key,
            ).bind_tools(self.tools)
        except Exception:
            return None

    def _normalize_history(self, history: Any) -> list[dict[str, str]]:
        """Normalizes any Gradio history representation (list of dicts or list of tuples)."""
        normalized: list[dict[str, str]] = []
        if not history:
            return normalized

        for item in history:
            if isinstance(item, dict):
                role = item.get("role", "user")
                content = item.get("content", "")
                text = extract_text_content(content)
                normalized.append({"role": role, "content": text})
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                user_msg, bot_msg = item
                if user_msg:
                    normalized.append({"role": "user", "content": extract_text_content(user_msg)})
                if bot_msg:
                    normalized.append(
                        {"role": "assistant", "content": extract_text_content(bot_msg)}
                    )

        return normalized

    def execute_stream(
        self,
        message: str,
        history: Any = None,
        system_prompt: str = COMMERCE_SYSTEM_PROMPT,
    ) -> Generator[dict[str, Any], None, None]:
        """Executes the agent and yields streaming events with stateful memory."""
        from agentic_commerce.backend.session import set_active_session_id

        set_active_session_id(self.session_id)
        user_query = message.strip()
        normalized_history = self._normalize_history(history)

        self.session.history = normalized_history
        self.session.add_message("user", user_query)

        context_summary = self.session.get_context_summary()
        enriched_system_prompt = (
            f"{system_prompt}\n\n"
            f"=== CURRENT SESSION COMMERCE STATE ===\n"
            f"{context_summary}\n"
            f"======================================\n"
        )

        with trace_turn(self.session_id, user_query):
            if not self.llm:
                yield from self._heuristic_fallback_stream(user_query, normalized_history)
                return

            try:
                from langchain_core.messages import (
                    AIMessage,
                    HumanMessage,
                    SystemMessage,
                    ToolMessage,
                )

                messages = [SystemMessage(content=enriched_system_prompt)]
                for turn in normalized_history[-10:]:
                    role = turn.get("role", "")
                    content = turn.get("content", "")
                    if role == "user":
                        messages.append(HumanMessage(content=content))
                    elif role == "assistant":
                        messages.append(AIMessage(content=content))

                messages.append(HumanMessage(content=user_query))

                # Step 1: Stream the first LLM turn, aggregating tokens + tool_calls.
                yield {"type": "status", "text": "Thinking..."}
                ai_msg = None
                first_text = ""
                with trace_llm_call(
                    self.model_name or "gemini-2.5-flash", self.temperature, self.session_id
                ) as llm_span:
                    for chunk in self.llm.stream(messages):
                        ai_msg = chunk if ai_msg is None else ai_msg + chunk
                        delta = extract_text_content(chunk.content)
                        if delta:
                            first_text += delta
                            yield {"type": "content", "text": delta}

                    usage = getattr(ai_msg, "usage_metadata", None) or {}
                    prompt_toks = usage.get("input_tokens") or sum(
                        estimate_tokens(str(m.content)) for m in messages
                    )
                    comp_toks = usage.get("output_tokens") or estimate_tokens(first_text)
                    record_llm_usage(
                        llm_span,
                        prompt_toks,
                        comp_toks,
                        self.model_name or "gemini-2.5-flash",
                        self.session_id,
                    )

                tool_calls = getattr(ai_msg, "tool_calls", None) if ai_msg else None

                # Step 2: If the model requested tools, run them and stream the synthesis.
                if tool_calls:
                    messages.append(ai_msg)

                    for tc in tool_calls:
                        tname = tc.get("name", "")
                        targs = tc.get("args", {})
                        yield {
                            "type": "tool_call",
                            "tool": tname,
                            "args": targs,
                            "text": f"Running `{tname}`...",
                        }

                        target_tool = self.tool_map.get(tname)
                        if target_tool:
                            try:
                                result = target_tool.invoke(targs)
                            except Exception as err:
                                result = f"Tool error: {err}"
                        else:
                            result = f"Tool '{tname}' not found."

                        yield {"type": "tool_result", "tool": tname, "result": result}
                        # Structured media payload for UI gallery (preserves images)
                        if tname == "search_products":
                            products = _serialize_products(self.session.last_searched_products)
                            if products:
                                yield {"type": "products", "products": products}
                        elif tname == "get_product_details":
                            active = self.session.active_product
                            if active:
                                yield {
                                    "type": "products",
                                    "products": _serialize_products([active]),
                                }
                        messages.append(
                            ToolMessage(content=str(result), tool_call_id=tc.get("id", tname))
                        )

                    final_text = ""
                    with trace_llm_call(
                        self.model_name or "gemini-2.5-flash", self.temperature, self.session_id
                    ) as synth_span:
                        synth_msg = None
                        for chunk in self.llm.stream(messages):
                            synth_msg = chunk if synth_msg is None else synth_msg + chunk
                            delta = extract_text_content(chunk.content)
                            if delta:
                                final_text += delta
                                yield {"type": "content", "text": delta}

                        usage = getattr(synth_msg, "usage_metadata", None) or {}
                        prompt_toks = usage.get("input_tokens") or sum(
                            estimate_tokens(str(m.content)) for m in messages
                        )
                        comp_toks = usage.get("output_tokens") or estimate_tokens(final_text)
                        record_llm_usage(
                            synth_span,
                            prompt_toks,
                            comp_toks,
                            self.model_name or "gemini-2.5-flash",
                            self.session_id,
                        )

                    self.session.add_message("assistant", final_text)
                else:
                    self.session.add_message("assistant", first_text)

            except Exception as e:
                yield {
                    "type": "status",
                    "text": f"*(Note: LLM error, engaging fallback: {e})*",
                }
                yield from self._heuristic_fallback_stream(user_query, normalized_history)

    def _heuristic_fallback_stream(
        self, query: str, history: list[dict[str, str]]
    ) -> Generator[dict[str, Any], None, None]:
        """Dynamic conversational fallback dispatcher maintaining shopping context."""
        lower = query.lower()

        # 1. Detail Lookup from History
        if any(w in lower for w in ["detail", "lookup", "info", "inspect"]):
            target_product = None
            prods = self.session.last_searched_products
            if prods:
                if "2" in lower or "second" in lower:
                    target_product = prods[1] if len(prods) > 1 else None
                elif "3" in lower or "third" in lower:
                    target_product = prods[2] if len(prods) > 2 else None
                else:
                    target_product = prods[0]

            if target_product:
                title = target_product.get("title", "Product")
                yield {
                    "type": "tool_call",
                    "tool": "get_product_details",
                    "text": f"Fetching details for {title}...",
                }
                res = self.tool_map["get_product_details"].invoke(
                    {"product_id": target_product.get("id")}
                )
                active = self.session.active_product
                if active:
                    yield {"type": "products", "products": _serialize_products([active])}
                yield {"type": "tool_result", "tool": "get_product_details", "result": res}
                self.session.add_message("assistant", res)
                yield {"type": "content", "text": res}
                return

        # 2. Add to Cart from History
        elif any(w in lower for w in ["add to cart", "add it to cart", "cart", "buy this"]):
            target_product = (
                self.session.last_searched_products[0]
                if self.session.last_searched_products
                else None
            )

            if target_product:
                title = target_product.get("title", "Product")
                variants = target_product.get("variants") or []
                variant_id = (
                    variants[0].get("id")
                    if variants and isinstance(variants[0], dict)
                    else "gid://shopify/ProductVariant/45422478098492"
                )
                seller = target_product.get("seller") or (
                    variants[0].get("seller") if variants and isinstance(variants[0], dict) else {}
                )
                merchant = (
                    seller.get("domain") or seller.get("name") or "shoppremiumoutlets.myshopify.com"
                )
                yield {
                    "type": "tool_call",
                    "tool": "add_to_cart",
                    "text": f"Creating Universal Cart for {title}...",
                }
                res = self.tool_map["add_to_cart"].invoke({
                    "merchant_domain": merchant,
                    "variant_id": variant_id,
                    "quantity": 1,
                })
                self.session.add_message("assistant", res)
                yield {"type": "content", "text": res}
                return

        # 3. Checkout Active Cart
        elif any(w in lower for w in ["checkout", "finish purchase", "pay now"]):
            cart = self.session.active_cart or {}
            cart_id = cart.get("id") or "gid://shopify/Cart/hWNGDxX0T6FxogSKi6dDtDwC"
            merchant = cart.get("merchant_domain") or "shoppremiumoutlets.myshopify.com"

            yield {
                "type": "tool_call",
                "tool": "checkout_cart",
                "text": "Preparing checkout session...",
            }
            res = self.tool_map["checkout_cart"].invoke({
                "cart_id": cart_id,
                "merchant_domain": merchant,
                "buyer_email": "shopper@example.com",
            })
            self.session.add_message("assistant", res)
            yield {"type": "content", "text": res}
            return

        # 4. AP2 Payment Mandate
        elif any(w in lower for w in ["mandate", "ap2", "authorize"]):
            cart = self.session.active_cart or {}
            cart_id = cart.get("id") or "gid://shopify/Cart/hWNGDxX0T6FxogSKi6dDtDwC"
            merchant = cart.get("merchant_domain") or "shoppremiumoutlets.myshopify.com"
            totals = cart.get("totals", [])
            total_obj = next((t for t in totals if t.get("type") == "total"), None)
            amount_cents = total_obj.get("amount", 2400) if total_obj else 2400

            yield {
                "type": "tool_call",
                "tool": "generate_ap2_mandate",
                "text": "Generating AP2 Payment Mandate...",
            }
            mandate_text = self.tool_map["generate_ap2_mandate"].invoke({
                "cart_id": cart_id,
                "amount_cents": amount_cents,
                "currency": "USD",
                "merchant_domain": merchant,
            })
            self.session.add_message("assistant", mandate_text)
            yield {"type": "content", "text": mandate_text}
            return

        # 5. Dynamic Search for Any Product Query / Request
        clean = (
            query.replace("now i want a", "")
            .replace("i want a", "")
            .replace("i want", "")
            .replace("find me", "")
            .replace("show me", "")
            .replace("bro", "")
        )
        clean_query = clean.strip() or query

        yield {
            "type": "tool_call",
            "tool": "search_products",
            "text": f"Searching Shopify Global Catalog for '{clean_query}'...",
        }
        result = self.tool_map["search_products"].invoke({"query": clean_query})
        yield {"type": "tool_result", "tool": "search_products", "result": result}
        products = _serialize_products(self.session.last_searched_products)
        if products:
            yield {"type": "products", "products": products}
        else:
            # Catalog covers only participating Shopify merchants; widen to the web
            # rather than dead-ending the shopper on "no products found".
            yield {
                "type": "tool_call",
                "tool": "search_web_products",
                "text": f"No catalog match — searching the web for '{clean_query}'...",
            }
            web_result = self.tool_map["search_web_products"].invoke({"query": clean_query})
            yield {"type": "tool_result", "tool": "search_web_products", "result": web_result}
            result = f"{result}\n\n{web_result}"
        response_text = (
            f"Here are top matching products from the Shopify Global Catalog for "
            f"**\"{clean_query}\"**:\n\n"
            f"{result}\n\n"
            f"> 💡 **What would you like to do next?** You can say *'show details for item 1'*, "
            f"*'add it to my cart'*, or *'checkout'*."
        )
        with trace_llm_call("heuristic-fallback", 0.0, self.session_id) as span:
            record_llm_usage(
                span,
                estimate_tokens(query),
                estimate_tokens(response_text),
                "heuristic-fallback",
                self.session_id,
            )
        self.session.add_message("assistant", response_text)
        yield {"type": "content", "text": response_text}
