/* Agentic Commerce front end.
 *
 * Consumes the SSE stream documented in openapi.json and renders four surfaces:
 * the agent timeline, the answer, the catalog/web result bands, and the Analyst's
 * comparison drawer.
 *
 * Security note: every value on screen may originate from a web search, which is
 * attacker-influenced text. Nothing here uses innerHTML with response data —
 * content goes in through textContent and attributes are set individually, with
 * URLs passed through safeUrl() so a `javascript:` href can never be assembled.
 */

const API = "";
const SESSION_KEY = "ac_session_id";
const state = {
  sessionId: null,
  streaming: false,
  controller: null,
  bestPick: null,
  products: [],
  web: [],
  cart: null,
  lastQuery: "",
  busy: false,
};

/* The session id is a capability token for a durable, server-side session, so it is
 * kept for the next visit rather than re-minted on every load — a refresh mid-checkout
 * used to lose the cart. The server also sets an HttpOnly cookie as a second path,
 * for the case where local storage is unavailable or cleared. */
function loadSessionId() {
  try {
    return window.localStorage.getItem(SESSION_KEY) || null;
  } catch {
    return null;
  }
}

function rememberSessionId(id) {
  if (!id) return;
  state.sessionId = id;
  try {
    window.localStorage.setItem(SESSION_KEY, id);
  } catch {
    /* Private browsing: the cookie is the fallback. */
  }
}

const el = (id) => document.getElementById(id);
const transcript = el("transcript");
const openingScreen = el("opening").cloneNode(true);

/* --------------------------------------------------------------- utilities */

/** Returns url only when it is an ordinary http(s) link, else null. */
function safeUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function money(amount, currency) {
  if (amount == null) return "";
  const value = (Number(amount) / 100).toFixed(2);
  return currency ? `${value} ${currency}` : `$${value}`;
}

function seconds(value) {
  return value == null ? "" : `${Number(value).toFixed(1)}s`;
}

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

/** The rating shape lives on the product for some merchants, the variant for others. */
function ratingOf(product) {
  if (product.rating && product.rating.value != null) return product.rating;
  for (const variant of product.variants || []) {
    if (variant.rating && variant.rating.value != null) return variant.rating;
  }
  return null;
}

/** Reads the fabric from the merchant's spec block, mirroring analyst.extract_fabric. */
function fabricOf(product) {
  const meta = product.metadata || {};
  const text = [meta.tech_specs, meta.top_features, plainText(product.description)]
    .filter(Boolean)
    .join("\n");
  const match = /(?:fabric|material)\s*:\s*([^\n\r]+)/i.exec(text);
  return match ? match[1].trim() : "";
}

function plainText(description) {
  if (!description) return "";
  return typeof description === "string" ? description : description.plain || "";
}

function priceOf(product) {
  const min = (product.price_range || {}).min;
  return min ? money(min.amount, min.currency) : "";
}

/* ------------------------------------------------------------ agent timeline */

/** One collapsible group of specialist steps, keyed by agent name. */
function createTimeline() {
  const wrap = node("div", "timeline");
  wrap.dataset.open = "false";

  const summary = node("button", "timeline-summary");
  summary.type = "button";
  summary.setAttribute("aria-expanded", "false");
  const chev = node("span", "chev", "›");
  const label = node("strong", null, "Working");
  const tail = node("span", null, "");
  summary.append(chev, label, tail);

  const rows = node("div", "timeline-rows");
  rows.setAttribute("role", "log");
  rows.setAttribute("aria-live", "polite");

  summary.addEventListener("click", () => {
    const open = wrap.dataset.open === "true";
    wrap.dataset.open = open ? "false" : "true";
    summary.setAttribute("aria-expanded", String(!open));
  });

  wrap.append(summary, rows);
  wrap.dataset.open = "true";
  summary.setAttribute("aria-expanded", "true");

  const steps = new Map();
  const started = performance.now();

  function upsert(agent, status, detail, duration, log) {
    let step = steps.get(agent);
    if (!step) {
      step = node("div", "step");
      const mark = node("span", "step-mark", "•");
      const body = node("span", "step-body");
      const name = node("span", "step-agent", agent);
      const text = node("span", "step-detail", "");
      body.append(name, document.createTextNode("  "), text);
      const time = node("span", "step-time", "");
      step.append(mark, body, time);
      steps.set(agent, step);
      rows.append(step);
    }
    step.dataset.status = status;
    step.querySelector(".step-mark").textContent =
      status === "done" ? "✓" : status === "error" ? "✕" : "•";
    step.querySelector(".step-detail").textContent = detail || "";
    step.querySelector(".step-time").textContent = seconds(duration);

    if (log) {
      let block = step.parentElement.querySelector(`[data-log="${CSS.escape(agent)}"]`);
      if (!block) {
        block = node("pre", "step-log");
        block.dataset.log = agent;
        block.hidden = true;
        step.after(block);
        step.style.cursor = "pointer";
        step.addEventListener("click", () => {
          block.hidden = !block.hidden;
        });
      }
      block.textContent = log;
    }
    return step;
  }

  function finish(summaryText) {
    const elapsed = (performance.now() - started) / 1000;
    label.textContent = summaryText || "Done";
    tail.textContent = ` ${elapsed.toFixed(1)}s`;

    // A failed specialist keeps the group open: collapsing it would hide the one
    // thing the shopper needs to see.
    const failed = [...steps.values()].some((s) => s.dataset.status === "error");
    if (!failed) {
      setTimeout(() => {
        wrap.dataset.open = "false";
        summary.setAttribute("aria-expanded", "false");
      }, 400);
    }
  }

  return { element: wrap, upsert, finish, count: () => steps.size };
}

/* --------------------------------------------------------------- transcript */

function startTurn(question) {
  el("opening")?.remove();

  const turn = node("section", "turn");
  turn.append(node("p", "turn-you", question));

  const timeline = createTimeline();
  const answer = node("div", "answer streaming");
  turn.append(timeline.element, answer);
  transcript.append(turn);
  scrollToEnd();

  return { turn, timeline, answer };
}

let pinned = true;
transcript.addEventListener("scroll", () => {
  const gap = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight;
  pinned = gap < 60;
  el("jump").hidden = pinned;
});

function scrollToEnd(force) {
  if (pinned || force) transcript.scrollTop = transcript.scrollHeight;
}

el("jump-btn").addEventListener("click", () => {
  pinned = true;
  scrollToEnd(true);
  el("jump").hidden = true;
});

/* ---------------------------------------------------------------- markdown

   The model answers in Markdown, so rendering it as plain text left `**bold**`
   and `* ` bullets visible on screen. This is a deliberately small subset —
   headings, lists, blockquotes, fenced code, bold/italic/code/links — built
   with createElement and textContent only.

   No innerHTML anywhere: an answer routinely quotes web-search titles and
   snippets, which are attacker-influenced, and a Markdown-to-HTML string would
   hand that text a route onto the page as markup. Link hrefs go through
   safeUrl(), so `[click](javascript:...)` renders as inert text. */

const INLINE_RE =
  /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))/g;

/** Appends *text* to *parent*, converting inline Markdown spans to elements. */
function appendInline(parent, text) {
  for (const piece of text.split(INLINE_RE)) {
    if (!piece) continue;

    const link = piece.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);

    if (/^\*\*[^*]+\*\*$/.test(piece) || /^__[^_]+__$/.test(piece)) {
      parent.append(node("strong", null, piece.slice(2, -2)));
    } else if (/^\*[^*]+\*$/.test(piece) || /^_[^_]+_$/.test(piece)) {
      parent.append(node("em", null, piece.slice(1, -1)));
    } else if (/^`[^`]+`$/.test(piece)) {
      parent.append(node("code", null, piece.slice(1, -1)));
    } else if (link) {
      const href = safeUrl(link[2]);
      if (href) {
        const anchor = node("a", null, link[1]);
        anchor.href = href;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer nofollow";
        parent.append(anchor);
      } else {
        // A link we will not follow is shown as text, not silently dropped.
        parent.append(document.createTextNode(link[1] + " (" + link[2] + ")"));
      }
    } else {
      parent.append(document.createTextNode(piece));
    }
  }
}

/** Replaces the contents of *host* with a rendering of the Markdown in *text*. */
function renderMarkdown(text, host) {
  const blocks = [];
  let fence = null;

  for (const line of text.split("\n")) {
    const fenced = line.match(/^\s*```/);
    if (fenced) {
      if (fence) {
        blocks.push({ type: "code", lines: fence });
        fence = null;
      } else {
        fence = [];
      }
      continue;
    }
    if (fence) {
      fence.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const quote = line.match(/^\s*>\s?(.*)$/);
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const ordered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    const last = blocks[blocks.length - 1];

    if (!line.trim()) {
      blocks.push({ type: "break" });
    } else if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
    } else if (bullet || ordered) {
      const kind = bullet ? "ul" : "ol";
      const item = bullet ? bullet[1] : ordered[2];
      if (last && last.type === "list" && last.kind === kind) last.items.push(item);
      else blocks.push({ type: "list", kind, items: [item] });
    } else if (quote) {
      if (last && last.type === "quote") last.lines.push(quote[1]);
      else blocks.push({ type: "quote", lines: [quote[1]] });
    } else if (last && last.type === "paragraph") {
      last.lines.push(line);
    } else {
      blocks.push({ type: "paragraph", lines: [line] });
    }
  }
  // An unterminated fence is mid-stream; show what has arrived so far.
  if (fence) blocks.push({ type: "code", lines: fence });

  const fragment = document.createDocumentFragment();
  for (const block of blocks) {
    if (block.type === "break") continue;

    if (block.type === "heading") {
      const heading = node("h" + Math.min(block.level + 2, 6), "answer-h");
      appendInline(heading, block.text);
      fragment.append(heading);
    } else if (block.type === "list") {
      const list = document.createElement(block.kind);
      for (const item of block.items) {
        const li = document.createElement("li");
        appendInline(li, item);
        list.append(li);
      }
      fragment.append(list);
    } else if (block.type === "quote") {
      const quote = document.createElement("blockquote");
      appendInline(quote, block.lines.join(" "));
      fragment.append(quote);
    } else if (block.type === "code") {
      const pre = document.createElement("pre");
      pre.append(node("code", null, block.lines.join("\n")));
      fragment.append(pre);
    } else {
      const paragraph = document.createElement("p");
      appendInline(paragraph, block.lines.join(" "));
      fragment.append(paragraph);
    }
  }
  host.replaceChildren(fragment);
}

/* ---------------------------------------------------------------- tickets

   A mandate and a receipt are the two things a turn produces that are objects,
   not sentences: they have an identity, a value, an expiry, and a signature you
   might want to check. Rendering them as prose loses all of that, so they are
   emitted as `artifact` events and rendered inline in the transcript as a
   ticket the shopper can expand, verify, and copy.

   Honesty rules, both structural rather than worded: a receipt always states
   whether real money moved (`live`), and a mandate always shows its own expiry
   counting down, because an authorization the shopper believes is live but is
   not is the failure that matters here. */

const tickets = new Set();

function ticketRow(list, label, value, mono) {
  if (value == null || value === "") return;
  const row = node("div", "t-row");
  row.append(node("span", "t-key", label));
  row.append(node("span", mono ? "t-val mono" : "t-val", String(value)));
  list.append(row);
}

function copyButton(label, payload) {
  const button = node("button", "t-act", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    const text = JSON.stringify(payload, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      // Clipboard is permission-gated; falling back to selection beats failing silently.
      button.textContent = "Press Ctrl+C";
      const area = document.createElement("textarea");
      area.value = text;
      document.body.append(area);
      area.select();
    }
    setTimeout(() => (button.textContent = label), 1600);
  });
  return button;
}

function remaining(expiresAt) {
  const left = Math.max(Math.floor(expiresAt - Date.now() / 1000), 0);
  if (!left) return "expired";
  const minutes = Math.floor(left / 60);
  const seconds = left % 60;
  return minutes ? minutes + "m " + seconds + "s left" : seconds + "s left";
}

/** Builds the inline ticket for one artifact. */
function buildTicket(kind, data) {
  const ticket = node("article", "ticket ticket-" + kind);
  const head = node("div", "t-head");

  const isMandate = kind === "mandate";
  head.append(node("span", "t-kind", isMandate ? "AP2 payment mandate" : "Payment receipt"));

  // A receipt that does not say whether money moved is the one thing this UI
  // must never render, so the badge is built before anything optional.
  if (!isMandate) {
    const live = data.live === true;
    head.append(node("span", "t-mode" + (live ? " live" : ""), live ? "LIVE" : "TEST MODE"));
  } else {
    head.append(node("span", "t-mode", String(data.status || "PENDING")));
  }
  ticket.append(head);

  const amountCents = Number(data.amount_cents);
  if (Number.isFinite(amountCents)) {
    ticket.append(node("p", "t-amount", money(amountCents, data.currency)));
  }

  const rows = node("div", "t-rows");
  if (isMandate) {
    ticketRow(rows, "Mandate", data.mandate_id, true);
    ticketRow(rows, "Cart", data.cart_id, true);
    ticketRow(rows, "Merchant", data.merchant_domain);
    if (Number(data.spending_limit_cents) !== amountCents) {
      ticketRow(rows, "Limit", money(data.spending_limit_cents, data.currency));
    }
    ticketRow(rows, "Signature", String(data.signature || "").slice(0, 24) + "...", true);
  } else {
    ticketRow(rows, "Reference", data.reference_id, true);
    ticketRow(rows, "Provider", data.provider);
    ticketRow(rows, "Status", data.status);
  }
  ticket.append(rows);

  if (isMandate && data.expires_at) {
    const clock = node("p", "t-clock", remaining(data.expires_at));
    ticket.append(clock);
    const tick = setInterval(() => {
      if (!clock.isConnected) {
        clearInterval(tick);
        tickets.delete(tick);
        return;
      }
      const text = remaining(data.expires_at);
      clock.textContent = text;
      if (text === "expired") {
        clock.classList.add("gone");
        ticket.classList.add("expired");
        clearInterval(tick);
        tickets.delete(tick);
      }
    }, 1000);
    tickets.add(tick);
  }

  const actions = node("div", "t-acts");
  if (isMandate) {
    const verify = node("button", "t-act", "Verify signature");
    verify.type = "button";
    const verdict = node("p", "t-verdict");
    verdict.hidden = true;

    verify.addEventListener("click", async () => {
      verify.disabled = true;
      verify.textContent = "Checking...";
      try {
        const response = await fetch(API + "/api/mandate/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (!response.ok) throw new Error("the server replied " + response.status);
        const result = await response.json();
        verdict.textContent = result.reason;
        verdict.className = "t-verdict " + (result.valid ? "ok" : "bad");
      } catch (error) {
        verdict.textContent = "Could not verify - " + error.message;
        verdict.className = "t-verdict bad";
      } finally {
        verdict.hidden = false;
        verify.disabled = false;
        verify.textContent = "Verify signature";
      }
    });
    actions.append(verify);
    ticket.append(actions, verdict);
  } else {
    const href = safeUrl(data.receipt_url);
    if (href) {
      const open = node("a", "t-act", "Open receipt");
      open.href = href;
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      actions.append(open);
    }
    ticket.append(actions);
  }

  actions.append(copyButton("Copy JSON", data));

  const raw = document.createElement("details");
  raw.className = "t-raw";
  const summary = document.createElement("summary");
  summary.textContent = "Raw payload";
  raw.append(summary);
  const pre = document.createElement("pre");
  pre.append(node("code", null, JSON.stringify(data, null, 2)));
  raw.append(pre);
  ticket.append(raw);

  return ticket;
}

/* ------------------------------------------------------------------- cards */

function buildFilm(product, film, dots) {
  const media = (product.media || []).filter((m) => safeUrl(m && m.url));
  const frames = media.length ? media : [null];

  frames.forEach((item, index) => {
    const figure = document.createElement("figure");
    const slot = node("div", "slot");

    if (item) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = item.alt_text || product.title || "Product image";
      img.addEventListener("load", () => img.classList.add("loaded"));
      img.addEventListener("error", () => {
        slot.replaceChildren(node("span", "absent", "image unavailable"));
      });
      img.src = safeUrl(item.url);
      img.addEventListener("click", () => openLightbox(img.src, img.alt));
      slot.append(img);
    } else {
      slot.append(node("span", "absent", "no image"));
    }

    figure.append(slot);
    film.append(figure);

    if (frames.length > 1) {
      const dot = node("button", "dot");
      dot.type = "button";
      dot.setAttribute("aria-label", `Image ${index + 1} of ${frames.length}`);
      dot.setAttribute("aria-current", String(index === 0));
      dot.addEventListener("click", () => {
        film.scrollTo({ left: film.clientWidth * index, behavior: "smooth" });
      });
      dots.append(dot);
    }
  });

  if (frames.length > 1) {
    dots.hidden = false;
    film.addEventListener("scroll", () => {
      const index = Math.round(film.scrollLeft / film.clientWidth);
      [...dots.children].forEach((dot, i) =>
        dot.setAttribute("aria-current", String(i === index))
      );
    });
    // Arrow keys page the strip; vertical scrolling is never intercepted.
    film.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const delta = event.key === "ArrowRight" ? 1 : -1;
      film.scrollBy({ left: film.clientWidth * delta, behavior: "smooth" });
    });
  }
}

function specRow(list, label, value) {
  if (!value) return;
  const row = node("div", "spec");
  row.append(node("dt", null, label), node("dd", null, value));
  list.append(row);
}

function buildCard(product, index) {
  const card = el("tpl-card").content.firstElementChild.cloneNode(true);
  card.style.setProperty("--delay", `${Math.min(index, 8) * 40}ms`);

  const isBest =
    state.bestPick &&
    state.bestPick.winner &&
    state.bestPick.winner.product_id === product.id;
  if (isBest) {
    card.classList.add("best");
    card.querySelector(".ribbon").hidden = false;
  }

  buildFilm(product, card.querySelector(".film"), card.querySelector(".dots"));
  card.querySelector(".card-title").textContent = product.title || "Untitled product";
  card.querySelector(".price").textContent = priceOf(product);

  const rating = ratingOf(product);
  const signal = card.querySelector(".signal");
  if (rating) {
    const count = Number(rating.count || 0);
    signal.textContent = count
      ? `${rating.value} · ${count} review${count === 1 ? "" : "s"}`
      : `${rating.value} rated`;
    if (count === 1) signal.title = "One review is weak evidence";
  } else {
    // Absent is not zero. An unrated product never renders as a bad rating.
    signal.textContent = "Not rated";
  }

  const variant = (product.variants || [])[0] || {};
  const specs = card.querySelector(".specs");
  specRow(specs, "Fabric", fabricOf(product));
  specRow(
    specs,
    "Options",
    (product.options || [])
      .filter((o) => o && o.name)
      .map((o) => `${o.name} (${(o.values || []).length})`)
      .join(", ")
  );
  specRow(
    specs,
    "Stock",
    variant.availability
      ? variant.availability.available
        ? "In stock"
        : "Unavailable"
      : ""
  );
  specRow(specs, "Seller", (variant.seller && variant.seller.name) || "");

  const link = safeUrl(variant.url || variant.checkout_url);
  if (link) {
    const open = document.createElement("a");
    open.className = "spec";
    open.href = link;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.textContent = "Open on the merchant's site";
    specs.append(open);
  }

  if (!specs.children.length) card.querySelector(".more").remove();

  const actions = card.querySelector(".actions");
  const purchasable =
    variant.id && variant.seller && variant.seller.domain &&
    (!variant.availability || variant.availability.available);

  if (purchasable) {
    const add = node("button", "add", "Add to cart");
    add.type = "button";
    add.addEventListener("click", () => addToCart(product, variant, add));
    actions.append(add);
  } else if (variant.availability && variant.availability.available === false) {
    // Out of stock is stated, not implied by a dead button.
    actions.append(node("span", "unavailable", "Out of stock"));
  }

  if (isBest) {
    const why = node("button", "why", "Why this one");
    why.type = "button";
    why.addEventListener("click", () => openDrawer(why));
    actions.append(why);
  }
  actions.hidden = actions.children.length === 0;

  return card;
}

/* ------------------------------------------------------------------- cart */

/** Creates a Universal Cart for one variant, without a model turn. */
async function addToCart(product, variant, button) {
  if (state.busy) return;
  state.busy = true;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Adding…";

  try {
    const response = await fetch(`${API}/api/cart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        merchant_domain: variant.seller.domain,
        variant_id: variant.id,
        quantity: 1,
        session_id: state.sessionId,
      }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `The merchant replied ${response.status}`);
    }
    state.cart = await response.json();
    state.cart.title = product.title || "";
    renderCart();
    showNotice("");
    button.textContent = "In cart";
  } catch (error) {
    showNotice(`Could not add to cart — ${error.message}`);
    button.textContent = label;
    button.disabled = false;
  } finally {
    state.busy = false;
  }
}

/** The cart stays on screen once it exists; it is the thing being bought. */
function renderCart() {
  const band = el("cart-band");
  const cart = state.cart;
  band.hidden = !cart;
  if (!cart) return;

  const totals = cart.totals || [];
  const total = totals.find((t) => t && t.type === "total");
  const body = el("cart-body");
  body.replaceChildren();

  if (cart.title) body.append(node("p", "cart-item", cart.title));
  if (total) body.append(node("p", "cart-total", money(total.amount, total.currency)));
  body.append(node("p", "cart-merchant", cart.merchant_domain || ""));

  const link = safeUrl(cart.continue_url);
  if (link) {
    const finish = document.createElement("a");
    finish.className = "cart-continue";
    finish.href = link;
    finish.target = "_blank";
    finish.rel = "noopener noreferrer";
    finish.textContent = "Finish on the merchant's site";
    body.append(finish);
  }
  el("cart-note").textContent = cart.id ? "held on the merchant" : "";
}

function buildRow(result) {
  const href = safeUrl(result.url);
  const row = document.createElement(href ? "a" : "div");
  row.className = "row";
  row.setAttribute("role", "listitem");
  if (href) {
    row.href = href;
    row.target = "_blank";
    row.rel = "noopener noreferrer nofollow";
  }

  const thumbUrl = safeUrl(result.image_url) || safeUrl(result.favicon_url);
  if (thumbUrl) {
    const img = document.createElement("img");
    img.className = result.image_url ? "row-thumb" : "row-thumb icon";
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = thumbUrl;
    row.append(img);
  } else {
    row.append(node("div", "row-thumb"));
  }

  const body = document.createElement("div");
  body.append(node("p", "row-title", result.title || "Listing"));
  body.append(node("span", "row-source", result.source || ""));
  if (result.snippet) body.append(node("p", "row-snippet", result.snippet));
  row.append(body);
  return row;
}

/* ---------------------------------------------------------------- the rail */

function renderResults() {
  const hasCatalog = state.products.length > 0;
  const hasWeb = state.web.length > 0;
  el("rail-empty").hidden = hasCatalog || hasWeb;
  el("refine").hidden = !hasCatalog && !hasWeb;

  const catalogBand = el("band-catalog");
  catalogBand.hidden = !hasCatalog;
  if (hasCatalog) {
    const grid = el("catalog-grid");
    grid.replaceChildren(...state.products.map(buildCard));
    el("catalog-count").textContent = `${state.products.length} in the catalog`;
  }

  const webBand = el("band-web");
  webBand.hidden = !hasWeb;
  if (hasWeb) {
    el("web-rows").replaceChildren(...state.web.map(buildRow));
  }
}

el("web-toggle").addEventListener("click", (event) => {
  const rows = el("web-rows");
  const hidden = rows.hidden;
  rows.hidden = !hidden;
  event.target.textContent = hidden ? "Hide" : "Show";
  event.target.setAttribute("aria-expanded", String(hidden));
});

/* --------------------------------------------------------- refinement (§4.2)

   Narrowing a search used to mean retyping the sentence and paying for another
   model turn. Price runs the scouts directly against the same query; sort is
   client-side, because the catalog exposes no sort parameter and pretending
   otherwise would silently reorder only the page you can see — which is exactly
   what this does, and says so. */

function priceCents(product) {
  const variant = (product.variants || [])[0] || {};
  if (variant.price && variant.price.amount != null) return Number(variant.price.amount);
  const min = (product.price_range || {}).min || {};
  return min.amount != null ? Number(min.amount) : null;
}

function sortProducts(products, mode) {
  const copy = products.slice();
  if (mode === "price-asc" || mode === "price-desc") {
    const direction = mode === "price-asc" ? 1 : -1;
    // Unpriced products sink rather than sorting as free.
    copy.sort((a, b) => {
      const pa = priceCents(a);
      const pb = priceCents(b);
      if (pa == null && pb == null) return 0;
      if (pa == null) return 1;
      if (pb == null) return -1;
      return (pa - pb) * direction;
    });
  } else if (mode === "rating") {
    copy.sort((a, b) => {
      const ra = ratingOf(a);
      const rb = ratingOf(b);
      const va = ra ? Number(ra.value) : null;
      const vb = rb ? Number(rb.value) : null;
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return vb - va;
    });
  }
  return copy;
}

async function refine(event) {
  event.preventDefault();
  if (state.busy || state.streaming) return;

  const raw = el("refine-price").value.trim();
  const maxPrice = raw === "" ? null : Number(raw);
  if (raw !== "" && (!Number.isFinite(maxPrice) || maxPrice <= 0)) {
    showNotice("Enter a price above zero, or leave it blank.");
    return;
  }

  const sort = el("refine-sort").value;
  const note = el("refine-note");

  if (maxPrice != null && state.lastQuery) {
    state.busy = true;
    el("refine-run").disabled = true;
    note.textContent = "Re-running the scouts…";
    try {
      const response = await fetch(`${API}/api/discovery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: state.lastQuery,
          max_price: maxPrice,
          session_id: state.sessionId,
          limit: 5,
        }),
      });
      if (!response.ok) throw new Error(`The server replied ${response.status}`);
      const report = await response.json();
      state.products = report.catalog_products || [];
      state.web = report.web_results || [];
      note.textContent = `Under ${maxPrice} — scouts re-run, no model turn.`;
    } catch (error) {
      showNotice(`Could not refine — ${error.message}`);
      note.textContent = "Runs the scouts directly — no model turn.";
    } finally {
      state.busy = false;
      el("refine-run").disabled = false;
    }
  } else if (sort !== "relevance") {
    note.textContent = "Sorted on this page only — the catalog has no sort field.";
  } else {
    note.textContent = "Runs the scouts directly — no model turn.";
  }

  state.products = sortProducts(state.products, sort);
  renderResults();
}

function showNotice(text) {
  const notice = el("notice");
  notice.textContent = text;
  notice.hidden = !text;
}

/* -------------------------------------------------------------- the drawer */

let drawerOpener = null;

function openDrawer(opener) {
  const pick = state.bestPick;
  if (!pick || !pick.winner) return;
  drawerOpener = opener || null;

  const body = el("drawer-body");
  body.replaceChildren();

  const verdict = node("div", "verdict");
  verdict.append(node("h3", null, pick.winner.title));
  if (pick.need) {
    verdict.append(node("p", null, `Judged against: ${pick.need}`));
  }

  const table = document.createElement("table");
  const head = document.createElement("tr");
  head.append(node("th", null, "Criterion"), node("th", null, "Why"), node("th", null, "Score"));
  table.append(head);

  for (const criterion of pick.winner.criteria || []) {
    const row = document.createElement("tr");
    row.append(
      node("th", null, String(criterion.name).replace(/_/g, " ")),
      node("td", null, criterion.evidence || ""),
      node("td", "score", Number(criterion.score).toFixed(2))
    );
    table.append(row);
  }
  verdict.append(table);

  // Blind spots are shown, never hidden: an unscored criterion is unknown, not zero.
  if ((pick.winner.missing || []).length) {
    const gaps = node("div", "unscored");
    gaps.append(node("strong", null, "Not scored"));
    gaps.append(node("p", null, pick.winner.missing.join(" · ")));
    verdict.append(gaps);
  }

  if ((pick.comparison || []).length) {
    verdict.append(node("h3", null, "Against the runners-up"));
    const list = document.createElement("ul");
    for (const line of pick.comparison) {
      list.append(node("li", null, line.replace(/\*\*/g, "")));
    }
    verdict.append(list);
  }

  verdict.append(node("p", "caveat", pick.caveat || ""));
  body.append(verdict);

  el("drawer").hidden = false;
  el("drawer-scrim").hidden = false;
  el("drawer-close").focus();
}

function closeDrawer() {
  el("drawer").hidden = true;
  el("drawer-scrim").hidden = true;
  if (drawerOpener) drawerOpener.focus();
}

el("drawer-close").addEventListener("click", closeDrawer);
el("drawer-scrim").addEventListener("click", closeDrawer);

/* ------------------------------------------------------------- the lightbox */

function openLightbox(src, alt) {
  const img = el("lightbox-img");
  img.src = src;
  img.alt = alt || "";
  el("lightbox").hidden = false;
  el("lightbox-close").focus();
}

function closeLightbox() {
  el("lightbox").hidden = true;
  el("lightbox-img").src = "";
}

el("lightbox-close").addEventListener("click", closeLightbox);
el("lightbox").addEventListener("click", (event) => {
  if (event.target === el("lightbox")) closeLightbox();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!el("lightbox").hidden) closeLightbox();
  else if (!el("drawer").hidden) closeDrawer();
  else if (state.streaming) stopTurn();
});

/* ------------------------------------------------------------------ stream */

function stopTurn() {
  if (state.controller) state.controller.abort();
}

async function send(question) {
  if (state.streaming || !question.trim()) return;
  state.lastQuery = question.trim();

  const { turn, timeline, answer } = startTurn(question.trim());
  state.streaming = true;
  state.controller = new AbortController();
  el("send").disabled = true;
  el("stop").hidden = false;
  showNotice("");

  // Token deltas are buffered and flushed on an animation frame, so a fast model
  // cannot force one layout per token.
  let buffer = "";
  let raw = "";
  let queued = false;
  const flush = () => {
    queued = false;
    if (!buffer) return;
    raw += buffer;
    buffer = "";
    // Re-rendered rather than appended: Markdown cannot be parsed one delta at a
    // time, and an answer is small enough that a full rebuild per frame is free.
    renderMarkdown(raw, answer);
    scrollToEnd();
  };
  const write = (text) => {
    buffer += text;
    if (!queued) {
      queued = true;
      requestAnimationFrame(flush);
    }
  };

  let toolsRun = 0;

  try {
    const response = await fetch(`${API}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, session_id: state.sessionId }),
      signal: state.controller.signal,
    });
    if (!response.ok) throw new Error(`The server replied ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let carry = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      carry += decoder.decode(value, { stream: true });

      const frames = carry.split("\n\n");
      carry = frames.pop() || "";

      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;

        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          continue;
        }

        switch (event.type) {
          case "status":
            timeline.upsert("Concierge", "running", event.text);
            break;
          case "tool_call":
            toolsRun += 1;
            timeline.upsert(event.tool, "running", "working");
            break;
          case "tool_result":
            timeline.upsert(event.tool, "done", "finished", null, event.result);
            break;
          case "crew":
            timeline.upsert(event.agent, event.status, event.detail, event.duration);
            if (event.status === "error") showNotice(`${event.agent}: ${event.detail}`);
            break;
          case "products":
            state.products = event.products || [];
            renderResults();
            break;
          case "artifact": {
            // Placed before the answer element so the object appears above the
            // prose describing it, and stays put as tokens keep arriving.
            const ticket = buildTicket(event.kind, event.data || {});
            turn.insertBefore(ticket, answer);
            scrollToEnd();
            break;
          }
          case "content":
            write(event.text || "");
            break;
          case "done":
            rememberSessionId(event.session_id || state.sessionId);
            timeline.finish(toolsRun ? `Used ${toolsRun} tool${toolsRun === 1 ? "" : "s"}` : "Answered");
            await refreshResults();
            break;
          case "error":
            showNotice(event.message || "The turn failed.");
            timeline.upsert("Concierge", "error", event.message);
            timeline.finish("Failed");
            break;
        }
      }
    }
  } catch (error) {
    if (error.name === "AbortError") {
      timeline.finish("Stopped");
      write("\n\n_[stopped]_");
    } else {
      showNotice(error.message);
      timeline.finish("Failed");
    }
  } finally {
    flush();
    answer.classList.remove("streaming");
    if (!raw.trim()) answer.remove();
    state.streaming = false;
    state.controller = null;
    el("send").disabled = false;
    el("stop").hidden = true;
    el("composer").focus();
  }
}

/** Pulls the authoritative view of results after a turn settles. */
async function refreshResults() {
  if (!state.sessionId) return;
  try {
    const response = await fetch(`${API}/api/session/${encodeURIComponent(state.sessionId)}`);
    if (!response.ok) return;
    const snapshot = await response.json();
    state.products = snapshot.last_searched_products || [];
    state.web = snapshot.last_web_results || [];
    state.bestPick = snapshot.best_pick || null;
    state.cart = snapshot.active_cart || null;
    renderResults();
    renderCart();
  } catch {
    /* The stream already rendered what it saw; a failed refresh changes nothing. */
  }
}

/* -------------------------------------------------------------- composer UI */

const composer = el("composer");

composer.addEventListener("input", () => {
  composer.style.height = "auto";
  composer.style.height = `${Math.min(composer.scrollHeight, 144)}px`;
});

composer.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el("composer-form").requestSubmit();
  }
});

el("composer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = composer.value;
  if (!question.trim() || state.streaming) return;
  composer.value = "";
  composer.style.height = "auto";
  send(question);
});

el("stop").addEventListener("click", stopTurn);

el("starters").addEventListener("click", (event) => {
  const button = event.target.closest(".starter");
  if (button) send(button.dataset.q);
});

el("refine").addEventListener("submit", refine);

/** Replaces the canned starters with prompts the catalog can actually answer (§4.4). */
async function loadExamples() {
  try {
    const response = await fetch(`${API}/api/examples`);
    if (!response.ok) return;
    const payload = await response.json();
    if (payload.source !== "catalog" || !Array.isArray(payload.examples)) return;

    el("starters").replaceChildren(
      ...payload.examples.map((text) => {
        const button = node("button", "starter", text);
        button.type = "button";
        button.dataset.q = text;
        return button;
      })
    );
  } catch {
    /* The static starters in index.html stay. */
  }
}

/* ------------------------------------------------- session controls (§new)

   "New session" mints a fresh id rather than wiping the old one: the previous
   session stays in the durable store under its own id, so starting over costs
   nothing the shopper might want back. The id is surfaced in the notice so it
   can be recovered.

   "Copy context" hands the session to another agent as a graph rather than a
   transcript — the same facts with each stated once and the relationships named,
   which is a fraction of the tokens. */

async function startNewSession() {
  if (state.streaming) stopTurn();

  const previous = state.sessionId;
  try {
    const response = await fetch(API + "/api/session", { method: "POST" });
    if (!response.ok) throw new Error("the server replied " + response.status);
    const { session_id: fresh } = await response.json();
    rememberSessionId(fresh);
  } catch (error) {
    showNotice("Could not start a new session - " + error.message);
    return;
  }

  state.products = [];
  state.web = [];
  state.bestPick = null;
  state.cart = null;
  state.lastQuery = "";
  transcript.replaceChildren(openingScreen.cloneNode(true));
  renderResults();
  renderCart();
  el("refine").hidden = true;
  el("rail-empty").hidden = false;
  pinned = true;
  el("jump").hidden = true;
  loadExamples();
  showNotice(previous ? "New session. The previous one is kept as " + previous : "");
  el("composer").focus();
}

/** Copies the session graph — the token-cheap form of "what happened here". */
async function copyContext() {
  const button = el("copy-context");
  if (!state.sessionId) {
    showNotice("Nothing to copy yet - ask something first.");
    return;
  }

  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Building...";
  try {
    const response = await fetch(
      API + "/api/session/" + encodeURIComponent(state.sessionId) + "/graph"
    );
    if (!response.ok) throw new Error("the server replied " + response.status);
    const payload = await response.json();
    await navigator.clipboard.writeText(payload.text);
    showNotice(
      "Copied the session graph - about " +
        payload.estimated_tokens +
        " tokens, against roughly " +
        payload.estimated_tokens_raw_snapshot +
        " for the raw snapshot (estimates)."
    );
    button.textContent = "Copied";
  } catch (error) {
    showNotice("Could not copy the context - " + error.message);
    button.textContent = label;
  } finally {
    setTimeout(() => {
      button.textContent = label;
      button.disabled = false;
    }, 1600);
  }
}

el("new-session").addEventListener("click", startNewSession);
el("copy-context").addEventListener("click", copyContext);

/* ------------------------------------------------------------------- boot */

(async function boot() {
  state.sessionId = loadSessionId();
  if (state.sessionId) {
    await refreshResults();
  }
  loadExamples();
  try {
    const health = await (await fetch(`${API}/api/health`)).json();
    const pay = el("provider-payments");
    pay.textContent = `payments ${health.payment_provider}`;
    pay.classList.add("ok");
    const search = el("provider-search");
    search.textContent = `search ${health.web_search_provider}`;
    search.classList.add("ok");
  } catch {
    el("provider-payments").textContent = "server unreachable";
  }
  composer.focus();
})();
