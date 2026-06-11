# Validator Report — subtask-4 (graph.py + config.py Gap 1 / Gap 8 closure)

## Verdict

**approve** — both rubrics (`code-vs-spec`, `constraint-compliance`) satisfied with zero critical and zero important gaps. The diff is intentionally minimal (config flag flip + one-line deterministic-assignment change + test updates); every checklist item and hard invariant traces to a verified code citation, and the validator-run unit suite reproduces 530 passed.

## Rubric: code-vs-spec

No gaps. Per-dimension trace:

- **D-spec-coverage** — C-01 (Gap 1): `graph.py:2278` `deterministic_preview_response = preview_response` replaces the prior `None if ... == "NO_PREVIEW"` collapse. The NO_PREVIEW sentinel now passes through to the `response is not None` branch (2325), which skips `_emit_deflection_text` for NO_PREVIEW (2326), leaves `_new_llm_count` unchanged (no second LLM call), and serializes the sentinel via `_serialize_no_preview_sentinel` at 2381 with `skip_validators=True`. C-13/C-14 (Gap 8): `config.py:86` flips `composition_table_live: False -> True`; `_resolve_product_search_tier_and_composition` (922-974) selects `table_composition` only when `mode==product_search AND composition_table_live` (944-947); table maps `exploratory->question_led` (graph.py:96). Spec contracts confirmed at the renderer (1154-1171): `shape`, `question.prompt`, `question.answers[>=2]`, demoted `carousel`, `browse_all_link`, `chat_affordance`.
- **D-edge-cases** — empty/short option lists (1155, 1173) return an empty block that `_is_empty_turn1_preview_block` (1217-1223) resolves to NO_PREVIEW; the narrow/zero-hit path is the exact Gap 1 surface and is exercised by `test_turn1_product_search_no_preview_emits_sentinel_without_llm`.
- **D-error-paths** — no new throw/catch introduced. NO_PREVIEW is treated as a deterministic value, not an exception. `_maybe_retry_on_tool_call_limit` (2365-2367) is a documented no-op on the deterministic-preview path because the sentinel response carries no `tool_calls`.
- **D-integration-points** — change is intra-module: a one-line assignment in `handle_regular_turn` plus a `Settings` field default. No new cross-module import/export edge. Existing consumers of `_PRODUCT_SEARCH_TIER_TO_COMPOSITION` (720-724, 909-913) are unchanged.
- **D-consistency** — comment style, `COMPOSITION_ENUM[n]` indexing idiom, and the `AIMessage(content="NO_PREVIEW")` sentinel pattern match the surrounding renderer code.
- **D-untested** — every changed branch has a targeting assertion: Gap 1 6th-edge fallback (`test_gap1_6th_edge_fallback_contains_no_products_block`), NO_PREVIEW no-LLM (`test_turn1_product_search_no_preview_emits_sentinel_without_llm`), Gap 8 question_led (`test_gap8_exploratory_tier_renders_question_led_with_live_table`), config flip (`test_composition_table_live_default_is_live`), and the cross-tier invariant (`test_product_search_tier_maps_to_authoritative_composition[...]`).
- **D-exercise-evidence** — impl-report `## Verification` (subtask-4-impl-report.md:44-104) is present and non-degenerate. `Exercised:` cites three documented cache-MISS live SSE artifacts (Yamaha F310 gap-1, accessories gap-8, guitar invariant) plus unit coverage with explicit assertions. `Not exercised, and why:` (101-104) carries bounded reasons (decisive/intractable have no convenient live query, covered by passing parametrized unit tests; turn-2 pivot covered by unit test in single-turn live mode). Not degenerate.

## Rubric: constraint-compliance

No gaps. Constraint glob classification (`.claude/knowledge/constraints/**/*.md`) for changed files `graph.py`, `config.py`:

- `conversational-agent-graph-gate-insertion-rules.md` (covers graph.py) — **does-not-apply**: governs new gate nodes that loop back to re-invoke a primary node; this diff adds no node, only a local assignment. Failure mode it prevents (unbounded verifier oscillation / synthesized tool_calls) is not in scope.
- `conversational-agent-langgraph-implementation-patterns.md` (covers graph.py) — **applies, compliant**: test code unwraps `Overwrite` via `.value` (`list(result["messages"].value)`); no new node/routing function added so the `Literal`-narrowing rule is not engaged.
- `conversational-search-env-file-overrides-code-defaults.md` (covers config.py) — **applies, deployment caveat satisfied**: a `config.py` default flip alone does not change runtime if `.env` overrides it, BUT all three live SSE artifacts report `composition_table_live=true` and `composition_table_live_switch_applied=true` at runtime, demonstrating the active state is in effect for the validated stack.
- `platform/platform-constraints.md` — read; no Python-runtime constraint contradicted by the diff.
- All other matched constraints govern non-applicable surfaces (proxy, benchmark harness, deploy/alembic, cross-repo enum) — **do-not-apply** to graph.py/config.py turn-1 composition logic.

Hard invariants C-15..C-19:

| Invariant | Determination | Evidence |
|---|---|---|
| C-15 lbjson chips extended not replaced | PASS | else-branch 1197-1201 preserves `chips:[{**option, facet}]`; guitar live shows `chips` key; the change adds the question_led branch alongside, not replacing chips |
| C-16 lbx.no_preview intact | PASS | sentinel path 2380-2382 `_serialize_no_preview_sentinel`; `test_..._no_preview_emits_sentinel_without_llm` PASS; Yamaha live event fires |
| C-17 turn-2 pivot inheritance intact | PASS | impl-report cites graph.py:2469-2474 + `test_inherited_question_led_emits_change_the_question_pivot` PASS |
| C-18 work_status sequence intact | PASS | `_emit_work_status` (2311) unchanged; `TestWorkStatusOrderingInvariant` in the 530-pass suite |
| C-19 <=1 LLM call turn-1 | PASS | NO_PREVIEW path adds zero LLM calls (no astream/ainvoke reached); three live runs show `llm_call_count=1` |
| composition_table_live flip preserves shapeable/decisive/intractable | PASS | table (93-98) maps each tier to its own composition; shapeable->refinement_chips_with_hatch unchanged; guitar live confirms tier=shapeable, composition=refinement_chips_with_hatch with switch active |

## Scope Covered

- `git -C conversational-search diff` (full): graph.py (+7/-... at 2272-2278), config.py:86, test_turn1_call_budget.py, test_config.py, test_graph_emit.py (+144)
- `graph.py` lines 92-100 (tier->composition table), 710-730 (`_resolve_composition_from_table`), 922-990 (`_resolve_product_search_tier_and_composition`), 1145-1240 (`_render_turn1_preview_block`, `_is_empty_turn1_preview_block`, `_render_turn1_preview_response`), 2255-2400 (`handle_regular_turn` sentinel/LLM/validator path)
- `subtask-4-impl-report.md` full (## Verification, Hard-invariant Checklist)
- `docs/v2-design/plan-v2-final-state-gap-closure.md` § Implementation Checklist (C-01..C-27), § Gap 1 (120-159), § Gap 8 (383-424)
- Knowledge consulted: `decisions/conversational-search-v2-discovery-digest.md#axis-a2-r4-diversity-primary-tier-classifier-phase-3-r4` [verified: graph.py:93-98 — TIER_ENUM shape-coded vocabulary matches code table]; `constraints/conversational-search-env-file-overrides-code-defaults.md` [verified: observed behavior — live SSE artifacts report composition_table_live=true at runtime]; `constraints/conversational-agent-graph-gate-insertion-rules.md` [verified: graph.py — no new gate node in diff]; `constraints/conversational-agent-langgraph-implementation-patterns.md` [verified: test_graph_emit.py — .value unwrap used]; `constraints/platform/platform-constraints.md` [verified: observed behavior — no constraint contradicted]
- Validator-run unit suite: `poetry run python3 -m pytest tests/ -q` -> 530 passed in 1.06s

## Scope Not Covered

- D-concurrency-idempotency: N/A (no-shared-state) — config flag default + deterministic local assignment; no locks/retries/concurrent callers introduced.
- D-security: N/A (no-untrusted-input, no-privilege-boundary, no-auth-code) — no exec/subprocess/secret/injection surface in the diff.
- D-sketch-adherence: N/A (no-design-phase-sketch-for-this-subtask) — no `{TASK}-R{N}-sketch.md` present in the worktree.
- Live SSE re-execution: not re-run by validator (read-only mandate). Live evidence validated from impl-report cited artifacts; correctness independently confirmed by validator unit re-run.
- decisive/intractable and turn-2 question_led-pivot live spot-checks: covered by passing parametrized unit tests; not independently re-run live (consistent with impl-report's bounded Not-exercised disclosure).

Findings emission self-check: 0 discoveries, 0 [promote:] annotations. (No knowledge-drift, novel constraint, decision, coupling, or gotcha surfaced — the env-override constraint is pre-existing and the live runs satisfy it.)
