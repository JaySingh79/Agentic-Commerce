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
    """Builds and returns the Gradio Blocks app with ChatInterface + product gallery.

    ChatInterface is kept for correct chat UX:
    - user bubbles are shown immediately
    - textbox clears after submit
    - each assistant turn is a separate bubble (no mixing)
    Gallery syncs from session state via chatbot.change (no double LLM).
    """
    if chat_engine is None:
        chat_engine = ChatEngine()

    def respond(
        message: str,
        history: list[dict[str, str]],
        model_name: str,
        temperature: float,
        system_prompt: str,
        request: gr.Request,
    ) -> Generator[str, None, None]:
        session_id = getattr(request, "session_hash", None) or "default_user_session"
        yield from chat_engine.stream_response(
            message=message,
            history=history,
            system_prompt=system_prompt,
            temperature=temperature,
            model_name=model_name,
            session_id=session_id,
        )

    # Gallery sync helper - reads session state written by agent tools
    def refresh_gallery(
        _chat_hist=None, request: gr.Request | None = None
    ) -> list[tuple[str, str]]:
        from agentic_commerce.backend.session import get_or_create_session
        from agentic_commerce.ui.chat_engine import get_gallery_items

        # Gradio injects request as keyword; _chat_hist is chatbot value when inputs=[chatbot]
        session_id = getattr(request, "session_hash", None) if request else None
        session_id = session_id or "default_user_session"
        session = get_or_create_session(session_id)
        return get_gallery_items(session)

    def clear_gallery_fn():
        return []

    chatbot = gr.Chatbot(
        height=620,
        show_label=False,
        placeholder=PLACEHOLDER_MD,
    )

    textbox = gr.Textbox(
        placeholder="Message Agentic Commerce (e.g., 'Find running shoes', 'AP2 mandate')...",  # noqa: E501
        container=False,
        scale=7,
        autofocus=True,
    )

    additional_inputs = [
        gr.Dropdown(
            choices=MODEL_CHOICES,
            value=MODEL_CHOICES[0],
            label="Active Model",
        ),
        gr.Slider(
            minimum=0.0,
            maximum=1.0,
            value=0.4,
            step=0.05,
            label="Temperature",
        ),
        gr.Textbox(
            value=DEFAULT_SYSTEM_PROMPT,
            label="System Directives",
            lines=6,
            max_lines=16,
        ),
    ]

    with gr.Blocks(title="Agentic Commerce Hub", fill_height=True) as demo:
        gr.Markdown(
            "# 🛍️ Agentic Commerce Hub\n*Autonomous AI Agent Stack (Layer 1 MCP • Layer 2 A2A • Layer 3 AP2)*"  # noqa: E501
        )
        with gr.Row():
            with gr.Column(scale=7):
                # ChatInterface handles user/message bubbles correctly
                gr.ChatInterface(
                    fn=respond,
                    chatbot=chatbot,
                    textbox=textbox,
                    title="Agentic Commerce Hub",
                    description="Autonomous AI Agent Stack (Layer 1 MCP • Layer 2 A2A • Layer 3 AP2)",  # noqa: E501
                    examples=EXAMPLES,
                    additional_inputs=additional_inputs,
                    additional_inputs_accordion=gr.Accordion(
                        label="⚙️ Agent & Generation Parameters",
                        open=False,
                    ),
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
                clear_btn.click(fn=clear_gallery_fn, outputs=[gallery])

        # Sync gallery whenever chatbot updates (after each stream chunk) - no double LLM
        chatbot.change(
            fn=refresh_gallery, inputs=[chatbot], outputs=[gallery], show_progress=False
        )

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
