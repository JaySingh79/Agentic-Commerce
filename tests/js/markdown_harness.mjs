/* Runs web/app.js's Markdown renderer outside a browser.
 *
 * The renderer is the one place where model output — which quotes
 * attacker-influenced web-search text — is turned into DOM, so it needs a real
 * test rather than a static grep. There is no bundler or JS test runner in this
 * project, so app.js is executed in a vm against a DOM shim just large enough
 * for it to load, and the resulting tree is serialised for assertions.
 *
 * Reads one JSON case per line on stdin:
 *   {"markdown": "..."}                       - render Markdown
 *   {"ticket": {"kind": "...", "data": {...}}} - build a mandate/receipt ticket
 * Writes one JSON result per line: {"html": "...", "text": "..."}
 */

import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "web", "app.js"), "utf8");

const VOID_TAGS = new Set(["img", "input", "br", "hr"]);

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = { setProperty() {} };
    this.classList = {
      add: () => {},
      remove: () => {},
      contains: () => false,
    };
    this._text = null;
  }

  set className(value) {
    this.attributes.class = value;
  }
  get className() {
    return this.attributes.class || "";
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    if (this._text != null) return this._text;
    return this.children.map((c) => c.textContent).join("");
  }

  set href(value) {
    this.attributes.href = value;
  }
  get href() {
    return this.attributes.href;
  }
  set target(value) {
    this.attributes.target = value;
  }
  set rel(value) {
    this.attributes.rel = value;
  }
  set hidden(value) {
    this.attributes.hidden = value;
  }
  set title(value) {
    this.attributes.title = value;
  }

  append(...nodes) {
    for (const child of nodes) {
      if (child && child.tag === "#fragment") this.children.push(...child.children);
      else this.children.push(child);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this._text = null;
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  addEventListener() {}
  querySelector() {
    return new FakeNode("div");
  }
  querySelectorAll() {
    return [];
  }
  remove() {}
  focus() {}
  get firstElementChild() {
    return this.children[0];
  }
  cloneNode() {
    return new FakeNode(this.tag);
  }

  serialize() {
    if (this.tag === "#text") return escapeText(this._text || "");
    const attrs = Object.entries(this.attributes)
      .map(([k, v]) => ` ${k}="${String(v)}"`)
      .join("");
    const inner =
      this._text != null
        ? escapeText(this._text)
        : this.children.map((c) => c.serialize()).join("");
    if (VOID_TAGS.has(this.tag)) return `<${this.tag}${attrs}>`;
    return `<${this.tag}${attrs}>${inner}</${this.tag}>`;
  }
}

function escapeText(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function makeTextNode(text) {
  const n = new FakeNode("#text");
  n._text = String(text);
  return n;
}

const document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: makeTextNode,
  createDocumentFragment: () => new FakeNode("#fragment"),
  getElementById: () => new FakeNode("div"),
  addEventListener: () => {},
  body: new FakeNode("body"),
};

const sandbox = {
  document,
  window: { location: { origin: "http://127.0.0.1:8010" }, localStorage: null },
  console,
  URL,
  fetch: () => Promise.reject(new Error("no network in the harness")),
  requestAnimationFrame: () => 0,
  performance: { now: () => 0 },
  AbortController,
  TextDecoder,
  CSS: { escape: (v) => v },
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  JSON,
  Math,
  Number,
  Date,
};
sandbox.globalThis = sandbox;

const context = createContext(sandbox);
runInContext(source, context, { filename: "app.js" });

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  for (const line of input.split("\n")) {
    if (!line.trim()) continue;
    const testCase = JSON.parse(line);
    const host = new FakeNode("div");
    if (testCase.ticket) {
      host.append(context.buildTicket(testCase.ticket.kind, testCase.ticket.data));
    } else {
      context.renderMarkdown(testCase.markdown, host);
    }
    process.stdout.write(
      JSON.stringify({
        html: host.children.map((c) => c.serialize()).join(""),
        text: host.textContent,
      }) + "\n"
    );
  }
});
