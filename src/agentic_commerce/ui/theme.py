"""Theme styling for the Gradio Chat Interface."""

import gradio as gr


def build_custom_theme() -> gr.Theme:
    """Builds a clean, modern, native dark theme."""
    return gr.themes.Ocean(
        primary_hue=gr.themes.colors.indigo,
        secondary_hue=gr.themes.colors.slate,
        neutral_hue=gr.themes.colors.slate,
        font=[
            gr.themes.GoogleFont("Inter"),
            "-apple-system",
            "BlinkMacSystemFont",
            "sans-serif",
        ],
    ).set(
        body_background_fill="#0b0f19",
        body_background_fill_dark="#0b0f19",
        block_background_fill="rgba(17, 24, 39, 0.7)",
        block_background_fill_dark="rgba(17, 24, 39, 0.7)",
        block_border_width="1px",
        block_border_color="rgba(255, 255, 255, 0.08)",
        block_radius="14px",
        input_background_fill="rgba(31, 41, 55, 0.5)",
        input_border_color="rgba(255, 255, 255, 0.12)",
        button_primary_background_fill="#6366f1",
        button_primary_background_fill_hover="#4f46e5",
        button_primary_text_color="#ffffff",
    )


CUSTOM_CSS = """
footer { visibility: hidden !important; }
.gradio-container {
    max-width: 1040px !important;
    margin: 0 auto !important;
}
/* Roomier, cleaner chat bubbles */
.message-row .message {
    line-height: 1.55 !important;
    font-size: 0.97rem !important;
}
/* Subtle divider + breathing room around the input row */
.gradio-container .form,
.gradio-container textarea {
    border-radius: 12px !important;
}
/* Tighten example prompt chips */
.gradio-container .examples button {
    border-radius: 999px !important;
    font-size: 0.85rem !important;
}
/* Cap product-preview images (Markdown) rendered in chat bubbles */
[class*="message"] img,
.prose img {
    max-width: 190px !important;
    height: auto !important;
    border-radius: 10px;
    margin: 6px 0 2px;
    display: block;
}
/* Product gallery styling */
#product-gallery {
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
    overflow: hidden;
    background: rgba(17,24,39,0.5);
}
#product-gallery .gallery-item img {
    border-radius: 10px;
}
.gallery-hint, .gallery-help {
    opacity: 0.85;
    font-size: 0.85rem;
}
"""
