# Subtask 9: advice three-route turn-1 fan-out [peer-review]

**Description**: Implement the `advice` mode turn-1 handler (mode parsing is DONE; the route lands here from Subtask 7). Per §6: advice turn-1 has THREE parallel routes — anchored chips, type-it-out, AND a chat link — the user picks how to express themselves. This is the `type_it_out_parallel_on` channel-3 derived field (hard commitment per R4: always true on turn-1 advice mode, per `TURN_STATE_ENVELOPE_FIELDS § hard_commitment_gates`). Also wire `chat_affordance_on` (derived gate) into the advice turn-1 output.

The gap analysis §5 confirms `chat_affordance_on` and `type_it_out_parallel_on` are defined in `TURN_STATE_ENVELOPE_FIELDS` but are NOWHERE in state.py or graph.py routing — so this subtask is what makes the derived gates actually drive output. They are DERIVED (not separately persisted) per the envelope note: derive them at graph level from the existing fields.

**Operator Decision #4 default (record in impl-report):** advice is ADVISORY-ONLY — the agent may give advisory framing but must NEVER say "don't buy X" (no SKU-level discouragement). The merchandising decision is deferred; this is the safe, reversible-upward default.

Like the other conversational modes: search bar stays, AI block becomes chat takeover, product strip hidden, `must_ask_before_recommending` gate applies, NO products in AI block turn-1. Preserve the one-LLM-call-turn-1 budget — the three routes are presentation affordances on the single turn-1 call's output, not three separate calls.

**Agent**: implementer

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 "Conversational compositions" (advice specifics: three parallel routes) + § 3 (hard commitments).
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § TURN_STATE_ENVELOPE_FIELDS § hard_commitment_gates (`type_it_out_parallel_on`, `chat_affordance_on` — both derived).

**Dependencies**: Subtask 7 (the dispatch route delivers `advice` to this handler).

**Context files**:
- `{session_dir}/` Subtask 7 impl-report — the route topology and handler-invocation convention.
- `{session_dir}/` Subtask 1 impl-report — confirm whether any of the derived gates needed a state field (they are derived, but the chat-takeover may need `chat_takeover_trigger`).

**Expected output**: advice turn-1 emits the three parallel routes; `type_it_out_parallel_on` and `chat_affordance_on` are derived and drive the output; advisory-only framing enforced; no products turn-1. Targeted test: advice turn-1 emits all three routes and the two derived gates are true; an advisory-only guard test (the response never emits SKU-level "don't buy"). Build green. impl-report records Operator Decision #4 default + turn-1 LLM-call count (expected 1).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (the three-route shape and advisory-only default are fully specified by §6 + Operator Decision #4).

**UX phase**: no
