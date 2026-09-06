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
    position: relative;
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
/* Favicon-only web listings (§5.1). A 128px favicon stretched into a square
   photo slot is what made the web grid look ragged, so results with no real
   image get a deliberately different, compact silhouette: short letterboxed
   strip, icon shown at its own size, never upscaled. */
.ac-card-compact .ac-card-media {
    aspect-ratio: 5 / 2;
    background: #0b1120;
}
.ac-card-compact .ac-card-media img {
    width: auto;
    height: 44px;
    max-height: 44px;
    object-fit: contain;
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
/* The clickable region is the anchor *inside* the card, so the <details>
   disclosure below it can be interactive without nesting a control in a link. */
.ac-card-link {
    display: flex;
    flex-direction: column;
    text-decoration: none !important;
    color: inherit !important;
    flex: 1;
}
.ac-card-more {
    border-top: 1px solid rgba(255, 255, 255, 0.07);
    font-size: 0.76rem;
}
.ac-card-more > summary {
    cursor: pointer;
    padding: 5px 10px;
    opacity: 0.75;
    list-style: none;
    user-select: none;
}
.ac-card-more > summary::-webkit-details-marker { display: none; }
.ac-card-more > summary::after {
    content: " BE";
    opacity: 0.6;
}
.ac-card-more[open] > summary::after { content: " B4"; }
.ac-card-more > summary:hover { opacity: 1; }
.ac-card-more > summary:focus-visible {
    outline: 2px solid rgba(99, 102, 241, 0.9);
    outline-offset: -2px;
}
.ac-detail-list {
    padding: 2px 10px 9px;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.ac-detail-row { display: flex; gap: 6px; align-items: baseline; }
.ac-detail-key {
    flex: 0 0 auto;
    opacity: 0.6;
    min-width: 62px;
}
.ac-detail-val { flex: 1; opacity: 0.92; overflow-wrap: anywhere; }
/* Analyst's verdict, rendered on the product rather than only in the chat. */
.ac-card-best { border-color: rgba(234, 179, 8, 0.65); }
.ac-card-ribbon {
    position: absolute;
    top: 6px;
    left: 6px;
    z-index: 2;
    font-size: 0.66rem;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 999px;
    background: rgba(234, 179, 8, 0.92);
    color: #1f2937;
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
/* ---------------- Results rail (§3.1) -------------------------------------
   Results get their own column and follow the reader down it, so scrolling the
   transcript never scrolls the products out of reach. The rail falls back to
   normal flow on short viewports, where sticky would pin a taller-than-screen
   block and trap the scroll. */
#results-rail {
    position: sticky;
    top: 8px;
    align-self: flex-start;
    max-height: calc(100vh - 24px);
    overflow-y: auto;
    scrollbar-width: thin;
}
@media (max-width: 860px), (max-height: 620px) {
    #results-rail {
        position: static;
        max-height: none;
        overflow-y: visible;
    }
}

/* Product gallery styling. Height is viewport-relative rather than a fixed
   520px block, so the close-up view shrinks with the rail instead of forcing
   the page to scroll on a laptop (§3.3). */
#product-gallery {
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
    overflow: hidden;
    background: rgba(17,24,39,0.5);
    max-height: min(46vh, 420px);
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
