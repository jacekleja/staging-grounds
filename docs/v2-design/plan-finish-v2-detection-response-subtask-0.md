# Subtask 0: Rebase feat/v2-campaign onto submodule main (GATED, safety-valve)

**Description**: Rebase the v2 work onto current submodule `main` to pick up the infra sprint (Kimi K2.5, signature cache, T1 message-shape restructure) that landed on `main` 2026-06-05 but postdates `feat/v2-campaign`. Work entirely inside the `conversational-search/` nested git repo.

Steps:
1. In `conversational-search/`, confirm the base: `git log --oneline feat/v2-campaign` should show the ~33 v2 commits (last 2026-05-26). DO NOT use `staging/v2-sprint-2026-06-05` — it is a TRAP that dropped all 33 v2 commits (verified in the gap analysis: `git diff --stat feat/v2-campaign staging/v2-sprint-2026-06-05` = 5497 deletions, dispatcher absent).
2. Create a NEW branch from feat/v2-campaign so the original stays recoverable: `git checkout feat/v2-campaign && git checkout -b feat/v2-campaign-rebased`. Do NOT force-move or delete `feat/v2-campaign`.
3. `git rebase main` (the submodule's local main, which carries the infra PRs). Resolve conflicts with REAL understanding of BOTH sides — the most likely collision is the v2 tier-classifier-folded-into-turn-1 design colliding with the infra T1 message-shape restructure in `graph.py` (`first_turn_init` / `create_graph`), and likely `config.py` / `llm_factory.py` / `langgraph_client.py`. CRITICAL: the infra sprint RETIRED `first_turn_init` and moved to `reset_tool_call_count → handle_regular_turn`; the v2 dispatcher/tier work must be reconciled INTO that current topology, not the old node names. Preserve the `bedrock_service_tier: ""` Kimi K2.5 override mask in `llm_factory.py § _BEDROCK_MODEL_OVERRIDES` (a rebase that drops it re-enables the ambient service-tier leak for Kimi).
4. VERIFY post-rebase: the submodule builds (e.g. `uv sync` / package import) and the existing test suite passes — explicitly including the FM-3 dispatcher gate test (`tests/integration/test_dispatch_prefix.py`) and the mode-stack LIFO test (`tests/unit/test_mode_stack_lifo.py`).
5. **SAFETY VALVE (load-bearing — operator is asleep, a broken base is worse than a halt):** if conflicts cannot be resolved with confidence OR any test fails post-rebase, run `git rebase --abort` to restore a pristine state, leave `feat/v2-campaign` and the new branch untouched/clean, and HALT this subtask with a clear report naming the exact conflicting files, the two sides' intents, and which test failed. Do NOT commit a silently-broken base.

**Agent**: implementer

**Knowledge**:
- `docs/v2-design/research-v2-gap-and-base.md` (Part 2 base verification + Part 3 recommended base — the staging trap evidence and the rebase rationale)
- `.claude/knowledge/conversational-agent/architecture.md` § Agent (LangGraph State Machine) — the CURRENT post-infra topology (`reset_tool_call_count → handle_regular_turn`); `first_turn_init` is retired. This is what the rebased graph must look like.
- `.claude/knowledge/constraints/bedrock-service-tier-ambient-settings-leak.md` — the Kimi K2.5 `bedrock_service_tier: ""` override mask to preserve through the rebase.

**Dependencies**: none (this is the gated prerequisite for everything else).

**Context files**: none.

**Expected output**: A new submodule branch `feat/v2-campaign-rebased` that builds and passes the existing test suite (incl. FM-3 gate), with `feat/v2-campaign` untouched — OR a clean `git rebase --abort` and a HALT report. The impl-report MUST state: the rebased branch name, the conflict files resolved and how, the test-suite result (pass/fail with the FM-3 gate named explicitly), and confirmation the original branch still exists. If halted: the conflict surface and why confidence was insufficient.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason research-only-no-design-decision (the rebase target and branch strategy are fully specified; conflict resolution is judgement within a constrained boundary, not an open design choice — if a conflict's resolution is genuinely two-defensible-ways the safety valve halts rather than guessing).

**UX phase**: no
