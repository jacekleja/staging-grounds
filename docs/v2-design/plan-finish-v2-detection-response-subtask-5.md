# Subtask 5: Composition renderers — question_led + hard_fork [solution-design] [peer-review]

**Description**: Implement the two broad/overwhelming composition renderers per handoff brief §6, emitting structured LBJSON.

- **`question_led` (broad):** emit `question:{prompt, answers:[]}` — ONE diagnostic question (from the strongest discriminating axis) + 2 answer cards (third folded into carousel), a DEMOTED sub-search carousel (smaller chips, mono label), a tiny "Show me all N results →" link, PLUS the always-on chat affordance. Primary surface is the question. **Turn-2 must also include a "← Change the question" affordance** so the user can pivot the narrowing axis (this turn-2 piece coordinates with Subtask 12).
- **`hard_fork` (overwhelming):** emit a fork card with `fork_card_filter_value` (state field from Subtask 1). "12 400 results is too many to scan. {q}" + 2 strong fork cards (primary tinted accent), NO carousel (at this scale 12 sub-searches multiplies the problem), tiny "Show me all N — just sorted by popularity" link.

**[solution-design] required FIRST** — `question_led`'s diagnostic question is "model-generated" per §6, which creates a real tension with the one-LLM-call-turn-1 commitment (concept-id t-3). Two defensible shapes:
- **(A) fold question generation into the SAME turn-1 LLM call** (the single existing call emits both the search-shaping output AND the diagnostic question in its structured output) — preserves the 1-call budget but constrains prompt design.
- **(B) template/axis-driven question** (no model generation; pick the question deterministically from the strongest discriminating axis signal) — zero LLM cost, but less adaptive phrasing.
The solution-designer picks A or B with rationale. §9.5 names a question-quality eval set as the gate for question_led going live; the chosen shape must be compatible with that gate. Whatever is chosen MUST NOT add a second turn-1 LLM call (architect Rejected Alternative #1).

Wire against the POST-REBASE topology (inside `handle_regular_turn` / `compile_system_prompt`, not a retired `first_turn_init`).

**Agent**: solution-designer (design phase), then implementer (build phase). Orchestrator inserts pre-flight-gate after the solution-designer output.

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 (`question_led`, `hard_fork`) and § 9.4/§9.5 (question-led phase + question-quality eval gate) and § 3 (hard commitments).
- `.claude/knowledge/conversational-agent/architecture.md § Agent (LangGraph State Machine)` — turn-1 = ONE LLM call; injection inside `handle_regular_turn`.
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § COMPOSITION_ENUM, TURN_STATE_ENVELOPE_FIELDS (`fork_card_filter_value` Channel-3).

**Dependencies**: Subtask 1 (`fork_card_filter_value` field), Subtask 2 (tier wire — `composition` available; shadow-mode gating).

**Context files**:
- `{session_dir}/` Subtask 1 impl-report — `fork_card_filter_value` field name/channel.
- `{session_dir}/` Subtask 2 impl-report — how `composition` reaches the renderer; shadow-mode gating.

**Expected output**: solution-designer artifact picks A or B for the question with rationale + eval-gate compatibility note; implementer lands both renderers emitting the §6 LBJSON shapes. Targeted unit tests: `question:{prompt,answers[]}` shape + demoted carousel + chat affordance; `hard_fork` 2 cards + `fork_card_filter_value` + NO carousel. Build green. impl-report records the A/B question choice and the turn-1 LLM-call count (MUST be 1).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: yes (question-generation A-vs-B is two defensible shapes with a direct call-budget consequence).

**UX phase**: no
