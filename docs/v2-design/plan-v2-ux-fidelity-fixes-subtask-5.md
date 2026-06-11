# Subtask 5: LIVE verify FIX-1 — out_of_scope LLM + sk/cs localization

**Description**: Confirm against the live stack that out_of_scope now makes an LLM call and localizes. Apply the freshness methodology + host workarounds from `signature-cache-validation-freshness-report.md`: force a fresh MISS before each live read (no cache hit), python-json-DECODE the SSE (unicode-escaped), extract proxy pid by address-match, psql via stdin/literals as needed. Stack: proxy `http://127.0.0.1:8000`, langgraph `http://127.0.0.1:2024`, tracker `8760-9189`.

**Critical methodology constraint (from `constraints/deflection-detection-english-only-vocabulary.md`):** out_of_scope detection is English-keyword-only. A Slovak out_of_scope prompt collapses to product_search and NEVER reaches `out_of_scope_deflect`. To prove sk/cs localization you MUST use an English-keyword out_of_scope query (e.g. a "weather"-class prompt that hits `_OUT_OF_SCOPE_KEYWORDS`) and force the OUTPUT language via the request metadata `language` field (sk, then cs) — NOT via a Slovak-language prompt. Verify the detection actually routed to out_of_scope (check `mode`/`dispatch_rationale_token`) before asserting on localization.

**Assertions to capture:** (1) the out_of_scope response shows `llm_call_count == 1` (was 0 — proves the LLM call now fires); (2) the reply is short, polite, contains NO apology; (3) the sk-forced and cs-forced reads render localized (non-English) text — real LLM output, distinct from the old hardcoded English template; (4) the en read still works. Capture each MISS read (request + decoded SSE) to a run file.

**Agent**: validator

**Knowledge**:
- `.claude/knowledge/constraints/deflection-detection-english-only-vocabulary.md`

**Dependencies**: 2

**Context files**:
- `/home/fanderman/projects/luigis-box/docs/v2-design/signature-cache-validation-freshness-report.md` — the binding freshness / MISS-decode methodology + host workarounds.
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-final-state-gap-closure-conformance-report.md` — the prior live-verification report shape to mirror.

**Expected output**: A verification report capturing the 4 assertions with decoded-SSE evidence, written to `docs/v2-design/_runs/` (worktree-absolute: `/home/fanderman/projects/luigis-box/.agent_context/worktrees/session-1781106672-6469-2465b2ba685c/docs/v2-design/_runs/fix1-out-of-scope-live-verify.md`). `## Verification` section split: Exercised (the MISS reads actually run, with mode-routing confirmation) / Not-exercised (Slovak-PROMPT out_of_scope — structurally unreachable per the English-keyword constraint; bounded reason). Return message states PASS/FAIL per assertion.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-exercise-only.

**UX phase**: no — live payload verification, not surface authoring.
