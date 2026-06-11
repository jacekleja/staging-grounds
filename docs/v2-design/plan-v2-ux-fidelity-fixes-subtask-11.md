# Subtask 11: FIX-4 disposition + A10-now-wired note + A4 deferral documentation

**Description**: Produce the conscious-deferral / follow-up document that records every gap NOT fully closed by this campaign and every documented non-blocking follow-up, so nothing is silent. Write a deferral note covering:

- **FIX-4 (A6 categorical-chip localization):** record the disposition from subtask 8 (WIRED or DEFERRED). If DEFERRED, transcribe the subtask-4 investigation finding (what the upstream facet feed does/does not carry) and state the deferral reason + the plumbing point (`turn1_selector.py (select_chips)` — categorical chips set `label == filter_value == raw v["value"]`). State explicitly: **placeholder translations were NOT used and must NOT be used here** — categorical chip labels are live catalogue DATA (arbitrary per-query category/brand names), not a fixed UI-string set; fabricating placeholder translations would fabricate catalogue data. The only valid future fix is wiring REAL upstream localized labels once the feed provides them.
- **A10 / FIX-5 (gift-anchor label localization) — NOW WIRED with placeholders (NOT a deferral; a documented FOLLOW-UP):** record that the 4 fixed anchor labels were localized with PLACEHOLDER sk/cs strings in subtask 7 (plumbing point `graph.py (_render_gift_advisor_takeover_block)` l.1518, routed through `_UI_STRINGS`/`_t` with independent English fallback; `filter_value` language-neutral). Carry the **native-speaker-polish follow-up** as an explicit, non-blocking deferral: the placeholder strings ship now and are flagged for a later native-speaker pass. Name where the placeholder strings live so the polish pass can find them.
- **A4 (engagement-of-preview state inheritance):** turn-2-only observable; the gap report marks it NOT-EXERCISED with plumbing present (`is_engagement_of_preview` in Channel 2; `_is_browse_hatch_engagement` / `_resolve_turn2_entry_kind`). Disposition: either note as already-implemented-unverified (turn-1-scope campaign) OR, if cheap, recommend an optional turn-2 verification. Make the disposition conscious — state which and why.

This is a documentation deliverable (a deferral / follow-up note), not a code change. The note feeds the coherence-auditor (subtask 13 verifies CL-5/CL-6/CL-7 against this).

**Agent**: implementer

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md` (§ label/data-resolution validation missing)

**Dependencies**: 4, 8

**Context files**:
- `{session_dir}/fix4-a6-upstream-investigation.md` — subtask 4's recommendation + finding, transcribed into the FIX-4 section.
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-mockup-ux-fidelity-report.md` — the A10/A4 verdicts (item 4; A4 NOT-EXERCISED) the note documents.

**Expected output**: A deferral / follow-up note at `/home/fanderman/projects/luigis-box/.agent_context/worktrees/session-1781106672-6469-2465b2ba685c/docs/v2-design/v2-ux-fidelity-deferrals.md` with one section per item: FIX-4 disposition (with the catalogue-data-vs-UI-string note), A10/FIX-5 native-speaker-polish follow-up (placeholder strings shipped, polish pending), and A4 disposition — each naming the plumbing point and the conscious reason. Return message states the FIX-4 disposition, confirms the FIX-5 native-polish follow-up is recorded, and confirms no fabricated catalogue-data translations.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason audit-gap-fix — the content is determined by the gap report + subtasks 4/8's dispositions; this records dispositions, it does not decide them.

**UX phase**: no — documentation artifact; no user-facing surface.
