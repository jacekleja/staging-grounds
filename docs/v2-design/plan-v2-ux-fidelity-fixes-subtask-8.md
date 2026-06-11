# Subtask 8: FIX-4 conditional wire — categorical-chip localization (degrades to defer-disposition)

**Description**: ALWAYS RUNS and ALWAYS COMPLETES; the WORK is conditional on subtask 4's recommendation, NOT the execution. This is the properly-typed code-authoring owner of any FIX-4 wire (resolves gate finding G-R1-PF-1 — no localization wire is routed through a researcher or a doc-only subtask).

**Branch on subtask 4's recommendation:**
- **WIRE-RECOMMENDED (real upstream localized labels exist)** → land the wire + unit tests. In `turn1_selector.py (select_chips)`, set categorical-facet (`category_upto_lvl_1`, `brand`) chip `label` from the upstream localized display name (the field subtask 4 identified), while keeping `filter_value` LANGUAGE-NEUTRAL (the raw identity value). Respect the three-localization-table coupling (route any graph-side string through the correct table) and the turn1_selector↔graph circular-import constraint (function-level `from conversational_search.agent.graph import _t` if `_t` is needed). Add unit tests: categorical chip `label` ≠ `filter_value` when upstream provides a localized name; `filter_value` stays raw/identity; `facet` identity stable across languages. Emit an impl-report with a code diff. Disposition: `FIX-4: WIRED`.
- **DEFER-RECOMMENDED (no real upstream localized labels)** → emit a NO-OP defer-disposition. Make NO code change. Do NOT fabricate sk/cs catalogue-data translations (the catalogue-data-vs-UI-string distinction forbids placeholder translations for arbitrary per-query catalogue names). The subtask still COMPLETES (it satisfies its dependents 11 and 12); it simply lands no diff and hands the deferral content to subtask 11. Disposition: `FIX-4: DEFERRED — no upstream localized label`.

**DAG note:** this subtask never breaks the DAG — on DEFER it completes with a no-op disposition rather than skipping. The only genuinely conditional/skippable subtask downstream is the FIX-4 LIVE verify (subtask 10), which runs only on `WIRED`.

**Agent**: implementer

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ turn1_selector imports graph._t via function-level import; § three language-keyed tables — independent English fallback)
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md` (§ label/data-resolution validation missing — the A6 gap)

**Dependencies**: 4

**Context files**:
- `{session_dir}/fix4-a6-upstream-investigation.md` — subtask 4's recommendation + the upstream-feed finding; determines WIRE vs. DEFER and (if WIRE) which field carries the localized name.
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-mockup-ux-fidelity-report.md` — the A6 PARTIAL verdict (divergence item 5) this fix scopes against.

**Expected output**: IF WIRED — modified `turn1_selector.py (select_chips)` + new unit tests under `conversational-search/tests/unit/`; impl-report with a code diff and a `## Verification` section (Exercised: tests run + pass count / Not-exercised: live behavior — deferred to subtask 10). IF DEFERRED — no code; impl-report stating the no-op disposition and that the deferral content is handed to subtask 11; `## Verification` section affirms "no new behavior to live-verify; subtask 10 skips." Return message states the disposition in one line: `FIX-4: WIRED` (names the test file) or `FIX-4: DEFERRED — <reason>`.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason audit-gap-fix — the decision rule and both branches are fully constrained by subtask 4's recommendation; this subtask only acts on it.

**UX phase**: no — backend label-resolution on an existing chip surface; no new layout, no new IA surface.
