# Plan: v2 UX-Fidelity Fixes (turn-1 conversational-search)

## Revision (R2)

This plan was revised after operator review + the pre-build gate. Two drivers:

- **Operator decision reversal (D2).** The operator clarified, verbatim: *"I meant that's fine to use placeholder translations."* PLACEHOLDER (provisional, non-native) Slovak/Czech translations are now ACCEPTABLE — flagged for a later native-speaker polish. This supersedes the prior "no fabricated/machine translations" stance and folds previously-deferred localization back in WHERE IT SENSIBLY APPLIES (A10/FIX-5 — a fixed bounded UI-string set — is now WIRED; A6/FIX-4 stays investigation-governed because its labels are live catalogue DATA, not a fixed UI-string set — see the explicit distinction below).
- **Pre-build gate verdict = concerns/request-changes** (`docs/v2-design/_gates/v2-ux-fidelity-fixes-plan-preflight.md`; 0 critical, 1 important, 2 low). The important gap **G-R1-PF-1** (FIX-4 WIRED branch had no properly-typed code-authoring subtask inside the validation/verify dependency sets) is resolved: every localization wire is now authored by a properly-typed IMPLEMENTER subtask sitting inside both the code-validation and the live-verify dependency sets. The two low advisories (CL-9 mapping, FIX-4-defer → coherence skip semantic) are made explicit.

What changed structurally: subtask 4 (FIX-4) is now PURE investigation (the contradictory "researcher hands WIRED code OR orchestrator inserts a follow-up" routing is removed); FIX-5 (gift-anchor) gains a real implementer subtask + a live-verify subtask; the FIX-4 conditional wire is a real implementer subtask that degrades to a no-op-disposition (not a DAG-breaking skip); code-validation and coherence renumbered to sit after all implements/verifies. Total subtask count: **15 → 18**.

**Already-executed dependents at risk:** none — the plan was operator-reviewed and pre-build-gated but NOT yet run (the campaign had not started execution).

## Goal (verbatim)

> Author an implementation plan to fix the divergences a UX-fidelity audit found between the implemented v2 conversational-search turn-1 behavior and the design handoff's intended contracts. The plan will be operator-reviewed before any code changes, then run as an implement → live-verify → commit campaign.

Source gap list: `docs/v2-design/v2-mockup-ux-fidelity-report.md` (4 PARTIAL + 1 DIVERGENT + 1 NOT-EXERCISED). Intended contracts: `docs/_handoff-pack/03 · Handoff brief.md` (§3 hard commitments; §6 per-mode specs; §8 guardrails).

## Operator decisions baked into this plan

1. **out_of_scope → FULL LLM-with-guidebook path** (FIX-1). Replace the hardcoded English template in `graph.py (out_of_scope_deflect)` (l.3270–3287) with an LLM call using the guidebook to produce a short, polite, no-apologies, LANGUAGE-ADAPTIVE reply, within the existing one-LLM-call-on-turn-1 budget (A1 must not break). Localization comes from real LLM output — NOT fabricated strings. **(UNCHANGED by R2.)**

2. **Placeholder translations ARE acceptable** (REVERSED from the prior "no fabricated/machine translations" — operator verbatim: *"I meant that's fine to use placeholder translations."*). Provisional, non-native sk/cs translations may be authored NOW for **fixed, bounded UI-string sets**, each such string flagged for a later native-speaker polish pass (carried as a documented follow-up, not a blocker). This reversal is applied per-fix according to whether the strings are a fixed UI-string set or live catalogue data:
   - **A10 / FIX-5 (gift-anchor labels) → WIRED.** The 4 anchor labels ("Hobbies & interests", "Lifestyle", "Practical / useful", "I have an idea") are a FIXED, bounded, 4-string set. Placeholder sk/cs is sensible and safe here. Localize via the correct localization table with PLACEHOLDER strings (flagged for native polish); route the `graph.py (_render_gift_advisor_takeover_block)` l.1518 `"label": anchor.label` through a localized lookup with independent English fallback. Properly-typed IMPLEMENTER subtask (code + unit tests) PLUS a live-verify subtask, both inside the validation net.
   - **A6 / FIX-4 (categorical chip labels) → STAYS investigation-governed, with an explicit data-vs-UI-string distinction.** Categorical chips (`category_upto_lvl_1`, `brand`) carry LIVE CATALOGUE DATA from the upstream facet feed — arbitrary, per-query category/brand names — NOT a fixed UI-string set. Placeholder translations DO NOT sensibly apply: pre-authoring placeholder translations for arbitrary catalogue category names would fabricate CATALOGUE data (worse than fabricating UI strings). So FIX-4 remains: investigate whether the upstream facet feed exposes a localized label field; if YES → wire (real data; `label ≠ filter_value`; `filter_value` stays language-neutral); if NO → document the gap. **No one fabricates catalogue-data translations.**

## Assumptions

- **A-1 (session role):** This is an L1-root session; the live stack is session-owned and already UP (proxy `http://127.0.0.1:8000`, langgraph `http://127.0.0.1:2024`; tracker `8760-9189` muziker.sk). If wrong, the verification subtasks' live-read steps invalidate (they assume a reachable stack) — they would need a stack-bring-up prefix. Invalidates: live-verify subtasks 5, 6, 9, 10.
- **A-2 (commit target):** Code fixes land on the agent-repo feature branch `feat/v2-campaign-rebased` (L1 push-to-origin); new docs land on the staging-grounds `knowledge-store-langgraph` branch. Do NOT push to main/master. If wrong, invalidates: housekeeping Commit+Push subtask (16).
- **A-3 (placeholder-translation acceptability, R2):** Operator confirmed placeholder sk/cs translations are acceptable for fixed bounded UI-string sets, flagged for native-speaker polish. If this is later reversed again, invalidates FIX-5 (subtask 7) and its live verify (subtask 9). The native-speaker-polish follow-up is a documented non-blocking deferral, recorded in subtask 11's deferral note.

## Knowledge Consulted

- `decisions/conversational-search-v2-marathon-findings-digest.md` § multilingual output-localization — THREE language-keyed tables (`_UI_STRINGS`/`_t`, `_TURN1_PREVIEW_INTRO_BY_LANGUAGE`, `_ISO_TO_LANGUAGE_NAME`); "single lookup point" claim is FALSE.
- `constraints/deflection-detection-english-only-vocabulary.md` — out_of_scope detection is English-keyword-only; Slovak input bypasses the deflect and collapses to product_search. Critical scope bound on FIX-1 live verification.
- `decisions/conversational-search-v2-discovery-digest.md` § label/data-resolution validation missing — categorical chips set `label == filter_value == raw v["value"]`; only price-fallback emits a distinct (hardcoded-Czech) label. Confirms A6 is live-catalogue-data, not a fixed UI-string set; basis for FIX-4 investigation.
- `decisions/conversational-search-v2-discovery-digest.md` § A.2.3 payload-slot trim — chat_affordance is server-emitted today via `_derive_chat_affordance_on` (not purely FE-derived); confirms FIX-2 surface.
- `conversational-search-v2-marathon-findings-digest.md` § turn1_selector imports graph._t via function-level import — circular-dep coupling; relevant to FIX-4 if wiring touches turn1_selector.
- Code: `graph.py` l.295–326 (tier/affordance constants), l.1415–1475 (`_render_turn1_preview_block`), l.1509–1569 (takeover blocks; l.1518 anchor label emit), l.1923–1975 (`_handle_gift_advisor_turn1` one-LLM pattern), l.3270–3303 (deflect nodes), l.365–818 (gift guidebook config — there is NO out_of_scope guidebook today), l.383–388 (`_DEFAULT_GIFT_GUIDEBOOK_ANCHORS` — the 4-anchor source).

## Implementation Checklist

The handoff brief §3/§6/§8 contracts these fixes must satisfy. Each item is the contract the coherence-auditor verifies against.

- **CL-1 (FIX-1, §6 out_of_scope):** out_of_scope_deflect makes an LLM call using a guidebook source; reply is short, polite, NO apologies, language-adaptive (sk/cs/en from real LLM output). `llm_call_count == 1` (A1 not broken). Hardcoded English template removed.
- **CL-2 (FIX-1 sub-decision):** the out_of_scope guidebook source/prompt is authored or its absence is resolved by an explicit design choice (new guidebook section vs. system-prompt-only); the chosen source produces the no-apologies/short/polite tone.
- **CL-3 (FIX-2, §3 A8):** chat affordance present on the decisive/narrow product-search tier (`refinement_chips` composition), scoped to product-search + conversational surfaces; NOT added to any deflect path (support/unsafe/out_of_scope), and specifically NOT after the unsafe-refuse path.
- **CL-4 (FIX-3, §3 A9):** `type_it_out` block emitted on the question_led (broad-browse / exploratory) surface in `_render_turn1_preview_block`, parallel in shape to the advice/gift `type_it_out` block.
- **CL-5 (FIX-4, §3 A6 — investigation-governed, catalogue-DATA distinction):** EITHER categorical chip labels are language-resolved from REAL upstream facet data (`label ≠ raw filter_value` where upstream provides a localized name) AND `filter_value` identity stays language-neutral; OR the gap is documented as a conscious deferral with the upstream-feed finding recorded. **No fabricated CATALOGUE-data translations either way** — placeholder translations do NOT apply to FIX-4 because the labels are arbitrary live catalogue data, not a fixed UI-string set.
- **CL-6 (A10 / FIX-5 — WIRED with placeholders):** gift-anchor labels (the fixed 4-string set) are localized via the correct localization table with PLACEHOLDER sk/cs strings, each flagged for a native-speaker-polish follow-up; the l.1518 `"label": anchor.label` emit is routed through a localized lookup with INDEPENDENT English fallback; `filter_value` identity stays language-neutral; unit tests assert localized label for sk/cs and English fallback. The native-speaker-polish follow-up is recorded as a documented non-blocking deferral.
- **CL-7 (A4 disposition):** engagement-of-preview state inheritance is either turn-2-verified live OR explicitly noted as already-implemented-unverified (turn-2-only observable). Conscious disposition, not silent.
- **CL-8 (tests):** each code fix adds unit tests; full agent test suite passes.
- **CL-9 (live re-verification — explicit mapping):** force-MISS SSE-decode live reads confirm each NEW behavior on the right surface, captured under `docs/v2-design/_runs/`. Explicit coverage map: **FIX-1 → subtask 5**; **FIX-2 + FIX-3 → subtask 6**; **FIX-5 gift-anchor placeholder localization → subtask 9**; **FIX-4 categorical-chip localization → subtask 10 (only if WIRED; SKIPPED-and-documented if DEFERRED)**.

## Coupling Analysis

- **Three localization tables, not one.** Any label-localization work (FIX-4 wire AND FIX-5 wire) MUST account for `_UI_STRINGS`/`_t`, `_TURN1_PREVIEW_INTRO_BY_LANGUAGE`, and `_ISO_TO_LANGUAGE_NAME` falling back to English independently. A "fix `_t` only" claim is known-false.
  - **FIX-5 (gift-anchor labels)** are fixed UI strings → route the placeholder sk/cs strings through the UI-string table (`_UI_STRINGS`/`_t`) — the implementer confirms anchor labels belong there vs. a dedicated anchor table, and preserves the independent English fallback at l.1518.
  - **FIX-4 (categorical-chip labels)**, if it wires, routes the REAL upstream localized name through the correct table(s); it does NOT author placeholder strings (catalogue data).
- **Catalogue-DATA vs. UI-STRING distinction (R2, binding).** FIX-5 localizes a FIXED, bounded UI-string set → placeholder translation is safe. FIX-4 localizes LIVE CATALOGUE DATA (arbitrary per-query category/brand names) → placeholder translation is FORBIDDEN (it would fabricate catalogue data). The plan states this so no one mistakes the D2 reversal as licensing fabricated catalogue-name translations.
- **turn1_selector ↔ graph circular import.** `turn1_selector.py` imports `graph._t` via a function-level import to avoid the circular dep (graph imports `select_turn1_options`). FIX-4, if it wires localization in `turn1_selector.select_chips`, MUST use the function-level import pattern.
- **out_of_scope detection is English-keyword-only.** FIX-1's live verification can ONLY exercise out_of_scope via an English keyword-hit query (e.g. "weather"); a Slovak out_of_scope prompt collapses to product_search and never reaches the node. The sk/cs localization proof for FIX-1 must force the OUTPUT language via metadata on an English-keyword-detected out_of_scope query — NOT via a Slovak-language out_of_scope prompt. Hard methodology constraint for live-verify subtask 5.
- **One-LLM-call budget (A1).** FIX-1 adds an LLM call to a node that today makes zero. out_of_scope is a turn-1 deflect terminal — it does not also run `regular_turn`, so adding ONE call keeps `llm_call_count == 1`. The implementer must confirm out_of_scope_deflect is a terminal node with no other LLM call on its path (mirror the `_handle_gift_advisor_turn1` single-`astream` pattern, l.1942–1959).
- **FIX-2 / FIX-3 co-located.** Both edits are inside `_render_turn1_preview_block` (l.1419–1475) — FIX-2 touches the affordance gate (l.1471 / the `_CHAT_AFFORDANCE_TIERS` constant l.302), FIX-3 touches the question_led branch (l.1429–1444). Same function, same agent, same tests file — merged into one subtask.

## Conditional-subtask skip semantics (R2 — explicit)

The FIX-4 branch is investigation-governed. To keep the DAG executable under BOTH dispositions:

- **Subtask 8 (FIX-4 conditional wire) ALWAYS RUNS and ALWAYS COMPLETES.** Its WORK is conditional on subtask 4's finding, NOT its execution: if subtask 4 found real upstream localized data → subtask 8 lands the wire + unit tests; if NOT → subtask 8 emits a no-op DEFER disposition (no code change) and hands the deferral to subtask 11. Either way subtask 8 completes and satisfies its dependents (11, 12). It is never a DAG-breaking skip.
- **Subtask 10 (LIVE verify FIX-4) is the only genuinely conditional subtask.** It runs ONLY if subtask 8 landed a wire (WIRED). If subtask 8 deferred, the orchestrator SKIPS subtask 10 — there is no new behavior to verify; the deferral is documented in subtask 11 and audited by subtask 13. **A skipped subtask 10 counts as a SATISFIED dependency for subtask 13** (coherence audit must not stall waiting for a subtask 10 that will never run). CL-9's FIX-4 row reads "subtask 10 (only if WIRED; SKIPPED-and-documented if DEFERRED)"; CC-5 covers both branches.

## Subtask Summary Table

| # | Title | Agent | Depends On | Subtask File |
|---|-------|-------|------------|--------------|
| 1 | FIX-1 design: out_of_scope LLM-with-guidebook path | solution-designer | -- | plan-v2-ux-fidelity-fixes-subtask-1.md |
| 2 | FIX-1 implement: out_of_scope LLM deflect | implementer | 1 | plan-v2-ux-fidelity-fixes-subtask-2.md |
| 3 | FIX-2 + FIX-3: chat affordance on decisive + type_it_out on question_led | implementer | -- | plan-v2-ux-fidelity-fixes-subtask-3.md |
| 4 | FIX-4 investigate (A6 upstream-feed; data-vs-UI-string) | researcher | -- | plan-v2-ux-fidelity-fixes-subtask-4.md |
| 5 | LIVE verify FIX-1 (out_of_scope LLM + sk/cs localization) | validator | 2 | plan-v2-ux-fidelity-fixes-subtask-5.md |
| 6 | LIVE verify FIX-2 + FIX-3 (affordances on right surfaces) | validator | 3 | plan-v2-ux-fidelity-fixes-subtask-6.md |
| 7 | FIX-5 implement: gift-anchor placeholder localization (code + tests) | implementer | -- | plan-v2-ux-fidelity-fixes-subtask-7.md |
| 8 | FIX-4 conditional wire: categorical-chip localization (degrades to defer-disposition) | implementer | 4 | plan-v2-ux-fidelity-fixes-subtask-8.md |
| 9 | LIVE verify FIX-5 (gift-anchor placeholder localization on sk/cs) | validator | 7 | plan-v2-ux-fidelity-fixes-subtask-9.md |
| 10 | LIVE verify FIX-4 (categorical-chip localization — CONDITIONAL on WIRED) | validator | 8 | plan-v2-ux-fidelity-fixes-subtask-10.md |
| 11 | FIX-4 disposition + A10-now-wired note + A4 deferral documentation | implementer | 4, 8 | plan-v2-ux-fidelity-fixes-subtask-11.md |
| 12 | Code validation pass (all fixes vs. spec + constraints) | validator | 2, 3, 7, 8, 11 | (inline) |
| 13 | Coherence audit vs. Implementation Checklist | coherence-auditor | 5, 6, 9, 10, 12 | (inline) |
| 14 | /cycling terminal [housekeeping] | orchestrator | 13 | (inline) |
| 15 | Session Audit [housekeeping] | orchestrator | 14 | (inline) |
| 16 | Commit + Push [housekeeping] | orchestrator | 15 | (inline) |
| 17 | /cycling terminal — finalize sentinel [housekeeping] | orchestrator | 16 | (inline) |
| 18 | Knowledge-Hygiene Pipeline [housekeeping] | orchestrator | 17 | (inline) |

Subtasks 1–11 carry full delegation bodies in their referenced `plan-v2-ux-fidelity-fixes-subtask-N.md` files. Subtasks 12, 13, and the housekeeping suffix (14–18) are inline below.

## Parallelism

- Subtasks 1, 3, 4, 7 have no dependencies and may dispatch in parallel at the start (1=FIX-1 design; 3=FIX-2+3 impl; 4=FIX-4 investigate; 7=FIX-5 gift-anchor impl).
- Subtask 2 follows 1; 5 follows 2; 6 follows 3; 9 follows 7; 8 follows 4; 10 follows 8 (conditional on WIRED); 11 follows 4 and 8.
- Subtask 12 (code validation) gates on all code-landing + deferral subtasks: 2, 3, 7, 8, 11. Subtask 13 (coherence) gates on all live-verifies + code-validation: 5, 6, 9, 10, 12 (a skipped subtask 10 counts as satisfied — see skip semantics above).
- DAG note: all dependency edges point to strictly lower-numbered subtasks (no forward refs); no cycles. Conditional subtask 10 degrades to skip-counts-as-satisfied without breaking the chain.

---

### Subtask 12: Code validation pass (all fixes vs. spec + constraints)

**Description**: Validate the landed code changes against the `code-vs-spec` and `constraint-compliance` rubrics: FIX-1 (subtask 2), FIX-2+FIX-3 (subtask 3), FIX-5 gift-anchor placeholder localization (subtask 7), and the FIX-4 wire IF it landed (subtask 8). Confirm: out_of_scope_deflect makes exactly one LLM call and the hardcoded template is gone (CL-1); the chat affordance was NOT added to any deflect path including unsafe-refuse (CL-3); the question_led `type_it_out` matches the advice/gift shape (CL-4); the gift-anchor labels (CL-6) are localized via the correct table with placeholder sk/cs strings carrying a native-polish flag, `filter_value` identity stays language-neutral, and English fallback is preserved; the three-localization-table coupling and the turn1_selector circular-import constraint were respected by any FIX-4 wire; **NO fabricated CATALOGUE-data translations were introduced for FIX-4** (catalogue-data vs. UI-string distinction); the FIX-4 DEFER branch (if taken) is documented in subtask 11, not coded; the full agent unit-test suite passes (CL-8). This is static code review against spec + constraints — live behavior is covered by subtasks 5/6/9/10.

**Agent**: validator

**Knowledge**:
- `.claude/knowledge/constraints/deflection-detection-english-only-vocabulary.md`
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ multilingual output-localization)

**Dependencies**: 2, 3, 7, 8, 11

**Context files**:
- `{session_dir}/fix1-out-of-scope-design.md` — the FIX-1 design contract to validate the implementation against.
- `{session_dir}/fix4-a6-upstream-investigation.md` — the FIX-4 disposition (validates whether a wire landed in subtask 8 and, if so, against what upstream data).
- `{session_dir}/docs/v2-design/v2-ux-fidelity-deferrals.md` — the deferral note (subtask 11), to confirm deferrals (FIX-4-if-deferred, A4, the FIX-5 native-polish follow-up) are documented rather than coded.

**Expected output**: Validation verdict (approve / request-changes) against the named rubrics, with any critical/important issues itemized. Return message states the verdict and the test-suite pass count.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-exercise-only.

**UX phase**: no — code review, not surface authoring.

---

### Subtask 13: Coherence audit vs. Implementation Checklist

**Description**: Audit the campaign for completeness against the Implementation Checklist (CL-1 … CL-9) above — NOT code quality (subtask 12 owns that). Confirm every checklist item is either satisfied by a landed change + its live verification, OR is a documented conscious deferral in `v2-ux-fidelity-deferrals.md`. Specifically verify: CL-5 (FIX-4 reached a conscious WIRED-or-DEFERRED disposition with the catalogue-data distinction honored — no fabricated catalogue-data translations); CL-6 (gift-anchor labels WIRED with placeholder sk/cs, native-polish follow-up flagged); CL-7 (A4 disposition conscious); CL-9 (each NEW behavior live-verified per the explicit map — FIX-1→5, FIX-2+3→6, FIX-5→9, FIX-4→10-or-documented-skip). A skipped subtask 10 (FIX-4 DEFERRED) counts as satisfied IF the deferral is documented in subtask 11. Name any checklist item that is neither satisfied nor consciously deferred as a gap.

**Agent**: coherence-auditor

**Knowledge**: none consulted (audits against this plan's checklist + the produced artifacts).

**Dependencies**: 5, 6, 9, 10, 12

**Context files**:
- `{session_dir}/docs/v2-design/plan-v2-ux-fidelity-fixes.md` — this plan; the Implementation Checklist is the contract audited against.
- `{session_dir}/docs/v2-design/v2-ux-fidelity-deferrals.md` — the deferral note; confirms CL-5 (if FIX-4 deferred)/CL-6 native-polish-flag/CL-7 conscious dispositions.
- `{session_dir}/docs/v2-design/_runs/` — the live-verification reports (subtasks 5, 6, 9, and 10-if-wired) confirming NEW behavior.

**Expected output**: Coherence verdict (complete / gaps) with each checklist item marked satisfied / consciously-deferred / gap. Return message states the verdict and lists any gaps.

**active_rubrics**: ["cross-artifact-coherence"]

**Design phase**: no with reason verification-exercise-only.

**UX phase**: no — completeness audit, not surface authoring.

---

### Subtask 14: /cycling terminal [housekeeping]

**Description**: Invoke `/cycling` in terminal-mode to promote marathon findings to the knowledge store and emit the completion sentinel. Terminal-mode handles findings promotion internally; per S6b, digests are cycle-mode-only and are NOT run in terminal-mode.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 13 (audit passes)

**Verification (PROMOTION_DONE-OR-HANDOFF-DONE — disjunctive predicate, MUST satisfy at least ONE branch before HK-2 begins):**
- **Branch A — terminal-mode completed.** Run `smart_bash: [ -f {session_dir}/promotion-complete ] && echo DONE || echo PENDING`. Branch passes if output is `DONE`.
- **Branch B — handoff-mode completed.** Run `smart_bash: ls -1t {session_dir}/cycle-checkpoint_*.json 2>/dev/null | head -n1`. If a path is returned, read the file and check the JSON `cycle_reason` field (underscore form). Branch passes if `cycle_reason` ∈ {`handoff-post-task`, `handoff-mid-task`}.
- **HALT** if BOTH branches fail. Do NOT proceed to HK-2. Surface: "HK-1a verification failed: promotion-complete sentinel absent AND no cycle-checkpoint with handoff cycle_reason. Re-invoke `/cycling terminal`." Then re-run this Verification step.

**Operator-facing terminal summary (HARD-GATED — MUST be present before surfacing "done"):** Compose from the `## Verification` sections of every per-subtask producer impl-report — do NOT invent. Aggregate flatly: every `Exercised:` bullet lands under `Verified end-to-end:`; every `Not exercised, and why:` bullet lands under `Not verified, and why:`. Empty "Not verified" is legal ONLY when affirmatively stated. Do NOT surface "done" without this split present.

---

### Subtask 15: Session Audit [housekeeping]

**Description**: Run `session(action='audit')` to aggregate tool telemetry. Skip if fewer than 3 subagent invocations in this session (this campaign has ≥10, so it runs).

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 14. MUST observe PROMOTION_DONE-OR-HANDOFF-DONE from HK-1a Verification before starting.

---

### Subtask 16: Commit + Push [housekeeping]

**Description**: Commit all code changes and integrate them upstream. This is an L1-root session — run the **L1 protocol** (push to `origin`). Role detection: `[ -n "$CAA_CHILD_SIDECAR_DIR" ]` will be empty for this L1 session → L1 protocol.

**Branch target (A-2):** code fixes are agent-repo (`conversational-search/`) — push to the agent feature branch `feat/v2-campaign-rebased` (L1 push-to-origin). New docs (the design sketch, deferral note, investigation report, run reports under `docs/v2-design/`) add to the staging-grounds `knowledge-store-langgraph` branch. Do NOT push to main/master.

Follow the full L1 protocol from the housekeeping-subtask-templates: acquire the main-mutate mutex via `flock`; pre-push merge (`git fetch` + `git merge origin/$BRANCH`, real merge not rebase); targeted `git add <path>` per path only (never `git add -A` / `-am`); respect the six in-place render-target skip-worktree paths (run `bin/render_divergence_check.py` only if an intentional divergence must persist); probe-instrument cleanup on both surfaces if any throwaway hook was authored (none expected here); push via `git push origin HEAD:$BRANCH`; post-push SHA-equality verification; on verified push write `{session_dir}/push-result.txt` with `pushed=success\nsha=<sha>`. On any failure path do NOT write the file (absence is the PUSH_FAILED signal). Use `set -o pipefail` (or `${PIPESTATUS[0]}`) — never `if ! cmd | tee ...`.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 15 (session audit)

---

### Subtask 17: /cycling terminal — finalize sentinel [housekeeping]

**Description**: Invoke `/cycling terminal` sentinel-finalization step (HK-1b sub-phase) after Commit + Push succeeds. Emits the `SESSION-COMPLETION-SENTINEL` with `status='success'` to the change-log.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 16 (commit + push). GATED on HK-3 success — for L1, check `{session_dir}/push-result.txt` contains `pushed=success`. If absent or lacking the success token, do not run this step.

**Verification (TERMINAL_FINALIZED):**
- Run `knowledge(action='change-log', file_exact='{session_dir}/SESSION-COMPLETION-SENTINEL', actor='external:cycling-terminal-sentinel', limit=1)`. Step passes if returned `entries[]` contains ≥1 entry with `status === 'success'`.
- Do NOT use `[ -f {session_dir}/SESSION-COMPLETION-SENTINEL ]` — the sentinel is a change-log entry only.

---

### Subtask 18: Knowledge-Hygiene Pipeline [housekeeping]

**Description**: Invoke the knowledge-hygiene pipeline (the Study Orchestrator) via CLI Bash (NOT the Agent tool). Fire-and-forget: `Bash(run_in_background=true, command="cd $MAIN_ROOT && bin/claude-study post-completion")`, capture the bash_id, yield turn immediately. Do NOT poll `BashOutput` post-launch.

**This change touches knowledge-relevant files** (docs under `docs/v2-design/`, and the campaign will likely surface knowledge drift on the localization/deflection constraints — in particular the placeholder-translation reversal vs. the prior "no fabricated translations" stance), so the Knowledge-Hygiene Pipeline MUST run — the skip condition (diff <5 lines AND no knowledge-relevant files) does not apply.

**Pre-launch mutex probe (DEFER / HUNG / LAUNCH):** read `.claude/knowledge/.study-state` JSON. LAUNCH if `running: false`/absent. DEFER if `running: true` AND `now - running_since < 35min` (record `H4 DEFERRED`; do NOT re-invoke). HUNG if `running: true` AND `>= 35min` (surface to user to clear, then fresh LAUNCH).

**Note:** HK-4 (the Study Orchestrator) DOES commit and push the knowledge edits it makes, scoped to the knowledge-store paths only — disjoint from HK-3's code/doc commit. Exit-code-2-on-success caveat applies: do not treat non-zero background exit as failure without inspecting runDir artifacts.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 17 (/cycling terminal — finalize sentinel)

---

## Post-Completion

The housekeeping subtasks above (/cycling terminal, Session Audit, Commit + Push, /cycling terminal — finalize sentinel, Knowledge-Hygiene Pipeline) cover the post-loop steps. After they complete: surface any deferred Proposed Criteria Additions (PCAs) and unmet completion criteria in the final response to the user — in particular the conscious deferrals (A10/FIX-5 gift-anchor placeholder strings → native-speaker polish follow-up; A6/FIX-4 if it DEFERRED on no upstream localized data; A4 turn-2 verification disposition).

## Completion Criteria

- **CC-1:** validator (subtask 12) finds no critical or important issues against `code-vs-spec` + `constraint-compliance` for all landed fixes.
- **CC-2 (CL-1, CL-2, CL-9):** FIX-1 live verification (subtask 5) shows out_of_scope makes exactly one LLM call (`llm_call_count == 1`), produces a short/polite/no-apologies reply, and renders sk AND cs localized output (forced via metadata language on an English-keyword out_of_scope query), captured under `docs/v2-design/_runs/`.
- **CC-3 (CL-3, CL-4, CL-9):** FIX-2 + FIX-3 live verification (subtask 6) shows the chat affordance on a decisive/narrow product-search response AND `type_it_out` on a question_led/broad-browse response; AND confirms NO chat affordance on an unsafe-refuse response.
- **CC-4 (CL-6, CL-9):** FIX-5 live verification (subtask 9) shows the gift-anchor takeover labels render the localized PLACEHOLDER sk and cs strings (distinct from the English fallback) on the gift-advisor surface, with `filter_value` identity language-neutral, captured under `docs/v2-design/_runs/`. The placeholder strings are flagged for a native-speaker polish follow-up (documented in subtask 11, non-blocking).
- **CC-5 (CL-5, CL-9):** FIX-4 reaches a conscious disposition — wired-and-live-verified (subtask 10) OR documented deferral (subtask 11) with the upstream-feed finding recorded. **No fabricated CATALOGUE-data translations in either branch.** A SKIPPED subtask 10 (DEFERRED) is satisfied iff the deferral is documented.
- **CC-6 (CL-7):** A4 disposition is documented (subtask 11), conscious and plumbing-point-identified — verified present by the coherence-auditor (subtask 13).
- **CC-7 (CL-8):** every code fix added unit tests; the full agent test suite passes.
- **CC-8:** coherence-auditor (subtask 13) reports the Implementation Checklist complete (or names exactly which items are conscious deferrals).

## Open sub-decisions surfaced for operator/implementer

- **OSD-1 (FIX-1 guidebook content):** There is NO out_of_scope guidebook entry/prompt today — the only guidebook is gift-advisor-only (`graph.py` `_gift_guidebook_anchors` / `guidebook/<shop>.yaml`, l.365–818). FIX-1 design (subtask 1) must decide: author a new out_of_scope guidebook section/file, OR use a system-prompt-only approach (tone instructed in the prompt, no new artifact). The plan routes this to `solution-designer` (subtask 1) because it is a genuine two-approach fork plus prompt-content authoring. The operator may pre-empt subtask 1 by stating a preference at review time.
- **OSD-2 (FIX-4 conditionality):** Whether A6 categorical-chip localization is WIRED or DEFERRED depends on subtask 4's upstream-feed investigation. Subtask 8 handles both branches (wire+tests, live-verified via subtask 10; or no-op defer-disposition documented via subtask 11); no operator action needed unless the operator wants to force one branch. Note: FIX-4 may NOT use placeholder translations (catalogue-data distinction).
- **OSD-3 (FIX-5 placeholder authorship, R2):** the placeholder sk/cs anchor strings are authored by the implementer (subtask 7) as provisional non-native text, each flagged for native-speaker polish. The operator may supply native strings at review time to skip the placeholder stage; otherwise the placeholder strings ship with the native-polish follow-up recorded.

Pre-emission self-audit: 18 subtasks present (11 with referenced bodies, 7 inline). All 9 checklist items map to ≥1 subtask + ≥1 completion criterion — explicitly: CL-1→{2,5,CC-2}; CL-2→{1,2,CC-2}; CL-3→{3,6,CC-3}; CL-4→{3,6,CC-3}; CL-5→{4,8,10/11,CC-5}; CL-6→{7,9,CC-4}; CL-7→{11,13,CC-6}; CL-8→{2,3,7,8,12,CC-7}; **CL-9 (now explicitly mapped)→{5,6,9,10} and CC-2/CC-3/CC-4/CC-5**. The D2 reversal is reflected: placeholder translations acceptable for fixed UI-string sets (A10/FIX-5 WIRED with placeholders + native-polish flag), FIX-4 stays investigation-governed with the catalogue-data-vs-UI-string distinction stated in the Decision-2 block, Coupling Analysis, CL-5, and subtasks 4/8/11. G-R1-PF-1 resolved: NO localization wire is routed through a researcher or doc-only implementer — FIX-5 wire = implementer subtask 7, FIX-4 wire = implementer subtask 8, both inside code-validation (12) and live-verify (9, 10) dependency sets; the contradictory "researcher hands WIRED code OR orchestrator inserts a follow-up" routing is removed from subtask 4. The two low advisories are tightened: CL-9 carries an explicit subtask→behavior map, and the FIX-4-DEFERRED → subtask-10-skip / subtask-13 satisfied semantic is stated in the "Conditional-subtask skip semantics" section. The English-keyword-only out_of_scope constraint is carried into FIX-1 verification (subtask 5) so a Slovak-prompt false-negative cannot pass. A4 disposition routed to subtask 11 as a conscious disposition. Housekeeping suffix is the 5-subtask templated sequence ending in Commit+Push to `feat/v2-campaign-rebased` (L1) + knowledge-store docs to `knowledge-store-langgraph`. DAG checked: all edges point to strictly lower-numbered subtasks (no forward references), no cycles; conditional subtask 10 degrades to skip-counts-as-satisfied without breaking the chain.
