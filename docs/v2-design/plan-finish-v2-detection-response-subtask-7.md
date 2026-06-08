# Subtask 7: Mode dispatch route + deflections (support / out_of_scope / unsafe) [solution-design] [peer-review] [completeness-risky-fallback-only]

**Description**: Add the downstream ROUTING that differentiates the 7 parsed modes (dispatcher PARSING is DONE per gap analysis §1 — `_parse_dispatch_prefix` decodes `MODE:`; what is missing is that all modes currently flow to the same `handle_regular_turn` body). Implement the conditional-edge route that sends each mode to its handler, AND implement the 3 deflection handlers in this subtask (they are template/keyword paths with no further fan-out):

- **`support`** — shop-fillable YAML keyword OR-match at dispatch entry (no LLM call on a keyword hit). Load `agent/support/{shop_id}.yaml` per the §8a schema (patterns with `detect:[]` OR-match, `target:{type,address|url}`, `cta_label`, `response_template` with mustache vars, plus a `fallback`). Validate the YAML at load (every `detect` non-empty; `target.type ∈ {email,url,form}`; mustache vars resolve; no shared `detect` keyword without priority). Redirect to shop CTAs.
- **`out_of_scope`** — guidebook-driven polite short-response template; NO product search; distinct from `product_search`.
- **`unsafe`** — hard-refuse, template-only response, NO UI surface even if the LLM tries to emit one, and a logged `turn_events` row carrying ALL `UNSAFE_ROW_REQUIRED_FIELDS` (`dispatch_rationale_token`, `confidence_signal`, `triggering_keyword` [nullable when LLM-fallback], `verbatim_query`). The `guardrail-keywords.yml` on the branch is for prompt-guardrail LINTING — the agent-side dispatch path is what's missing here.

**Mis-dispatch fallback default (Operator Decision #7 — record in impl-report):** route a borderline/ambiguous query to `product_search` (degrades gracefully) NOT `out_of_scope` (which would refuse a possibly-valid shopper).

**[solution-design] required FIRST** — the route topology is a genuine design choice: the architect design proposes NEW nodes (`mode_dispatch`, `out_of_scope_turn1`, `template_deflect`) + a `dispatch_route` conditional edge re-pointing `START`. But the architect design's node names assume the retired `first_turn_init` topology. On the POST-REBASE graph the entry is `reset_tool_call_count → handle_regular_turn`. The solution-designer must reconcile: do we add a dispatch node BEFORE `handle_regular_turn`, or branch INSIDE it? Either is defensible; the choice affects every mode handler (Subtasks 8/9/10). The single-funnel metrics constraint is binding: every new handler MUST edge to the existing metrics/emit funnel or `llm_call_count` under-counts. Deflection handlers (support/unsafe) make ZERO LLM calls on a keyword hit — preserving the budget.

**[completeness-risky-fallback-only]:** this subtask enumerates the routing for all 7 modes and a downstream consumer (the mode handlers + coherence audit) is materially blind if a mode is dropped. The referent is meaning-bound (no tool produces "the correct route for each mode" — it lives in the §6/§8a spec + frozen enum). The producer (solution-designer) emits a Frame Block per `.claude/knowledge/constraints/frame-declaration.md` enumerating all 7 `MODE_ENUM` values and the route decision for each (this subtask handles support/out_of_scope/unsafe directly; gift_advisor/comparison/advice are routed here and handled in Subtasks 8/9/10; product_search is the existing default).

**Agent**: solution-designer (route topology), then implementer (build).

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 "Single-turn deflections", § 8a (support config schema + validation rules), § 2 (architecture).
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § MODE_ENUM, UNSAFE_ROW_REQUIRED_FIELDS.
- `.claude/knowledge/conversational-agent/architecture.md § Agent (LangGraph State Machine)` — the POST-REBASE entry topology the route must integrate with.
- `docs/v2-design/design-v2-detection-response-shapes.md § Integration Points` — REFERENCE for the proposed node set, but treat its `first_turn_init`/`START → first_turn_init` claims as STALE; reconcile to the live topology.

**Dependencies**: Subtask 1 (state fields), Subtask 6 (composition table — product_search route consumes it; the dispatch route is the parent of the mode handlers).

**Context files**:
- `{session_dir}/` Subtask 6 impl-report — the composition table the product_search route uses.
- `{session_dir}/` Subtask 0 impl-report — the rebased graph topology (node names actually present).

**Expected output**: solution-designer route-topology artifact with the 7-mode Frame Block; implementer lands the `dispatch_route` (or in-node branch) + the 3 deflection handlers + support-YAML loader/validator + the unsafe `turn_events` row. Targeted tests: support keyword OR-match hits the CTA path with zero LLM calls; out_of_scope emits the polite template with no product search; unsafe writes a `turn_events` row with all required fields and emits no UI; mis-dispatch falls back to product_search. Build green. impl-report records Operator Decision #7 default + per-mode LLM-call counts (deflections = 0 on keyword hit).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: yes (route topology — node-before vs branch-inside — is two defensible shapes that propagate to every mode handler).

**UX phase**: no
