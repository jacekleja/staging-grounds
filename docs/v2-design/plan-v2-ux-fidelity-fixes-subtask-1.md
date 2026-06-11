# Subtask 1: FIX-1 design — out_of_scope LLM-with-guidebook path

**Description**: Decide HOW out_of_scope_deflect should make its one LLM call to produce a short, polite, no-apologies, language-adaptive reply. The decision has a genuine fork because there is NO out_of_scope guidebook source today (only `graph.py` `_gift_guidebook_anchors` / `guidebook/<shop>.yaml` exist, gift-advisor-only — confirmed l.365–818). Resolve: (a) author a new out_of_scope guidebook section/file the deflect reads, OR (b) a system-prompt-only approach (no new guidebook artifact, tone instructed in the prompt). Produce the chosen system-prompt text (the no-apologies/short/polite/language-adaptive instruction), name where the language is injected (mirror `_resolve_language_name` + `_ISO_TO_LANGUAGE_NAME` used by `_handle_gift_advisor_turn1` l.1934), and confirm the single-`astream` shape keeps `llm_call_count == 1`. Output a sketch the implementer (subtask 2) builds verbatim. Do NOT write graph.py code — produce the design sketch only.

**Agent**: solution-designer

**Knowledge**:
- `.claude/knowledge/constraints/deflection-detection-english-only-vocabulary.md`
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ multilingual output-localization — three language-keyed tables)

**Dependencies**: --

**Context files**:
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-mockup-ux-fidelity-report.md` — the DIVERGENT verdict for out_of_scope (item 1, §6 / §D copy check) the design must satisfy.
- `/home/fanderman/projects/luigis-box/docs/_handoff-pack/03 · Handoff brief.md` — §6 out_of_scope spec ("LLM with guidebook: short, polite, no apologies") the design must honor.

**Expected output**: A design sketch at `{session_dir}/fix1-out-of-scope-design.md` naming: the chosen approach (guidebook vs system-prompt-only) with rationale; the verbatim system-prompt text; the language-injection point; the single-LLM-call shape (mirroring `_handle_gift_advisor_turn1` l.1942–1959); and an explicit answer to the open sub-question "does an out_of_scope guidebook entry need authoring?" Return message states the approach chosen and flags any residual operator decision.

**active_rubrics**: ["generator-preflight"]

**Design phase**: yes — two defensible approaches (new guidebook artifact vs. system-prompt-only) and prompt-content authoring; the implementer cannot proceed without this pick.

**UX phase**: no — this is a deflect-tier text-response design (LLM prompt shape), not a user-facing interaction-surface layout. No new IA surface.

**[peer-review]** — the deflect-tone prompt design is substantive (it defines the localized, no-apologies behavior A1-budget-bound) and benefits from a cross-family read; solution-designer output routes through pre-flight-gate by default but the prompt-content judgement is worth a substance check.
