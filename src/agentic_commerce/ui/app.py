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

from agentic_commerce.backend.session import (  # noqa: E402
    get_or_create_session,
    new_session_id,
)
from agentic_commerce.ui.cards import render_results_panel  # noqa: E402
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
    ["Search the web for niche trail runners the Shopify catalog doesn't carry"],
    ["Generate an AP2 checkout payment mandate for my active cart"],
    ["Show details and available variants for the first product"],
]

PLACEHOLDER_MD = """
# 🛍️ Agentic Commerce Hub
### Autonomous AI assistant for discovery, negotiation, and AP2 checkout.

Try one of the example prompts below or type your instruction.
"""


def resolve_session_id(stored: str | None) -> str:
    """Returns the durable session id for this browser, minting one on first visit.

    Gradio's ``request.session_hash`` is deliberately *not* used: it is regenerated on
    every page load, so keying commerce state on it meant a refresh orphaned the
    shopper's cart. The id lives in ``gr.BrowserState`` (browser local storage) and is
    the same capability token the HTTP API issues, so both front doors read one
    durable session (``backend/session_store.py``).
    """
    token = (stored or "").strip()
    return token or new_session_id()


def create_chat_app(chat_engine: ChatEngine | None = None) -> gr.Blocks:
    """Builds and returns the Gradio Blocks app with ChatInterface + product gallery.

    Features:
    - Native dual-stream synchronization via
      ``ChatInterface(additional_outputs=[gallery, results_panel, session_state])``
    - A durable, browser-stored session id, so a refresh keeps the cart
    - Results in their own rail rather than competing with the transcript for one column
    - OpenTelemetry observability with live token and latency metrics
    """
    if chat_engine is None:
        chat_engine = ChatEngine()

    def respond(
        message: str,
        history: list[dict[str, str]],
        model_name: str,
        temperature: float,
        system_prompt: str,
        session_token: str | None,
        request: gr.Request,
    ) -> Generator[tuple[str, list[tuple[str, str]], str, str], None, None]:
        session_id = resolve_session_id(session_token)
        for chat, gallery_items, panel in chat_engine.stream_with_gallery(
            message=message,
            history=history,
            system_prompt=system_prompt,
            temperature=temperature,
            model_name=model_name,
            session_id=session_id,
        ):
            # The id is echoed back on every chunk so BrowserState persists the one
            # minted on the first turn.
            yield chat, gallery_items, panel, session_id

    def clear_gallery_fn(session_token: str | None = None) -> tuple[list, str, str]:
        session_id = resolve_session_id(session_token)
        get_or_create_session(session_id).clear_search_results()
        return [], render_results_panel([], []), session_id

    with gr.Blocks(
        title="Agentic Commerce Hub",
        fill_height=True,
    ) as demo:
        gr.Markdown(
            "# 🛍️ Agentic Commerce Hub\n"
            "*Autonomous AI Agent Stack (Layer 1 MCP • Layer 2 A2A • Layer 3 AP2)*"
        )

        # Roles are split (§3.2): the results panel is the browsable set, this gallery
        # is the close-up view of whichever product is active. Column count and height
        # are left to CSS so the rail reflows instead of forcing a 520px block on a
        # phone (§3.3).
        gallery = gr.Gallery(
            label="Product Images",
            columns=2,
            object_fit="contain",
            preview=True,
            show_label=False,
            elem_id="product-gallery",
            render=False,
        )

        # Product imagery lives here as a CSS grid instead of stacked Markdown
        # images inside the chat bubble (see ui/cards.py for the escaping rules).
        results_panel = gr.HTML(
            value=render_results_panel([], []),
            elem_id="results-panel",
            render=False,
        )

        # Minted on the first turn, then kept in browser local storage so a refresh
        # returns to the same cart, mandate and negotiation.
        session_state = gr.BrowserState("", storage_key="ac_session_id")

        chatbot = gr.Chatbot(
            height=560,
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

        # Chat and results no longer share a column (§3.1): the transcript keeps the
        # left, everything produced by a turn lives in a sticky right rail.
        with gr.Row():
            with gr.Column(scale=6, min_width=380):
                gr.ChatInterface(
                    fn=respond,
                    chatbot=chatbot,
                    textbox=textbox,
                    examples=EXAMPLES,
                    additional_inputs=[*additional_inputs, session_state],
                    additional_outputs=[gallery, results_panel, session_state],
                    additional_inputs_accordion=gr.Accordion(
                        label="⚙️ Agent & Generation Parameters",
                        open=False,
                    ),
                )
            with gr.Column(scale=4, min_width=320, elem_id="results-rail"):
                gr.Markdown("### 🛍️ Results")
                results_panel.render()
                gr.Markdown("### 🖼️ Close-up")
                gr.Markdown(
                    "*Images for the active product — click to open the full-size view*",
                    elem_classes=["gallery-hint"],
                )
                gallery.render()
                clear_btn = gr.Button("Clear results", variant="secondary")
                clear_btn.click(
                    fn=clear_gallery_fn,
                    inputs=[session_state],
                    outputs=[gallery, results_panel, session_state],
                )

                with gr.Accordion("📊 Telemetry & Observability", open=False):
                    gr.Markdown(
                        "- **Grafana Dashboard:** [http://localhost:3000](http://localhost:3000)\n"
                        "- **OTLP Collector:** `:4318` → Tempo (traces) + "
                        "[Prometheus](http://localhost:9090) (metrics)\n"
                        "- **Monitored:** Token usage, LLM latency, tool spans\n"
                        "- Start with `docker compose -f docker-compose.telemetry.yml up -d`"
                    )

    return demo


def launch_chat_app(
    port: int = 7860,
    server_name: str = "127.0.0.1",
    share: bool = False,
    chat_engine: ChatEngine | None = None,
) -> None:
    """Creates the Gradio app and launches it with the custom theme and CSS.

    The theme and CSS are passed to ``.launch()``, not to the ``Blocks`` constructor.
    """
    app = create_chat_app(chat_engine=chat_engine)
    app.launch(
        server_name=server_name,
        server_port=port,
        share=share,
        theme=build_custom_theme(),
        css=CUSTOM_CSS,
        debug=True,
    )


if __name__ == "__main__":
    server_port = int(os.environ.get("PORT", 7860))
    launch_chat_app(port=server_port)
