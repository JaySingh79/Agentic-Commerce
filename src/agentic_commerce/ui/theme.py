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
    max-width: 1360px !important;
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
/* Cap any stray Markdown image still rendered inside a chat bubble */
[class*="message"] img,
.prose img {
    max-width: 190px !important;
    height: auto !important;
    border-radius: 10px;
    margin: 6px 0 2px;
    display: block;
}

/* ---------------- Product result grid (gr.HTML panel, see ui/cards.py) ------
   Markdown images can only stack one-per-line, so results are rendered as a
   real CSS grid here: cards flow in blocks and reflow by available width. */
.ac-results { margin: 4px 0 14px; }
.ac-grid-head {
    font-weight: 600;
    font-size: 0.95rem;
    margin: 10px 0 2px;
    opacity: 0.9;
}
.ac-grid-note { font-size: 0.8rem; opacity: 0.65; margin-bottom: 8px; }
.ac-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
    align-items: stretch;
}
.ac-card {
    display: flex;
    flex-direction: column;
    text-decoration: none !important;
    color: inherit !important;
    background: rgba(17, 24, 39, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    overflow: hidden;
    transition: transform 0.12s ease, border-color 0.12s ease;
}
.ac-card:hover {
    transform: translateY(-2px);
    border-color: rgba(99, 102, 241, 0.6);
}
.ac-card-media {
    aspect-ratio: 1 / 1;
    background: #0f1626;
    display: flex;
    align-items: center;
    justify-content: center;
}
.ac-card-media img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    margin: 0 !important;
    max-width: none !important;
    border-radius: 0;
}
.ac-card-body { padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 3px; }
.ac-card-title {
    font-size: 0.85rem;
    font-weight: 600;
    line-height: 1.25;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
.ac-card-sub { font-size: 0.82rem; opacity: 0.8; }
.ac-card-badge {
    align-self: flex-start;
    font-size: 0.68rem;
    padding: 1px 7px;
    border-radius: 999px;
    background: rgba(99, 102, 241, 0.18);
    border: 1px solid rgba(99, 102, 241, 0.35);
    opacity: 0.9;
}
.ac-empty { opacity: 0.55; font-size: 0.88rem; padding: 10px 2px; }
/* Telemetry badge in chat */
.message-row blockquote {
    border-left: 3px solid #6366f1 !important;
    background: rgba(99, 102, 241, 0.08) !important;
    padding: 6px 12px !important;
    border-radius: 6px;
    font-size: 0.88rem !important;
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
.telemetry-card {
    background: rgba(31, 41, 55, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    padding: 8px 12px;
    margin-top: 10px;
    font-size: 0.85rem;
}
"""
