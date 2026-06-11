# Subtask 2: FIX-1 implement — out_of_scope LLM deflect

**Description**: Replace the hardcoded English template in `conversational-search/src/conversational_search/agent/graph.py (out_of_scope_deflect)` (l.3270–3287) with the LLM-with-guidebook path designed in subtask 1. Build the design sketch verbatim: make exactly one `llm.astream` call (mirror `_handle_gift_advisor_turn1` l.1942–1959 shape — `create_chat_model(with_tools=False, ...)`, accumulate chunks, `RuntimeError` on zero chunks), inject the language via the resolved language name, route the system prompt from the design's chosen source, and keep the existing `_emit_deflection_classification` / `_emit_deflection_text` / `_deflection_update` wiring. Increment `llm_call_count` to 1 for this node. Localization MUST come from real LLM output — do NOT fabricate sk/cs strings. Add unit tests covering: (1) out_of_scope_deflect now invokes the LLM (mock the model, assert one call); (2) `llm_call_count == 1` after the node; (3) the system prompt carries the resolved language name for sk and cs inputs.

**Agent**: implementer

**Knowledge**:
- `.claude/knowledge/constraints/deflection-detection-english-only-vocabulary.md`
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ multilingual output-localization)

**Dependencies**: 1

**Context files**:
- `{session_dir}/fix1-out-of-scope-design.md` — the verbatim design (approach, system-prompt text, language-injection point, single-LLM-call shape) this subtask builds.

**Expected output**: Modified `graph.py (out_of_scope_deflect)`; new/updated unit tests under `conversational-search/tests/unit/`. Impl-report with a `## Verification` section: Exercised (tests run + pass count) / Not-exercised (live behavior — deferred to subtask 5). Return message names the test file and the LLM-call-count assertion result.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason research-only-no-design-decision — the design is fully constrained by subtask 1's sketch; this subtask only builds it.

**UX phase**: no — backend agent node; no user-facing surface layout. No new IA surface.
