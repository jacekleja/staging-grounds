# Subtask 13: Integration tests + model-call-budget assertion

**Description**: Add end-to-end integration tests that exercise the completed spine + modes + turn-2+ paths together, and make the one-LLM-call-on-turn-1 commitment (concept-id t-3) a MACHINE-ENFORCED assertion, not just a per-impl-report claim. This is the convergence subtask: it depends on the spine (6), the modes (7,8,9,10), and turn-2+ (12).

Coverage:
- **Spine end-to-end:** a product_search turn-1 through the rebased topology (`reset_tool_call_count → handle_regular_turn`) produces a tier-classified composition (in shadow mode: tier logged, composition computed) and emits the correct §6 LBJSON block for each of the 4 tiers (decisive→refinement_chips, shapeable→refinement_chips_with_hatch, exploratory→question_led, intractable→hard_fork). NO product block on turn-1.
- **Modes:** each of the 7 modes routes to its distinct downstream handler (not the shared body): product_search (spine), gift_advisor (chat takeover + anchored chips), advice (3 routes), comparison (side-by-side, mid-flow), support (keyword CTA, 0 LLM calls), out_of_scope (polite template, no search), unsafe (hard refuse + turn_events row).
- **Turn-2+:** the 3 entry kinds (chip / typed / chat) each take their distinct path; tier/composition inherited; products permitted turn-2.
- **MODEL-CALL BUDGET (the t-3 gate):** an assertion that turn-1 makes EXACTLY ONE streaming LLM call on every mode path (use the existing `llm_call_count` metric / instrumentation — knowledge notes turn-1 nominal = 1 streaming call from `handle_regular_turn`; `verify_search_intent` uses `ainvoke`+`TAG_NOSTREAM` and is not a streaming call). Deflection keyword hits make 0 calls. This test FAILS the build if any turn-1 path regresses to 2 calls.

Reconcile with the known multi-turn benchmark fixture constraints (the harness `_run_one` single-turn limitation + the `additional_search_finish` trailing-tool_use leak) so the new multi-turn integration tests do not silently misroute — read the linked constraints.

**Agent**: implementer

**Knowledge**:
- `.claude/knowledge/conversational-agent/architecture.md § Agent (LangGraph State Machine)` — turn-1 = ONE streaming call; the `llm_call_count` metric site; `verify_search_intent` non-streaming.
- `.claude/knowledge/constraints/benchmark-multi-turn-fixtures-misroute.md` — the harness single-turn limitation + fixture shape requirement (so multi-turn integration tests are authored correctly).
- `.claude/knowledge/constraints/benchmark-multi-turn-tool-use-leak-additional-search-finish.md` — the trailing-tool_use leak to avoid in multi-turn fixtures.
- `docs/_handoff-pack/03 · Handoff brief.md` § 3 (hard commitments) + § 6 (the per-mode/per-tier shapes the tests assert).

**Dependencies**: Subtask 6 (spine), Subtask 7 (dispatch + deflections), Subtasks 8/9/10 (conversational modes), Subtask 12 (turn-2+).

**Context files**:
- `{session_dir}/` impl-reports from Subtasks 2,3,4,5,6,7,8,9,10,12 — each reports its per-path LLM-call count; this subtask turns those claims into machine assertions and the per-mode shape contracts into test fixtures.

**Expected output**: An integration test suite covering the spine, all 7 modes, and the 3 turn-2+ entry kinds, PLUS a turn-1 single-call-budget assertion that fails the build on regression. All tests pass on the rebased branch; build green. impl-report names the budget-assertion mechanism and lists which paths it covers (and any path it could NOT cover with a bounded reason).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-exercise-only (tests assert already-specified contracts; no design decision).

**UX phase**: no
