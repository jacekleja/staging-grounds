# Subtask 6: (mode, tier) → composition decision table [peer-review]

**Description**: Implement the `(mode, tier) → composition` mapping per handoff brief §6 and make the composition renderer branch on the TABLE output rather than accepting whatever the LLM emits as the `COMPOSITION:` prefix. Today (gap analysis §3) the composition value is parsed from the LLM prefix; this subtask makes the table authoritative.

The canonical product_search tier→composition mapping (R4 vocabulary, §6):
- `decisive` (narrow) → `refinement_chips`
- `shapeable` (mid) → `refinement_chips_with_hatch`
- `exploratory` (broad) → `question_led`
- `intractable` (overwhelming) → `hard_fork`

For non-`product_search` modes, the composition is the mode's own shape (chat takeover etc.) — the table must cover the mode dimension too, not just tier. Use `MODE_ENUM` / `TIER_ENUM` / `COMPOSITION_ENUM` from `canonical_enums.py` as the closed key/value sets; the table MUST be total over the (mode, tier) product or have an explicit documented fallback for the cells that don't apply (e.g. tier is only meaningful for `product_search`).

**SHADOW MODE coupling (Operator Decision #1):** in shadow mode the table COMPUTES the composition and LOGS it, but the live composition-switch stays gated until tier boundaries are calibrated (Subtask 2). The table must support a "compute + log, do not switch" mode and a "live" mode behind a flag.

Wire against the POST-REBASE topology.

**Agent**: implementer

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 6 (the per-tier composition mapping) and § 5 (tier vocabulary).
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § MODE_ENUM / TIER_ENUM / COMPOSITION_ENUM and the R4-vocabulary note (narrow→decisive etc.).
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md` — tier→composition mapping provenance (axis-a.2).

**Dependencies**: Subtask 2 (tier wire + shadow-mode flag), Subtask 3 (`refinement_chips` / `refinement_chips_with_hatch` renderers exist), Subtask 5 (`question_led` / `hard_fork` renderers exist). The table is the join point for all 4 product compositions.

**Context files**:
- `{session_dir}/` Subtask 2 impl-report — the shadow-mode flag mechanism.
- `{session_dir}/` Subtask 3 + Subtask 5 impl-reports — the 4 renderer entry points the table dispatches to.

**Expected output**: A `(mode, tier) → composition` table that drives the renderer; the renderer no longer trusts the raw LLM `COMPOSITION:` prefix for product_search. Targeted unit test: each of the 4 product_search (tier → composition) cells maps correctly and dispatches the right renderer; a shadow-mode test asserts compute+log without live switch. Build green. impl-report records the shadow-mode default and the LLM-call count (table is pure logic; no LLM call added).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (the mapping is fully specified by §6; the only variability — shadow vs live — is fixed by Operator Decision #1's default).

**UX phase**: no
