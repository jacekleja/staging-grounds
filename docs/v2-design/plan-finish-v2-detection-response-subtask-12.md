# Subtask 12: Turn-2+ entry-kind handling (chip / typed / chat) [solution-design] [peer-review] [completeness-risky-fallback-only]

**Description**: Implement turn-2+ handling — the NAMED DELIVERABLE (concept-id t-4). Per §6 and the architect's "Turn-1 vs turn-2+ detection" section, turn-2+ has THREE entry kinds the agent must differentiate:
1. **Chip click** — incoming `filter_value` from a chip → turn-2 narrowing search (distinct from a typed follow-up).
2. **Typed follow-up** — free text the user types directly.
3. **Chat-affordance open** — the user opens the always-on chat affordance.

The differentiator is the FE-owned side-channel, NOT in-band NLP (architect Rejected Alternative #4: re-deriving entry-kind from text is strictly lossier — a typed query identical to a chip label is indistinguishable in-band). Lean on `is_engagement_of_preview` (Subtask 11 wire) + the Channel-3 fields (`chat_takeover_trigger`, `fork_card_filter_value`). The FE owns the eager/deferred firing toggle.

Turn-2+ INHERITS tier/composition from turn-1 (do not re-classify); add the "← Change the question" pivot for `question_led` (lets the user pivot the narrowing axis, not just refine within it — coordinates with Subtask 5's question_led renderer).

**Operator Decision #8 default (record in impl-report):** chat-affordance routing EXTENDS the existing turn sequence with `prior_search_context` injected (the one-shot Channel-1 thread_metadata field), rather than starting a fresh conversation — inherits context without a thread-create round-trip.

NOTE: the multi-turn `is_first_turn` bug is ALREADY FIXED on feat/v2-campaign (turn number derived from `conversation_turn = len(human messages)`) — do NOT re-plan it. Build the entry-kind differentiation ON TOP of the correct turn boundary.

**[solution-design] required FIRST** — there are 2+ defensible shapes for where entry-kind differentiation lives (a single turn-2 dispatch reading the FE flags, vs. per-entry-kind branches), and how `prior_search_context` is consumed/cleared (the envelope says proxy sets on chat-takeover click, agent clears on consume). The architect rejected a SEPARATE turn-2 detection engine (Rejected Alternative #3: rule drift) — the design must reuse the shared dispatch engine with an entry-kind signal. The solution-designer picks the shape consistent with that constraint.

**[completeness-risky-fallback-only]:** enumerates the 3 entry kinds; a downstream consumer (turn-2 routing + integration tests + coherence audit) is blind if one is dropped. The referent is meaning-bound (the 3 kinds live in §6 + the architect's named deliverable, not a tool output). The solution-designer emits a Frame Block enumerating all 3 entry kinds + the differentiating signal for each + the inheritance rule.

**Agent**: solution-designer (entry-kind dispatch shape), then implementer (build).

**Knowledge**:
- `docs/v2-design/design-v2-detection-response-shapes.md § 6 Turn-1 vs turn-2+ detection (NAMED DELIVERABLE)` + Rejected Alternatives #3/#4 — REFERENCE.
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 (`question_led` "← Change the question" turn-2 pivot; browse turn-2; products-permitted turn-2+).
- `conversational-search/src/conversational_search/agent/canonical_enums.py § TURN_STATE_ENVELOPE_FIELDS` — `is_engagement_of_preview` (Channel-2), `chat_takeover_trigger`/`fork_card_filter_value` (Channel-3), `prior_search_context` (Channel-1 one-shot), `products_permitted` derived gate.

**Dependencies**: Subtask 4 (browse_intent turn-2 handler — a related turn-2 path), Subtask 6 (composition table — turn-2 inherits composition), Subtask 11 (proxy wire fields the entry-kind detection reads).

**Context files**:
- `{session_dir}/` Subtask 11 impl-report — the proxy wire fields + how they reach agent state.
- `{session_dir}/` Subtask 6 impl-report — how composition is computed (for turn-2 inheritance).
- `{session_dir}/` Subtask 4 impl-report — the existing turn-2 browse path to integrate with, not duplicate.
- `{session_dir}/` Subtask 5 impl-report — the question_led renderer for the "← Change the question" pivot.

**Expected output**: solution-designer entry-kind dispatch artifact with the 3-entry-kind Frame Block; implementer lands the differentiation (chip vs typed vs chat) via the FE side-channel, tier/composition inheritance, the question_led pivot, and `prior_search_context` inject/clear. Targeted tests: chip-click turn-2 narrows via `filter_value`; typed follow-up takes the text path; chat-affordance open extends the sequence with `prior_search_context`; a typed-query-identical-to-chip-label test confirms the FE signal (not in-band text) disambiguates. Build green. impl-report records Operator Decision #8 default + per-path LLM-call counts (turn-2 calls are legitimate; turn-1 unchanged).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: yes (entry-kind dispatch shape + prior_search_context consume/clear is two defensible shapes constrained by the shared-engine requirement).

**UX phase**: no
