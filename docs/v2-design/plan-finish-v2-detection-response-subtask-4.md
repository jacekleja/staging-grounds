# Subtask 4: browse_intent hatch-click turn-2 handler [peer-review]

**Description**: Implement the turn-2 handler for the "Just looking / browse" hatch introduced by `refinement_chips_with_hatch` (Subtask 3). When the user clicks the hatch, the FE sends the engagement signal; the agent writes `browse_intent` (state field from Subtask 1) and on turn-2 routes to the browse destination.

**Operator Decision #2 default (record in impl-report):** the hatch destination is a CHAT TAKEOVER with vibe-anchored quick replies (the prototype's shipped behavior — lowest surprise). Per §6 "Browse-hatch destination": softer opening ("Sure — let's just chat. No commitments."), vibe-anchored quick replies + always-available type-it-out, and one quick reply "Throw me some starting points" which produces a thin curated list on turn-2 (products ARE allowed turn-2+, since the no-products commitment is turn-1-only).

Wire against the POST-REBASE topology: turn-2 is NOT turn-1 (`is_first_turn` is derived from `conversation_turn == 1` in `reset_tool_call_count`; turn-2 has `conversation_turn >= 2`, so `products_permitted` is true per the `hard_commitment_gates` in `TURN_STATE_ENVELOPE_FIELDS`). The handler reads `browse_intent` from state and the FE `is_engagement_of_preview` side-channel to distinguish the hatch click.

**Agent**: implementer

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 "Browse-hatch destination (when user clicks Just looking)" and § 3 (products-permitted turn-2+).
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § TURN_STATE_ENVELOPE_FIELDS (`browse_intent` Channel-1; `products_permitted` derived gate; `is_engagement_of_preview` Channel-2).
- `.claude/knowledge/conversational-agent/architecture.md § Agent (LangGraph State Machine)` — turn boundary derivation; the 4-message slice rule context for turn-2.

**Dependencies**: Subtask 1 (`browse_intent` field), Subtask 3 (the hatch block must exist to be clicked).

**Context files**:
- `{session_dir}/` Subtask 3 impl-report — the `hatch:{}` block shape and how the FE signals the click.
- `{session_dir}/` Subtask 1 impl-report — `browse_intent` field name/channel.

**Expected output**: Turn-2 hatch-click routes to the chat-takeover browse destination; `browse_intent` is written on click and read on turn-2; "Throw me some starting points" produces a turn-2 curated list (products allowed). Targeted test: a 2-turn fixture exercising hatch click → browse takeover, asserting `browse_intent` transition and that products ARE emitted on turn-2 (but were NOT on turn-1). Build green. impl-report records the Operator Decision #2 default and the per-path LLM-call count (turn-2 may legitimately call the LLM; turn-1 path unchanged at 1).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (Operator Decision #2 fixes the destination behavior to the prototype default; the state transition is mechanical).

**UX phase**: no
