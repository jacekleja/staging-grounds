# V2 Gap Analysis and Working Base

**Executive summary.** `feat/v2-campaign` is substantially implemented: mode dispatcher, tier enum, composition enum, state envelope, mode-stack LIFO, and the FM-3 gate test all land correctly. The major gaps are (a) no per-mode downstream handler differentiation beyond `product_search` (all 6 non-default modes parse but route to the same `handle_regular_turn`), (b) no turn-2+ handling beyond a slice-rule stub, (c) no composition renderer that emits structured lbjson affordance blocks, (d) `browse_intent`/`chat_takeover_trigger`/`fork_card_filter_value` state fields absent from state.py, and (e) the conversational-proxy carries no v2 wire fields. **BASE = `feat/v2-campaign`** — `staging/v2-sprint-2026-06-05` has dropped all 33 v2 commits and is NOT a valid rebase.

---

## Part 1 — Gap Analysis

### 1. Mode Dispatcher

**PARTIAL.**

`_parse_dispatch_prefix()` in `graph.py` (line 120) decodes `MODE: / TIER: / COMPOSITION:` structured-output prefixes from LLM text. All 7 canonical values are present in `MODE_ENUM` in `canonical_enums.py`. The parsed `_mode` value is written to state (`"mode": _mode`) at end of `handle_regular_turn`. Safe-defaults on parse failure (`product_search / shapeable / refinement_chips_with_hatch`) plus `lbx.dispatch_parse_failure` telemetry emit are implemented.

**What is missing:** the parsed mode is written to state but no downstream routing differentiates on it. There is no `if _mode == "gift_advisor"` branch, no `if _mode == "support"` redirect, no `if _mode == "unsafe"` hard-refuse path, and no `if _mode == "advice"` parallel-route fan-out. All turns flow to the same `handle_regular_turn` body regardless of mode. The Mode-B deferred short-circuit (`conversation_turn == 0 + firing_mode == "deferred"` → return empty) is implemented (line 219), but that is a firing-mode gate, not a mode-dispatcher downstream route.

[Verified: `conversational-search/src/conversational_search/agent/graph.py (_parse_dispatch_prefix)`]
[Verified: `conversational-search/src/conversational_search/agent/graph.py` — grep for `gift_advisor`, `out_of_scope`, `unsafe`, `advice`, `support` returned zero hits in routing logic]

### 2. Tier Classifier

**PARTIAL — proxy side only; agent side is enum-only.**

`canonical_enums.py` defines `TIER_ENUM = ["decisive", "shapeable", "exploratory", "intractable"]` (R4 vocabulary). The tier value is parsed from the LLM prefix and written to state. The proxy-side `tier_signal_computer.py` (referenced in knowledge entries from ep-14/ep-25) implements hot-path heuristics with `top_share_max`, `axis_entropy`, `filled_axes`, `has_brand_token` signals and boundary thresholds. However, `feat/v2-campaign` contains NO proxy directory — `git ls-tree -r feat/v2-campaign` shows only agent-side files; the proxy work lives on `main` (merged via infra PRs #30-#33). The agent-side "classifier" is entirely LLM-delegated: the prompt instructs the model to emit `TIER: <value>`, and `_parse_dispatch_prefix` decodes it. There is no agent-side hot-path heuristic.

**What is missing:** wiring between the proxy's `TierSignalComputer` output and the agent's tier field — the proxy computes tier signals but the agent currently gets tier from the LLM prefix, not from the proxy. Whether the final design intends LLM-derived tier or proxy-injected tier needs resolution before turn-2 handling can be built.

[Verified: `conversational-search/src/conversational_search/agent/canonical_enums.py (TIER_ENUM)`]
[Verified: `git ls-tree -r feat/v2-campaign` — no `conversational-proxy/` tree present]

### 3. Composition Renderer

**PARTIAL — enum declared; structured-output emission missing.**

`COMPOSITION_ENUM = ["refinement_chips", "refinement_chips_with_hatch", "question_led", "hard_fork"]` is defined. The composition value is parsed from the LLM prefix and written to state. However, there is no renderer that maps `(mode, tier) → composition` and emits the corresponding lbjson affordance block. The handoff brief §6 specifies that `refinement_chips_with_hatch` populates a `hatch:{}` block, `question_led` populates `question:{prompt, answers[]}`, and `hard_fork` populates a fork card — none of these affordance structs are emitted by any code on `feat/v2-campaign`.

`turn1_selector.py` (from `feat/pr-b2-core-production-features` lineage, present in the commit history as `Sprint 1 (M1)`) carries an earlier-generation vocabulary (`products_only`, `products_plus_chips`, `chips_only`, `zero_hit_recovery`) — a different and now-superseded shape set.

**What is missing:** all 4 composition renderers as structured lbjson output; (mode,tier)→composition decision table; `question_led` server-side question generation; `hard_fork` fork-card filter_value.

[Verified: `conversational-search/src/conversational_search/agent/canonical_enums.py (COMPOSITION_ENUM)`]
[Verified: `conversational-search/src/conversational_search/agent/graph.py` — grep for `refinement_chips`, `question_led`, `hard_fork` in rendering/emit code returned zero hits]

### 4. State Envelope

**PARTIAL.**

Present and wired in `state.py` on `feat/v2-campaign`:
- `tier: str` — written + read ✓
- `composition: str` — written + read ✓
- `mode_stack: list[str]` — written (LIFO push/pop logic at graph.py line 399-418) + read ✓
- `mode_stack_depth: int` — written as `len(_new_stack)` ✓
- `mode_at_compile: str | None` — written on compile turns ✓
- `firing_mode: str` — in `TURN_STATE_ENVELOPE_FIELDS` channel 1 ✓
- `conversation_turn: int` — written via `reset_tool_call_count` subtask 3.3 ✓

**Missing from state.py on `feat/v2-campaign`:**
- `browse_intent` — in `TURN_STATE_ENVELOPE_FIELDS` channel 1 spec but absent from `state.py` TypedDict
- `chat_takeover_trigger` — absent
- `fork_card_filter_value` — absent

[Verified: `conversational-search/src/conversational_search/agent/state.py` — grep for `browse_intent`, `chat_takeover_trigger`, `fork_card_filter_value` returned empty]
[Verified: `conversational-search/src/conversational_search/agent/canonical_enums.py (TURN_STATE_ENVELOPE_FIELDS)` — all three listed]

### 5. Conversational Modes — gift_advisor, comparison, advice

**MISSING for all three (downstream handling only; parsing DONE).**

- **gift_advisor** — mode parses correctly; no downstream turn-1 chat-takeover handler, no anchored category chip set, no guidebook-based quick replies.
- **comparison** — mode parses; LIFO mode-stack push/pop for `comparison` is implemented (graph.py line 402-404 pops `comparison` off stack when turn resolves to a different mode) — this is the D.4 stack mechanic. But no comparison-specific response shape (no two-product side-by-side, no structured comparison output).
- **advice** — mode parses; no three-parallel-route turn-1 fan-out (`type_it_out_parallel_on` is defined in `TURN_STATE_ENVELOPE_FIELDS` as a channel-3 derived field but `chat_affordance_on` and `type_it_out_parallel_on` are nowhere in state.py or graph.py routing).

[Verified: `conversational-search/src/conversational_search/agent/graph.py` — mode_stack comparison pop at lines 402-404 present]
[Verified: `conversational-search/src/conversational_search/agent/graph.py` — grep for `gift_advisor`, `advice`, `type_it_out` returned zero hits in routing]

### 6. Deflections — support, out_of_scope, unsafe

**MISSING (downstream handling); DONE (enum/parse).**

All three appear in `MODE_ENUM`. The `guardrail-keywords.yml` file is present on the branch (for unsafe keyword matching at the guardrail layer via `ci/lint-prompt-guardrails.py`). However:
- `support`: no shop-fillable YAML keyword OR-match at dispatch time; no CTA redirect path.
- `out_of_scope`: no polite short-response template emission; no LLM-with-guidebook path distinct from `product_search`.
- `unsafe`: `guardrail-keywords.yml` exists for prompt guardrail linting but the agent-side dispatch to a hard-refuse, no-UI-surface, logged path is absent.

[Verified: `git ls-tree feat/v2-campaign src/conversational_search/agent/guardrail-keywords.yml` — present]
[Verified: `conversational-search/src/conversational_search/agent/graph.py` — grep for `support`, `out_of_scope`, `unsafe` in routing returned zero hits]

### 7. Turn-1 vs Turn-2+

**PARTIAL — turn-1 fully gated; turn-2+ stub only.**

`is_first_turn` is still the primary gate (`_is_first_turn = state.get("is_first_turn", False)` at graph.py line 238). Turn-1 detection uses `conversation_turn == 1` derived in `reset_tool_call_count` (line 759: `result["is_first_turn"] = ct == 1`) — this FIXES the is_first_turn bug for turns 3+ because `ct` is derived from `conversation_turn` (proxy-sent canonical counter), not from a toggle that stays True.

Turn-2+ specific handling: a 4-message slice rule is referenced in a comment ("Slice rule: the last 4 messages — [HumanMessage(turn-2 user)...] at graph.py line ~793") but no chip-click handler, no typed-follow-up path differentiation, and no chat-affordance routing exist.

[Verified: `conversational-search/src/conversational_search/agent/graph.py:~759` — `is_first_turn = ct == 1`]
[Verified: `conversational-search/src/conversational_search/agent/graph.py:~793` — 4-message slice comment present, no handler]

### 8. Model-Call Budget (one-LLM-call turn-1 commitment)

**DONE — budget tracking implemented; structural commitment held by design.**

`max_llm_calls_per_turn_1` soft assertion is implemented (graph.py line 373-384): a budget-violation event is emitted if `llm_call_count > settings.max_llm_calls_per_turn_1`. Turn-1 uses `llm.astream()` (single call). The Mode-B short-circuit returns `{"messages": []}` with zero LLM calls (line 229). The verifier node short-circuits on `is_first_turn` (line 860-862): no second LLM call on turn 1. Overall turn-1 LLM call count = 1 (astream in `handle_regular_turn`) + 0 (verifier skipped). Commitment held.

[Verified: `conversational-search/src/conversational_search/agent/graph.py:~373` — budget violation soft assertion]
[Verified: `conversational-search/src/conversational_search/agent/graph.py:860` — verifier turn-1 short-circuit]

### 9. Proxy Wire Fields

**NOT ON FEAT/V2-CAMPAIGN — proxy not in branch scope.**

`feat/v2-campaign` contains no `conversational-proxy/` directory. The proxy work (TierSignalComputer, ConverseRequest v2 fields, conversation_service.py with firing-mode + tier/composition wire fields) is on `main` via merged infra PRs. Whether the proxy's `ConverseRequest` already carries v2 fields (`tier`, `composition`, `mode`, `conversation_turn`) needs verification against `main`'s proxy code — outside the scope of the agent branch.

[Verified: `git ls-tree -r feat/v2-campaign | grep proxy` — empty output]

### 10. Test Coverage

**PARTIAL.**

Tests present on `feat/v2-campaign`:
- `tests/integration/test_dispatch_prefix.py` (465 lines) — covers AC-2.2-1 through AC-2.2-9: all 7 modes parsed, R4 vocabulary, parse failure safe-defaults, `lbx.turn_classification` emit, Mode-B LLM skip, Mode-A fall-through. This is the FM-3 gate test.
- `tests/unit/test_canonical_enums.py` — enum parity and count assertions.
- `tests/unit/test_mode_stack_lifo.py` — LIFO stack push/pop invariants.
- `tests/unit/test_state_shape.py` — state field presence.

**Untested:**
- Any per-mode downstream handler (none exist yet, so no tests possible)
- Composition renderer / affordance block emission
- `browse_intent`, `chat_takeover_trigger`, `fork_card_filter_value` state transitions
- Turn-2+ chip-click and typed-follow-up paths
- `support` keyword-match deflection
- `unsafe` hard-refuse path
- Tier-signal → composition decision table

[Verified: `git ls-tree -r feat/v2-campaign tests/` — full test file list]
[Verified: `conversational-search/tests/integration/test_dispatch_prefix.py` — 465 lines, covers AC-2.2-1..9]

---

## Part 2 — Base Verification

### Submodule Status

`conversational-search` is a **nested git repo** (not a tracked submodule — `.gitmodules` is empty in the parent repo). The live checkout at `/home/fanderman/projects/luigis-box/conversational-search` is currently on **`main`** (`git branch --show-current` = `main`). There are 3 untracked items (`conversational-proxy/`, `runs/`, `uv.lock.local-pre-ff-2026-06-01`).

[Verified: `conversational-search/` — `git branch --show-current` output `main`]
[Verified: `luigis-box/.gitmodules` — empty]

### Is staging a clean rebase of feat/v2-campaign onto main?

**NO. staging/v2-sprint-2026-06-05 has dropped all 33 feat/v2-campaign commits.**

Evidence:
- `git log --oneline feat/v2-campaign ^staging/v2-sprint-2026-06-05` lists all 33 v2 commits as absent from staging.
- `git diff feat/v2-campaign staging/v2-sprint-2026-06-05 --stat` shows `49 files changed, 2007 insertions(+), 5497 deletions(-)` — staging has 5,497 fewer lines than feat/v2-campaign.
- `git show staging/v2-sprint-2026-06-05:src/conversational_search/agent/graph.py | grep _parse_dispatch_prefix` = 0 hits. The dispatcher is entirely absent from staging.
- `git show staging/v2-sprint-2026-06-05:src/conversational_search/agent/state.py | grep tier` = empty. The v2 state fields are gone.
- `staging` has 10 commits ahead of main (the infra PRs: Kimi K2.5, cache, Guardrails).

Staging does NOT contain the v2 work. It is `main` + the 10 infra commits, **not** `feat/v2-campaign` rebased onto anything.

No conflict markers (0 hits for `<<<<<<<`) — the branch is internally clean, it simply excludes the v2 commits entirely.

[Verified: `git log --oneline feat/v2-campaign ^staging/v2-sprint-2026-06-05` — 33 commits listed]
[Verified: `git diff --stat feat/v2-campaign staging/v2-sprint-2026-06-05` — 5497 deletions]
[Verified: `git show staging/v2-sprint-2026-06-05:src/conversational_search/agent/graph.py | grep -c _parse_dispatch_prefix` = 0]

### Does staging lose anything vs feat/v2-campaign?

Yes — it drops everything: dispatcher, tier/composition state fields, mode-stack LIFO, FM-3 test, canonical_enums additions, turn_events migrations (v0/v1 DDL), signature_cache schema, prompt_fingerprint + mode_at_compile, and the FM-3 test. The diff removes `guardrail-keywords.yml`, `ci/lint-prompt-guardrails.py`, multiple migration files, and condenses `custom_events.py` and `graph.py` back to pre-v2 shapes.

---

## Part 3 — Recommended Base

**BASE = `feat/v2-campaign`.**

Staging is not a rebase — it is a decoy. Using staging would discard 33 commits of scaffolding and require rebuilding everything from scratch.

The correct first work item is: **rebase `feat/v2-campaign` onto `main`** (which already contains the infra PRs: Kimi K2.5 default, agent-side cache surface, Bedrock Guardrails). This is the equivalent of what staging was supposed to do but didn't. Expect moderate graph.py conflict surface — both lineages touch graph.py heavily.

---

## Part 4 — Remaining Work (PARTIAL / MISSING only), Phase-Aligned

Work items are ordered with the `product_search` spine first, then conversational modes, then deflections, then infrastructure.

### Phase 3 (tier classifier) — spine prerequisite
1. **Proxy-to-agent tier signal handoff** [PARTIAL]: Decide whether the agent derives tier from the LLM prefix (current implementation) or from proxy-injected `TierSignalComputer` output. If proxy-injected, add the wire field to `ConverseRequest` v2 and read it in `compile_system_prompt` / state initialization before the LLM call. Until resolved, tier is LLM-only.

### Phase 4 (composition renderer) — `product_search` spine
2. **Composition renderer for `refinement_chips`** [MISSING]: Emit `chips:[{label,filter_value,facet,count}]` block when `composition == "refinement_chips"`. Reuse/extend existing chip-emit path.
3. **Composition renderer for `refinement_chips_with_hatch`** [MISSING]: Add `hatch:{}` block for "Just looking / browse" affordance. Requires `browse_intent` state field and turn-2 hatch-click handler.
4. **`browse_intent` state field + hatch-click turn-2 handler** [MISSING]: Add `browse_intent` to `ConversationState`, write on hatch click, read in turn-2 to route to browse carousel template.
5. **Composition renderer for `question_led`** [MISSING]: Emit `question:{prompt, answers:[]}` — model-generated or template-driven discriminating question. Requires question-quality eval set (handoff brief §9.5) as gate.
6. **Composition renderer for `hard_fork`** [MISSING]: Emit fork-card with `filter_value`. Add `fork_card_filter_value` state field.

### Phase 3/4 — (mode,tier)→composition decision table
7. **Decision table wire-up** [MISSING]: Implement the `(mode, tier) → composition` mapping (handoff brief §6) and make composition renderer branch on it, rather than accepting whatever the LLM emits as the composition prefix.

### Phase 5 (mode dispatcher) — downstream routing
8. **`gift_advisor` turn-1 handler** [MISSING]: Chat-takeover path; anchored category chip set (Hobbies, Lifestyle, Practical, I have an idea). Requires `chat_takeover_trigger` state field.
9. **`comparison` response shape** [MISSING]: Mode-stack LIFO push/pop is DONE. What is missing: structured comparison output shape, two-product side-by-side affordance block.
10. **`advice` three-route turn-1 fan-out** [MISSING]: `type_it_out_parallel_on` channel-3 derived field; three parallel routes (type-it-out, chips, direct answer). Add `chat_affordance_on` to state.
11. **`support` keyword-match deflection** [MISSING]: Shop-fillable YAML keyword OR-match at dispatch entry; redirect to shop CTAs (no LLM call on keyword hit).
12. **`out_of_scope` short-response handler** [MISSING]: Guidebook-driven polite response template; no product search.
13. **`unsafe` hard-refuse path** [MISSING]: Template-only response, no UI surface, logged turn_events row with `triggering_keyword` field (A.1 export spec in `canonical_enums.py`).

### Turn-2+ handling
14. **Turn-2 chip-click handler** [MISSING]: Incoming `filter_value` from chip click → turn-2 narrowing search. Distinct from typed follow-up.
15. **Turn-2 typed-follow-up differentiation** [MISSING]: Distinguish typed follow-up from chip click at proxy/agent boundary. `chat_takeover_trigger` / `prior_search_context` field needed.

### Proxy v2 wire fields
16. **ConverseRequest v2 fields** [status unknown — proxy on main, not in agent branch]: Verify `main`'s `ConverseRequest` carries `conversation_turn`, `mode`, `tier`, `composition`, `browse_intent`, `fork_card_filter_value`, `chat_takeover_trigger`. Add any missing fields.

### Infrastructure / rebase
17. **Rebase `feat/v2-campaign` onto `main`** [prerequisite work item]: Integrate the 10 infra-sprint commits (Kimi K2.5, cache, Guardrails, T1 message-shape). Resolve graph.py conflicts.

---

Pre-emission self-audit: 18 citations verified, 4 parts present, 3 contradictions checked (staging dispatcher absence confirmed; state field absence confirmed; is_first_turn bug fix confirmed).
