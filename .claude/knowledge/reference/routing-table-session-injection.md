---
audience: dev
subsystem: dispatch-agent
type: reference
status: current
covers:
  - .claude/knowledge/decisions/routing-table-caa-agents.md
---

<!-- record-meta
status: current
record_class: reference
title: Routing table session injection
covers: producer and critic/gate subagent_types only (the rows in the two tables below); dispatchable agents outside these tables (e.g. records-curator, ux-aesthetic-critic) carry no route guidance from this source
provenance: distilled from .claude/knowledge/decisions/routing-table-caa-agents.md § Per-agent recommendations (that record remains the canonical bench rationale/provenance home)
-->

Use this table as dispatch guidance, not benchmark narrative. For each dispatch, pass the Primary route in the agent's row; use the Fallback or alternative only when the primary is unavailable or the row's condition applies. On any `gemini-3.1-pro-preview` route, `exit_code=0` alone is not a success signal — gemini-CLI 0.44.1 can silently return an empty response (`error.type=INVALID_STREAM`) with a zero exit. Before trusting a gemini return, confirm it carries real, non-empty output; the standard post-stop-verify verification-sidecar read (see the verify-sidecar discipline elsewhere in this prompt) is the automated backstop and downgrades an INVALID_STREAM return to `verdict: warn`. Treat an empty or downgraded gemini return as a dispatch failure and re-route to the Fallback. Detection mechanism and the subprocess-consumer-vs-orchestrator split: `.claude/knowledge/decisions/routing-table-caa-agents.md § Gemini-CLI INVALID_STREAM — mandatory envelope inspection`.

Route-token legend — each table cell names a route token; map it to `dispatch_agent` parameters as: `gpt-5.5-<tier>` → `model_route="gpt"`, `model="gpt-5.5"`, `reasoning_effort=<tier>` with `<tier>` one of `medium | high | xhigh`; `gemini-3.1-pro-preview` → `model_route="gemini"`, `model="gemini-3.1-pro-preview"`; `claude-default` → native Claude route (`model_route="claude"` or `"claude-subprocess"`, default model). A token is shorthand for that parameter set, not a literal string to pass through.

### Producers

| Agent | Primary route | Fallback / alternative | Dispatch action |
|---|---|---|---|
| `agent-content-author` | `claude-default` | `gpt-5.5-xhigh` | Route every agent-content-author dispatch to Claude by default — operator directive, applies to all agent-content-author work; use the GPT fallback only when Claude is unavailable. |
| `architect` | `gpt-5.5-xhigh` | `claude-default` | Route architecture and solution-shape producer work to xhigh. |
| `design-planner` (default) | `gpt-5.5-high` | `gpt-5.5-xhigh` | Use for normal enumeration and decomposition under gate coverage. |
| `design-planner` (decision-load-bearing axes) | `gpt-5.5-xhigh` | `claude-default` | Use for open architectural questions, fresh design fan-outs, and decisions where a missed dimension is expensive to recover from. |
| `diagnostician` | `gpt-5.5-high` | `gpt-5.5-xhigh` | Use high by default; escalate to xhigh when the root-cause question is unusually scope-sensitive. |
| `implementer` | `gpt-5.5-xhigh` | `claude-default` | Use for hard-spec and creative-latitude code work; include scope, build/test/verification, and scope-readback requirements in the delegation. |
| `planner` | `gpt-5.5-high` | `gpt-5.5-xhigh` | Use for enumeration and decomposition; escalate when missed steps would be costly to recover from. |
| `researcher` | `gpt-5.5-medium` | `gpt-5.5-xhigh`; `gemini-3.1-pro-preview` third option | Use medium for open-ended investigation; escalate to xhigh for unusually scope-sensitive questions. Use gemini only when the task has no strict canonical-vocabulary or tight-scope requirement and the gemini envelope check passes. |
| `solution-designer` | `gpt-5.5-xhigh` | `claude-default` | Route bounded solution-design producer work to xhigh. |
| `synthesizer` | `gpt-5.5-xhigh` | `claude-default` | Use for high-consequence synthesis and join artifacts. |

### Critics / gates

| Agent | Primary route | Acceptable alternative | Dispatch action |
|---|---|---|---|
| `coherence-auditor` | `claude-default` | `gemini-3.1-pro-preview` | Use Claude for cross-artifact drift checks; gemini is acceptable only with the envelope-failure check. |
| `pre-flight-gate` | `claude-default` | `gemini-3.1-pro-preview` | Use Claude for verdict-only pre-dispatch gating; gemini is acceptable only with the envelope-failure check. |
| `surface-gate` | `claude-default` | `gemini-3.1-pro-preview` | Use Claude for cold-reader opacity audits; gemini is acceptable only with the envelope-failure check. |
| `validator` | `claude-default` | `gemini-3.1-pro-preview` | Use Claude for rubric validation; gemini is acceptable only with the envelope-failure check. |

If pre-flight-gate, validator, coherence-auditor, or peer review are bypassed for strict-scope producer work, prefer `claude-default` over GPT-primary producer routing until gate coverage returns.
