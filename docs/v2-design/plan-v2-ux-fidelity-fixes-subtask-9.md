# Subtask 9: LIVE verify FIX-5 — gift-anchor placeholder localization (sk/cs)

**Description**: Confirm against the live stack that the gift-advisor takeover anchor labels now render the localized PLACEHOLDER sk/cs strings landed in subtask 7, distinct from the English fallback, while the anchor `filter_value` identity stays language-neutral. Apply the freshness methodology + host workarounds from `signature-cache-validation-freshness-report.md`: force a fresh MISS before each live read (no cache hit), python-json-DECODE the SSE (unicode-escaped). Stack: proxy `http://127.0.0.1:8000`, langgraph `http://127.0.0.1:2024`, tracker `8760-9189`.

**Reads:** a turn-1 query that routes to the gift-advisor takeover (so the takeover block with the 4 anchors renders), forced to sk then cs via request metadata `language`, plus an en baseline. Assert: (1) for sk and cs, each anchor `label` is the localized placeholder string (≠ the English label) — matching the placeholder strings landed in subtask 7; (2) the en read renders the English label (independent English fallback intact); (3) the anchor `filter_value` / identity is the same language-neutral value across sk/cs/en. Capture each MISS read (request + decoded SSE) to a run file.

**Note on placeholder quality:** these are provisional non-native strings flagged for a later native-speaker polish (a documented non-blocking follow-up in subtask 11). The live verify confirms the WIRING (localized label renders, fallback intact, filter_value neutral) — it does NOT judge translation quality.

**Agent**: validator

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ multilingual output-localization — independent English fallback; identity-stability evidence shape)

**Dependencies**: 7

**Context files**:
- `/home/fanderman/projects/luigis-box/docs/v2-design/signature-cache-validation-freshness-report.md` — the binding freshness / MISS-decode methodology + host workarounds.
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-final-state-gap-closure-conformance-report.md` — the prior live-verification report shape to mirror.

**Expected output**: A verification report capturing the 3 assertions with decoded-SSE evidence, written to `/home/fanderman/projects/luigis-box/.agent_context/worktrees/session-1781106672-6469-2465b2ba685c/docs/v2-design/_runs/fix5-gift-anchor-localization-live-verify.md`. `## Verification` section split: Exercised (the sk/cs/en MISS reads actually run, with takeover-routing confirmation) / Not-exercised (native-speaker translation quality — out of scope; deferred as a documented follow-up). Return message states PASS/FAIL per assertion.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-exercise-only.

**UX phase**: no — live payload verification, not surface authoring.
