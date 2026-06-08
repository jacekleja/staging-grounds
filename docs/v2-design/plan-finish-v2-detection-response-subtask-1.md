# Subtask 1: State envelope — add the 3 missing fields + tier-signal fields

**Description**: On the rebased branch (`feat/v2-campaign-rebased`), add the state fields that downstream subtasks read/write. This lands FIRST because `state.py` is read by everything. Add to `ConversationState` in `conversational-search/src/conversational_search/agent/state.py`:
- `browse_intent` (Channel-1 thread_metadata per `TURN_STATE_ENVELOPE_FIELDS`; written on "Just looking" hatch click) — currently absent.
- `chat_takeover_trigger` (Channel-3 per_turn_sse; FE flag on "Chat with me instead") — currently absent.
- `fork_card_filter_value` (Channel-3 per_turn_sse; FE flag on hard_fork card click, carries filter_value) — currently absent.
- Any tier-signal observability fields the tier wire (Subtask 2) needs to LOG in shadow mode (e.g. `tier_signals`, `classifier_path`) — add them here so Subtask 2 has somewhere to write. Use `CLASSIFIER_PATH_ENUM` / `TIER_EXTRA_STATES` from `canonical_enums.py` as the value vocabulary; do NOT redefine values locally.

Each field's channel placement MUST match `TURN_STATE_ENVELOPE_FIELDS` in `canonical_enums.py` exactly (Channel 1 = thread_metadata, Channel 2 = trimmed_messages, Channel 3 = per_turn_sse). Do NOT touch the already-present fields (`tier`, `composition`, `mode_stack`, `mode_stack_depth`, `mode_at_compile`, `firing_mode`, `conversation_turn`, `prior_search_context`) — the gap analysis confirms those are wired.

**Agent**: implementer

**Knowledge**:
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § TURN_STATE_ENVELOPE_FIELDS — the authoritative channel/field map; the 3 missing fields are listed there.
- `docs/v2-design/research-v2-gap-and-base.md` § 4 State Envelope — the DONE-vs-MISSING field breakdown (the 3 missing fields verified absent from state.py).

**Dependencies**: Subtask 0 (rebase must land first — these edits are against the rebased topology).

**Context files**:
- `{session_dir}/` Subtask 0 impl-report — confirms the rebased branch name and that `state.py` is in its post-rebase shape.

**Expected output**: `state.py` carries the 3 missing fields + tier-signal log fields with correct channel placement and types; the existing `test_state_shape.py` (state field presence) is extended to assert the new fields and passes; build green. impl-report names each added field, its channel, and the test result. LLM-call count: this subtask touches no LLM path (state-only); state that explicitly.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason trivial-file-edit (field set and channel placement are fully specified by the frozen envelope; no defensible alternative shape).

**UX phase**: no
