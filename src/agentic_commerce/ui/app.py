"""Production AI Chat Interface for Agentic Commerce with product image gallery."""

import os
import sys
from collections.abc import Generator
from pathlib import Path

# Add 'src' directory to sys.path if run directly as a script
_SRC_DIR = str(Path(__file__).resolve().parents[2])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import gradio as gr  # noqa: E402

from agentic_commerce.ui.chat_engine import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    ChatEngine,
)
from agentic_commerce.ui.theme import CUSTOM_CSS, build_custom_theme  # noqa: E402

# Real, selectable Gemini model ids (env MODEL first, then common tiers).
MODEL_CHOICES = list(dict.fromkeys([DEFAULT_MODEL, "gemini-2.5-flash", "gemini-2.5-pro"]))

EXAMPLES = [
    ["Search for high-performance running shoes under $150"],
    ["Generate an AP2 checkout payment mandate for my active cart"],
    ["Negotiate bulk discount with merchant supplier agent for order #402"],
    ["Show all autonomous agent tools and commerce protocols"],
]

PLACEHOLDER_MD = """
# 🛍️ Agentic Commerce Hub
### Autonomous AI assistant for discovery, negotiation, and AP2 checkout.

Try one of the example prompts below or type your instruction.
"""


def create_chat_app(chat_engine: ChatEngine | None = None) -> gr.Blocks:
    """Builds and returns the Gradio Blocks app with chat + product gallery."""
    if chat_engine is None:
        chat_engine = ChatEngine()

    def build_history_for_chatbot(
        history: list[dict[str, str]], pending_answer: str
    ) -> list[dict[str, str]]:
        """Converts messages history + pending assistant markdown into Gradio messages."""
        out = list(history) if history else []
        if pending_answer:
            out.append({"role": "assistant", "content": pending_answer})
        return out

    def stream_chat_with_gallery(
        message: str,
        history: list[dict[str, str]],
        model_name: str,
        temperature: float,
        system_prompt: str,
        request: gr.Request,
    ) -> Generator[tuple[list[dict[str, str]], list[tuple[str, str]]], None, None]:
        session_id = getattr(request, "session_hash", None) or "default_user_session"
        # Normalize history from Gradio messages format
        norm_history = history if isinstance(history, list) else []
        # Stream dual output
        final_gallery: list[tuple[str, str]] = []
        pending = ""
        for chat_md, gal in chat_engine.stream_with_gallery(
            message=message,
            history=norm_history,
            system_prompt=system_prompt,
            temperature=temperature,
            model_name=model_name,
            session_id=session_id,
        ):
            pending = chat_md
            final_gallery = gal
            yield build_history_for_chatbot(norm_history, pending), final_gallery
        # Ensure final frame
        if not pending:
            yield build_history_for_chatbot(norm_history, pending), final_gallery

    with gr.Blocks(title="Agentic Commerce Hub", fill_height=True) as demo:
        gr.Markdown(
            "# 🛍️ Agentic Commerce Hub\n*Autonomous AI Agent Stack (Layer 1 MCP • Layer 2 A2A • Layer 3 AP2)*"  # noqa: E501
        )
        gr.Markdown("Autonomous AI Agent Stack (Layer 1 MCP • Layer 2 A2A • Layer 3 AP2)")
        with gr.Row():
            with gr.Column(scale=7):
                chatbot = gr.Chatbot(
                    height=620,
                    show_label=False,
                    placeholder=PLACEHOLDER_MD,
                )
                with gr.Row():
                    textbox = gr.Textbox(
                        placeholder="Message Agentic Commerce (e.g., 'Find running shoes', "  # noqa: E501
                        "'AP2 mandate')...",
                        container=False,
                        scale=7,
                        autofocus=True,
                        show_label=False,
                    )
                    send_btn = gr.Button("Send", variant="primary", scale=1)
                gr.Examples(examples=EXAMPLES, inputs=[textbox], label="Try examples")
                with gr.Accordion(label="⚙️ Agent & Generation Parameters", open=False):
                    model_dd = gr.Dropdown(
                        choices=MODEL_CHOICES,
                        value=MODEL_CHOICES[0],
                        label="Active Model",
                    )
                    temp_sl = gr.Slider(
                        minimum=0.0,
                        maximum=1.0,
                        value=0.4,
                        step=0.05,
                        label="Temperature",
                    )
                    sys_tb = gr.Textbox(
                        value=DEFAULT_SYSTEM_PROMPT,
                        label="System Directives",
                        lines=6,
                        max_lines=16,
                    )
            with gr.Column(scale=3, min_width=280):
                gr.Markdown("### 🖼️ Product Gallery")
                gr.Markdown(  # noqa: E501
                    "*Images from `product.media[].url` — click to preview*",
                    elem_classes=["gallery-hint"],
                )
                gallery = gr.Gallery(
                    label="Product Images",
                    columns=2,
                    rows=3,
                    height=520,
                    object_fit="contain",
                    preview=True,
                    show_label=False,
                    elem_id="product-gallery",
                )
                gr.Markdown(
                    "> *Tip: search for products to populate this gallery. "
                    "Variant-specific images appear when you filter by color.*",
                    elem_classes=["gallery-help"],
                )
                clear_btn = gr.Button("Clear Gallery", variant="secondary")

        # Wire chat submit -> updates both chatbot and gallery
        textbox.submit(
            fn=stream_chat_with_gallery,
            inputs=[textbox, chatbot, model_dd, temp_sl, sys_tb],
            outputs=[chatbot, gallery],
        )
        send_btn.click(
            fn=stream_chat_with_gallery,
            inputs=[textbox, chatbot, model_dd, temp_sl, sys_tb],
            outputs=[chatbot, gallery],
        )

        def clear_gallery_fn():
            return []

        clear_btn.click(fn=clear_gallery_fn, outputs=[gallery])

        # Legacy compatibility: helper for tests that call respond directly  # noqa: E501
        # (not mounted — just ensures stream_response still works if imported)
        _ = chat_engine  # suppress unused

    return demo


def launch_chat_app(
    port: int = 7860,
    server_name: str = "127.0.0.1",
    share: bool = False,
    chat_engine: ChatEngine | None = None,
) -> None:
    """Creates and launches the Gradio chat application (theme/CSS applied on the Blocks)."""
    app = create_chat_app(chat_engine=chat_engine)
    app.launch(
        server_name=server_name,
        server_port=port,
        share=share,
        theme=build_custom_theme(),
        css=CUSTOM_CSS,
        debug=True
    )


if __name__ == "__main__":
    server_port = int(os.environ.get("PORT", 7860))
    launch_chat_app(port=server_port)
