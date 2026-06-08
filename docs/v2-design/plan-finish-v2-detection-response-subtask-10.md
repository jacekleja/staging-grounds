# Subtask 10: comparison response shape [peer-review]

**Description**: Implement the `comparison` mode RESPONSE SHAPE (mode parsing AND the mode-stack LIFO push/pop are DONE per gap analysis §5 — graph.py already pops `comparison` off the stack when a turn resolves to a different mode; that is the D.4 stack mechanic). What is MISSING: the comparison-specific response shape — a structured two-product side-by-side affordance block. There is currently no comparison output shape.

Per §6: comparison is invocable mid-flow — the dispatcher detects "vs / or / compare" tokens in ANY turn of an existing conversation, swaps composition for that single turn, then RESTORES the prior mode's state. The transition needs an inline mode-shift note ("comparison detected, swapping side-by-side for this turn"). This subtask builds the side-by-side output block and the inline mode-shift note; the stack push/pop that restores prior state is already present — wire the new shape into the existing stack mechanic, do NOT reimplement the LIFO.

**Operator Decision #6 default (record in impl-report):** mode_stack is LIFO depth 3 (already the frozen-enum/prototype assumption; this is a "confirm the default" call, not new work — verify the existing push/pop honors depth 3 and the new shape respects it).

Mid-flow comparison can occur on turn-2+ (it is invocable in any turn), so it depends on the correct turn boundary — the gap analysis confirms the multi-turn `is_first_turn` bug is ALREADY FIXED on feat/v2-campaign (`is_first_turn = conversation_turn == 1` in `reset_tool_call_count`), so do NOT re-plan that fix. Preserve the call budget: a mid-flow comparison turn is its own turn with its own single LLM call.

**Agent**: implementer

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 "Conversational compositions" (comparison invocable mid-flow, inline mode-shift note, LIFO depth 3).
- `docs/v2-design/research-v2-gap-and-base.md` § 5 (comparison: mode-stack pop at graph.py:402-404 is DONE; response shape is MISSING) + § 7 (is_first_turn fix already landed).
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § MODE_ENUM (`comparison` mode-stack note).

**Dependencies**: Subtask 7 (the dispatch route delivers `comparison` to this handler; the route topology determines how a mid-flow swap re-enters).

**Context files**:
- `{session_dir}/` Subtask 7 impl-report — the route topology + how mid-flow mode swaps are handled.
- `{session_dir}/` Subtask 0 impl-report — confirm the mode-stack LIFO push/pop is intact post-rebase (graph.py:402-404 region).

**Expected output**: comparison emits the two-product side-by-side block + the inline mode-shift note; the new shape wires into the EXISTING mode-stack LIFO (push/pop unchanged) and respects depth 3; prior mode state restores after the comparison turn. Targeted test: a mid-flow comparison turn within an existing product_search conversation emits the side-by-side shape, pushes/pops the stack correctly (depth ≤ 3), and restores the prior mode on the next turn. Build green. impl-report records Operator Decision #6 confirmation + per-turn LLM-call count.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (the side-by-side shape is specified by §6; the stack mechanic it wires into already exists — no new design decision).

**UX phase**: no
