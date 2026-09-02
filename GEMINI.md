# AGENT DIRECTIVES & OPERATING HARNESS

## 1. Deterministic Execution & Environment Rules
- **Package Management:** ALWAYS use uv for Python environment & package management (uv run, uv pip install, uv venv). NEVER invoke raw pip or standard python directly.
- **Node.js Management:** ALWAYS use 
pm run <script> or 
px with precise arguments. Check package.json before introducing or updating any dependency.
- **Environment Isolation:** Execute commands strictly inside project boundaries and virtual environments. NEVER mutate global system settings or external directories.

## 2. Strict Negative Constraints (Forbidden Behaviors)
- **NO Unverified Dependencies:** DO NOT add third-party libraries or packages without first searching existing code utilities and verifying package.json / 
equirements.txt.
- **NO Symptom Patching:** NEVER swallow exceptions, return dummy fallback values, comment out failing assertions, or delete failing tests to force a passing run.
- **NO Noisy Output Spills:** DO NOT execute verbose shell commands (git log, raw pytest, un-flagged builds) that flood stdout. Always limit line output using flags (-n 20), pipes (grep, head), or scoped globs.
- **NO Unanchored String Edits:** DO NOT make ambiguous multi-line string replacements. Edits must target unique, anchored code blocks to prevent unintended regressions.

## 3. Eval-Driven & Defensive Verification Loop
Before declaring ANY task or fix complete, you MUST execute the following 4-step loop:
1. **Plan & Isolate:** Trace the precise root cause before editing. Formulate a minimal, targeted diff.
2. **Deterministic Edit:** Apply changes targeting exact line blocks without affecting surrounding logic or un-related docstrings.
3. **Empirical Verification:** Run strict linters, typecheckers, and tests:
   - Python: uv run ruff check . / uv run pytest
   - JS/TS: 
pm run lint / 
pm test
4. **Zero-Error Enforcement:** Inspect stderr and exit codes. Re-evaluate if ANY error, lint warning, or regression is produced. NEVER declare success without clean terminal evidence.

## 4. Subagent Management Protocol (Context Isolation Harness)
- **Primary Agent Role:** High-level planning, architectural reasoning, deterministic tool execution, and state verification.
- **Subagent Role:** Isolated discovery, multi-file inspection, log parsing, and repository-wide audits.
- **Delegation Trigger:** Delegate to a read-only research subagent whenever a task requires inspecting **>3 files**, searching large log traces, auditing repo-wide dependencies, or surveying unfamiliar modules. NEVER flood the primary orchestrator's context window with bulk code reading.
- **Strict Role Isolation:** Assign explicit, single-purpose roles (e.g., Codebase Auditor, Log Trace Debugger, API Schema Analyzer).
- **Read-Only Enclosure:** Research subagents MUST operate in read-only mode to prevent unintended state mutation.
- **Payload Distillation (MapReduce Pattern):** Subagents must NEVER return raw code dumps or un-truncated file views. All returns must be compressed into:
  1. Executive Insight (1-3 sentences)
  2. Exact Code Anchors (ile:line)
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