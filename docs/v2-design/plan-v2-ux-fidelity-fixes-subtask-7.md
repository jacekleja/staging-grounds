# Subtask 7: FIX-5 implement — gift-anchor placeholder localization (code + tests)

**Description**: Localize the gift-advisor takeover anchor labels with PLACEHOLDER sk/cs translations (operator decision D2, R2: placeholder translations are acceptable for fixed, bounded UI-string sets, flagged for native-speaker polish). The anchor set is the FIXED 4-string set in `_DEFAULT_GIFT_GUIDEBOOK_ANCHORS` (`graph.py` l.383–388): "Hobbies & interests", "Lifestyle", "Practical / useful", "I have an idea". Today `graph.py (_render_gift_advisor_takeover_block)` l.1518 emits `"label": anchor.label` — raw English, NOT routed through any localization table.

**What to build:**
- Author provisional, non-native PLACEHOLDER sk and cs strings for the 4 anchor labels. Mark each placeholder explicitly (e.g. a code comment `# PLACEHOLDER — native-speaker polish pending`) so the native-polish follow-up is traceable.
- Route the strings through the CORRECT localization table. The anchor labels are fixed UI strings → place them in the UI-string table (`_UI_STRINGS` / consumed via `_t`). CONFIRM anchor labels belong in `_UI_STRINGS`/`_t` vs. a dedicated anchor-label structure before adding; do not invent a fourth table. Respect the three-table coupling: `_UI_STRINGS`/`_t`, `_TURN1_PREVIEW_INTRO_BY_LANGUAGE`, and `_ISO_TO_LANGUAGE_NAME` each fall back to English INDEPENDENTLY — your new entries must preserve an independent English fallback so an un-added language (or a missing key) still renders the English label.
- Change l.1518 `"label": anchor.label` to route through the localized lookup (e.g. `_t(...)` keyed on the anchor identity) while keeping the anchor's `filter_value` / identity LANGUAGE-NEUTRAL (do not localize the filter value — only the display label).

**Unit tests (CL-8):** (1) for sk and cs, the rendered anchor `label` is the localized placeholder string (≠ English); (2) for an unsupported/absent language, the label falls back to English; (3) the anchor `filter_value` / identity is unchanged across languages (language-neutral). Mirror the existing localization test patterns in the agent test suite.

**No fabricated CATALOGUE data:** this subtask localizes a FIXED UI-string set only. Do NOT touch categorical-chip catalogue labels (that is FIX-4 / subtask 8, which may NOT use placeholders).

**Agent**: implementer

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ multilingual output-localization — the three language-keyed tables and independent English fallback)

**Dependencies**: --

**Context files**:
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-mockup-ux-fidelity-report.md` — the A10 verdict (divergence item 4) this fix closes.

**Expected output**: Modified `graph.py` (`_UI_STRINGS`/`_t` table entries for the 4 anchor labels in sk/cs as placeholders + the l.1518 localized-lookup change); new/updated unit tests under `conversational-search/tests/unit/`. Impl-report with a `## Verification` section: Exercised (tests run + pass count) / Not-exercised (live behavior — deferred to subtask 9; native-speaker polish — deferred as a documented non-blocking follow-up recorded in subtask 11). Return message names the test file, confirms placeholder strings are flagged for native polish, and confirms `filter_value` identity stays language-neutral.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason audit-gap-fix — the plumbing point (l.1518), the table (`_UI_STRINGS`/`_t`), the string set (4 fixed anchors), and the placeholder-with-native-polish-flag policy are all fully constrained; this subtask only builds it.

**UX phase**: no — backend label-resolution on an existing surface; no new layout, no new IA surface. The takeover block already exists; only its label source changes.
