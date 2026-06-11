# Subtask 10: LIVE verify FIX-4 — categorical-chip localization (CONDITIONAL on WIRED)

**Description**: CONDITIONAL — run ONLY if subtask 8's disposition is `FIX-4: WIRED`. If subtask 8 returned `FIX-4: DEFERRED`, the orchestrator SKIPS this subtask (there is no new behavior to verify; the deferral is documented in subtask 11). A SKIPPED subtask 10 COUNTS AS A SATISFIED dependency for subtask 13 — coherence audit must not stall waiting for it (see the plan's "Conditional-subtask skip semantics" section).

If WIRED: confirm against the live stack that categorical-facet chips (`category_upto_lvl_1`, `brand`) now render a localized `label` distinct from the language-neutral `filter_value`. Apply the freshness methodology + host workarounds from `signature-cache-validation-freshness-report.md`: force a fresh MISS, python-json-DECODE the SSE. Stack: proxy `http://127.0.0.1:8000`, langgraph `http://127.0.0.1:2024`, tracker `8760-9189`.

**Reads:** a product-search query that yields categorical chips, forced to sk then cs via request metadata `language`. Assert: chip `label` is the localized display name from the REAL upstream feed (matches the upstream localized value subtask 4 identified, NOT a fabricated/placeholder string and NOT the raw identity); chip `filter_value` remains the raw/identity value (language-neutral); the same chip's `facet` identity is stable across languages. Mirror the C-23/C-24 identity-stability checks from the conformance report.

**Catalogue-data guard:** the localized label MUST originate from real upstream data — if the live read shows the label is identical to `filter_value` (no real localization) or shows a fabricated string, that is a FAIL (the wire did not actually resolve real upstream localized data).

**Agent**: validator

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ czech question-led selection identity fields — prior live identity-stability evidence shape)

**Dependencies**: 8

**Context files**:
- `{session_dir}/fix4-a6-upstream-investigation.md` — confirms the WIRED disposition and what upstream localized label to expect.
- `/home/fanderman/projects/luigis-box/docs/v2-design/signature-cache-validation-freshness-report.md` — freshness / MISS-decode methodology + host workarounds.
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-final-state-gap-closure-conformance-report.md` — prior live-verification report shape to mirror.

**Expected output**: IF WIRED — a verification report at `/home/fanderman/projects/luigis-box/.agent_context/worktrees/session-1781106672-6469-2465b2ba685c/docs/v2-design/_runs/fix4-a6-localization-live-verify.md` with sk/cs decoded-SSE evidence and the `label ≠ filter_value` + identity-stable + real-upstream-data assertions. IF SKIPPED (DEFERRED upstream) — the orchestrator records the skip; no artifact. Return message states PASS/FAIL per assertion or SKIPPED-DEFERRED.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-exercise-only.

**UX phase**: no — live payload verification, not surface authoring.
