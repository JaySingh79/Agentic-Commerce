# Master Global Memory

This is the central entry point for the global memory layer. It contains key rules that are always or usually true across all projects, pointing to sub-memory files for deep-dives.

## Stable Preferences
- **Clean Code & Strict Typing**: Prefer readable, self-documenting code with explicit typing (TypeScript, Python type hints). Reference [coding_standards.md](file:///D:/AI/global/memory/coding_standards.md).
- **LLM and Prompt Standards**: Use structured JSON outputs (via Pydantic, Zod) and low-temperature settings for coding tasks. Reference [llm_best_practices.md](file:///D:/AI/global/memory/llm_best_practices.md).
- **ML Engineering Integrity**: Ensure reproducibility by setting global random seeds and versioning data pipelines. Reference [ml_engineering.md](file:///D:/AI/global/memory/ml_engineering.md).
- **Modular Multi-Agent Systems**: Design specialized agents with narrow scopes and deterministic routing paths. Reference [agent_design.md](file:///D:/AI/global/memory/agent_design.md).
- **Standard Tooling & Environments**: Maintain clean, virtualized project environments with structured config files. Reference [project_defaults.md](file:///D:/AI/global/memory/project_defaults.md).

## Debugging Habits
- Gather exact logs, stack traces, and system specs before proposing fixes.
- Propose a clear verification plan first; avoid "trial-and-error" code changes.
- Write a regression test when patching complex logic.

## Do-Not-Repeat Rules
- **No global package pollution**: Never install project-specific packages globally.
- **No hardcoded secrets**: Never commit API keys, database URLs, or access tokens. Always use environment variables.
- **No massive files**: Break down large components or modules when they exceed 300 lines of code.
- **No placeholders**: Never use "TODO" or placeholder code in finalized pull requests.
- **No anonymous or implicit work**: Always explain proposed file changes, directory writes, and command executions in plain, detailed language. Obtain explicit user confirmation before running or editing anything. Never execute state changes silently or in the background.
