# Plan: v2 UX Follow-up Fixes

## Goal (verbatim)

> A live e2e exercise of the v2 conversational-search stack found real UX/quality bugs; a diagnostician triaged the headline ones. The operator authorized fixing a defined set, in parallel where possible. Produce the implementation plan.
>
> WORK ITEMS TO PLAN (fix ALL of these; the operator excluded only the Slovak-language item):
> - **A - `iss_fb9225612f5e` (HIGH, safety):** Unsafe mode returns a BLANK user response. Root cause from triage: classification is correct and refusal text is in final graph state, but the deterministic deflection AIMessage updates are NOT emitted on the `messages` stream the proxy forwards. Fix locus: agent `graph.py` deflection streaming + proxy `app/clients/langgraph_client.py` forwarding. Cross-repo, bounded.
> - **B - `iss_38043b16e87b` (MED):** `tier=zero_results` reported on every product_search despite real product counts. Root cause: tier-signal computed from empty facet stubs (proxy). Fix locus: proxy `app/service/conversation_service.py` / `tier_signal_computer.py`, possibly agent `graph.py` if tier is computed post-search. Bounded->larger.
> - **C - `iss_5e7d7e656c5f` (MED):** support & out_of_scope prompts fall through to `handle_regular_turn` (real dispatch coverage gap; the usable prose is accidental LLM output, not the intended deterministic deflection). Fix locus: agent `_dispatch_for_query()` + `agent/support/*.yaml` deflection configs.
> - **D - `iss_21df04a6f143` (LOW):** `scripts/setup_dev.py` hard-codes a stale LangGraph assistant id for tracker `8760-9189`, breaking first smoke `/converse`. Tiny fix (proxy). Make local setup resolve/seed a valid assistant id instead of a hard-coded stale one.
> - **F - `iss_4a485870b3d8` (quality):** first-turn opening sentence over-promises products it doesn't render on turn 1 (prompt/copy). Agent prompt fix.
> - **G - cache-HIT verification + remediation:** the exercise could NOT exercise the cache-HIT path (proxy had no cache DB DSN - `CONVERSATIONAL_CACHE_DATABASE_URL`/`DATABASE_URL` unset; repeats stayed MISS). Stand up the cache DB, exercise the HIT path live, capture RAW SSE, and remediate any prod-FE wire-shape divergences found. Known candidate divergences (see knowledge store `decisions/v2-cache-hit-path-sse-synth-shape-digest.md`): HIT-path emitting stray `__work_status__` events, and a bare `[DONE]` terminator (prod FE expects `[DONE conversational_run_<uuid>]`). VERIFY-then-FIX - structure it so the verification result gates the remediation.
> - **H - test debt (two parts):** (H1) `iss_96ed68c55648` - 3 stale-fixture proxy tests that fail even with `ENV=test` (mock threads use `intended_assistant_id` while `converse()` reads `assistant_ref`; `advisory-shop` not seeded into the test product-feed Redis set). (H2) `iss_de7e66973835` - conversational-search agent origin baseline regressed (~82 pass + 17 fail vs an earlier 221-pass claim); investigate + restore green or document the true baseline.

## Knowledge Consulted

- `overview.md` - conversational-search agent and conversational-proxy ownership context.
- `.agent_context/v2-ux-findings-triage.md` - root causes, fix loci, and issue mapping.
- `.agent_context/v2-e2e-exercise-results.md` - live transcript judgments and cache-HIT gap.
- `v2-e2e-exercise-runbook.md` - live Bedrock/proxy/LangGraph setup and cache-HIT exercise requirements.
- `docs/v2-design/plan-finish-v2-detection-response.md` - completed v2 campaign status and prior topology/coupling context.
- `decisions/v2-cache-hit-path-sse-synth-shape-digest.md` - HIT/MISS SSE symmetry, `__meta__`, and `[DONE conversational_run_*]` contract.
- `decisions/conversational-search-v2-marathon-findings-digest.md` - tier metadata, graph.py routing, and cross-family review warning.
- `decisions/conversational-agent-v2-marathon-findings-digest.md` - safety dispatch and deflection routing gotchas.
- `decisions/conversational-proxy-v2-marathon-findings-digest.md` - proxy branch, tier wire, and hydrator coupling.
- `constraints/conversational-proxy-structural.md` - proxy is an independent nested git repo, not part of the agent repo commit.
- `constraints/langgraph-dev-server-store-persistence.md` - stale assistant-id root cause and recovery path.
- `housekeeping-subtask-templates` skill - mandatory five-subtask housekeeping suffix.

## Implementation Checklist

- C-A1: Unsafe prompt streams visible refusal text to the user, not only final graph state.
- C-A2: Proxy forwards deterministic deflection text through the same SSE text path the frontend consumes.
- C-A3: Unsafe response carries correct `mode=unsafe` metadata and no product UI surface.
- C-B1: Product-search tier metadata no longer computes from `{}` facet stubs on live product turns.
- C-B2: Tier/result metadata agrees with real product-search counts for broad, narrow, and turn-2 product-search captures.
- C-B3: Tier fix preserves cache-key, turn_events, and `__meta__.turn_classification` invariants.
- C-C1: Support prompt for tracker `8760-9189` routes to deterministic `support_deflect`, not `handle_regular_turn`.
- C-C2: Out-of-scope prompt variants including `weather` route to deterministic `out_of_scope_deflect`.
- C-C3: Support/out-of-scope deflections use zero LLM calls and no product-search composition.
- C-D1: Local `scripts/setup_dev.py` resolves or creates a valid LangGraph assistant id for `8760-9189` instead of seeding a stale hard-coded UUID.
- C-D2: First local smoke `/converse` works after setup without manual Redis assistant-id repair.
- C-F1: First-turn prompt/copy no longer promises products when only refinement controls are rendered.
- C-F2: Prompt regression tests cover the captured English child-piano over-promise pattern.
- C-G1: Cache DB is configured locally with `CONVERSATIONAL_CACHE_DATABASE_URL` or `DATABASE_URL` and migrations/seeding sufficient to produce a real HIT.
- C-G2: Raw MISS and HIT SSE captures are saved and compared against prod-FE wire expectations.
- C-G3: HIT path emits no FE-visible stray `__work_status__` events unless verified as identical to MISS/prod behavior.
- C-G4: HIT path terminates with `[DONE conversational_run_<uuid>]`, not a bare `[DONE]`.
- C-H1: The three stale proxy tests pass via `scripts/test.sh` with `ENV=test` wrapper behavior.
- C-H2: Agent baseline is either restored green or documented as the true baseline with failing tests classified, scoped, and not masking follow-up fix validation.
- C-X1: Proxy validation always uses `scripts/test.sh`; agent validation uses the agent pytest runner from `conversational-search`.
- C-X2: Production streaming/SSE code fixes A, B, and any G remediation receive cross-family peer-review and validator review.

## Coupling Analysis

`conversational-search` is a symlink to `/home/fanderman/projects/luigis-box/conversational-search`, a separate nested git repo on branch `feat/v2-campaign-rebased`. The proxy under `conversational-search/conversational-proxy` is another independent nested git repo on branch `reconcile/proxy-v2-the-rest-on-origin-master`; it is not a submodule and must be committed/pushed from its own repo root. The parent worktree only owns this plan and other docs.

H2 runs first for the agent repo because the reported agent baseline regression makes unscoped `pytest` results ambiguous. Until Subtask 1 lands, downstream agent subtasks must validate with focused pytest selections and report whether failures are pre-existing. Proxy tests use the `scripts/test.sh` wrapper from the proxy repo; bare `poetry run pytest` is explicitly invalid for proxy validation because setup differs under `ENV=test`.

A and C are intentionally merged into one deflection subtask. Both edit `src/conversational_search/agent/graph.py`, both concern deterministic deflection nodes, and A's streaming mechanism is reusable for support/out-of-scope once C routes them correctly. Splitting them would create blind parallel edits in the same routing/deflection code.

B has a placement decision: proxy-side tier metadata currently comes from a fake empty facet stub, but the real per-query search result lives on the agent side after `search_products_tool`. A short design subtask decides whether to feed proxy `TierSignalComputer` from real proxy-observable data, move real tier computation into the agent post-search path, or use a hybrid. The implementation is sequenced after the A+C graph edit so any `graph.py` tier changes do not collide.

G is verification-gated. The researcher first proves a real cache HIT with a local cache DB and raw SSE captures. The remediation subtask only changes code if the report shows FE wire-shape divergence; otherwise it writes a no-op implementation report with evidence. G depends on D because the live stack setup must not require manual stale assistant-id repair, and on B because cache keys include tier signal.

## Parallelization Graph

Wave 1 can fan out immediately: Subtask 1 (H2 agent baseline), Subtask 2 (H1 proxy fixture debt), and Subtask 3 (D setup_dev assistant id).

Wave 2 can fan out after its local baselines: Subtask 4 (B tier-source design) after Subtask 2, Subtask 6 (A+C deflection routing/streaming) after Subtask 1, and Subtask 7 (F prompt copy) after Subtask 1.

Wave 3 is sequenced: Subtask 5 (B implementation) after Subtasks 2, 4, and 6. This is conservative because B may edit `graph.py`; if Subtask 4 proves a proxy-only patch, the orchestrator may relax the Subtask 6 dependency.

Wave 4 is live verification: Subtask 8 (G cache-HIT exercise) after Subtasks 3 and 5.

Wave 5 is gated remediation: Subtask 9 only runs after Subtask 8 and only edits code if raw HIT SSE diverges.

Wave 6 is audit and housekeeping: Subtask 10 coherence audit, then Subtasks 11-15 housekeeping in order.

## Subtask Summary Table

| # | Title | Agent | Depends On | Subtask File |
|---|-------|-------|------------|--------------|
| 1 | H2 agent baseline regression [no-peer-review] | implementer | -- | (inline) |
| 2 | H1 proxy stale-fixture tests [no-peer-review] | implementer | -- | (inline) |
| 3 | D local setup assistant-id seeding [no-peer-review] | implementer | -- | (inline) |
| 4 | B tier-signal source decision [peer-review] | solution-designer | 2 | (inline) |
| 5 | B real tier metadata implementation [peer-review] | implementer | 2, 4, 6 | (inline) |
| 6 | A+C deterministic deflection routing and streaming [peer-review] | implementer | 1 | (inline) |
| 7 | F first-turn prompt honesty [no-peer-review] | implementer | 1 | (inline) |
| 8 | G live cache-HIT exercise and SSE report [peer-review] | researcher | 3, 5 | (inline) |
| 9 | G cache-HIT wire-shape remediation [peer-review] | implementer | 8 | (inline) |
| 10 | Coherence audit against checklist | coherence-auditor | 1,2,3,5,6,7,8,9 | (inline) |
| 11 | /cycling terminal [housekeeping] | orchestrator | 10 | (inline) |
| 12 | Session Audit [housekeeping] | orchestrator | 11 | (inline) |
| 13 | Commit + Push [housekeeping] | orchestrator | 12 | (inline) |
| 14 | /cycling terminal - finalize sentinel [housekeeping] | orchestrator | 13 | (inline) |
| 15 | Knowledge-Hygiene Pipeline / Study Orchestrator [housekeeping] | orchestrator | 14 | (inline) |

## Completion Criteria

- Validator approves every code-producing subtask with no critical or important findings under `code-vs-spec` and `constraint-compliance`.
- Cross-family peer-review completes for Subtasks 4, 5, 6, 8, and 9; any request-changes finding is fixed or explicitly escalated before coherence audit.
- H2 produces a trustworthy agent baseline: full green where feasible, or a documented true baseline plus scoped validation commands for all later agent fixes.
- Proxy test debt is resolved with `conversational-search/conversational-proxy/scripts/test.sh`, not bare pytest.
- Live cache-HIT exercise captures raw SSE evidence and either proves no remediation is needed or verifies the remediation against the candidate FE wire-shape divergences.
- Coherence-auditor maps every checklist item C-A1 through C-X2 to landed work, verified evidence, no-op evidence, or an explicit deferred gap.
- Housekeeping commits and pushes the parent docs repo, the agent repo, and the proxy repo as separate repositories according to their actual git roots.

### Subtask 1: H2 agent baseline regression [no-peer-review]

**Description**: In repo `conversational-search` on branch `feat/v2-campaign-rebased`, investigate `iss_de7e66973835`: the agent baseline reportedly regressed to about 82 pass + 17 fail versus an earlier 221-pass claim. Restore the true green baseline if failures are stale or caused by fixtures; otherwise document the true current baseline with failing tests classified as pre-existing, v2-followup-relevant, or unrelated. Target files are discovered by pytest, but likely surfaces include `tests/`, `benchmarks/`, `pyproject.toml`, and any source files implicated by true regressions. Do not touch proxy files.

**Agent**: implementer

**Knowledge**:
- `decisions/conversational-agent-v2-marathon-findings-digest.md`
- `decisions/conversational-search-v2-marathon-findings-digest.md`
- `constraints/benchmarks-harness-provider-aware-model-id.md`

**Dependencies**: none

**Context files**: none

**Expected output**: Implementation report with: baseline command output summary; list of failures fixed; list of failures still present with rationale if full green is not currently true; scoped validation command downstream agent subtasks must use if full-suite green is not restored. Validation command: `cd conversational-search && poetry run pytest` plus focused reruns for fixed files; also run `cd conversational-search && poetry run ruff check src tests` if code changed.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason baseline-stabilization

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[no-peer-review]`; this is test debt/baseline restoration. Validator review still required because it can change tests or source.

### Subtask 2: H1 proxy stale-fixture tests [no-peer-review]

**Description**: In repo `conversational-search/conversational-proxy` on branch `reconcile/proxy-v2-the-rest-on-origin-master`, fix `iss_96ed68c55648`. The known stale fixtures are: mock threads using `intended_assistant_id` while `ConversationService.converse()` reads `assistant_ref`, and `advisory-shop` not seeded into the test product-feed Redis set. Target files include `tests/integration/conftest.py`, `tests/integration/test_conversation_api_v2.py`, `tests/integration/test_conversation_api.py`, and any fixture/helpers that seed tracker sets. Do not change production code unless the test proves production behavior is wrong.

**Agent**: implementer

**Knowledge**:
- `constraints/conversational-proxy-structural.md`
- `decisions/conversational-proxy-v2-marathon-findings-digest.md`

**Dependencies**: none

**Context files**: none

**Expected output**: Implementation report naming the three stale tests and the fixture changes. Validation command: `cd conversational-search/conversational-proxy && scripts/test.sh tests/integration/test_conversation_api.py tests/integration/test_conversation_api_v2.py tests/integration/test_boundary_contract.py` and then `cd conversational-search/conversational-proxy && scripts/test.sh` if the focused set passes.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason stale-fixture-repair

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[no-peer-review]`; fixture/test-only repair with validator review.

### Subtask 3: D local setup assistant-id seeding [no-peer-review]

**Description**: In repo `conversational-search/conversational-proxy`, fix `iss_21df04a6f143`. Replace the stale hard-coded `8760-9189` assistant id in `scripts/setup_dev.py` with a local resolver/seed path that discovers or creates a valid LangGraph assistant for graph id `agent` at the configured local LangGraph URL, then writes that id to Redis. Target files include `scripts/setup_dev.py`; use existing `app/clients/langgraph_client.py` helpers if appropriate, but keep the setup script runnable as a local developer command. Preserve tracker product-feed and throttling seed behavior.

**Agent**: implementer

**Knowledge**:
- `constraints/langgraph-dev-server-store-persistence.md`
- `constraints/conversational-proxy-structural.md`

**Dependencies**: none

**Context files**: none

**Expected output**: Implementation report with before/after setup behavior. Validation commands: `cd conversational-search/conversational-proxy && scripts/test.sh tests/unit tests/integration/test_conversation_api_v2.py` plus a local smoke where LangGraph is running: `ENV=development poetry run python scripts/setup_dev.py`, then initiate/converse for tracker `8760-9189` without manual Redis repair.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason tiny-dev-seed-fix

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[no-peer-review]`; dev-seed fix, validator review only.

### Subtask 4: B tier-signal source decision [peer-review]

**Description**: Decide the implementation placement for `iss_38043b16e87b` before code changes. Compare: proxy-side real data source, agent-side post-search computation with `lbx.turn_classification`, and hybrid proxy cache-key placeholder plus agent authoritative display metadata. The decision must preserve cache-key semantics, `turn_events`, `__meta__.turn_classification`, and product-search latency. Inspect `conversational-search/conversational-proxy/app/service/conversation_service.py`, `app/service/tier_signal_computer.py`, `app/clients/langgraph_client.py`, and `conversational-search/src/conversational_search/agent/graph.py`.

**Agent**: solution-designer

**Knowledge**:
- `decisions/conversational-search-v2-marathon-findings-digest.md`
- `decisions/conversational-proxy-v2-marathon-findings-digest.md`
- `decisions/v2-cache-hit-path-sse-synth-shape-digest.md`

**Dependencies**: Subtask 2

**Context files**: none

**Expected output**: `{session_dir}/tier-signal-source-decision.md` with: selected placement, exact write targets, rejected alternatives, cache-key impact, and validation plan. The artifact must state whether Subtask 5 needs to edit agent `graph.py`; if yes, keep Subtask 5 sequenced after Subtask 6.

**active_rubrics**: ["generator-preflight"]

**Design phase**: yes

**UX phase**: no

**target_artifact_type**: design

**Review policy**: `[peer-review]`; B is production metadata/SSE-adjacent and the design choice is consequential.

### Subtask 5: B real tier metadata implementation [peer-review]

**Description**: Implement the decision from Subtask 4 for `iss_38043b16e87b`. Stop reporting `tier=zero_results` for real product-search turns with nonzero results. Target files are dictated by the design artifact, with likely proxy targets `app/service/conversation_service.py`, `app/service/tier_signal_computer.py`, `app/clients/langgraph_client.py`, `tests/unit/test_tier_signal_computer.py`, `tests/integration/test_phase4_panel.py`, and possibly agent `src/conversational_search/agent/graph.py` plus `tests/integration/test_dispatch_prefix.py` if tier is computed post-search. Preserve thread metadata, `turn_events`, cache-key tier dimension, and FE debug meta shape.

**Agent**: implementer

**Knowledge**:
- `decisions/conversational-search-v2-marathon-findings-digest.md`
- `decisions/conversational-proxy-v2-marathon-findings-digest.md`
- `decisions/v2-cache-hit-path-sse-synth-shape-digest.md`

**Dependencies**: Subtask 2, Subtask 4, Subtask 6

**Context files**:
- `{session_dir}/tier-signal-source-decision.md` - selected placement and validation plan.

**Expected output**: Implementation report with before/after evidence for broad `kytara`, narrow `Fender American Professional II Stratocaster`, and turn-2 `Fender` tier metadata. Validation commands: proxy path `cd conversational-search/conversational-proxy && scripts/test.sh tests/unit/test_tier_signal_computer.py tests/integration/test_phase4_panel.py tests/integration/test_signature_cache_miss_path.py tests/integration/test_signature_cache_hit_path.py`; agent path if touched `cd conversational-search && poetry run pytest tests/integration/test_dispatch_prefix.py tests/unit/test_graph_emit.py`; then repo-appropriate full or scoped suites per Subtask 1/2 baseline results.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason design-artifact-provided

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[peer-review]`; mandatory cross-family peer-review plus validator because this changes production tier/SSE metadata behavior.

### Subtask 6: A+C deterministic deflection routing and streaming [peer-review]

**Description**: Fix `iss_fb9225612f5e` and `iss_5e7d7e656c5f` as one deflection concern. In agent `src/conversational_search/agent/graph.py`, make support and out-of-scope captured prompts route to deterministic deflection nodes, add tracker `8760-9189` or default support config coverage under `src/conversational_search/agent/support/*.yaml`, broaden out-of-scope keyword coverage for `weather`, and make deterministic deflection text emit through a stream channel the proxy forwards. In proxy `app/clients/langgraph_client.py`, forward the new deflection-text custom event or equivalent as user-visible text without breaking existing `lbx.work_status`, `lbx.search_context`, `lbx.no_preview`, and `lbx.turn_classification` handling. Unsafe must remain highest priority and zero-LLM.

**Agent**: implementer

**Knowledge**:
- `decisions/conversational-agent-v2-marathon-findings-digest.md`
- `decisions/conversational-search-v2-marathon-findings-digest.md`
- `constraints/conversational-proxy-structural.md`

**Dependencies**: Subtask 1

**Context files**:
- Subtask 1 implementation report - baseline status and scoped agent validation instructions.

**Expected output**: Implementation report with raw SSE or stream evidence for unsafe, support, and out-of-scope prompts. Validation commands: `cd conversational-search && poetry run pytest tests/integration/test_dispatch_prefix.py tests/unit/test_graph_emit.py`; proxy command `cd conversational-search/conversational-proxy && scripts/test.sh tests/unit/test_stream_result.py tests/integration/test_conversation_api_v2.py`; live or local LangGraph stream check for unsafe raw SSE contains visible refusal text and `[DONE conversational_run_*]` terminator.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason bounded-deflection-routing-and-streaming-fix

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[peer-review]`; mandatory cross-family peer-review plus validator because this is safety and production streaming/SSE behavior.

### Subtask 7: F first-turn prompt honesty [no-peer-review]

**Description**: Fix `iss_4a485870b3d8` in the agent prompt/copy path. The first visible sentence for turn-1 product-search preview must frame refinement controls honestly and must not imply that products are being rendered when the LBJSON only contains chips/options. Target files include `src/conversational_search/agent/prompts.py` and prompt/preview tests under `tests/unit/test_graph_emit.py` or `tests/integration/test_dispatch_prefix.py`. Use the captured English child-piano scenario as the regression seed.

**Agent**: implementer

**Knowledge**:
- `meta/prompt-design.md`
- `decisions/conversational-agent-v2-marathon-findings-digest.md`

**Dependencies**: Subtask 1

**Context files**:
- Subtask 1 implementation report - baseline status and scoped agent validation instructions.

**Expected output**: Implementation report with prompt diff summary and regression assertion. Validation command: `cd conversational-search && poetry run pytest tests/unit/test_graph_emit.py tests/integration/test_dispatch_prefix.py -k "preview or overpromise or Turn1Composition"`; run broader agent suite only if Subtask 1 restored full green.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason prompt-copy-fix

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[no-peer-review]`; prompt/copy fix with validator review is sufficient unless implementation changes renderer logic.

### Subtask 8: G live cache-HIT exercise and SSE report [peer-review]

**Description**: Verify the cache-HIT path live before remediation. Using `v2-e2e-exercise-runbook.md`, stand up LangGraph and proxy with live AWS Bedrock enabled, configure a local cache DB by setting `CONVERSATIONAL_CACHE_DATABASE_URL` or `DATABASE_URL`, run migrations or setup needed for signature cache tables, seed tracker `8760-9189`, and issue repeat product-search requests until a real `cache.status=HIT` is observed. Capture raw MISS and HIT SSE. Compare HIT wire shape to prod-FE expectations: no stray FE-visible `__work_status__` divergence and no bare `[DONE]`; expected terminator is `[DONE conversational_run_<uuid>]`.

**Agent**: researcher

**Knowledge**:
- `decisions/v2-cache-hit-path-sse-synth-shape-digest.md`
- `v2-e2e-exercise-runbook.md`
- `constraints/langgraph-dev-server-store-persistence.md`

**Dependencies**: Subtask 3, Subtask 5

**Context files**:
- Subtask 3 implementation report - setup_dev smoke evidence.
- Subtask 5 implementation report - tier/cache-key behavior after B fix.

**Expected output**: `{session_dir}/cache-hit-live-sse-report.md` plus raw capture paths under `.agent_context/v2-e2e-artifacts/` or `{session_dir}/`. The report must include commands run, env vars used excluding secrets, DB/migration status, cache MISS/HIT proof, raw SSE file paths, and a `remediation_required: yes/no` verdict with exact divergences if yes.

**active_rubrics**: ["generator-preflight"]

**Design phase**: no with reason research-only-no-design-decision

**UX phase**: no

**target_artifact_type**: research

**Review policy**: `[peer-review]`; live SSE interpretation is consequential and gates production wire-shape code.

### Subtask 9: G cache-HIT wire-shape remediation [peer-review]

**Description**: Read Subtask 8's cache-HIT report. If `remediation_required: no`, do not edit code; write a no-op implementation report citing the raw SSE evidence. If remediation is required, patch only the divergent HIT-path wire-shape behavior in proxy code. Likely targets are `app/service/conversation_service.py`, `app/service/signature_cache.py`, `tests/unit/test_sse_synth_from_payload.py`, `tests/integration/test_signature_cache_hit_path.py`, and `tests/integration/test_turn_events_cache_hit.py`. Preserve MISS-path behavior and HIT/MISS `__meta__` symmetry.

**Agent**: implementer

**Knowledge**:
- `decisions/v2-cache-hit-path-sse-synth-shape-digest.md`
- `constraints/conversational-proxy-structural.md`

**Dependencies**: Subtask 8

**Context files**:
- `{session_dir}/cache-hit-live-sse-report.md` - authoritative gate and raw SSE evidence.

**Expected output**: Implementation report or no-op report. If code changed, validation commands: `cd conversational-search/conversational-proxy && scripts/test.sh tests/unit/test_sse_synth_from_payload.py tests/integration/test_signature_cache_hit_path.py tests/integration/test_turn_events_cache_hit.py`; then repeat the live HIT capture from Subtask 8 and save raw SSE proving the divergence is gone.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-gated-remediation

**UX phase**: no

**target_artifact_type**: code

**Review policy**: `[peer-review]`; mandatory cross-family peer-review plus validator for any code change. If no-op, peer-review checks the evidence/no-op judgment.

### Subtask 10: Coherence audit against checklist

**Description**: Audit completed work for checklist completeness, not code quality. Confirm C-A1 through C-X2 are satisfied by implementation reports, raw SSE captures, validation output, or explicit no-op evidence. Pay special attention to the excluded Slovak-language item: it must remain out of scope and not silently reappear as an unmet criterion. Confirm A+C shared `graph.py` work did not clobber B if B touched agent code, and confirm G remediation only ran when the live verification report required it.

**Agent**: coherence-auditor

**Knowledge**:
- `docs/v2-design/plan-v2-ux-followup-fixes.md`
- `.agent_context/v2-ux-findings-triage.md`
- `.agent_context/v2-e2e-exercise-results.md`
- `decisions/v2-cache-hit-path-sse-synth-shape-digest.md`

**Dependencies**: Subtask 1, Subtask 2, Subtask 3, Subtask 5, Subtask 6, Subtask 7, Subtask 8, Subtask 9

**Context files**:
- `{session_dir}/tier-signal-source-decision.md` - B placement rationale.
- `{session_dir}/cache-hit-live-sse-report.md` - G verification gate.
- Impl reports from Subtasks 1, 2, 3, 5, 6, 7, and 9 - checklist evidence.

**Expected output**: Coherence verdict with one line per checklist item C-A1 through C-X2: satisfied, no-op-satisfied, deferred, or gap. Any gap becomes an R2 fix-round input.

**active_rubrics**: ["cross-artifact-coherence"]

**Design phase**: no with reason verification-exercise-only

**UX phase**: no

**target_artifact_type**: synthesis

**Review policy**: standard coherence audit; no peer-review token needed.

### Subtask 11: /cycling terminal [housekeeping]

**Description**: Invoke `/cycling` in terminal-mode to promote marathon findings to the knowledge store and emit the completion sentinel. Terminal-mode handles findings promotion internally; per S6b, digests are cycle-mode-only and are NOT run in terminal-mode.

**Agent**: orchestrator (direct - not delegated)

**Dependencies**: Subtask 10 (audit passes)

**Verification (PROMOTION_DONE-OR-HANDOFF-DONE - disjunctive predicate, MUST satisfy at least ONE branch before HK-2 begins):**
- **Branch A - terminal-mode completed.** Run `smart_bash: [ -f {session_dir}/promotion-complete ] && echo DONE || echo PENDING`. Branch passes if output is `DONE`.
- **Branch B - handoff-mode completed.** Run `smart_bash: ls -1t {session_dir}/cycle-checkpoint_*.json 2>/dev/null | head -n1`. If a path is returned, read the file and check the JSON `cycle_reason` field (note: underscore form, NOT hyphen). Branch passes if `cycle_reason` in {`handoff-post-task`, `handoff-mid-task`}.
- **HALT** if BOTH branches fail. Do NOT proceed to HK-2 (Session Audit). Surface the failure with: "HK-1a verification failed: promotion-complete sentinel absent AND no cycle-checkpoint with handoff cycle_reason. Re-invoke `/cycling terminal` (terminal-mode) or, if handoff was intended, re-run the handoff cycle to produce the checkpoint." Then re-run this Verification step.

**Operator-facing terminal summary (HARD-GATED - MUST be present before surfacing "done"):**

Compose from the `## Verification` sections of every per-subtask producer impl-report - do NOT invent. Aggregate flatly across all subtasks: every `Exercised:` bullet from every impl-report lands in one list under `Verified end-to-end:`; every `Not exercised, and why:` bullet lands in one list under `Not verified, and why:`. No per-subtask grouping, no subtask labels preserved.

```
Verified end-to-end:
- <what was actually exercised, with evidence path / observable output / sentinel write>
- ...

Not verified, and why:
- <what was assumed but not exercised, with bounded reason>
- ...
```

Rules: empty "Not verified" is legal only when affirmatively stated; bounded reasons name structural infeasibility, not effort; do not surface "done" without this split present.

**active_rubrics**: []

**Design phase**: no with reason housekeeping

**UX phase**: no

### Subtask 12: Session Audit [housekeeping]

**Description**: Run `session(action='audit')` to aggregate tool telemetry. Skip if fewer than 3 subagent invocations in this session.

**Agent**: orchestrator (direct - not delegated)

**Dependencies**: Subtask 11. MUST observe PROMOTION_DONE-OR-HANDOFF-DONE from HK-1a Verification before starting.

**active_rubrics**: []

**Design phase**: no with reason housekeeping

**UX phase**: no

### Subtask 13: Commit + Push [housekeeping]

**Description**: Commit all code changes and integrate them upstream. This plan has three git roots: parent repo for `docs/v2-design/plan-v2-ux-followup-fixes.md`, agent repo at `conversational-search`, and proxy repo at `conversational-search/conversational-proxy`. Commit and push each touched repo from its own git root with targeted `git add <path>` only. Do not treat proxy as an agent submodule; it has its own branch and remote. Use the standard housekeeping role-detection protocol (`[ -n "$CAA_CHILD_SIDECAR_DIR" ]`) to decide L1 push versus L2+ merge for the parent/session context, but preserve the nested-repo separation for agent and proxy changes.

**Agent**: orchestrator (direct - not delegated)

**Dependencies**: Subtask 12 (session audit)

**Repository-specific staging discipline**:
- Parent repo: stage this plan and any parent docs only.
- Agent repo: `cd conversational-search`; stage only changed agent source/tests/prompt files.
- Proxy repo: `cd conversational-search/conversational-proxy`; stage only changed proxy source/tests/setup files.
- Never use `git add -A`, `git add .`, or `git commit -am` in any repo.
- Preserve untracked unrelated files seen during planning: `conversational-search/agent_diff.txt`, `conversational-search/runs/`, `conversational-search/uv.lock.local-pre-ff-2026-06-01`, and proxy `dump.rdb` unless a subtask explicitly claims them.

**Witness sentinel**: write `{session_dir}/push-result.txt` with `pushed=success\nsha=<sha>` only after verified push/merge for all touched repos required by this plan. On any failure path do not write the file.

**active_rubrics**: []

**Design phase**: no with reason housekeeping

**UX phase**: no

### Subtask 14: /cycling terminal - finalize sentinel [housekeeping]

**Description**: Invoke `/cycling terminal` sentinel-finalization step (HK-1b sub-phase) after Commit + Push succeeds. This step emits the `SESSION-COMPLETION-SENTINEL` with `status='success'` to the change-log, marking the session as successfully completed for the study pipeline.

**Agent**: orchestrator (direct - not delegated)

**Dependencies**: Subtask 13. GATED on HK-3 success. For L1, check `{session_dir}/push-result.txt` contains `pushed=success`; for L2+, check `{session_dir}/merge-result.txt` contains `merged=success`. If the role-appropriate witness is absent or does not contain the success token, do not run this step.

**Verification (TERMINAL_FINALIZED):**
- Run `knowledge(action='change-log', file_exact='{session_dir}/SESSION-COMPLETION-SENTINEL', actor='external:cycling-terminal-sentinel', limit=1)`. Step passes if returned `entries[]` contains at least one entry with `status === 'success'`.
- Do NOT use `[ -f {session_dir}/SESSION-COMPLETION-SENTINEL ]`; the sentinel is a change-log entry only.

**active_rubrics**: []

**Design phase**: no with reason housekeeping

**UX phase**: no

### Subtask 15: Knowledge-Hygiene Pipeline / Study Orchestrator [housekeeping]

**Description**: Invoke the knowledge-hygiene pipeline, the Study Orchestrator, via CLI Bash, not the Agent tool. The launch is fire-and-forget: `Bash(run_in_background=true, command="cd $MAIN_ROOT && bin/claude-study post-completion")`, capture the bash_id, yield turn immediately, and do not poll `BashOutput` post-launch. This change touches code and docs across knowledge-relevant conversational-search/proxy surfaces, so the skip condition is not met and the pipeline must run.

**Agent**: orchestrator (direct - not delegated)

**Dependencies**: Subtask 14

**Pre-launch mutex probe (DEFER / HUNG / LAUNCH):** Before invoking, read `.claude/knowledge/.study-state` JSON and branch on its contents:
- **LAUNCH** - `running: false` or absent: proceed to the background launch.
- **DEFER** - `running: true` and `now - running_since < 35min`: record `H4 DEFERRED: prior run started <Nm> ago`; do not re-invoke.
- **HUNG** - `running: true` and `now - running_since >= 35min`: surface to user and ask them to manually set `running: false` in `.study-state`, then launch after confirmation.

**Exit-code-2-on-success caveat**: under worktree-cwd execution paths, the study process can exit non-zero after applying knowledge edits because git staging crosses symlink boundaries. Treat exit 2 as ambiguous-success unless run artifacts prove failure.

**active_rubrics**: []

**Design phase**: no with reason housekeeping

**UX phase**: no

## Post-Completion

The housekeeping subtasks above cover the post-loop steps. After they complete: surface any deferred PCAs and unmet completion criteria in the final response to the user.

## Plan Peer-Review Disposition

Peer-review: applies

Pre-emission self-audit: 15 subtasks present; implementation/fix subtasks cover A, B, C, D, F, G, H1, and H2; Slovak-language issue intentionally excluded; coherence-auditor present; 5 mandatory housekeeping subtasks appended in order including Study Orchestrator; production streaming/SSE subtasks A+C, B, and G remediation carry `[peer-review]`; H2 is first-wave to establish agent baseline; proxy validations use `scripts/test.sh`.
