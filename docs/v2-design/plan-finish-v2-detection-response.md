# Plan: Finish v2 Conversational-Search Detection + Response-Shape System

## Goal (verbatim)

> Produce an implementation plan to FINISH the v2 conversational-search detection + response-shape system. The work is substantially started on a feature branch; your job is to decompose only the REMAINING work into ordered, independently-verifiable subtasks. Output is a plan document.

Task id: `plan-finish-v2-detection-response`. Round: R1.

## STATUS (2026-06-09) — COMPLETE

All implementation subtasks **0–13 are DONE and cross-family reviewed**; the coherence audit (subtask 14) returned **verdict = complete, zero gaps** — every checklist item C1–C11 satisfied and every concept-id t-1..t-6 covered. Housekeeping (subtasks 15–19) is in progress. The work landed across **three repositories** (this plan originally assumed two — the proxy is in fact a separate nested git repo with its own remote):

| Repo | Branch | Remote | Contents |
|---|---|---|---|
| `conversational-search` (agent) | `feat/v2-campaign-rebased` | luigisbox/conversational-search | rebase + agent-side subtasks 0–13 (renderers, decision table, mode dispatch, turn-2 handling, turn-1 budget gate) |
| `conversational-proxy` | `reconcile/proxy-v2-the-rest-on-origin-master` | luigisbox/conversational-proxy | ConverseRequest v2 wire fields + tier observability + Option-A `prior_search_context` lifecycle |
| `luigisbox` (parent) | default | this repo | design docs + submodule pointer |

### Small-PR / review-unit boundaries (Topic 3)

Each feature branch carries clean per-subtask commits, reviewable commit-by-commit. Suggested bounded, independently-reviewable slices:

1. **Base** — rebase reconciliation (subtasks 0 / 0b): rebased branch + proxy reconciliation (option A).
2. **Spine** — state envelope + tier wire + 4 composition renderers + (mode,tier) decision table (subtasks 1–6); ships shadow-mode by default.
3. **Modes** — mode dispatch + 3 deflections + gift_advisor / advice / comparison handlers (subtasks 7–10).
4. **Proxy wire** — ConverseRequest v2 fields + cross-repo parity (subtask 11) — the `conversational-proxy` PR.
5. **Turn-2** — entry-kind handling + `prior_search_context` lifecycle (subtask 12; spans both agent and proxy repos).
6. **Budget gate** — integration tests + turn-1 single-call assertion (subtask 13).

Reviewers can take these as separate PRs or review each feature branch in these slices.

## Assumptions

The plan PROCEEDS on each architect-recommended default (operator is asleep; defaults are reversible, not blockers). Each is recorded in the subtask that depends on it so it can be confirmed/corrected asynchronously. If a default proves wrong, the listed subtask is the one to revisit:

- **Tier boundaries ship in SHADOW MODE first** (Operator Decision #1) — log tier + signals to `turn_events`, do NOT switch composition on the live thresholds until calibrated against logged traffic. Invalidates: Subtask 2 (tier wire) and Subtask 6 (decision table) if the operator wants live-from-day-1.
- **`browse_intent` turn-2 hatch → chat-takeover with vibe-anchored quick replies** (Operator Decision #2). Invalidates: Subtask 4.
- **Mis-dispatch fallback target = `product_search`** (Operator Decision #7). Invalidates: Subtask 7.
- **`comparison` mode_stack depth = 3, LIFO** (Operator Decision #6; already in frozen enum). Invalidates: Subtask 10.
- **`advice` advisory-only, never "don't buy X"** (Operator Decision #4). Invalidates: Subtask 9.
- **Chat-affordance routing = extend existing turn sequence with `prior_search_context` injected** (Operator Decision #8). Invalidates: Subtask 12.
- **Gift anchored chips shop-configurable** (Operator Decision #9), mirror support-config pattern. Invalidates: Subtask 8.
- **`facets_csv_capped` fallback to single-axis chips, never refuse to emit** (Operator Decision #10). Invalidates: Subtask 3.
- **Multi-language tone = ship English anchors, gate non-EN behind native-speaker pass** (Operator Decision #11) — out of structural scope; flagged, not built.

## Knowledge Consulted

- `docs/v2-design/research-v2-gap-and-base.md` — THE gap analysis; DONE/PARTIAL/MISSING per feature + Part 4 remaining-work list (the spine of this plan).
- `docs/_handoff-pack/03 · Handoff brief.md` §2/§3/§5/§6/§8a/§9/§11 — composition specs, hard commitments, tier signals, support config schema, phased rollout, open questions.
- `conversational-search/.../agent/canonical_enums.py` — FROZEN MODE/TIER/COMPOSITION enums, TURN_STATE_ENVELOPE_FIELDS (3 channels), UNSAFE_ROW_REQUIRED_FIELDS.
- `docs/v2-design/design-v2-detection-response-shapes.md` — REFERENCE only (Integration Points, Rejected Alternatives, Operator Decisions). NOTE: its `first_turn_init` node references are STALE (see Coupling Analysis).
- `.agent_context/sessions/1780902545-2133228-f5b65e67ac75/user-intent.md` — concept-ids t-1..t-6.
- knowledge `conversational-agent/architecture.md § Agent (LangGraph State Machine)` — current topology is `reset_tool_call_count → handle_regular_turn`; `first_turn_init` was RETIRED on main 2026-05-13. Turn-1 = exactly ONE streaming LLM call.
- knowledge `constraints/conversational-proxy-cross-repo-enum-replication.md` — proxy CANNOT import agent enums; TIER/COMPOSITION/CLASSIFIER_PATH replicated in `tier_signal_computer.py` with parity tests; `test_tier_signal_computer.py` has a hard-coded worktree path bug to fix on merge-out.
- knowledge `decisions/conversational-search-v2-discovery-digest.md § v2 boundary-contract violations` — `tracker_id` required at /converse; facet_config TTL coupling to tier classifier.
- knowledge `constraints/bedrock-service-tier-ambient-settings-leak.md` — Kimi K2.5 override masking; relevant to post-rebase build sanity.

## Coupling Analysis

**The single biggest risk is the stale-topology trap.** The architect design (`design-v2-detection-response-shapes.md § Integration Points`) instructs implementers to modify `first_turn_init` (graph.py:154-270) and re-point `START → first_turn_init`. **That node does not exist on the working base.** The knowledge store (`conversational-agent/architecture.md`) and gap analysis agree: the live v2 topology on `feat/v2-campaign` is `START → reset_tool_call_count → handle_regular_turn → (tool loop) → verify_search_intent → END`, with turn-1 detection derived in `reset_tool_call_count` (`is_first_turn = conversation_turn == 1`) and turn-1 preview injection gated INSIDE `handle_regular_turn`. Every subtask below that touches graph topology MUST wire against the rebased `handle_regular_turn` / `reset_tool_call_count` structure, NOT the architect-design node names. The rebase (Subtask 0) is what reconciles the v2 work with the infra-sprint topology; all downstream subtasks read the POST-REBASE graph.py.

**Rebase collision surface (Subtask 0):** infra sprint (Kimi K2.5, signature cache, T1 message-shape restructure) and the v2 work both modify `graph.py` (the tier-classifier-folded-into-turn-1 design vs. the message-shape restructure is the most likely collision), and likely `config.py` / `llm_factory.py` / `langgraph_client.py`. `bedrock_service_tier` override masking (Kimi K2.5) must survive the rebase.

**Cross-repo enum replication (Subtasks 2, 6, 11):** the proxy is a separate nested repo and CANNOT import `canonical_enums`. `tier_signal_computer.py` already replicates TIER/COMPOSITION/CLASSIFIER_PATH proxy-side with parity tests. Any subtask that adds an enum value or a wire field must update BOTH sides and keep the parity tests green. `test_tier_signal_computer.py`'s `TestEnumParity.AGENT_ENUMS_PATH` carries a hard-coded worktree path that breaks outside the worktree — fix it (worktree-relative/env-driven) in Subtask 2.

**State-first dependency (Subtasks 3,4,6,7,12):** `state.py` is read/written by everything. The three missing fields (`browse_intent`, `chat_takeover_trigger`, `fork_card_filter_value`) plus any tier-signal fields land FIRST in their owning subtask (Subtask 1) before consumers wire to them.

**Single-funnel metrics:** all turn-1 handlers must continue to flow through the existing metrics/emit funnel or the `llm_call_count` metric under-counts. The one-LLM-call-on-turn-1 commitment (concept-id t-3) is a standing success-criterion on every turn-1-touching subtask.

**Submodule wrinkle:** `conversational-search` is a nested git repo (`.gitmodules` empty in parent). Commits land INSIDE the submodule on the rebased branch AND the parent repo's submodule pointer is updated — flagged in HK-3 (Subtask 17).

## Concept-id Coverage Map

| concept-id | Topic | Satisfied by |
|---|---|---|
| t-1 | Mode detection — downstream routing for the 7 modes | Subtasks 7 (dispatch route + deflections), 8, 9, 10 |
| t-2 | Response shapes / 4 composition renderers + (mode,tier)→composition table | Subtasks 3, 4, 5, 6 |
| t-3 | Minimal model calls — preserve 1-LLM-call-on-turn-1 | Standing success-criterion on Subtasks 2,3,4,5,6,7,8,9,10,12; gate-checked in Subtask 13 (integration) |
| t-4 | Turn-1 vs turn-2+ logic | Subtask 12 (turn-2+ entry kinds + state fields), Subtask 11 (proxy wire) |
| t-5 | Correct working base — rebase feat/v2-campaign onto post-infra main | Subtask 0 |
| t-6 | Tier classifier completion — proxy→agent wire + boundary calibration | Subtask 2 |

## Subtask Summary Table

| # | Title | Agent | Depends On | Subtask File |
|---|-------|-------|------------|--------------|
| 0 | Rebase feat/v2-campaign onto main (GATED, safety-valve) | implementer | -- | plan-finish-v2-detection-response-subtask-0.md |
| 1 | State envelope: add 3 missing fields + tier-signal fields | implementer | 0 | plan-finish-v2-detection-response-subtask-1.md |
| 2 | Tier classifier wire (proxy→agent) + shadow-mode calibration [solution-design] [peer-review] | solution-designer then implementer | 1 | plan-finish-v2-detection-response-subtask-2.md |
| 3 | Renderer: refinement_chips + refinement_chips_with_hatch [peer-review] | implementer | 1, 2 | plan-finish-v2-detection-response-subtask-3.md |
| 4 | browse_intent hatch-click turn-2 handler [peer-review] | implementer | 1, 3 | plan-finish-v2-detection-response-subtask-4.md |
| 5 | Renderer: question_led + hard_fork [solution-design] [peer-review] | solution-designer then implementer | 1, 2 | plan-finish-v2-detection-response-subtask-5.md |
| 6 | (mode,tier)→composition decision table [peer-review] | implementer | 2, 3, 5 | plan-finish-v2-detection-response-subtask-6.md |
| 7 | Mode dispatch route + deflections (support/out_of_scope/unsafe) [solution-design] [peer-review] | solution-designer then implementer | 1, 6 | plan-finish-v2-detection-response-subtask-7.md |
| 8 | gift_advisor turn-1 handler [peer-review] | implementer | 7 | plan-finish-v2-detection-response-subtask-8.md |
| 9 | advice three-route turn-1 fan-out [peer-review] | implementer | 7 | plan-finish-v2-detection-response-subtask-9.md |
| 10 | comparison response shape (mode-stack already done) [peer-review] | implementer | 7 | plan-finish-v2-detection-response-subtask-10.md |
| 11 | Proxy ConverseRequest v2 wire fields [peer-review] | implementer | 1 | plan-finish-v2-detection-response-subtask-11.md |
| 12 | Turn-2+ entry-kind handling (chip / typed / chat) [solution-design] [peer-review] | solution-designer then implementer | 4, 6, 11 | plan-finish-v2-detection-response-subtask-12.md |
| 13 | Integration tests + model-call-budget assertion | implementer | 6, 7, 8, 9, 10, 12 | plan-finish-v2-detection-response-subtask-13.md |
| 14 | Coherence audit against checklist | coherence-auditor | 13 | (inline) |
| 15 | /cycling terminal [housekeeping] | orchestrator | 14 | (inline) |
| 16 | Session Audit [housekeeping] | orchestrator | 15 | (inline) |
| 17 | Commit + Push (submodule + parent pointer) [housekeeping] | orchestrator | 16 | (inline) |
| 18 | /cycling terminal — finalize sentinel [housekeeping] | orchestrator | 17 | (inline) |
| 19 | Knowledge-Hygiene Pipeline [housekeeping] | orchestrator | 18 | (inline) |

**Parallelism once Subtask 0 lands:** Subtask 1 unblocks the spine. After 1, Subtask 11 (proxy wire) runs in parallel with the agent-side spine. After 2, Subtasks 3 and 5 run in parallel. After 7, Subtasks 8/9/10 run in parallel. Critical path: 0 → 1 → 2 → (3,5) → 6 → 7 → (8,9,10) → 12 → 13 → 14 → housekeeping.

## Implementation Checklist

The contract the coherence-auditor (Subtask 14) verifies against. IDs referenced by Completion Criteria.

- **C1** — `feat/v2-campaign` is rebased onto current submodule `main` on a NEW branch (original branch untouched); submodule builds; existing test suite incl. FM-3 dispatcher gate passes. (t-5)
- **C2** — `state.py` carries `browse_intent`, `chat_takeover_trigger`, `fork_card_filter_value` plus any tier-signal fields, each in the correct envelope channel per `TURN_STATE_ENVELOPE_FIELDS`. (t-2,t-4)
- **C3** — Tier wire decision is made (LLM-prefix vs proxy-injected) with rationale; if proxy-injected, the wire field is read pre-LLM; tier ships in SHADOW MODE (logged, not composition-switching) per Operator Decision #1. (t-6)
- **C4** — All 4 composition renderers emit the §6 structured LBJSON: `refinement_chips` → `chips:[{label,filter_value,facet,count}]`; `refinement_chips_with_hatch` → chips + `hatch:{}` + always-on chat affordance; `question_led` → `question:{prompt,answers[]}` + demoted carousel + chat affordance; `hard_fork` → 2 fork cards + `filter_value`, no carousel. (t-2)
- **C5** — `(mode, tier) → composition` decision table per §6 drives composition; the renderer branches on the table output, not on a raw LLM prefix. (t-1,t-2)
- **C6** — Mode dispatch route differentiates all 7 modes downstream; `support` = shop-fillable YAML OR-match (no LLM on keyword hit); `out_of_scope` = guidebook polite template; `unsafe` = hard-refuse, no UI surface, `turn_events` row with `UNSAFE_ROW_REQUIRED_FIELDS`. Mis-dispatch fallback = `product_search`. (t-1)
- **C7** — `gift_advisor` turn-1 chat-takeover + anchored category chips (shop-configurable); `advice` three parallel routes + `type_it_out_parallel_on`; `comparison` structured side-by-side shape (mode-stack LIFO depth-3 already done). (t-1)
- **C8** — Proxy `ConverseRequest` v2 carries `is_engagement_of_preview`, `chat_takeover_trigger`, `fork_card_filter_value` (and threads them into the LangGraph run config); cross-repo enum/field parity tests green. (t-4)
- **C9** — Turn-2+ handles the 3 entry kinds (chip click, typed follow-up, chat-affordance open) using the FE-owned `is_engagement_of_preview` side-channel; tier/composition inherited; `← Change the question` pivot for `question_led`. (t-4)
- **C10** — Hard commitments preserved: NO products in AI block turn-1; always-on chat affordance per §6; turn-1 = exactly ONE LLM call on every path touched (each implementer reports per-path LLM-call count). (t-3)
- **C11** — Integration tests cover the spine end-to-end and assert the turn-1 single-call budget; targeted unit tests per renderer/handler pass. (t-3)

## Completion Criteria

Each verifiable by an agent, not by assertion of quality:

1. **Base** — C1 holds: validator/coherence-auditor confirm the rebased branch builds and the pre-existing test suite (incl. FM-3 gate) passes; original `feat/v2-campaign` ref still exists.
2. **Spine** — C2,C3,C4,C5,C10 hold: validator finds no critical/important issues against the 4 renderers, the decision table, and the tier wire; the turn-1 single-call budget is asserted by a passing test.
3. **Modes** — C6,C7 hold: validator confirms each of the 7 modes routes to a distinct downstream handler with the §6-specified shape; deflection paths emit no product UI.
4. **Turn-2+** — C8,C9 hold: validator confirms the 3 entry kinds are handled and the proxy wire fields thread through.
5. **Model-call budget** — C10 holds across all touched paths: every implementer impl-report names the per-path LLM-call count; Subtask 13's integration test asserts exactly 1 on turn-1.
6. **Tests** — C11 holds: build green on the rebased branch; targeted tests per subtask pass; integration suite passes.
7. **Coherence** — Subtask 14 (coherence-auditor) finds every checklist item C1–C11 satisfied or explicitly deferred with rationale.
8. **Constraint compliance** — validator confirms cross-repo enum-replication parity tests pass and the `tier_signal_computer` worktree-path bug is fixed.

---

## Inline Subtasks

### Subtask 14: Coherence audit against checklist

**Description**: Audit the completed implementation for COMPLETENESS against the Implementation Checklist (C1–C11) and the concept-id coverage map — NOT code quality (that is the validator's job per-subtask). Confirm every checklist item is satisfied or explicitly deferred with a one-sentence rationale. Confirm each concept-id t-1..t-6 maps to landed work. Flag any §6 composition-shape field that is specified but not emitted, any of the 7 modes that still routes to the shared `handle_regular_turn` body, and any turn-1 path whose impl-report did not report an LLM-call count.

**Agent**: coherence-auditor

**Knowledge**:
- `docs/v2-design/plan-finish-v2-detection-response.md` (this plan — the checklist is the contract)
- `docs/_handoff-pack/03 · Handoff brief.md` (§3 hard commitments, §6 compositions)
- `conversational-search/.../agent/canonical_enums.py` (frozen enums + envelope fields)

**Dependencies**: Subtask 13

**Context files**:
- `{session_dir}/` impl-reports from Subtasks 0–13 — the per-subtask Verification sections are the evidence base for checklist satisfaction.

**Expected output**: Coherence verdict (complete / gaps-found) with a per-checklist-item (C1–C11) satisfied/deferred/gap line and a per-concept-id (t-1..t-6) coverage line. Gaps become R2 fix-round inputs for the orchestrator.

**active_rubrics**: ["cross-artifact-coherence"]

**Design phase**: no with reason verification-exercise-only

**UX phase**: no

### Subtask 15: /cycling terminal [housekeeping]

**Description**: Invoke `/cycling` in terminal-mode to promote marathon findings to the knowledge store and emit the completion sentinel. Terminal-mode handles findings promotion internally; per S6b, digests are cycle-mode-only and are NOT run in terminal-mode.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 14 (coherence audit passes)

**Verification (PROMOTION_DONE-OR-HANDOFF-DONE — disjunctive predicate, MUST satisfy at least ONE branch before HK-2 begins):**
- **Branch A — terminal-mode completed.** Run `smart_bash: [ -f {session_dir}/promotion-complete ] && echo DONE || echo PENDING`. Branch passes if output is `DONE`.
- **Branch B — handoff-mode completed.** Run `smart_bash: ls -1t {session_dir}/cycle-checkpoint_*.json 2>/dev/null | head -n1`. If a path is returned, read the file and check the JSON `cycle_reason` field (underscore form). Branch passes if `cycle_reason` ∈ {`handoff-post-task`, `handoff-mid-task`}.
- **HALT** if BOTH branches fail. Do NOT proceed to HK-2. Surface: "HK-1a verification failed: promotion-complete sentinel absent AND no cycle-checkpoint with handoff cycle_reason. Re-invoke `/cycling terminal` or re-run the handoff cycle." Then re-run this Verification step.

**Operator-facing terminal summary (HARD-GATED — MUST be present before surfacing "done"):**

Compose from the `## Verification` sections of every per-subtask producer impl-report — do NOT invent. Aggregate flatly: every `Exercised:` bullet lands under `Verified end-to-end:`; every `Not exercised, and why:` bullet lands under `Not verified, and why:`. No per-subtask grouping.

```
Verified end-to-end:
- <what was actually exercised, with evidence path / observable output / sentinel write>
- ...

Not verified, and why:
- <what was assumed but not exercised, with bounded reason>
- ...
```

Rules: empty "Not verified" legal ONLY when affirmatively stated. Bounded reasons name structural infeasibility, not effort. Do NOT surface "done" without this split present (hard gate).

### Subtask 16: Session Audit [housekeeping]

**Description**: Run `session(action='audit')` to aggregate tool telemetry. Skip if fewer than 3 subagent invocations in this session (this plan dispatches many, so it runs).

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 15. MUST observe PROMOTION_DONE-OR-HANDOFF-DONE from HK-1a Verification before starting.

### Subtask 17: Commit + Push (submodule + parent pointer) [housekeeping]

**Description**: Commit all code changes and integrate them upstream per the L1/L2 role-detection protocol in the housekeeping-subtask-templates skill (run `[ -n "$CAA_CHILD_SIDECAR_DIR" ]` first; L1 → push origin, L2+ → merge to parent). **SUBMODULE WRINKLE (load-bearing for this plan):** `conversational-search` is a NESTED git repo (parent `.gitmodules` is empty), and the rebase happened on a NEW submodule branch (`feat/v2-campaign-rebased`). The commit step MUST: (a) commit the v2 implementation INSIDE `conversational-search/` on the rebased branch and push that branch to the submodule's origin; (b) THEN, in the parent repo, stage the updated submodule pointer (the parent tracks `conversational-search` as a gitlink) plus the plan/docs changes, and commit+push per the standard protocol. Apply the staging discipline, pipeline-correctness discipline, post-push verification, and witness-sentinel rules verbatim from the skill for the PARENT-repo push. Do NOT `git add -A` in either repo. The proxy lives inside the submodule (`conversational-search/conversational-proxy/`) — its changes commit with the submodule, not separately.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 16 (session audit)

**Witness sentinel**: write `{session_dir}/push-result.txt` (`pushed=success\nsha=<sha>`) only on verified PARENT-repo push, per skill rules. On any failure path do NOT write the file.

### Subtask 18: /cycling terminal — finalize sentinel [housekeeping]

**Description**: Invoke `/cycling terminal` sentinel-finalization step (HK-1b) after Commit + Push succeeds. Emits `SESSION-COMPLETION-SENTINEL` with `status='success'` to the change-log.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 17. GATED on HK-3 success — for L1 check `{session_dir}/push-result.txt` contains `pushed=success`; for L2+ check `{session_dir}/merge-result.txt` contains `merged=success`. If the witness is absent or lacks the success token, do not run this step.

**Verification (TERMINAL_FINALIZED):**
- Run `knowledge(action='change-log', file_exact='{session_dir}/SESSION-COMPLETION-SENTINEL', actor='external:cycling-terminal-sentinel', limit=1)`. Passes if `entries[]` contains ≥1 entry with `status === 'success'`.
- Do NOT use a file-test on the sentinel path — it is a change-log entry only.

### Subtask 19: Knowledge-Hygiene Pipeline [housekeeping]

**Description**: Invoke the knowledge-hygiene pipeline (the Study Orchestrator) via CLI Bash (NOT the Agent tool). Fire-and-forget: `Bash(run_in_background=true, command="cd $MAIN_ROOT && bin/claude-study post-completion")`, capture bash_id, yield turn immediately. Do NOT poll BashOutput post-launch. **This change touches knowledge-relevant files (agent code, docs, plan) and is large, so the pipeline MUST run** (skip-condition not met). Apply the DEFER / HUNG / LAUNCH mutex probe against `.claude/knowledge/.study-state` per the skill; treat exit-code 2 as ambiguous-success per the skill's caveat.

**Agent**: orchestrator (direct — not delegated)

**Dependencies**: Subtask 18

## Post-Completion

The housekeeping subtasks above (/cycling terminal, Session Audit, Commit + Push, /cycling terminal — finalize sentinel, Knowledge-Hygiene Pipeline) cover the post-loop steps. After they complete: surface any deferred Proposed Criteria Additions (PCAs) and unmet completion criteria in the final response to the user. Note the submodule wrinkle was handled in Subtask 17 — confirm both the submodule branch push AND the parent submodule-pointer update landed.

---

Pre-emission self-audit: 19 subtasks present (0–13 implementation + 14 coherence-auditor + 15–19 housekeeping). 5 mandatory housekeeping subtasks appended in order (/cycling terminal, Session Audit, Commit + Push, /cycling terminal finalize, Knowledge-Hygiene Pipeline / Study Orchestrator). All 6 concept-ids (t-1..t-6) mapped in the coverage table. Subtask 0 (rebase) is the gated prerequisite with a git-rebase --abort safety valve; every implementation subtask depends on it. Submodule + parent-pointer wrinkle flagged in HK-3. Stale-topology trap (architect-design first_turn_init vs. live handle_regular_turn) called out in Coupling Analysis and carried into every topology-touching subtask file. 4 subtasks marked [solution-design] (2, 5, 7, 12); 12 substantive subtasks marked [peer-review]; plan itself flagged for peer-review.
