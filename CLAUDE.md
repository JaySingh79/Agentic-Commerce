# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                        # install (Python 3.11+, uv only — never raw pip/python)
uv run python -m agentic_commerce.api.server   # FastAPI + vanilla-JS storefront on :8010
uv run python app.py                           # Gradio chat on :7860
uv run ruff check src tests                    # lint (E,F,I,UP,B,SIM · line-length 100)
uv run pytest -q                               # full suite (~65s, 26 files)
uv run pytest tests/test_payments.py -q        # one file
uv run pytest tests/test_payments.py::test_name -q          # one test
docker compose up -d --build                                # api :8010 · ui :7860 · razorpay-mcp (stdio) · telemetry :3000 (all included)
docker compose up -d --build api ui razorpay-mcp             # opt out of telemetry
node --env-file=.env src/agentic_commerce/backend/ucp_demo.js   # UCP demo scripts (not in image)
```

`pytest` sets `pythonpath = ["src"]`; `app.py` inserts `src/` itself. Browser JS (`web/app.js`) is
tested from pytest through a Node `vm` harness (`tests/js/markdown_harness.mjs`) — no JS runner.

Config is env-only (`.env` locally, `env_file` in compose). Full variable table in `README.md` §5.

## Architecture

Three transports over one domain core. The load-bearing boundary is the **SSE event contract**,
not any UI:

```
Domain (UI-independent)      Agent runtime          Transports
a2a/ ap2/ core/ ucp/         backend/               api/  ui/  web/
payments/ ── shim ──→        backend/payments.py    (re-export only)
```

- **Domain packages** (`a2a/`, `ap2/`, `core/`, `ucp/`, `payments/`) return structured data
  (dataclasses with `as_dict()`), never Markdown, and know nothing about a UI.
- **`backend/`** is the agent runtime. `CommerceAgent.execute_stream` (`backend/agent.py`) is the
  single turn engine; `backend/tools.py` wraps domain calls as LangChain `@tool`s that format
  Markdown **for the model only**; `backend/crew.py` fans `CatalogScout` + `WebScout` out through
  `asyncio.gather` into a `DiscoveryReport`; `backend/session.py` + `session_store.py` hold
  per-session state (SQLite snapshots); `session_graph.py` renders that state as a compact
  node/edge graph.
- **`backend/payments.py` and `backend/ap2.py` are thin re-export shims.** Logic lives in
  `payments/` and `ap2/`. Add a public name to the domain module *and* the shim. Inside
  `payments/`, `config.py`, `razorpay.py` and `mcp.py` are likewise shims over
  `settings.py` and `providers/`.
- **`payments/` layering:** `settings.py` (the one guard site) → `models.py` (states, error
  taxonomy) → `ledger.py` (SQLite idempotency + mandate claims) → `providers/` (one protocol,
  four backends) → `gateway.py` (resolution + reconciliation) → `flow.py` (the mandate-gated
  state machine). Money enters through `flow.py`, not `gateway.py`.
- **`api/server.py`** exposes 24 paths (mirrored in hand-kept `openapi.json`); routes call domain
  functions directly and skip the Markdown layer. `ui/chat_engine.py` drives the same
  `CommerceAgent` for Gradio. `web/app.js` is the reference SSE consumer.
- **`orchestrator.py`** is a separate sequential pipeline (discover → cart → mandate → authorize →
  settlement → order), distinct from the agent loop — do not conflate the two. Both reach money
  through `payments/flow.py`, so the guards cannot diverge between them.

SSE vocabulary emitted by `execute_stream`: `status`, `content`, `tool_call`, `tool_result`,
`products`, `crew`, `artifact`, `done`, `error`. Any new client builds against this.

### Invariants that are not style preferences

- **Test-mode payments only.** `rzp_test_*` / `sk_test_*`; anything else raises
  `PaymentConfigurationError` and degrades to the simulated gateway. Orders/PaymentIntents are
  created, never paid or confirmed; capture raises `CaptureNotEnabledError`. Nothing here moves
  money. Guard test: `tests/test_payments.py`.
- **`live` is derived, never written.** Only `PaymentSettings.derive_live()` decides it. A
  provider claiming `livemode` under test credentials is an error, not a value to copy.
- **Fallback only before transmission.** `ProviderTransientError`/`PaymentConfigurationError` may
  try the next provider; `ProviderUnknownError` must reconcile via `find_by_receipt`. Classifying
  a post-send failure as transient is how you get duplicate orders.
- **A mandate authorizes exactly one payment.** Signature (canonical JSON over the whole payload),
  expiry, spending limit, currency, cart, merchant, then a ledger claim — all in `payments/flow.py`.
- **Raw `httpx` for payments** — no payment SDK becomes a dependency. Razorpay has no idempotency
  header, so `payments/ledger.py` enforces the dedupe keyed on the receipt.
- **Honesty is structural, not cosmetic.** Missing data is *absent*, never zeroed: no rating key on
  unrated products, `ProductScore.missing` names dropped signals (remaining weights renormalized,
  not zero-scored), web results get no cart button at all, `PaymentResult.live` always rendered.
- **Independent failure.** Either scout or either payment provider can fail; the turn degrades with
  the gap named, never a silent total failure.

### Adding an agent tool

Domain function returning structured data → `@tool` wrapper in `backend/tools.py` (Markdown for the
model, progress via `trace_tool_execution`) → behaviour-named tests (`test_<invariant>_...`, not
`test_<function>_...`) → optional thin route in `api/server.py` plus an `openapi.json` entry.

### Docs that carry truth

`README.md` (full operator guide), `project_memory.md` (UCP/MCP protocol versions, auth tiers, tool
specs), `project_context.md` (endpoints, key files), `FEATURES.md` (what runs today plus an explicit
**Not built yet** list — do not list something until it runs).

---

# AGENT DIRECTIVES & OPERATING HARNESS

## 1. Deterministic Execution & Environment Rules
- **Package Management:** ALWAYS use uv for Python environment & package management (uv run, uv pip install, uv venv). NEVER invoke raw pip or standard python directly.
- **Node.js Management:** ALWAYS use `npm run <script>` or `npx` with precise arguments. Check package.json before introducing or updating any dependency.
- **Environment Isolation:** Execute commands strictly inside project boundaries and virtual environments. NEVER mutate global system settings or external directories.

## 2. Strict Negative Constraints (Forbidden Behaviors)
- **NO Unverified Dependencies:** DO NOT add third-party libraries or packages without first searching existing code utilities and verifying pyproject.toml / package.json.
- **NO Symptom Patching:** NEVER swallow exceptions, return dummy fallback values, comment out failing assertions, or delete failing tests to force a passing run. A test documenting an outdated contract may change only as an explicitly declared contract change.
- **NO Noisy Output Spills:** DO NOT execute verbose shell commands (git log, raw pytest, un-flagged builds) that flood stdout. Always limit line output using flags (-n 20), pipes (grep, head), or scoped globs.
- **NO Unanchored String Edits:** DO NOT make ambiguous multi-line string replacements. Edits must target unique, anchored code blocks to prevent unintended regressions.

## 3. Eval-Driven & Defensive Verification Loop
Before declaring ANY task or fix complete, you MUST execute the following 4-step loop:
1. **Plan & Isolate:** Trace the precise root cause before editing. Formulate a minimal, targeted diff.
2. **Deterministic Edit:** Apply changes targeting exact line blocks without affecting surrounding logic or un-related docstrings.
3. **Empirical Verification:** `uv run ruff check src tests` and `uv run pytest -q`.
4. **Zero-Error Enforcement:** Inspect stderr and exit codes. Re-evaluate if ANY error, lint warning, or regression is produced. NEVER declare success without clean terminal evidence.

## 4. Subagent Management Protocol (Context Isolation Harness)
- **Primary Agent Role:** High-level planning, architectural reasoning, deterministic tool execution, and state verification.
- **Subagent Role:** Isolated discovery, multi-file inspection, log parsing, and repository-wide audits.
- **Delegation Trigger:** Delegate to a read-only research subagent whenever a task requires inspecting **>3 files**, searching large log traces, auditing repo-wide dependencies, or surveying unfamiliar modules. NEVER flood the primary orchestrator's context window with bulk code reading.
- **Strict Role Isolation:** Assign explicit, single-purpose roles (e.g., Codebase Auditor, Log Trace Debugger, API Schema Analyzer).
- **Read-Only Enclosure:** Research subagents MUST operate in read-only mode to prevent unintended state mutation.
- **Payload Distillation (MapReduce Pattern):** Subagents must NEVER return raw code dumps or un-truncated file views. All returns must be compressed into:
  1. Executive Insight (1-3 sentences)
  2. Exact Code Anchors (file:line)
  3. Extracted Interfaces/Signatures only
  4. Explicit Callouts of blockers/edge cases
- **Asynchronous Lifecycle:** Launch subagents asynchronously. NEVER poll or status-check in tight loops; rely strictly on reactive system wakeups.

## 5. Context Window & Token Management
- **Targeted Code Reading:** Use scoped line views (StartLine/EndLine) and specific file patterns. NEVER dump entire multi-thousand-line files into context unless mandatory.
- **Compaction Readiness:** When pivoting to a new sub-task or major feature branch, request context compaction or summarize prior state to prevent context rot.

## 6. Codebase Integrity & Clean Architecture
- **Preserve API Contracts:** Maintain existing function signatures, parameter names, and return types. If modifying a signature, search and update all call sites across the codebase.
- **Single Responsibility:** Write modular, decoupled helpers rather than monolithic scripts. Search the repository for pre-existing utility functions before inventing new ones.
- **Explicit Typing & Error Handling:** Enforce strict type hints and explicit runtime validations at system boundaries (file IO, web requests, CLI arguments).

# graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.
