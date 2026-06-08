---
audience: dev
subsystem: dispatch-agent
type: reference
status: current
description: Per-route model allowlists for dispatch_agent — canonical SoT loaded at MCP server boot by buildModelAllowlists() in dispatch-agent.ts.
covers:
  - .claude/mcp/context-tools/src/tools/dispatch-agent.ts
  - .claude/mcp/context-tools/src/index.ts
---

# dispatch-agent model allowlist

This file is the canonical source-of-truth for the `dispatch_agent` tool per-route model allowlists.
Loaded at MCP server boot by `buildModelAllowlists()` in `.claude/mcp/context-tools/src/tools/dispatch-agent.ts`.
Restart the MCP server after editing this file — changes are not hot-reloaded.

**Parser contract:** `buildModelAllowlists()` reads this file at boot, splits by H2 headings, and
looks for `default: <value>` and `allowed: <comma-separated-list>` lines inside each route section.
The default value must appear in the allowed list; violation causes a malformed-file diagnostic and
fall-back to the hardcoded constant in `MODEL_ALLOWLISTS_FALLBACK`.

## gemini route

default: gemini-3.1-pro-preview
allowed: gemini-3.1-pro-preview, gemini-3-pro-preview, gemini-3-flash-preview, gemini-2.5-flash-lite

Empirically verified against gemini CLI v0.41.2 on 2026-05-18 (Pro-subscription OAuth tier).
See `constraints/gemini-model-allowlist-empirical.md` for the full per-model verification record.

Note: `gemini-2.5-flash-lite` is the empirically-verified `-m` INPUT string (resolves via alias to internal `gemini-3.1-flash-lite`). The allowlist enforces the INPUT-string contract, not the resolved internal name. See the constraint file's Working table for the input↔resolved-name mapping.



## gpt route

default: gpt-5.5
allowed: gpt-5.5

`gpt-5-codex` and `gpt-5.2-codex` were removed (confirmed unavailable on ChatGPT OAuth; see
`decisions/agent-capability-bench-design-digest.md § R4 pre-cut audit` B-5 bullet for the audit trail).
