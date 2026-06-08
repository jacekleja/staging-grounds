# Subtask 8: gift_advisor turn-1 handler [peer-review]

**Description**: Implement the `gift_advisor` turn-1 downstream handler (mode parsing is DONE; the route lands here from Subtask 7). Per §6 conversational compositions:
- Turn-1 is a CHAT TAKEOVER: search bar stays at top, AI block becomes a chat takeover, shop catalogue results strip hidden (or shows fuzzy matches with a "0 exact matches" note). Sets `chat_takeover_trigger` (state field from Subtask 1) as appropriate.
- Turn-1 chips are ANCHORED CATEGORIES from the guidebook (Hobbies, Lifestyle, Practical, "I have an idea"), NOT LLM-generated guesses. Type-it-out always present.
- A `must_ask_before_recommending` array gates recommendations — the LLM cannot emit recs until those facts are gathered (no products in the AI block turn-1 regardless).

**Operator Decision #9 default (record in impl-report):** anchored chips are SHOP-CONFIGURABLE, mirroring the support-config pattern — an `agent/guidebook/{shop_id}` override of the 4 default anchors (the brief flags that e.g. a bookstore wants different anchors). Ship the 4 defaults + the per-shop override hook.

Preserve the one-LLM-call-turn-1 budget: the anchored chips are static/guidebook (no LLM); the chat-takeover opener is part of the single existing turn-1 call.

**Agent**: implementer

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 "Conversational compositions" (gift_advisor specifics) + § 8a (support-config pattern to mirror for the shop-configurable anchors).
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § TURN_STATE_ENVELOPE_FIELDS (`chat_takeover_trigger`, `prior_search_context`).
- `.claude/knowledge/decisions/conversational-search-muziker-shop-discriminator.md` — multi-shop dispatch uses tracker_id branching; the per-shop guidebook override should follow the same shop-discriminator pattern.

**Dependencies**: Subtask 7 (the dispatch route delivers `gift_advisor` to this handler).

**Context files**:
- `{session_dir}/` Subtask 7 impl-report — the route topology (node-before vs branch-inside) and how the handler is invoked; the support-config loader pattern to mirror.
- `{session_dir}/` Subtask 1 impl-report — `chat_takeover_trigger` field.

**Expected output**: gift_advisor turn-1 emits the chat-takeover composition with anchored category chips (4 defaults + per-shop override), type-it-out, and the `must_ask_before_recommending` gate; no products in the AI block turn-1. Targeted test: gift_advisor turn-1 emits anchored chips (asserting they are guidebook-sourced, not LLM-generated), hides the product strip, and respects the no-recs-before-facts gate; a per-shop-override test. Build green. impl-report records Operator Decision #9 default + turn-1 LLM-call count (expected 1).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (the gift_advisor shape and the shop-configurable-anchors default are fully specified by §6 + Operator Decision #9).

**UX phase**: no
