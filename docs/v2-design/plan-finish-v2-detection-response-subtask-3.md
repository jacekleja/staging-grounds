# Subtask 3: Composition renderers — refinement_chips + refinement_chips_with_hatch [peer-review]

**Description**: Implement the two narrow/mid composition renderers per handoff brief §6, emitting structured LBJSON the frontend consumes. These two ship together because they share the chip-emit path.

- **`refinement_chips` (narrow):** emit `chips:[{label, filter_value, facet, count}]`, 2–4 chips max, no browse hatch. Anti-pattern guard: do NOT show a chip row for sub-30 results — go straight to results. Reuse/extend the existing chip-emit path (the gap analysis notes an earlier-generation `turn1_selector.py` vocabulary exists but is SUPERSEDED — do not revive `products_only`/`chips_only`; emit the new structured shape).
- **`refinement_chips_with_hatch` (mid):** the chips block PLUS a `hatch:{}` block ("Just browsing — show me popular searches", quiet 12px grey link) PLUS the always-on chat affordance (dashed pill below chips). The hatch click is consumed in Subtask 4 (turn-2 handler) and writes `browse_intent` (added in Subtask 1).

HARD COMMITMENTS (preserve): NO products in the AI block on turn-1; always-on chat affordance present per §6; turn-1 stays at exactly ONE LLM call (these renderers fold into the system-prompt / post-search emit path — they MUST NOT add a second turn-1 LLM call; per the architect's Rejected Alternative #1, a composition node that re-invokes the LLM is forbidden).

Wire against the POST-REBASE topology: turn-1 preview injection is gated INSIDE `handle_regular_turn` (NOT a retired `first_turn_init` node). The composition-specific instruction block is appended in `compile_system_prompt`; the structured emit happens in the existing single-stream emit path.

**`facets_csv_capped` default (Operator Decision #10):** if the CSV cap fires and axes are lost, fall back to single-axis chips — do NOT refuse to emit. Record this default in the impl-report.

**Agent**: implementer

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 (`refinement_chips`, `refinement_chips_with_hatch`, Browse-hatch destination) and § 3 (hard commitments).
- `.claude/knowledge/conversational-agent/architecture.md § Agent (LangGraph State Machine)` — turn-1 preview injection is inside `handle_regular_turn`; turn-1 = ONE LLM call.
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § COMPOSITION_ENUM.

**Dependencies**: Subtask 1 (`browse_intent` state field), Subtask 2 (tier wire — the renderer needs `tier`/`composition` available even though live-switch is gated; in shadow mode the renderer can be exercised by forcing `composition`).

**Context files**:
- `{session_dir}/` Subtask 1 impl-report — `browse_intent` field name/channel.
- `{session_dir}/` Subtask 2 impl-report — how `composition` reaches the renderer (A/B wire choice) and the shadow-mode gating.

**Expected output**: Both renderers emit the §6 LBJSON shape; the sub-30 anti-pattern guard and the `facets_csv_capped` single-axis fallback are implemented. Targeted unit tests: one per renderer asserting the exact emitted block shape (`chips[]` fields; `hatch:{}` presence; chat-affordance presence); a test asserting NO product block on turn-1. Build green. impl-report MUST report the turn-1 LLM-call count for the paths touched (expected: 1) and the `facets_csv_capped` default assumed.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (the §6 shapes are fully specified field-by-field; the renderer reuses the existing emit path — no defensible alternative architecture).

**UX phase**: no
