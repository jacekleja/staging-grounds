# Subtask 3: FIX-2 + FIX-3 — chat affordance on decisive + type_it_out on question_led

**Description**: Two co-located edits inside `conversational-search/src/conversational_search/agent/graph.py`, both in/around `_render_turn1_preview_block` (l.1419–1475).

**FIX-2 (chat affordance on decisive/narrow tier):** today the chat affordance is gated at l.1471 on `_COMPOSITION_TIER_BY_CANONICAL_ORDER[composition] in _CHAT_AFFORDANCE_TIERS`, where `_CHAT_AFFORDANCE_TIERS = set(TIER_ENUM[1:])` (l.302) excludes the first tier `decisive`. Include `decisive` so the `refinement_chips` (decisive/narrow) product-search composition carries the chat affordance. Scope: this change is confined to `_render_turn1_preview_block`, which renders ONLY product-search preview compositions — the deflect nodes (`out_of_scope_deflect`, `unsafe_deflect`, `support_deflect`, l.3232–3303) do NOT call this function, so they remain unaffected. Confirm no deflect path gains the affordance (especially the unsafe-refuse path — offering chat after a harm-refusal is wrong). Prefer changing `_CHAT_AFFORDANCE_TIERS` to `set(TIER_ENUM)` (all four tiers) ONLY if that does not leak the affordance to a non-product-search surface; if any takeover/deflect path consumes `_CHAT_AFFORDANCE_TIERS`, instead adjust the l.1471 gate locally. Verify the constant's consumers before choosing.

**FIX-3 (type_it_out on question_led / broad-browse):** the question_led branch (l.1429–1444, `composition == COMPOSITION_ENUM[2]`) emits no `type_it_out` key. Add one, parallel in shape to the gift/advice `type_it_out` block (l.1525–1529): `{"enabled": True, "label": _t(raw_language, "type_it_out"), "style": "free_text"}`. Use the existing `_t(raw_language, "type_it_out")` key (already localized).

Add unit tests for both: (FIX-2) a decisive-tier preview block now contains `chat_affordance`; an unsafe_deflect response does NOT; (FIX-3) a question_led preview block now contains `type_it_out` with the localized label.

**Agent**: implementer

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md` (§ Axis A.2.3 — chat_affordance is server-emitted, not FE-derived)

**Dependencies**: --

**Context files**: none — fully specified from the gap report + code locations in the Description.

**Expected output**: Modified `graph.py`; new/updated unit tests under `conversational-search/tests/unit/`. Impl-report with `## Verification`: Exercised (test pass count; the constant-consumer check result) / Not-exercised (live behavior — deferred to subtask 6). Return message names the chosen FIX-2 approach (constant vs. local gate) and why.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason audit-gap-fix — both edits are precisely located and the affordance shape is given by existing code; the only judgement (constant vs. local gate) is a mechanical safety check, not a design fork.

**UX phase**: no — these are turn-1 payload affordances on existing product-search surfaces; the affordance shapes already exist and ship localized. No new IA surface.
