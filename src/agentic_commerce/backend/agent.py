"""Autonomous Commerce Agent with dynamic conversational reasoning and UCP tool calling."""

import os
from collections.abc import Generator
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from agentic_commerce.backend.session import CommerceSession, get_or_create_session  # noqa: E402
from agentic_commerce.backend.tools import (  # noqa: E402
    ALL_COMMERCE_TOOLS,
    add_to_cart,
    checkout_cart,
    generate_ap2_mandate,
    get_product_details,
    search_products,
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
3. Product Details: Call `get_product_details` when the user asks about specific details
or materials.
4. Cart & Checkout: Call `add_to_cart` when the user chooses an item, and offer to prepare checkout
with `checkout_cart` or generate an AP2 mandate with `generate_ap2_mandate`.
5. Multi-turn Continuity: Always remember previously discussed items, active carts, and preferences.
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
            ).bind_tools(ALL_COMMERCE_TOOLS)
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

        if not self.llm:
            yield from self._heuristic_fallback_stream(user_query, normalized_history)
            return

        try:
            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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
            for chunk in self.llm.stream(messages):
                ai_msg = chunk if ai_msg is None else ai_msg + chunk
                delta = extract_text_content(chunk.content)
                if delta:
                    first_text += delta
                    yield {"type": "content", "text": delta}

            tool_calls = getattr(ai_msg, "tool_calls", None) if ai_msg else None

            # Step 2: If the model requested tools, run them and stream the synthesis.
            if tool_calls:
                tool_dict = {t.name: t for t in ALL_COMMERCE_TOOLS}
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

                    target_tool = tool_dict.get(tname)
                    if target_tool:
                        try:
                            result = target_tool.invoke(targs)
                        except Exception as err:
                            result = f"Tool error: {err}"
                    else:
                        result = f"Tool '{tname}' not found."

                    yield {"type": "tool_result", "tool": tname, "result": result}
                    # Structured media payload for UI gallery (preserves images across LLM rewrite)
                    if tname == "search_products":
                        products = _serialize_products(self.session.last_searched_products)
                        if products:
                            yield {"type": "products", "products": products}
                    elif tname == "get_product_details":
                        active = self.session.active_product
                        if active:
                            yield {"type": "products", "products": _serialize_products([active])}
                    messages.append(
                        ToolMessage(content=str(result), tool_call_id=tc.get("id", tname))
                    )

                final_text = ""
                for chunk in self.llm.stream(messages):
                    delta = extract_text_content(chunk.content)
                    if delta:
                        final_text += delta
                        yield {"type": "content", "text": delta}
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
                res = get_product_details.invoke({"product_id": target_product.get("id")})
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
                yield {
                    "type": "tool_call",
                    "tool": "add_to_cart",
                    "text": f"Creating Universal Cart for {title}...",
                }
                res = add_to_cart.invoke({
                    "merchant_domain": "shoppremiumoutlets.myshopify.com",
                    "variant_id": "gid://shopify/ProductVariant/45422478098492",
                    "quantity": 1,
                })
                self.session.add_message("assistant", res)
                yield {"type": "content", "text": res}
                return

        # 3. Checkout Active Cart
        elif any(w in lower for w in ["checkout", "finish purchase", "pay now"]):
            cart_id = (
                self.session.active_cart.get("id")
                if self.session.active_cart
                else "gid://shopify/Cart/hWNGDxX0T6FxogSKi6dDtDwC"
            )
            merchant = (
                self.session.active_cart.get("merchant_domain")
                if self.session.active_cart
                else "shoppremiumoutlets.myshopify.com"
            )

            yield {
                "type": "tool_call",
                "tool": "checkout_cart",
                "text": "Preparing checkout session...",
            }
            res = checkout_cart.invoke({
                "cart_id": cart_id,
                "merchant_domain": merchant,
                "buyer_email": "shopper@example.com",
            })
            self.session.add_message("assistant", res)
            yield {"type": "content", "text": res}
            return

        # 4. AP2 Payment Mandate
        elif any(w in lower for w in ["mandate", "ap2", "authorize"]):
            cart_id = (
                self.session.active_cart.get("id")
                if self.session.active_cart
                else "gid://shopify/Cart/hWNGDxX0T6FxogSKi6dDtDwC"
            )
            merchant = (
                self.session.active_cart.get("merchant_domain")
                if self.session.active_cart
                else "shoppremiumoutlets.myshopify.com"
            )

            yield {
                "type": "tool_call",
                "tool": "generate_ap2_mandate",
                "text": "Generating AP2 Payment Mandate...",
            }
            mandate_text = generate_ap2_mandate.invoke({
                "cart_id": cart_id,
                "amount_cents": 2400,
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
        result = search_products.invoke({"query": clean_query})
        yield {"type": "tool_result", "tool": "search_products", "result": result}
        products = _serialize_products(self.session.last_searched_products)
        if products:
            yield {"type": "products", "products": products}
        response_text = (
            f"Here are top matching products from the Shopify Global Catalog for "
            f"**\"{clean_query}\"**:\n\n"
            f"{result}\n\n"
            f"> 💡 **What would you like to do next?** You can say *'show details for item 1'*, "
            f"*'add it to my cart'*, or *'checkout'*."
        )
        self.session.add_message("assistant", response_text)
        yield {"type": "content", "text": response_text}
