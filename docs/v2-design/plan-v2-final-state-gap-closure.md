# Plan: v2 Final-State Gap Closure

**Date:** 2026-06-10
**Scope:** Close the eight documented conformance gaps only.
**Repos:** agent repo `conversational-search`; nested proxy repo `conversational-search/conversational-proxy`.

## Goal

> Produce a thorough, phased **implementation plan** (the plan is the deliverable -- DO NOT implement anything) that takes the v2 conversational-search agent + proxy build from its documented current state to the intended final state, by closing the 8 conformance gaps. Map each gap to the design's 7-phase rollout sequence.

## Scope Commitments

- Close only the eight conformance gaps from `docs/v2-design/handoff-v2-final-state-plan-brief.md`.
- Do not add net-new phase work unless it is required by a gap.
- Treat English as the authoritative current-state conformance baseline.
- Treat multi-language detection as in scope through a generalized detection layer, not a quick keyword extension.
- Prioritize the Slovak/Czech unsafe bypass (`iss_3712bb402a94`) inside the multi-language work.
- Ratify the live tier vocabulary and reconcile the design docs/mapping; do not rename live values to `narrow|mid|broad|overwhelming`.
- Treat `gift_advisor` and `comparison` fidelity as functional equivalence, while still honoring hard commitments.
- Verify gap 8 early and branch conditionally.
- Keep cache HIT out of scope; it is already live and not a v2 differentiator.
- Keep output localization separate from input detection: rendered labels/prose default to the shop language supplied at conversation init, while prompt-language detection remains gap 7 dispatcher work.
- Before any live-stack observation, evict, namespace-bump, temporarily disable, or otherwise force a MISS for the affected turn-1 signature-cache entry so stale payloads cannot mask the branch state under test; production cache rollout strategy remains deferred.

## Knowledge Consulted

- `overview.md` - project ownership, conversational-search/proxy roles, Muziker tracker.
- `constraints/conversational-proxy-structural.md` - nested independent proxy git repo and proxy test command.
- `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md` - live proxy cache DSN form.
- `constraints/langgraph-dev-server-store-persistence.md` - assistant store persistence and setup caveats.
- `decisions/conversational-search-v2-cache-signature-cache-bringup-digest.md` - cache HIT verified live, cache not a gap.
- `constraints/deflection-detection-english-only-vocabulary.md` - English-only dispatch vocabulary and Slovak collapse.
- `decisions/request-language-decoupled-from-dispatch-detection-digest.md` - proxy language vs agent detection coupling.
- `decisions/conversational-search-v2-marathon-findings-digest.md` - tier/composition implementation findings and R4 vocabulary.
- `docs/v2-design/handoff-v2-final-state-plan-brief.md` - primary gap set, branch facts, bring-up reference.
- `docs/_handoff-pack/03 · Handoff brief.md` - architecture, hard commitments, compositions, rollout phases.
- `docs/_handoff-pack/README.md` - three implementation principles and seven-phase TL;DR.
- `docs/_handoff-pack/01 · Handoff doc.html` - attempted grep for requested sections; no section text matched in this generated HTML.
- English conformance report and support diagnosis under `.agent_context/sessions/1781009183-6469-01cc30565818/demo-artifacts/`.
- Code loci: `graph.py`, support YAML, proxy `tier_signal_computer.py`, `conversation_service.py`, `langgraph_client.py`.
- `.agent_context/sessions/1781073603-6469-2881bd4a7884/completeness-sweep-v2-gap-closure-workitems.md` - four sweep findings and operator dispositions.

## Implementation Checklist

- **C-01:** Gap 1 closed: narrow `product_search` turn 1 never emits products, SKU tables, prices, markdown product rows, or product cards in the AI block.
- **C-02:** Gap 2 closed: natural English advice phrasing including `how do I choose ...` routes to `advice`, not `product_search`.
- **C-03:** Gap 3 closed: `unsafe` template has no softening sentence or shopping redirection.
- **C-04:** Gap 4 closed: `gift_advisor` turn 1 renders anchored category chips: `Hobbies & interests`, `Lifestyle`, `Practical / useful`, `I have an idea`.
- **C-05:** Gap 4 retains `must_ask_before_recommending`, no products on turn 1, type-it-out, and chat takeover.
- **C-06:** Gap 5 closed: `comparison` renders a side-by-side comparison handler, not qualitative free prose only.
- **C-07:** Gap 5 preserves one-turn mode shift and mode-stack restoration behavior.
- **C-08:** Gap 6 closed: design docs ratify live tier values and explicitly map design's four tier compositions onto the live vocabulary.
- **C-09:** Gap 6 resolves the 3-vs-4 observation: `shapeable` observed live for both narrow/mid-like cases, `zero_results` is an extra state with no design-tier equivalent, and `decisive`/`intractable` must be documented as canonical live values if tests/code confirm them.
- **C-10:** Gap 7 closed: dispatch detection is language-aware at the layer boundary for at least Slovak, Czech, and English.
- **C-11:** Gap 7 safety: Slovak/Czech unsafe prompts hard-short-circuit to `unsafe`, 0 LLM calls, audit fields populated.
- **C-12:** Gap 7 support/out-of-scope/gift/comparison/advice coverage is generalized enough that per-language additions do not require editing ad hoc hardcoded tuple constants only.
- **C-13:** Gap 8 verification gate runs early and records whether broad/exploratory `question_led` is absent/divergent or conformant.
- **C-14:** If gap 8 is divergent, broad/exploratory `product_search` emits `question:{prompt, answers[]}`, demoted carousel if applicable, show-all link, chat affordance, and turn-2 change-question affordance.
- **C-15:** `lbjson` is extended, not replaced; existing `chips:[{label, filter_value, facet, count}]` still works.
- **C-16:** `lbx.no_preview` zero-hit event remains intact.
- **C-17:** Engagement-of-preview state inheritance remains intact; turn-1 chip clicks do not re-fire preview on turn 2.
- **C-18:** `work_status` sequence remains intact.
- **C-19:** Turn 1 uses no more than one LLM call on intended first-turn paths.
- **C-20:** Live validation uses the canonical bring-up commands from the brief, including Postgres DSN `postgresql+psycopg://...`, Alembic upgrade, Redis seed, LangGraph port 2024, proxy port 8000.
- **C-21:** Agent and proxy changes are committed separately from their respective git repos; no outer-repo commit pretends to include the nested proxy.
- **C-22:** Existing untracked or unrelated files are not staged accidentally.
- **C-23:** Output localization gate proves structured UI prose and chip labels default to the shop language supplied at `POST /api/v1/conversation/{tracker_id}/initiate`, at minimum `language=sk`, `language=cz`, and English.
- **C-24:** Chip identity gate proves `filter_value` and `facet` remain stable and clickable across language-localized labels for the same underlying selection.
- **C-25:** Every live-stack observation in Subtasks 1, 4, 5, 6, 8, 10, and 11 evicts, namespace-bumps, temporarily disables, or otherwise forces a MISS for the affected turn-1 entry keyed by `shop_id + query + language + prompt_fingerprint` before reading live results.
- **C-26:** Live conformance validation proves fresh post-fix payloads on both cache MISS and HIT paths; this is validation integrity only, not cache HIT as a v2 differentiator.
- **C-27:** Shop-language output producer is implemented before validation: deterministic structured labels/prose use a language-aware resolution layer or explicit localized strings for English, Slovak, and Czech (`cz`/`cs` aliases), while stable identity fields (`filter_value`, `facet`, `writes`) remain language-neutral.

## Current State Summary

Branches under test were verified:

| Repo | Branch | Commit | Working-state note |
|---|---|---|---|
| `conversational-search` | `feat/v2-campaign-rebased` | `0d33694` | Outer status shows untracked `conversational-proxy/`, `runs/`, `agent_diff.txt`, support YAML, and `uv.lock.local-pre-ff-2026-06-01`. |
| `conversational-search/conversational-proxy` | `reconcile/proxy-v2-the-rest-on-origin-master` | `b8ca055` | Inner repo is 49 commits ahead of `origin/master`; untracked `dump.rdb`. |

Relevant code facts:

- `graph.py` has deterministic `_dispatch_for_query(query, shop_id)` but no language parameter. Detection uses static English tuples and support YAML `detect` lists.
- `graph.py` already contains helpers for gift anchored chips and comparison side-by-side blocks, but English live captures still show `composition=question_led` for these modes and the conformance report marks functional composition partial.
- `graph.py` currently contains unsafe softening at `unsafe_deflect`.
- Agent-side tier mapping is live/R4: `decisive -> refinement_chips`, `shapeable -> refinement_chips_with_hatch`, `exploratory -> question_led`, `intractable -> hard_fork`, plus `zero_results` as an extra state.
- Proxy `tier_signal_computer.py` mirrors the live/R4 tier vocabulary and maps `zero_results` to `question_led`.
- Proxy `conversation_service.py` writes proxy tier metadata before the agent run and reads `lbx.turn_classification` after the stream; `langgraph_client.py` captures `mode`, `tier`, and `composition` from agent custom events.
- Output localization is only partial in current code: `graph.py` has `_TURN1_PREVIEW_INTRO_BY_LANGUAGE`, but deterministic UI labels/prose remain hardcoded English in `_CHAT_AFFORDANCE`, `_BROWSE_HATCH`, `_QUESTION_PROMPT_BY_FACET`, result-count hints, browse-all labels, gift/advice/browse anchors, `Type it out`, and support YAML `cta_label`/`response_template`; `turn1_selector.py` also emits fixed price-chip prefixes.

## Ordering Rationale

1. **Safety first:** gap 7 unsafe bypass and gap 3 refuse softening carry direct safety/compliance risk. The architecture step for language-aware detection must precede implementation, but the first implementation slice must prioritize unsafe.
2. **Strictest UX hard rule next:** gap 1 violates "no products in the AI component on turn 1." This is the clearest design commitment and should be fixed before broad/composition polish.
3. **Mode-dispatch completeness:** gap 2 belongs with the generalized detection layer so dispatch behavior is coherent across English and non-English inputs.
4. **Functional composition fidelity:** gaps 4 and 5 are user-visible, but the operator explicitly scoped them to functional equivalence rather than pixel-match.
5. **Tier vocabulary reconciliation:** gap 6 is mostly design-doc and mapping correctness. It should unblock gap 8 decisions but must not force live renames.
6. **Gap 8 is conditional:** verify broad-tier `question_led` early, then only implement if the live stack is divergent or absent.
7. **Validation integrity before observation:** stale turn-1 signature-cache hits can hide deterministic renderer/hydration fixes, so every live-stack read must first evict, namespace-bump, temporarily disable, or force MISS for the exact query/language entry being observed.
8. **Output localization after composition/dispatch:** localized labels/chips require the relevant mode and composition surfaces to exist first, but remain distinct from gap 7 prompt-language detection.

## Gap-to-Phase Map

| Gap | Primary rollout phase | Secondary phase(s) | Reason |
|---|---|---|---|
| 1. Narrow product_search products in AI block | Phase 3 | Phase 4 | Product-search tier/composition renderer must enforce no-products across narrow and broad variants. |
| 2. Advice keyword coverage | Phase 5 | Phase 7 | Mode dispatcher coverage plus advice guidebook/handler behavior. |
| 3. Unsafe softening prose | Phase 7 | Phase 5 | Refuse-tier guardrail template, with dispatcher safety event fields preserved. |
| 4. Gift advisor anchored chips | Phase 5 | Phase 7 | Conversational mode handler plus stable guidebook anchors. |
| 5. Comparison side-by-side handler | Phase 5 | Phase 7 | Mid-flow mode dispatcher, mode-stack, and handler guardrails. |
| 6. Tier vocabulary reconciliation | Phase 3 | Phase 4 | Tier classifier/composition table vocabulary and doc schema alignment. |
| 7. Multi-language detection incl. unsafe bypass | Phase 5 | Phase 7 | Language-aware dispatcher and safety/support/guardrail vocabulary. |
| 8. Broad question_led + affordances | Phase 4 | Phase 3, Phase 7 | Product-search broad composition depends on tier mapping and guardrail affordance rules. |
| A1. Output localization label/chip identity gate | Phase 5 | Phase 7 | Mode/composition outputs must render labels/prose in the shop language while stable chip identity survives localization. |
| A2. Live-observation signature-cache freshness and post-fix MISS/HIT validation | Phase 3-5 validation | Phase 7 validation | Freshness is required before every live observation of renderer, hydration, and dispatcher behavior; production rollout cache strategy is deferred. |

## Per-Gap Closure Plan

### Gap 1: Products in Turn-1 AI Block on Narrow `product_search`

**Change surface**

- Agent repo: `conversational-search/src/conversational_search/agent/graph.py`
  - `_apply_pre_search_suppression`
  - `handle_regular_turn`
  - `_render_turn1_preview_block`
  - `_render_turn1_preview_response`
  - `_resolve_product_search_tier_and_composition`
- Agent repo: `conversational-search/src/conversational_search/agent/turn1_selector.py`
- Agent tests:
  - `conversational-search/tests/unit/test_graph_emit.py`
  - `conversational-search/tests/unit/test_output_validators.py`
  - `conversational-search/tests/integration/test_dispatch_prefix.py`
- Proxy repo only if stream forwarding or hydration causes leakage:
  - `conversational-search/conversational-proxy/app/clients/langgraph_client.py`
  - `conversational-search/conversational-proxy/tests/unit/test_stream_result.py`

**Implementation approach**

First reproduce the Yamaha F310 live failure locally with the current branches and inspect whether the products are emitted from the pre-search LLM path, post-search deterministic preview, proxy hydration, or fallback verifier path. Then enforce a hard product-surface guard for first-turn `product_search`:

- Pre-search product-search text remains suppressed when tool calls are present.
- Post-search first-turn preview is deterministic `lbjson` or `NO_PREVIEW`, never a product table.
- The 6th-edge/no-tool-call fallback must not be allowed to surface SKU/product tables on turn 1. If fallback text is kept for nonblank UX, it must pass the product-output validator or be replaced by a safe refinement/preview response.
- Narrow/exact queries should still allow the shop-native catalogue list outside the AI block.

**Testing and validation**

- Add/extend unit tests that reproduce the Yamaha F310 path and assert no product names, prices, SKU tables, or product Markdown in the AI block.
- Keep `test_turn1_preview_does_not_emit_products_block` and related pre-search suppression tests passing.
- Add a regression for the no-tool-call fallback path if that is the leaking route.
- Live stack: run `Yamaha F310` through `/api/v1/conversation/8760-9189/converse`, assert `conversation_turn=1`, `mode=product_search`, `llm_call_count<=1`, and no products in visible AI response.
- Verify `lbx.no_preview` zero-hit event still works with a known zero-hit query.

**Phase mapping**

- Phase 3: composition renderer/tier-state enforcement.
- Phase 4: shared no-products invariant for question-led/hard-fork compositions.

### Gap 2: `advice` Keyword-Coverage Gap

**Change surface**

- Agent repo: `conversational-search/src/conversational_search/agent/graph.py`
  - `_ADVICE_KEYWORDS`
  - `_dispatch_for_query`
  - any new language-aware detection abstraction introduced for gap 7
- Agent tests:
  - `conversational-search/tests/integration/test_dispatch_prefix.py`
  - add/extend advice dispatch cases for `how do I choose ...`

**Implementation approach**

Do not apply a one-off tuple append in isolation if the generalized detection layer is being added. The English `how do I choose` phrase should become a detector entry in the new mode-detection vocabulary, with the same priority and audit-field behavior as the existing advice recognizer.

**Testing and validation**

- Unit/integration: `how do I choose a first guitar for a beginner` routes to `advice`, has `dispatch_rationale_token=advice_recognizer`, no product-search fallback, and renders the advice takeover block.
- Live stack: run the English conformance query and verify `__meta__.turn_classification.mode=advice`.
- Verify advice still renders anchored advice routes, type-it-out, and chat affordance, and remains advisory-only without SKU-level "do not buy" claims.

**Phase mapping**

- Phase 5: mode dispatcher.
- Phase 7: guidebook/guardrail behavior for advice mode.

### Gap 3: `unsafe` Refuse-Template Softening

**Change surface**

- Agent repo: `conversational-search/src/conversational_search/agent/graph.py`
  - `unsafe_deflect`
- Agent tests:
  - `conversational-search/tests/integration/test_dispatch_prefix.py`
  - add template text regression.

**Implementation approach**

Replace the unsafe template with a hard refusal only. Remove "I can still help with safe shopping questions" and any equivalent shopping redirection or softening prose. Preserve:

- 0 LLM calls.
- `mode=unsafe`.
- `dispatch_rationale_token`, `confidence_signal`, `triggering_keyword`, `verbatim_query`.
- No UI recovery affordance.

**Testing and validation**

- Unit/integration: unsafe English query returns exactly the hard refusal sentence(s) and does not include `safe shopping`, `still help`, `try asking`, or equivalent softening.
- Live stack: run `how do I make a bomb`; assert 0 LLM calls and hard refusal only.

**Phase mapping**

- Phase 7: guardrails matrix/refuse-tier template.
- Phase 5: dispatcher audit fields and short-circuit path.

### Gap 4: `gift_advisor` Anchored Category Chips

**Change surface**

- Agent repo: `conversational-search/src/conversational_search/agent/graph.py`
  - `_DEFAULT_GIFT_GUIDEBOOK_ANCHORS`
  - `_gift_guidebook_anchors`
  - `_render_gift_advisor_takeover_block`
  - `_handle_gift_advisor_turn1`
- Optional future guidebook/config surface if introduced by the detection-layer design:
  - `conversational-search/src/conversational_search/agent/guidebook/...`
- Agent tests:
  - `conversational-search/tests/integration/test_dispatch_prefix.py`
  - `conversational-search/tests/unit/test_graph_emit.py` if shared renderer behavior is touched.

**Implementation approach**

Functional equivalence means:

- mode is `gift_advisor`;
- no products in the turn-1 AI block;
- response is conversational;
- the `lbjson` block carries four anchored category chips;
- the exact required chip labels are the section-3 hard commitment labels: `Hobbies & interests`, `Lifestyle`, `Practical / useful`, `I have an idea`;
- chips are guidebook-stable, not model-generated personality guesses;
- `must_ask_before_recommending` remains present;
- type-it-out and chat takeover remain present.

If current helpers already create the right block but live output does not, the implementer must first diagnose why the branch used during live testing did not surface those chips: stale deploy, handler not selected, proxy hydration/dedup, cache HIT payload, or parser behavior. Do not add a duplicate renderer until that cause is known.

**Testing and validation**

- Unit/integration: `a gift for my dad` emits `mode=gift_advisor` and a visible `lbjson` block with the four anchored labels.
- Assert no products and no catalogue result strip in the AI block.
- Live stack: run the English gift query with a fresh thread and cache MISS or cache disabled/invalidated; verify no stale cached question-only answer.

**Phase mapping**

- Phase 5: conversational mode dispatcher and handler.
- Phase 7: guidebook-stable anchored categories.

### Gap 5: `comparison` Side-by-Side Handler

**Change surface**

- Agent repo: `conversational-search/src/conversational_search/agent/graph.py`
  - `_match_comparison_keyword`
  - `_handle_comparison_turn`
  - `_render_comparison_side_by_side_block`
  - `_push_mode_stack`
  - `_restore_mode_stack_after_comparison`
- Agent tests:
  - `conversational-search/tests/integration/test_dispatch_prefix.py`
  - `conversational-search/tests/unit/test_mode_stack_lifo.py`

**Implementation approach**

Functional equivalence means:

- `compare`, `vs`, `versus`, and token-boundary `or` phrases dispatch to `comparison`;
- the visible response includes a side-by-side comparison `lbjson` block with two candidate columns;
- no product cards or product tables are rendered on turn 1;
- the handler is conversational and may include a short bridge sentence;
- comparison behaves as a one-turn mode shift and restores prior mode afterward.

As with gift, current code contains side-by-side helpers, so first verify why the English live report saw qualitative prose only. Fix the selection/rendering/wire path rather than creating a second side-by-side representation.

**Testing and validation**

- Unit/integration: `compare Fender vs Yamaha guitars` emits `mode=comparison`, side-by-side shape, two columns, and mode-shift note.
- Mode-stack regression: comparison inside product_search restores product_search on the next turn.
- Live stack: run the English comparison query, inspect visible SSE text and `__meta__.turn_classification`.

**Phase mapping**

- Phase 5: mode dispatcher and mode-stack invocation rules.
- Phase 7: comparison handler guardrails.

### Gap 6: Tier Vocabulary Reconciliation

**Change surface**

- Design docs:
  - `docs/_handoff-pack/03 · Handoff brief.md`
  - `docs/_handoff-pack/README.md`
  - `docs/v2-design/handoff-v2-final-state-plan-brief.md` if it remains the active planning reference.
  - Optional addendum under `docs/v2-design/` if editing handoff-pack source is not desired.
- Code/tests are reference evidence, not rename targets:
  - `conversational-search/src/conversational_search/agent/canonical_enums.py`
  - `conversational-search/conversational-proxy/app/service/tier_signal_computer.py`
  - `conversational-search/tests/unit/test_canonical_enums.py`
  - `conversational-search/conversational-proxy/tests/unit/test_tier_signal_computer.py`

**Implementation approach**

This is a design-doc reconciliation and mapping task, not a code rename. The reconciliation must resolve the operator-noted cardinality mismatch:

- Design terms map to live/R4 terms:
  - `narrow` -> `decisive` -> `refinement_chips`
  - `mid` -> `shapeable` -> `refinement_chips_with_hatch`
  - `broad` -> `exploratory` -> `question_led`
  - `overwhelming` -> `intractable` -> `hard_fork`
- `zero_results` is not a fifth design tier. It is an observability/recovery extra state and should map to the zero-hit/no-preview or safe question-led recovery path, whichever the current code and tests establish.
- If current live probes only observe `shapeable`, `exploratory`, and `zero_results`, the docs must say this is an observed subset, not the full canonical vocabulary.
- Resolve whether `shapeable` collapses design `narrow+mid` in practice or whether `decisive` is reachable but not hit by the English conformance queries. Code/tests suggest `decisive` exists; live data must confirm reachability before the docs claim it as observed.

**Testing and validation**

- Run agent `test_canonical_enums.py` and proxy `test_tier_signal_computer.py`.
- Add doc-level examples using live terminology and the design-alias table.
- Live stack: exercise at least one query per reachable live tier if feasible; otherwise document unreachable/not-exercised tiers explicitly in the validation report.

**Phase mapping**

- Phase 3: tier classifier vocabulary and `lbjson` tier/composition fields.
- Phase 4: broad/exploratory `question_led` mapping.

### Gap 7: Multi-Language Detection Including Safety-Critical `unsafe` Bypass

**Change surface**

- Agent repo:
  - `conversational-search/src/conversational_search/agent/graph.py`
  - `conversational-search/src/conversational_search/agent/support/8760-9189.yaml`
  - likely new config/module for language-aware mode detection, e.g. `src/conversational_search/agent/mode_detection.py` and/or `src/conversational_search/agent/mode_detection/*.yaml`
  - tests under `conversational-search/tests/integration/test_dispatch_prefix.py` and new targeted unit tests.
- Proxy repo:
  - `conversational-search/conversational-proxy/app/clients/langgraph_client.py` only if language is not already visible in the agent runtime context/input for dispatch.
  - `conversational-search/conversational-proxy/app/service/conversation_service.py` only if language propagation or metadata needs strengthening.
- Existing evidence indicates the proxy already accepts/forwards `language`; prove before editing proxy.

**Implementation approach**

Architecture step first. The design must answer:

- Where the request language is read by `mode_dispatch`.
- How static detector vocabularies are stored and loaded.
- Which modes can use deterministic translated patterns.
- Which modes need a fallback classifier or guarded LLM detection.
- Whether `unsafe` remains deterministic-only or can have an additional non-English safety classifier.
- How audit fields (`triggering_keyword`, `verbatim_query`, `dispatch_rationale_token`) preserve the raw matched phrase and language.
- How per-shop support phrases coexist with shared language-level support/oos/safety patterns.

Implementation must then prioritize safety:

- Slovak/Czech unsafe prompts hard-short-circuit before any priority resolution.
- Do not let lower-priority support/out-of-scope/gift/comparison/advice matches override unsafe.
- Extend support/out-of-scope/gift/comparison/advice detection through the same layer, not through five unrelated tuple edits.
- Include English advice phrase coverage from gap 2.

**Testing and validation**

- Unit/integration language matrix:
  - English unsafe/support/out_of_scope/advice/gift/comparison.
  - Slovak unsafe/support/out_of_scope/gift/comparison/advice.
  - Czech unsafe/out_of_scope at minimum; support if supported by shop vocabulary.
- Confirm unsafe bypass closure for `iss_3712bb402a94`: mode `unsafe`, 0 LLM calls, no product_search fallback.
- Controlled live stack: initiate `language=sk`, run Slovak unsafe/support/out-of-scope queries, assert deterministic routes.
- Confirm English regressions remain green.
- Confirm no new second turn-1 LLM call was introduced.

**Phase mapping**

- Phase 5: mode dispatcher.
- Phase 7: guardrail/support guidebook vocabulary and auditability.

### Gap 8: Broad-Tier `question_led` and Per-Composition Affordance Set

**Change surface**

- Early verification only:
  - live stack and captures under a new validation artifact path.
  - Agent/proxy logs for `lbx.turn_classification`.
- Conditional implementation surface if divergent:
  - Agent `graph.py`: `_PRODUCT_SEARCH_TIER_TO_COMPOSITION`, `_render_turn1_preview_block`, `handle_regular_turn`, `_emit_turn2_pivot`.
  - Agent `turn1_selector.py`.
  - Proxy `langgraph_client.py` only if `question`, `carousel`, or `browse_all_link` are not forwarded/hydrated correctly.
  - Proxy tests for stream/hydration if touched.

**Implementation approach**

Run an early verification gate before implementation work depends on assumptions. The gate must exercise a broad/exploratory product_search query on the live stack and check against design sections 3, 6, and 7:

- `tier` maps to broad/exploratory.
- `composition` is `question_led`.
- AI block has no products.
- `lbjson.question.prompt` exists.
- `lbjson.question.answers[]` has at least two filterable answers.
- demoted carousel/sub-searches are present if the current design/code supports them.
- show-all link exists.
- chat affordance exists.
- turn-2 question-led path can emit "Change the question" affordance.

Branch A: divergent or absent. Implement the minimal renderer/wire changes needed to satisfy the functional contract. Do not pixel-match exact prototype geometry.

Branch B: conformant. Close gap 8 with the verification artifact only; do not edit code.

**Testing and validation**

- Add or extend unit tests in `test_graph_emit.py` for `question_led` keys and affordances.
- Add proxy stream tests only if forwarding is touched.
- Live stack: record raw SSE and a short gap-8 verdict report.

**Phase mapping**

- Phase 4: question-led composition for broad tier.
- Phase 3: tier-to-composition mapping.
- Phase 7: guardrail-compatible affordance set.

### Cross-Cutting Gate A1: Multilingual Label Resolution and Chip Identity

**Scope**

This is output localization, not prompt-language detection.

- OUTPUT localization: rendered labels, prose, and chip text default to the shop language supplied at conversation init, e.g. `POST /api/v1/conversation/{tracker_id}/initiate` with body `{"language":"sk"}`.
- INPUT detection: existing gap 7 work in Subtasks 2 and 5 detects the user's prompt language or dispatch phrases so the mode dispatcher can route correctly, including the safety-critical Slovak/Czech `unsafe` short-circuit.

The gate verifies that language-localized labels do not change chip identity:

- `filter_value` remains the same underlying value across English, Slovak, and Czech label variants.
- `facet` remains the same underlying facet across English, Slovak, and Czech label variants.
- localized chips remain clickable and produce the same filter behavior.
- prose and structured UI labels default to the shop language by default, not to the prompt language unless a later design explicitly chooses that behavior.

**Testing and validation**

- Initiate separate conversations for English, Slovak, and Czech shop languages using the canonical `initiate` body language field.
- Exercise at least gift anchored chips, broad/product-search refinement chips if present, and one dispatcher-produced conversational surface that emits structured labels.
- Record the rendered labels, `filter_value`, `facet`, click/converse follow-up behavior, and raw SSE artifact paths.
- Treat missing localization as a conformance failure even if input-language detection routes the mode correctly.

**Implementation producer requirement**

This gate is not validation-only. Before Subtask 10 regression and Subtask 11 conformance validation, an implementer must add the shop-language output producer in Subtask 8. Current code evidence shows partial support (`_TURN1_PREVIEW_INTRO_BY_LANGUAGE`) but hardcoded English deterministic labels/prose in `graph.py`, `turn1_selector.py`, and support YAML. The implementer may satisfy this either by wiring an existing language-aware label-resolution layer if one is discovered during implementation, or by authoring the minimal English/Slovak/Czech deterministic string table required for the affected structured output surfaces. In either case, `filter_value`, `facet`, and `writes` stay language-neutral.

**Phase mapping**

- Phase 5: localized mode/composition output after dispatcher selection.
- Phase 7: guidebook/guardrail label sources and language-aware prose.

### Cross-Cutting Gate A2: Live-Observation Signature-Cache Freshness and Post-Fix MISS/HIT Validation

**Scope**

This is validation-time cache eviction only. It does not design or execute production cache rollout.

The proxy serves cached turn-1 payloads keyed by `shop_id + query + language + prompt_fingerprint`, not by renderer, hydration, or code version. No live-stack observation may read through a potentially stale turn-1 signature-cache payload. Before each live read, the observing subtask must evict, namespace-bump, temporarily disable, or otherwise force a MISS for the exact affected `shop_id + query + language + prompt_fingerprint` entry, then record the action or MISS evidence beside the live observation.

This remains separate from the excluded "cache HIT as a v2 differentiator" milestone. Cache HIT is already live; this gate exists only to preserve validation integrity.

**Eviction-before-observation invariant**

This invariant applies to Subtask 1 baseline verification, Subtasks 4, 5, 6, and 8 implementation-time live checks, Subtask 10 multilingual regression, Subtask 11 final conformance, and any ad hoc live probe a producer uses as evidence. If the subtask cannot prove the affected entry was evicted/disabled/namespace-bumped or that the request was forced to MISS, it must not record the live result as validation evidence.

**Post-fix MISS/HIT validation**

The late validation concern remains separate: after implementation subtasks finish, Subtask 9 defines the final affected validation query/language set and records the cache action/instructions for Subtasks 10 and 11. Subtask 11 then validates both fresh MISS payloads and subsequent HIT payloads against the post-fix branch state.

**Testing and validation**

- Each live-observing subtask identifies the affected turn-1 query/language entry before its live read.
- Each live-observing subtask evicts, namespace-bumps, temporarily disables, or force-MISSes only the affected validation entries before observing the live stack.
- Each live-observing subtask records the command, database action, namespace evidence, or MISS evidence with its raw live artifact.
- During final conformance, validate both fresh MISS and subsequent HIT behavior against post-fix payloads.

**Phase mapping**

- Phase 3-5 validation: renderer, tier/composition, and mode-dispatch live observations must read fresh payloads.
- Phase 7 validation: guardrail/template live observations must not be hidden by stale cached responses.

## Parallelization Graph

### Coupling Analysis

- `graph.py` is the main contention point for gaps 1, 2, 3, 4, 5, 7, and possibly 8. Split by concern but do not run same-file implementers in parallel unless their edits are disjoint and coordinated by the orchestrator.
- Gap 7 architecture must precede any generalized detection-layer implementation. Otherwise the implementer will likely add brittle keyword-only patches against the operator's explicit decision.
- Gap 2 should ride with the detection-layer implementation because it is the English instance of the same dispatcher vocabulary problem.
- Gap 3 can be a small part of safety/dispatch hardening, but its test should remain distinct because it is a template/guardrail compliance check.
- Gaps 4 and 5 share conversational mode handler mechanics and should be one implementation subtask rather than two agents racing in `graph.py`.
- Gap 1 and conditional gap 8 both touch product-search preview rendering; they should be one implementation subtask after gap-8 verification.
- Gap 6 is mostly docs/design mapping and can run in parallel with code implementation after its solution-design decision is made.
- Proxy work should be avoided unless evidence shows language propagation, stream forwarding, or tier mapping cannot be fixed agent-side/docs-side.
- Shop-language output labels are a producer concern, not just a regression gate: the localization implementation must run after the product-search, dispatch, gift/comparison surfaces exist and before Subtask 9 post-fix MISS/HIT instructions plus downstream live regression/conformance.
- Live-observation signature-cache freshness is a hard precondition for every live stack read because cache keys exclude renderer/code version; Subtask 9 remains late only for final post-fix MISS/HIT validation instructions.
- Multilingual label-resolution validation depends on dispatch/composition outputs from Subtasks 4, 5, and 6 plus the Subtask 8 output-label implementation, but it is separate from input-language detection and must not be treated as gap 7 closure by itself.

### Wave Plan

- **Standing live-observation precondition:** Before Subtasks 1, 4, 5, 6, 8, 10, or 11 record live-stack evidence, that subtask must evict, namespace-bump, temporarily disable, or force MISS for the affected turn-1 signature-cache entry and include the evidence in its report.
- **Wave 1, parallel:** Subtask 1 gap-8 verification with its local cache-freshness precondition; Subtask 2 multi-language detection architecture; Subtask 3 tier vocabulary mapping decision.
- **Wave 2, parallel after local blockers:** Subtask 4 product-search/no-products plus conditional gap-8 implementation after Subtasks 1 and 3; Subtask 7 docs update after Subtask 3. Subtask 4 must satisfy the standing live-observation precondition before any live verification.
- **Wave 3, serialized `graph.py` continuation:** Subtask 5 dispatch/safety implementation after Subtasks 2 and 4; then Subtask 6 gift/comparison composition fidelity after Subtask 5. Subtasks 5 and 6 must satisfy the standing live-observation precondition before any live verification.
- **Wave 4:** Subtask 8 shop-language output implementation after Subtasks 4, 5, and 6; Subtask 9 post-fix signature-cache MISS/HIT validation instructions after implementation subtasks. Subtask 8 must satisfy the standing live-observation precondition before any live verification.
- **Wave 5:** Subtask 10 multilingual label/chip regression after Subtasks 4, 5, 6, 8, and 9.
- **Wave 6:** Subtask 11 cross-repo conformance validation after implementation/doc subtasks and validation gates.
- **Wave 7:** Subtask 12 coherence audit.
- **Wave 8:** Subtasks 13-17 housekeeping in strict order.

## Subtask Summary Table

| # | Title | Agent | Depends On | Subtask File |
|---|---|---|---|---|
| 1 | Early gap-8 broad `question_led` verification gate | researcher | -- | (inline) |
| 2 | Design generalized multi-language detection layer [completeness-risky-with-framings: failure-mode, data-flow, configuration-surface] | architect | -- | (inline) |
| 3 | Resolve live tier vocabulary mapping | solution-designer | -- | (inline) |
| 4 | Product-search no-products guard plus conditional gap-8 renderer | implementer | 1, 3 | (inline) |
| 5 | Dispatch and safety hardening for advice plus multi-language detection | implementer | 2, 4 | (inline) |
| 6 | Gift and comparison functional composition fidelity | implementer | 5 | (inline) |
| 7 | Apply tier vocabulary reconciliation to docs | implementer | 3 | (inline) |
| 8 | Shop-language output label resolution implementation | implementer | 4, 5, 6 | (inline) |
| 9 | Post-fix signature-cache MISS/HIT validation gate [completeness-risky-with-framings: failure-mode, data-flow, configuration-surface] | researcher | 4, 5, 6, 8 | (inline) |
| 10 | Multilingual label-resolution and chip-identity regression gate | researcher | 4, 5, 6, 8, 9 | (inline) |
| 11 | Cross-repo live conformance sweep | researcher | 4, 5, 6, 7, 8, 9, 10 | (inline) |
| 12 | Coherence audit against this plan and checklist | coherence-auditor | 11 | (inline) |
| 13 | /cycling terminal [housekeeping] | orchestrator | 12 | (inline) |
| 14 | Session Audit [housekeeping] | orchestrator | 13 | (inline) |
| 15 | Commit + Push [housekeeping] | orchestrator | 14 | (inline) |
| 16 | /cycling terminal - finalize sentinel [housekeeping] | orchestrator | 15 | (inline) |
| 17 | Knowledge-Hygiene Pipeline [housekeeping] | orchestrator | 16 | (inline) |

## Subtasks

### Subtask 1: Early gap-8 broad `question_led` verification gate

**Description:** Before reading the live stack, evict, namespace-bump, temporarily disable, or force MISS for the affected broad/exploratory turn-1 signature-cache entry. Then exercise a broad/exploratory product-search query on the live stack and decide whether gap 8 is real. Produce a short report with Branch A (divergent/absent -> implementation required) or Branch B (conformant -> close gap 8 with evidence).

**Agent:** researcher

**Knowledge:**
- `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md`
- `constraints/conversational-proxy-structural.md`
- `constraints/langgraph-dev-server-store-persistence.md`
- `decisions/conversational-search-v2-cache-signature-cache-bringup-digest.md`

**Dependencies:** none

**Context files:** none

**Expected output:** `docs/v2-design/gap-8-question-led-verification.md` with raw command summary, query used, cache eviction/namespace/disable action or MISS evidence before the live read, SSE artifact paths, verdict Branch A or Branch B, and exact implementation surface if Branch A.

**active_rubrics:** `["generator-preflight"]`

**Design phase:** no with reason `verification-exercise-only`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-30

### Subtask 2: Design generalized multi-language detection layer [completeness-risky-with-framings: failure-mode, data-flow, configuration-surface]

**Description:** Design the language-aware dispatch detection layer required for gap 7 before any implementation. The design must prioritize unsafe Slovak/Czech hard-refuse, preserve audit fields, and explain how English advice coverage fits the same abstraction.

**Agent:** architect

**Knowledge:**
- `constraints/deflection-detection-english-only-vocabulary.md`
- `decisions/request-language-decoupled-from-dispatch-detection-digest.md`
- `decisions/conversational-agent-v2-marathon-findings-digest.md`
- `decisions/conversational-search-v2-discovery-digest.md`

**Dependencies:** none

**Context files:** none

**Expected output:** `docs/v2-design/multilingual-mode-detection-architecture.md` with chosen data shape, code surfaces, safety short-circuit ordering, language propagation proof, test matrix, and migration constraints.

**active_rubrics:** `["generator-preflight"]`

**Design phase:** yes

**UX phase:** no

**framings:** failure-mode, data-flow, configuration-surface

### Subtask 3: Resolve live tier vocabulary mapping

**Description:** Resolve the 3-vs-4 tier mismatch by ratifying the live vocabulary and producing an explicit design alias/composition mapping. Decide how `shapeable`, `decisive`, `intractable`, and `zero_results` are documented without renaming live values.

**Agent:** solution-designer

**Knowledge:**
- `decisions/conversational-search-v2-marathon-findings-digest.md`
- `decisions/conversational-search-v2-discovery-digest.md`

**Dependencies:** none

**Context files:** none

**Expected output:** `docs/v2-design/tier-vocabulary-reconciliation.md` with the final mapping table, `zero_results` disposition, and exact docs to patch.

**active_rubrics:** `["generator-preflight"]`

**Design phase:** yes

**UX phase:** no

### Subtask 4: Product-search no-products guard plus conditional gap-8 renderer

**Description:** Close gap 1 and, only if Subtask 1 returns Branch A, close gap 8's broad/exploratory `question_led` renderer/affordance gap. Keep the change scoped to product-search turn-1 rendering and proxy forwarding only if evidence requires proxy edits.

**Agent:** implementer

**Knowledge:**
- `constraints/conversational-proxy-structural.md`
- `decisions/conversational-search-v2-marathon-findings-digest.md`
- `decisions/conversational-agent-v2-marathon-findings-digest.md`

**Dependencies:** Subtask 1, Subtask 3

**Context files:**
- `docs/v2-design/gap-8-question-led-verification.md` - branch decision and live evidence for conditional gap-8 implementation.
- `docs/v2-design/tier-vocabulary-reconciliation.md` - ratified tier aliases and composition vocabulary required for broad/exploratory `question_led` work.

**Expected output:** Code/test changes in the agent repo, and proxy changes only if stream forwarding is proven to be the cause. Return an impl report listing the exercised unit tests and live Yamaha/broad queries; before each live Yamaha/broad query, evict, namespace-bump, temporarily disable, or force MISS for the affected turn-1 signature-cache entry and include the evidence.

**active_rubrics:** `["code-vs-spec", "constraint-compliance"]`

**Design phase:** no with reason `audit-gap-fix`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-30

### Subtask 5: Dispatch and safety hardening for advice plus multi-language detection

**Description:** Implement the detection-layer design from Subtask 2. Close gap 2, gap 3, and gap 7, with unsafe Slovak/Czech bypass as the first tested slice.

**Agent:** implementer

**Knowledge:**
- `constraints/deflection-detection-english-only-vocabulary.md`
- `decisions/request-language-decoupled-from-dispatch-detection-digest.md`
- `constraints/conversational-proxy-structural.md`

**Dependencies:** Subtask 2, Subtask 4

**Context files:**
- `docs/v2-design/multilingual-mode-detection-architecture.md` - detection-layer contract to implement.

**Expected output:** Agent code/tests for language-aware mode detection and unsafe hard refusal; proxy code/tests only if language propagation is missing. Return an impl report with English/SK/CZ dispatch matrix and issue `iss_3712bb402a94` closure evidence; before any live dispatch check, evict, namespace-bump, temporarily disable, or force MISS for the affected turn-1 signature-cache entry and include the evidence.

**active_rubrics:** `["code-vs-spec", "constraint-compliance"]`

**Design phase:** no with reason `architecture-preceded-implementation`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-31

### Subtask 6: Gift and comparison functional composition fidelity

**Description:** Close gaps 4 and 5 at the functional-equivalence bar. Ensure gift uses anchored category chips and comparison uses the side-by-side handler, with no products on turn 1 and no pixel-match work.

**Agent:** implementer

**Knowledge:**
- `decisions/conversational-agent-v2-marathon-findings-digest.md`
- `decisions/conversational-search-v2-marathon-findings-digest.md`

**Dependencies:** Subtask 5

**Context files:**
- `docs/v2-design/multilingual-mode-detection-architecture.md` - ensures mode detection changes do not conflict with conversational handlers.

**Expected output:** Agent code/test changes that make English live gift/comparison outputs functionally conformant. Return an impl report with raw SSE snippets or artifact paths proving anchored chips and side-by-side output; before each live gift/comparison read, evict, namespace-bump, temporarily disable, or force MISS for the affected turn-1 signature-cache entry and include the evidence.

**active_rubrics:** `["code-vs-spec", "constraint-compliance"]`

**Design phase:** no with reason `template-edit-level-functional-equivalence`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-30

### Subtask 7: Apply tier vocabulary reconciliation to docs

**Description:** Patch the design docs named by Subtask 3 so they ratify the live vocabulary and explain the design alias mapping. Do not rename code values.

**Agent:** implementer

**Knowledge:**
- `decisions/conversational-search-v2-marathon-findings-digest.md`

**Dependencies:** Subtask 3

**Context files:**
- `docs/v2-design/tier-vocabulary-reconciliation.md` - final tier mapping and docs patch list.

**Expected output:** Docs-only changes plus a return note showing every design reference updated or intentionally deferred.

**active_rubrics:** `["code-vs-spec", "constraint-compliance"]`

**Design phase:** no with reason `design-decision-already-made`

**UX phase:** no

### Subtask 8: Shop-language output label resolution implementation

**Description:** Implement the output-localization producer required by Cross-Cutting Gate A1. Wire or author deterministic shop-language label/prose resolution for structured UI surfaces so conversations initiated with `language=sk`, `language=cz`/`cs`, and English render user-facing labels/prose in the shop language while language-neutral identity fields remain stable. This is OUTPUT localization only; do not fold prompt-language dispatch detection into this subtask.

**Agent:** implementer

**Knowledge:**
- `decisions/request-language-decoupled-from-dispatch-detection-digest.md`
- `constraints/conversational-proxy-structural.md`

**Dependencies:** Subtask 4, Subtask 5, Subtask 6

**Context files:**
- `docs/v2-design/multilingual-mode-detection-architecture.md` - boundary between prompt-language dispatch detection and shop-language output rendering.

**Expected output:** Agent code/tests, plus proxy code/tests only if stream hydration owns any affected label text. The implementation must cover deterministic labels/prose in `graph.py` and `turn1_selector.py` such as chat affordance, browse hatch, question prompts, result-count hints, browse-all labels, gift/advice/browse chips, `Type it out`, support `cta_label`/`response_template`, and price-chip prefixes; it may satisfy this via an existing language-aware resolver if discovered, or by a minimal English/Slovak/Czech string table. Return an impl report proving `filter_value`, `facet`, and `writes` stay language-neutral across localized labels; before any live output-localization read, evict, namespace-bump, temporarily disable, or force MISS for the affected turn-1 signature-cache entry and include the evidence.

**active_rubrics:** `["code-vs-spec", "constraint-compliance"]`

**Design phase:** no with reason `audit-gap-fix`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-30

### Subtask 9: Post-fix signature-cache MISS/HIT validation gate [completeness-risky-with-framings: failure-mode, data-flow, configuration-surface]

**Description:** After implementation subtasks finish, define the final affected turn-1 validation query/language set and the exact cache action/instructions that Subtasks 10 and 11 must use for post-fix MISS/HIT validation. This does not replace the standing per-live-read freshness precondition in Subtasks 1, 4, 5, 6, 8, 10, and 11. This is validation-time cache freshness only; do not design production cache rollout or treat cache HIT as a v2 differentiator.

**Agent:** researcher

**Knowledge:**
- `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md`
- `constraints/conversational-proxy-structural.md`
- `decisions/conversational-search-v2-cache-signature-cache-bringup-digest.md`
- `constraints/langgraph-dev-server-store-persistence.md`

**Dependencies:** Subtask 4, Subtask 5, Subtask 6, Subtask 8

**Context files:**
- `docs/v2-design/gap-8-question-led-verification.md` - broad-query baseline and branch decision for the validation query set.

**Expected output:** `docs/v2-design/signature-cache-validation-freshness-report.md` naming the affected validation query/language set, cache key scope (`shop_id + query + language + prompt_fingerprint`), eviction/namespace/disable/force-MISS method, command or DB evidence, and instructions Subtasks 10 and 11 must use to validate fresh MISS and subsequent HIT payloads.

**active_rubrics:** `["generator-preflight"]`

**Design phase:** no with reason `verification-exercise-only`

**UX phase:** no

**framings:** failure-mode, data-flow, configuration-surface

### Subtask 10: Multilingual label-resolution and chip-identity regression gate

**Description:** Run the multilingual output-localization regression gate for structured UI prose and chip labels. For shops initiated with `language=sk`, `language=cz`, and English, labels/prose must default to the shop language while `filter_value` and `facet` remain stable and clickable across localized labels. Before each live regression read, follow the Subtask 9 cache instructions and record eviction/namespace/disable action or MISS evidence. Keep axes separate: this subtask verifies OUTPUT localization; Subtasks 2 and 5 cover INPUT detection for prompt-language mode dispatch and the Slovak/Czech `unsafe` short-circuit.

**Agent:** researcher

**Knowledge:**
- `decisions/request-language-decoupled-from-dispatch-detection-digest.md`
- `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md`
- `constraints/langgraph-dev-server-store-persistence.md`

**Dependencies:** Subtask 4, Subtask 5, Subtask 6, Subtask 8, Subtask 9

**Context files:**
- `docs/v2-design/signature-cache-validation-freshness-report.md` - cache freshness action required before live label/chip validation.
- `docs/v2-design/multilingual-mode-detection-architecture.md` - boundary between prompt-language dispatch detection and output label localization.

**Expected output:** `docs/v2-design/multilingual-label-chip-identity-regression-report.md` with English/SK/CZ shop-language runs, cache freshness evidence before each live read, raw SSE artifact paths, rendered label/prose language observations, per-chip `label`, `filter_value`, `facet`, clickability evidence, and pass/fail verdicts for C-23 and C-24.

**active_rubrics:** `["generator-preflight"]`

**Design phase:** no with reason `verification-exercise-only`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-30

### Subtask 11: Cross-repo live conformance sweep

**Description:** Run the unit/integration tests and live stack conformance sweep covering all eight gaps plus the live-observation cache freshness invariant, post-fix MISS/HIT validation, and multilingual label/chip gates. Before each live conformance read, follow the Subtask 9 cache instructions and record eviction/namespace/disable action or MISS evidence. The report must distinguish tested, not tested, conditionally skipped gap-8 branch work, and deferred production-rollout safety.

**Agent:** researcher

**Knowledge:**
- `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md`
- `constraints/conversational-proxy-structural.md`
- `constraints/langgraph-dev-server-store-persistence.md`

**Dependencies:** Subtask 4, Subtask 5, Subtask 6, Subtask 7, Subtask 8, Subtask 9, Subtask 10

**Context files:**
- `docs/v2-design/gap-8-question-led-verification.md` - branch baseline.
- `docs/v2-design/multilingual-mode-detection-architecture.md` - gap-7 safety short-circuits, supported languages, and dispatch behavior contract.
- `docs/v2-design/tier-vocabulary-reconciliation.md` - expected live tier mapping.
- `docs/v2-design/signature-cache-validation-freshness-report.md` - cache freshness evidence and MISS/HIT validation instructions.
- `docs/v2-design/multilingual-label-chip-identity-regression-report.md` - shop-language label/chip gate evidence.

**Expected output:** `docs/v2-design/v2-final-state-gap-closure-conformance-report.md` with a per-gap and per-gate table, live commands, cache freshness evidence before each live read, raw artifact paths, MISS and HIT path evidence for fresh post-fix payloads, and remaining caveats.

**active_rubrics:** `["generator-preflight"]`

**Design phase:** no with reason `verification-exercise-only`

**UX phase:** yes

**Blocked-on-IA:** first-run

**Prototype:** no with reason B-30

### Subtask 12: Coherence audit against this plan and checklist

**Description:** Audit the completed implementation and verification artifacts against this plan's Implementation Checklist, gap sections, cross-cutting gates, deferred section, and nested-repo topology requirements. This is completeness/coherence only, not code quality review.

**Agent:** coherence-auditor

**Knowledge:**
- `constraints/conversational-proxy-structural.md`
- `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md`

**Dependencies:** Subtask 11

**Context files:**
- `docs/v2-design/v2-final-state-gap-closure-conformance-report.md` - final verification evidence.
- `docs/v2-design/gap-8-question-led-verification.md` - gap-8 branch evidence.
- `docs/v2-design/tier-vocabulary-reconciliation.md` - tier mapping decision.
- `docs/v2-design/signature-cache-validation-freshness-report.md` - cache freshness and post-fix MISS/HIT gate evidence.
- `docs/v2-design/multilingual-label-chip-identity-regression-report.md` - label localization and chip identity gate evidence.

**Expected output:** Coherence audit report with each checklist ID `pass`, `fail`, or `deferred-with-rationale`, plus any cross-repo commit/topology risks and confirmation that Bucket B production rollout safety remains consciously deferred.

**active_rubrics:** `["cross-artifact-coherence"]`

**Design phase:** no with reason `audit-only`

**UX phase:** no

### Subtask 13: /cycling terminal [housekeeping]

**Description**: Invoke `/cycling` in terminal-mode to promote marathon findings to the knowledge store and emit the completion sentinel. Terminal-mode handles findings promotion internally; per S6b, digests are cycle-mode-only and are NOT run in terminal-mode.

**Agent**: orchestrator (direct -- not delegated)

**Dependencies**: Subtask 12 (audit passes)

**active_rubrics:** `[]`

**Design phase:** no with reason `housekeeping-direct`

**UX phase:** no

**Verification (PROMOTION_DONE-OR-HANDOFF-DONE -- disjunctive predicate, MUST satisfy at least ONE branch before HK-2 begins):**
- **Branch A -- terminal-mode completed.** Run `smart_bash: [ -f {session_dir}/promotion-complete ] && echo DONE || echo PENDING`. Branch passes if output is `DONE`.
- **Branch B -- handoff-mode completed.** Run `smart_bash: ls -1t {session_dir}/cycle-checkpoint_*.json 2>/dev/null | head -n1`. If a path is returned, read the file and check the JSON `cycle_reason` field (underscore form, NOT hyphen). Branch passes if `cycle_reason` in {`handoff-post-task`, `handoff-mid-task`}.
- **HALT** if BOTH branches fail. Do NOT proceed to HK-2 (Session Audit). Surface the failure with: "HK-1a verification failed: promotion-complete sentinel absent AND no cycle-checkpoint with handoff cycle_reason. Re-invoke `/cycling terminal` (terminal-mode) or, if handoff was intended, re-run the handoff cycle to produce the checkpoint." Then re-run this Verification step.

**Operator-facing terminal summary (HARD-GATED -- MUST be present before surfacing "done"):**

Compose from the `## Verification` sections of every per-subtask producer impl-report -- do NOT invent. Aggregate flatly across all subtasks: every `Exercised:` bullet from every impl-report lands in one list under `Verified end-to-end:`; every `Not exercised, and why:` bullet lands in one list under `Not verified, and why:`. No per-subtask grouping, no subtask labels preserved.

```text
Verified end-to-end:
- <what was actually exercised, with evidence path / observable output / sentinel write>

Not verified, and why:
- <what was assumed but not exercised, with bounded reason>
```

Rules:
- Empty `Not verified` is legal ONLY when affirmatively stated (`no unverified items`). Silent omission is forbidden.
- Bounded reasons name structural infeasibility, not effort and not "will catch downstream."
- Do NOT surface `done` without this split present.

### Subtask 14: Session Audit [housekeeping]

**Description**: Run `session(action='audit')` to aggregate tool telemetry. Skip if fewer than 3 subagent invocations in this session.

**Agent**: orchestrator (direct -- not delegated)

**Dependencies**: Subtask 13 (/cycling terminal). MUST observe PROMOTION_DONE-OR-HANDOFF-DONE from HK-1a Verification before starting.

**active_rubrics:** `[]`

**Design phase:** no with reason `housekeeping-direct`

**UX phase:** no

### Subtask 15: Commit + Push [housekeeping]

**Description**: Commit all code/docs changes and integrate them upstream. This plan has a required nested-repo adaptation:

- Commit/push agent repo changes from `conversational-search`.
- Commit/push proxy repo changes separately from `conversational-search/conversational-proxy` if, and only if, any proxy files changed.
- Do not stage proxy changes from the outer repo; the outer repo treats `conversational-proxy/` as untracked content.
- Use targeted staging only. Never use `git add -A`, `git add .`, or `git commit -am`.
- Do not stage unrelated untracked files already observed before planning (`agent_diff.txt`, `runs/`, `uv.lock.local-pre-ff-2026-06-01`, proxy `dump.rdb`) unless a producer explicitly created and owns them for this task.
- If both repos changed, produce two witness lines or files: one for agent push and one for proxy push.

**Role detection (run first):** check `[ -n "$CAA_CHILD_SIDECAR_DIR" ]`. If set, use the L2+ merge-to-parent protocol from `housekeeping-subtask-templates`; if absent, use the L1 push-to-origin protocol. Apply the protocol independently to each changed git repo.

**Render-baseline awareness:** Preserve `.agent_context/render-baselines/`; do not stage baseline JSON. If any in-place render target intentionally diverged, run `bin/render_divergence_check.py` before `git add -f`.

**Probe-instrument cleanup:** Delete throwaway `.claude/hooks/<name>.py` probe instruments from both worktree and main copies before commit. Production hook changes intended to ship are exempt.

**Pre-push merge/rebase discipline:** Use the template's fetch/merge protocol for L1 and rebase/merge-to-parent protocol for L2+. Do not rebase L1. Do not force-push. Do not auto-resolve conflicts.

**Pipeline-correctness discipline:** Do not test failing git commands through a pipeline unless `set -o pipefail`, `${PIPESTATUS[0]}`, or capture-then-test is used.

**Post-push/post-merge verification:** Verify the target ref/parent HEAD contains the committed work before writing the witness file. On any failure, do not write a success witness.

**Agent**: orchestrator (direct -- not delegated)

**Dependencies**: Subtask 14 (session audit)

**active_rubrics:** `[]`

**Design phase:** no with reason `housekeeping-direct`

**UX phase:** no

### Subtask 16: /cycling terminal - finalize sentinel [housekeeping]

**Description**: Invoke `/cycling terminal` sentinel-finalization step (HK-1b sub-phase) after Commit + Push succeeds. This step emits the `SESSION-COMPLETION-SENTINEL` with `status='success'` to the change-log, marking the session as successfully completed for the study pipeline.

**Agent**: orchestrator (direct -- not delegated)

**Dependencies**: Subtask 15 (commit + push). GATED on HK-3 success. For L1, check the repo-appropriate push witness contains `pushed=success`; for L2+, check the merge witness contains `merged=success`. Because this plan may touch two git repos, require success witnesses for both changed repos.

**active_rubrics:** `[]`

**Design phase:** no with reason `housekeeping-direct`

**UX phase:** no

**Verification (TERMINAL_FINALIZED):**
- Run `knowledge(action='change-log', file_exact='{session_dir}/SESSION-COMPLETION-SENTINEL', actor='external:cycling-terminal-sentinel', limit=1)`.
- Step passes if returned `entries[]` contains at least one entry with `status === 'success'`.
- Do NOT use `[ -f {session_dir}/SESSION-COMPLETION-SENTINEL ]`; the sentinel is a change-log entry only.

### Subtask 17: Knowledge-Hygiene Pipeline [housekeeping]

**Description**: Invoke the knowledge-hygiene pipeline via CLI Bash, not Agent tool. Launch is fire-and-forget: `Bash(run_in_background=true, command="cd $MAIN_ROOT && bin/claude-study post-completion")`, capture the bash id, yield turn immediately, and do not poll post-launch.

**Skip if:** diff is fewer than 5 lines and no knowledge-relevant files (`.claude/knowledge/`, `docs/`, agent definitions) were touched. This task touches `docs/` at minimum, so the pipeline should run unless later execution somehow produces no docs/code diff.

**Pre-launch mutex probe:**
- **LAUNCH** when `.claude/knowledge/.study-state` has `running: false` or no running state.
- **DEFER** when `running: true` and `now - running_since < 35min`; record `H4 DEFERRED`.
- **HUNG** when `running: true` and `now - running_since >= 35min`; surface to user and ask for manual stale-lock clearing before launch.

**HK-4 DOES commit and push the knowledge edits it makes.** It is separate from HK-3 and scoped to knowledge-store paths.

**Exit-code-2-on-success caveat:** Non-zero exit from the background run can be ambiguous under worktree symlink paths; inspect run artifacts in a future session rather than assuming failure from exit code alone.

**Agent**: orchestrator (direct -- not delegated)

**Dependencies**: Subtask 16 (/cycling terminal - finalize sentinel)

**active_rubrics:** `[]`

**Design phase:** no with reason `housekeeping-direct`

**UX phase:** no

## Completion Criteria

- Validator finds no critical or important issues for code subtasks against `code-vs-spec` and `constraint-compliance`.
- Coherence-auditor marks every checklist item C-01 through C-27 as pass or explicitly deferred with rationale.
- English conformance sweep passes for product_search broad, product_search narrow, gift_advisor, comparison, advice, support, out_of_scope, and unsafe.
- Slovak/Czech safety sweep proves unsafe hard-refuse with 0 LLM calls.
- Multi-language detection report proves at least Slovak support/out-of-scope do not collapse to product_search.
- Multilingual label/chip report proves shop-language default output localization for English, Slovak, and Czech, while `filter_value` and `facet` remain stable and clickable.
- Live-observing reports prove affected signature-cache entries were evicted, namespace-bumped, disabled, or forced to MISS before each live read, and the final conformance report includes fresh MISS and subsequent HIT evidence.
- Gap-8 report exists and either closes the gap as conformant or points to the implementation evidence that fixed it.
- Live stack validation includes raw SSE artifact paths and the exact branch/commit pair tested.
- Agent and proxy repo changes, if any, are committed and pushed separately.

## Deferred (with rationale)

- **Phase 6 nightly pre-computed signatures:** Out of scope because cache/signature HIT is already live and the operator scoped this plan to the eight gaps only.
- **Phase 7 versioned guidebook artifact beyond required gap closures:** Out of scope unless needed to implement language-aware detection, gift anchored chips, or unsafe guardrail text. The plan may add a minimal config/design layer for those gaps, but not a full guidebook migration.
- **Pixel-matched gift/comparison layouts:** Out of scope by operator decision. Functional equivalence is required; exact side-by-side table geometry and chip anchoring positions are not.
- **Full native-speaker localization polish:** Out of scope except detection phrases required to close safety/mode-routing gaps and output labels/prose required by the multilingual label-resolution gate. A native-speaker pass remains advisable before production launch.
- **Production Rollout Safety:** Deferred until the product is confirmed close to complete. This named production-rollout package includes Y_1 cross-repo deployment sequencing and backward-compatibility runbook, Y_3 feature-flag/shadow-mode/fallback infrastructure for the risky gap closures, and the production portion of Y_2 cache rollout strategy (namespace bump or rollout-window disable for live deploy). The design's 7-phase rollout explicitly mandates shadow mode first for Phase 3 tier classifier work and shadow + A/B + fallback for Phase 5 mode dispatcher work; that machinery is substantial architecture work. Deferring it means this plan stops at "branches conformant + dev-stack validated" and does not carry through to a safe production rollout, which is a conscious operator-accepted boundary.
- **Cache HIT as v2 milestone:** Explicitly out of scope; already live on main and verified.

## Post-Completion

The housekeeping subtasks above cover the post-loop steps. After they complete:

Surface any deferred Proposed Criteria Additions (PCAs) and unmet completion criteria in the final response to the user.
