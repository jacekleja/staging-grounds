# v2 Conversational-Search — Detection + Response-Shape System (LangGraph Design)

> **OUTPUT-PATH DEVIATION (read first):** The delegation output-contract path is `.agent_context/sessions/1780902545-2133228-f5b65e67ac75/design-v2-detection-response-shapes.md`. `smart_write` HARD-REJECTS every path under `.agent_context/` ("Path traversal rejected: resolves outside project root") — a documented limitation (discovery-digest § smart_write path-traversal). The documented workaround is a native `Write`/`Bash` heredoc; neither tool is in this architect's loadout. This file is therefore written to the nearest accepted in-worktree path. **Orchestrator: `mv` this file to the contract path.**

> **Interpretation (stated, proceeding):** This worktree's `graph.py` / `state.py` / proxy `conversation_schema_v2.py` are the **v1 shipped baseline** — none of the v2 detection/tier/composition code described in the discovery digest is present here. Verified absences: no `tier_signal_computer.py` anywhere under `conversational-search/`; `state.py` (ConversationState) carries none of the `TURN_STATE_ENVELOPE_FIELDS`; proxy `ConverseRequest` (conversation_schema_v2.py:52-58) has no `is_engagement_of_preview` / `chat_takeover_trigger` / `fork_card_filter_value`. I design the detection + response-shape system **from scratch, layered onto the v1 graph**, treating `canonical_enums.py` as the frozen vocabulary and `docs/_handoff-pack/03 · Handoff brief.md` as the product spec. The discovery digest's references to those modules describe work on a **different branch** (`feat/pr-b2-core-production-features`), not this worktree.

---

## Knowledge Consulted

- `docs/_handoff-pack/03 · Handoff brief.md` §§2,3,4,5,6,8a,9,10,11 — PRIMARY product/UX spec; architecture, hard commitments, tier signals, compositions, phased rollout, open questions. [verified: handoff brief lines 15-413]
- `conversational-search/.../agent/canonical_enums.py` — FROZEN vocabulary (MODE_ENUM, TIER_ENUM, COMPOSITION_ENUM, CLASSIFIER_PATH_ENUM, TURN_STATE_ENVELOPE_FIELDS) + R4 tier-name supersession. [verified: canonical_enums.py lines 24-105]
- `conversational-search/.../agent/graph.py` — current topology + node behavior (`route_entry`, `first_turn_init`, `handle_regular_turn`, `compile_system_prompt`, `verify_search_intent`, `emit_metrics`, `create_graph`). [verified: graph.py lines 154-1015]
- `conversational-search/.../agent/state.py` — current (v1) state schema, no envelope fields. [verified: state.py lines 34-82]
- `conversational-search/.../agent/tools.py` — `search_products` returns `{products, total_hits, guid, facets}`; `_compact_facets` shows facet buckets are on the wire. [verified: tools.py lines 82-92, 211-227]
- `conversational-search/.../agent/prompts.py` §First-Turn Behavior — single-LLM-call turn-1 prompt shape the mode/composition guidance folds into. [verified: prompts.py lines 19-62]
- `decisions/conversational-agent-turn1-preview-redesign.md` (SUPERSEDED) + `...smart-path-direction.md` (SUPERSEDED) — both superseded by the discovery digest's EP5 fix; the 4-message simulated-tool-call shape they describe IS live in `first_turn_init`. [verified: graph.py lines 216-232]
- `decisions/conversational-search-v2-discovery-digest.md` §§ Phase-4 milestones, ep-14 tier-signal cluster, firing-mode toggle — campaign map. [verified: knowledge search, 2 hits]
- `.agent_context/.../research-v2-campaign-state.md` — prior orientation digest (used as map; verified against sources above). [verified: observed behavior]

---

## Problems to Solve

The v1 graph routes every request through a **binary** `is_first_turn` gate (graph.py:935) into one of two monolithic handlers (`first_turn_init`, `handle_regular_turn`). Both treat **every** query the same way: pre-search → one LLM call → emit "opening sentence + suggestions block" (prompts.py:32-34). There is no concept of:

1. **Request mode.** A gift query, a comparison, a support request, and a product search all hit the same handler and get the same chip-style response. The 7 modes in `MODE_ENUM` have no detection and no per-mode response shape.
2. **Result-set tier.** A 12-result query and a 40,000-result query both render the same generic suggestion pills. The handoff brief shows count-only routing misroutes ~4/6 representative queries (§5); the 4 tiers in `TIER_ENUM` are computed nowhere.
3. **Composition.** The frontend wants structured `lbjson` v2 (`chips` | `question` | `carousel` | `hard_fork` | `browse_all_link`, each nullable per §4), driven server-side by `composition`. The current response is free-text prose + a flat suggestions block.
4. **Turn-2+ entry-kind discrimination.** Turn-2+ has three distinct entry kinds (chip click, typed follow-up, chat-affordance open) that the current single `regular_turn` handler cannot tell apart and so cannot inherit composition/mode state from turn-1.

The design must add detection (mode + tier) and response-shaping (composition) **without adding a turn-1 LLM call** (frozen, §3), reusing the existing graph rather than rewriting it.

---

## Proposed Approach

### 0. Shape in one sentence

Add **two hot-path (no-LLM) nodes** — a **mode dispatcher** before `first_turn_init`, and a **tier classifier** folded into `first_turn_init` after the search returns — and turn the single turn-1 LLM call into a **composition-aware emitter** by folding mode + tier + composition guidance into the existing `compile_system_prompt`. Detection is deterministic and free; the LLM's one call shapes the chosen composition's LBJSON. Turn-2+ reuses one handler that branches on a frontend-supplied `entry_kind` signal, not on separate detection logic.

**Positive tradeoff (what this shape gives up):** folding composition into the single prompt means **the model, not the server, is the last line of enforcement on response shape** — the server picks the composition deterministically, but the affordance *content* is model-generated inside the one call. We give up hard server-side shape guarantees (a dedicated render node could validate-and-reject) in exchange for holding the frozen 1-call turn-1 budget. The cost is real: a mis-behaving model can emit a malformed composition, mitigated only by shadow-mode + eval, not by a structural gate.

### 1. Graph topology diff

Reuse the current ASCII style. **New nodes** in **bold**, **new conditional edges** in *italic*.

```
START
  └─ route_entry (conditional, UNCHANGED predicate is_first_turn)
       ├─ "first_turn" → **mode_dispatch**                         ◄── NEW NODE (hot-path, no LLM)
       │     └─ *dispatch_route* (conditional)                     ◄── NEW EDGE
       │           ├─ "product_search" → first_turn_init           (UNCHANGED node, +tier-classify inside)
       │           │     └─ additional_search_needed (conditional, UNCHANGED)
       │           │           ├─ "yes" → additional_search → additional_search_finish → … → emit_metrics
       │           │           └─ "no"  → emit_metrics
       │           ├─ "conversational" → **conversational_turn1**  ◄── NEW NODE (1 LLM call; gift/comparison/advice)
       │           │     └─ emit_metrics
       │           ├─ "deflect_llm"    → **out_of_scope_turn1**    ◄── NEW NODE (1 LLM call; out_of_scope only)
       │           │     └─ emit_metrics
       │           └─ "template"       → **template_deflect**      ◄── NEW NODE (NO LLM; support + unsafe)
       │                 └─ emit_metrics
       └─ "regular_turn" → reset_tool_call_count → regular_turn    (UNCHANGED loop)
             └─ should_continue (conditional, UNCHANGED) … verify_search_intent … emit_metrics
emit_metrics → END   (UNCHANGED single funnel)
```

**What is preserved unchanged:** `route_entry`, `first_turn_init`'s search+gather+single-stream core, the `additional_search` loop, `reset_tool_call_count`, `handle_regular_turn`, `should_continue`, `verify_search_intent`, `emit_metrics` as the single pre-END funnel. The dispatcher is inserted *between* `route_entry`'s `first_turn` branch and `first_turn_init` — it does not touch the regular-turn subgraph.

**Composition is NOT a new node.** It is computed in `first_turn_init` (tier → composition lookup) and rendered by the **single existing LLM call**, guided by composition-specific instructions folded into the system prompt. The renderer is an emit-shape concern, not a graph node — this keeps the turn-1 call budget at 1.

### 2. Mode dispatcher

**Where it runs:** new node `mode_dispatch`, the first node on the `first_turn` branch (before `first_turn_init`). Runs on turn-1 only.

**Two-layer detection (hot-path-first, LLM as last resort):**

- **Layer 1 — keyword/YAML match (NO LLM, ~0ms):** Ordered, deterministic rules from the guidebook (`agent/guidebook/v{N}.yaml § modes`, handoff brief §7) and the per-shop support config (`agent/support/{shop_id}.yaml § patterns.*.detect`, §8a). Order (highest-precedence first, because precedence resolves ambiguous multi-match):
  1. `unsafe` — hard refusal keyword/regex list (prompt-injection, data-extraction, illegal). Highest precedence: a query that is both "gift" and unsafe must refuse.
  2. `support` — per-shop YAML `detect` OR-match (order/return/refund/etc).
  3. `comparison` — `vs / or / compare` tokens (also detectable mid-flow on turn-2+, see §6).
  4. `gift_advisor` — `gift for / present for / something for`.
  5. `advice` — `should I / is X worth`.
  6. `product_search` — **default fall-through** (noun-phrase queries). No keyword needed; it is what you get when nothing else matches.
- **Layer 2 — LLM guard (FOLDED, not a new call):** When Layer-1 is *ambiguous* (no keyword fired OR multiple non-default keywords fired with no priority), the dispatcher does **not** issue its own LLM call on turn-1. Instead it routes to `product_search` (the safe default per §3 hard-commitment that mis-dispatch must be cheaper than today's wrong-axis chip row), and the **single turn-1 LLM call inside the chosen handler** carries a guidebook instruction that lets the model self-correct the framing within `product_search`'s response shape. The `out_of_scope` mode is the one mode whose detection is *intrinsically* LLM-shaped (it is "not a product request and not a named deflection") — it is reached only when Layer-1 fired no product/conversational/deflection keyword AND the model, in its single call, declines to treat the query as a product search (the existing off-topic refusal path, prompts.py:24, already does exactly this). See **Model-call accounting** below for the proof this respects the 1-call budget.

**What it reads:** `state["messages"][-1]["content"]` (the raw user query, same extraction as graph.py:188), the guidebook mode-detect rules, and the per-shop support config (loaded by `tracker_id` / `shop_id`, cached).

**What it writes to state (new fields, see State Envelope):** `mode: str` (a MODE_ENUM value), `dispatch_rationale_token: str`, `confidence_signal: "high"|"medium"|"low"`, `triggering_keyword: str|None`. For `unsafe` it additionally writes the `UNSAFE_ROW_REQUIRED_FIELDS` (canonical_enums.py:100-105) for the A→G turn_events audit row.

**Mis-dispatch fallback:** any low-confidence dispatch → `product_search` (Operator Decision #7 default; the alternative `out_of_scope` is more user-hostile). `support`/`unsafe` are template-only and cannot mis-route into an LLM call. The fallback is **graph-structural**: `dispatch_route` returns `"product_search"` whenever `confidence_signal == "low"`.

### 3. Tier classifier

**Where it runs:** *inside* `first_turn_init`, immediately after `search_products()` returns (graph.py:228, the `search_results` string) and **before** the LLM stream (graph.py:250). It is a pure synchronous function `classify_tier(parsed_search, query) -> TierSignals`. NO LLM call.

**Exact inputs — all from the LBX search result already on the wire** (`search_products` returns `{products, total_hits, guid, facets}`, tools.py:212; `facets` carries per-axis buckets with counts, tools.py:82-92):

| Signal | Source field | Derivation |
|---|---|---|
| `result_count` | `total_hits` | direct |
| `top_share_max` | `facets[axis].buckets` | `max over axes of (top_bucket_count / sum_bucket_counts)` |
| `axis_entropy` | `facets[axis].buckets` | Shannon entropy of bucket-count distribution, max over axes (high = heterogeneous) |
| `filled_axes` | `facets` | count of axes with ≥2 buckets above a floor (floor default = 1% of total_hits) |
| `has_brand_token` | query + shop brand list (guidebook) | regex match |
| `has_model_token` | query + shop model list (guidebook) | regex match |
| `query_token_count` | query string | whitespace token count |
| `price_spread` | `facets[price_*].{min,max}` | top-decile ÷ bottom-decile (already compacted to min/max by `_compact_facets`, tools.py:82) |

**Tier boundaries (handoff brief §5 starting points — calibration is Operator Decision #1):**

```
decisive    (narrow)       : result_count < 80
shapeable   (mid)          : result_count < 2000  AND top_share_max > 0.35
exploratory (broad)        : result_count < 12000 OR  axis_entropy > 0.65
intractable (overwhelming) : result_count >= 12000 AND axis_entropy > 0.70
```

**Vocabulary bridge (load-bearing):** the handoff brief §5 uses **narrow/mid/broad/overwhelming** but `TIER_ENUM` froze the **R4** names **decisive/shapeable/exploratory/intractable** (canonical_enums.py:18-19, 36-37). The classifier emits the R4 names. The mapping is 1:1 positional: narrow→decisive, mid→shapeable, broad→exploratory, overwhelming→intractable. Implementers MUST write R4 names to state.

**Extra states (observability only, NOT in the 4-state machine):** `zero_results` (total_hits==0 → also fires the existing `lbx.no_preview` event path, §3) and `no_facet_config` (the tracker had no facet probe configured, `first_turn_facets is None`, graph.py:196). These map to `TIER_EXTRA_STATES` (canonical_enums.py:38).

**What it writes to state:** `tier: str` (TIER_ENUM or TIER_EXTRA_STATES), `composition: str` (COMPOSITION_ENUM, derived next), `classifier_path: "hot-path"` (CLASSIFIER_PATH_ENUM:53), `tier_signals: dict` (the raw signals, for turn_events logging). The `precomputed` and `llm-second-opinion` classifier paths (canonical_enums.py:52,54) are **out of scope** for this design (Phase 6) — hot-path only.

### 4. Composition renderer

**(mode, tier) → COMPOSITION_ENUM → payload.** For `product_search`, the (tier → composition) map is fixed:

| tier (R4) | composition (COMPOSITION_ENUM) | LBJSON payload populated | nulled affordances |
|---|---|---|---|
| `decisive` | `refinement_chips` | `preview`, `chips[]` (2-4, axis-prefixed) | `question`, `carousel`, `browse_all_link` (no hatch, no chat affordance — §6) |
| `shapeable` | `refinement_chips_with_hatch` | `preview`, `chips[]` (4 from best axis), `browse_all_link` (the "Just browsing" hatch), chat-affordance pill | `question`, `carousel` |
| `exploratory` | `question_led` | `preview`, `question{prompt, answers[]}` (2 answers), `carousel[]` (demoted), `browse_all_link` ("Show all N"), chat-affordance pill | `chips` |
| `intractable` | `hard_fork` | `preview` ("N too many"), `question{prompt, answers[]}` as 2 fork cards, `browse_all_link` ("sorted by popularity") | `chips`, `carousel` (no carousel at this scale, §6) |

**For non-product modes** the composition field is the mode's own handler shape (not a COMPOSITION_ENUM value — COMPOSITION_ENUM is product_search-only per the §4 schema comment): `gift_advisor`/`comparison`/`advice` render a **chat-takeover** payload (`must_ask_before_recommending[]`, anchored chips for gift, side-by-side for comparison, 3-route for advice); `support`/`unsafe` render template text + back-to-shop chips; `out_of_scope` renders LLM-with-guidebook prose. (These are treated at "interface + open-questions" depth per the delegation's depth priority.)

**How the renderer emits LBJSON:** the renderer is **not** a separate node. The composition decision (a COMPOSITION_ENUM string) is written to state by the tier classifier, then folded into the **single turn-1 LLM call's system prompt** via `compile_system_prompt` (graph.py:590) — a new `composition` template var selects a composition-specific instruction block (analogous to the existing `COMPACT_PRODUCTS_INSTRUCTION` append, graph.py:624-627). The LLM emits the LBJSON block; the proxy parses it (existing `lbjson` contract, §3) and the frontend renders. **The `null`-means-do-not-render constraint (§4) is enforced server-side**: the composition instruction tells the model exactly which affordance keys to populate and which to omit; the proxy treats absent keys as null. This preserves "one LLM call on turn-1" because composition selection is deterministic (free) and only the *content* of the affordances is model-generated, inside the one call already budgeted.

### 5. State envelope

New fields added to `ConversationState` (state.py:34) and wired to the three channels in `TURN_STATE_ENVELOPE_FIELDS` (canonical_enums.py:63-91). Types and writer/reader:

| Field | Type | Channel | Writer | Reader |
|---|---|---|---|---|
| `mode` | `str` (MODE_ENUM) | derived/per-turn | `mode_dispatch` | `dispatch_route`, handlers, turn_events |
| `tier` | `str` (TIER_ENUM/EXTRA) | derived/per-turn | `first_turn_init` (classifier) | composition fold, turn_events |
| `composition` | `str` (COMPOSITION_ENUM) | derived/per-turn | `first_turn_init` (classifier) | `compile_system_prompt`, proxy |
| `classifier_path` | `str` (CLASSIFIER_PATH_ENUM) | derived/per-turn | `first_turn_init` | turn_events |
| `tier_signals` | `dict` | per-turn (observability) | `first_turn_init` | turn_events |
| `mode_stack` | `list[str]` (LIFO, max 3) | thread_metadata | comparison handler push/pop | next-turn dispatcher |
| `browse_intent` | `bool` | thread_metadata | handler on "Just browsing" hatch click consume | turn-2 dispatcher |
| `chat_takeover_trigger` | `bool` | per_turn_sse (FE→agent) | proxy (from FE click) | dispatcher |
| `fork_card_filter_value` | `str` | per_turn_sse (FE→agent) | proxy (from FE fork-card click) | turn-2 handler |
| `prior_search_context` | `str` (one-shot) | thread_metadata | proxy on chat-takeover click | agent, cleared on consume |
| `dispatch_rationale_token` | `str` | turn_events | `mode_dispatch` | turn_events writer |
| `confidence_signal` | `str` | turn_events | `mode_dispatch` | `dispatch_route`, turn_events |
| `triggering_keyword` | `str\|None` | turn_events | `mode_dispatch` | turn_events writer |

**`mode_stack` push/pop (LIFO depth 3 for comparison):** when the dispatcher detects `comparison` *mid-flow* (turn-2+ token match in an existing conversation, §6 / handoff brief §213), it **pushes** the current mode onto `mode_stack` (capped at len 3 — push beyond 3 drops the oldest, i.e. a bounded stack), swaps composition to side-by-side for that single turn, then on the next turn **pops** back to the restored mode. The stack lives in thread_metadata (survives the 64K message trim, canonical_enums.py:64-69). State writes use `Command(update)` per the canonical_enums.py:69 note. Depth-3 is Operator Decision #6 (handoff brief §11.8 — "confirm").

### 6. Turn-1 vs turn-2+ detection — NAMED DELIVERABLE

**Stance: turn-2+ does NOT need a structurally different *detection engine* than turn-1, but it DOES need a different *entry signal* and it MUST NOT re-run the tier classifier.** Concretely:

- **Detection logic is shared.** The same mode-keyword layer (gift/comparison/advice/support/unsafe/product_search) applies on every turn — `comparison` in particular is explicitly mid-flow-invocable (§6/§213), so the dispatcher's keyword layer must run on turn-2+ too. Re-implementing a second detector would duplicate the guidebook rules.
- **The tier classifier is turn-1-only.** Tier is a property of the *initial* result set; turn-2+ is refinement *within* an established tier/composition. Turn-2+ inherits `tier`/`composition` from thread state and does not recompute (no new search-result-shape analysis needed unless the user pivots axes — the "← Change the question" affordance, §6/§192).
- **Three turn-2+ entry kinds, distinguished by a frontend-supplied signal (lean on the wire contract):**
  1. **Chip click** — FE sends the chip's `filter_value` as the next prompt + `is_engagement_of_preview=true`. The agent treats this as a *refinement within the inherited composition*; it does NOT re-fire the preview path (§3 engagement-of-preview inheritance hard-commitment). For `hard_fork`, the click arrives as `fork_card_filter_value`.
  2. **Typed follow-up** — FE sends free text + `is_engagement_of_preview=false`. The dispatcher's mode layer runs (catches a mid-flow `comparison`); otherwise it is a normal `regular_turn` refinement.
  3. **Chat-affordance open** ("Chat with me instead" / "Just browsing") — FE sends `chat_takeover_trigger=true` (+ `prior_search_context` injected by the proxy). The agent swaps to chat-takeover composition, inheriting search context (Operator Decision #8: extend the existing turn sequence rather than fork a new conversation — default recommended).

  The distinguishing signal is **frontend-owned** (the FE owns the eager/deferred firing toggle, per the 2026-05-21 management directive; it sends `is_engagement_of_preview`, `chat_takeover_trigger`, `fork_card_filter_value`). The agent reads these from the per_turn_sse channel; it does **not** infer entry-kind from message content. This is the cheapest correct design: the FE already knows which UI element the user touched.

- **Dependence on the `is_first_turn` multi-turn bug:** the known bug is that turns 3+ report `is_first_turn=True` when `is_engagement_of_preview=false` (research digest §6 / preview-redesign § Scope NOT resolved). **This design's turn-2+ path depends on a correct turn boundary** — the dispatcher's "mid-flow comparison" and "inherit tier/composition" behaviors require knowing it is NOT turn-1. **Recommendation: fix the bug as a Phase-5 prerequisite** by deriving turn number from `conversation_turn` (canonical_enums.py:71, "derived per-turn idempotent from len(human messages)") rather than the FE boolean. The product_search turn-1 spine (the first implementation slice) does NOT depend on the fix; only the dispatcher's turn-2+ mid-flow behaviors do. This is Operator Decision #5.

### 7. Model-call accounting (per-path)

| Path | Turn | LLM calls | Where | Budget proof |
|---|---|---|---|---|
| `product_search` | turn-1 | **1** | `first_turn_init` single `astream` (graph.py:250) | dispatcher = 0 (keyword), tier classifier = 0 (hot-path), composition = 0 (deterministic); only the existing call fires. **Respects §3.** |
| `product_search` + additional_search | turn-1 | **1** | same call re-issues a tool call, loops once (additional_search_count cap 1, state.py:62), then the same node's stream finalizes | the additional-search loop is the *existing* behavior; it is a re-stream, accounted as turn-1's one call (unchanged from v1). |
| `gift_advisor`/`comparison`/`advice` | turn-1 | **1** | `conversational_turn1` single call | dispatcher = 0 (keyword); one chat-takeover call. **Respects §3.** |
| `out_of_scope` | turn-1 | **1** | `out_of_scope_turn1` (LLM-with-guidebook) | dispatcher = 0; one call. |
| `support`/`unsafe` | turn-1 | **0** | `template_deflect` (no LLM) | template-only; cheaper than budget. |
| chip click (turn-2+) | turn-2 | **1** | `regular_turn` single call (verifier short-circuits, see below) | inherits composition; one refinement call. |
| typed follow-up (turn-2+) | turn-2 | **1, or 2 if verifier fires** | `regular_turn` + optional `verify_search_intent` | the verifier gate (graph.py:792) is the *existing* second-call risk on the regular path, capped at 1 fire (verify_count, state.py:67). Not a new call. |
| chat-affordance (turn-2+) | turn-2 | **1** | chat-takeover handler | one call. |

**Net new LLM calls introduced by this design: 0 on turn-1, 0 on turn-2+.** All detection is hot-path. The only multi-call paths are the *pre-existing* additional-search loop and the *pre-existing* verifier gate, both already capped.

---

## Assumptions

1. **The LBX `facets` response carries per-axis bucket counts in `first_turn_init`'s `search_results`.** Verified that `search_products` returns `{products, total_hits, guid, facets}` (tools.py:212) and `_compact_facets` iterates `facet["buckets"]`-shaped data (tools.py:82-92). **If wrong** (facets absent or shape differs), the tier classifier degrades to `result_count`-only (the count-only thresholds in §5 still work) and emits `classifier_path` reflecting the degradation — the spine still ships, just at lower accuracy.
2. **The single turn-1 LLM call can be steered to emit composition-specific LBJSON via a system-prompt instruction block** (the same mechanism as `COMPACT_PRODUCTS_INSTRUCTION`, graph.py:624). **If wrong** (model ignores composition guidance), the response shape is wrong but no extra call fires; mitigated by shadow-mode + eval per §9 Phase-4.
3. **The frontend will send `is_engagement_of_preview`, `chat_takeover_trigger`, `fork_card_filter_value` on the v2 `/converse` request.** These are NOT on the current `ConverseRequest` (conversation_schema_v2.py:52-58) — they must be added to the proxy schema AND the FE wire. **If wrong** (FE does not send them), turn-2+ entry-kind discrimination collapses to message-content heuristics (worse, but degradable). This is the design's single largest external dependency.
4. **The guidebook (`agent/guidebook/v{N}.yaml`) and per-shop support config (`agent/support/{shop_id}.yaml`) will exist as loadable artifacts.** Neither exists yet (handoff brief §7/§8a describe them as to-build). The dispatcher's keyword layer reads them. **If wrong** (not built), the dispatcher falls back to inline keyword constants (a worse maintenance story but functionally equivalent for the spine).
5. **`mode_stack` in thread_metadata survives the 64K message trim.** Asserted by canonical_enums.py:64 ("survives 64K trim; LangGraph config['metadata']"). **If wrong**, comparison mid-flow restore loses the prior mode and falls back to `product_search` on pop.

---

## Unknowns

1. **Exact facet bucket JSON shape inside `search_results`.** I verified the keys (`facets`) and that buckets exist, but not the precise nesting (`facets[i].buckets[j].count` vs a dict). *Resolves by:* reading `search_products` return-construction (tools.py:228-237, beyond the range I read) or one live response capture. The tier classifier's signal-extraction code depends on this; the boundaries do not.
2. **Whether `compile_system_prompt` template vars can carry a per-composition block cleanly via Langfuse-hosted prompts** (graph.py:618-628 loads client-specific prompts from Langfuse). *Resolves by:* checking whether the Langfuse prompt templates accept a `{composition}` var or whether the append-instruction pattern (graph.py:624-627) is the only safe injection point. The append pattern is the safe default.
3. **Whether `out_of_scope` detection genuinely needs zero dedicated turn-1 logic, or whether folding it into product_search's single call produces enough refusals.** *Resolves by:* the existing off-topic refusal eval (prompts.py:24 already does this) — measure miss rate. Tied to Operator Decision #7.
4. **The turn-2 contract for `browse_intent`** (handoff brief §10.3, open question §11.2): clarifying-template vs free-form chat vs filtered SERP. *Resolves by:* operator product decision (Operator Decision #2).

---

## Integration Points

**First-order (files the implementer modifies), in dependency order:**

1. `conversational-search/.../agent/state.py` (ConversationState) — add the 13 new fields (§5). FIRST: everything reads/writes state. No other file changes until this lands.
2. `conversational-search/.../agent/graph.py`:
   - New function `classify_tier(parsed_search, query) -> TierSignals` + a `(tier → composition)` map.
   - Modify `first_turn_init` (graph.py:154-270): after the `json.loads(search_results)` parse that already exists (graph.py:238), call `classify_tier`, write `tier`/`composition`/`classifier_path`/`tier_signals` into the return dict (graph.py:259-269).
   - Modify `compile_system_prompt` (graph.py:590-652): accept `composition` (and `mode`) template vars; append a composition-specific instruction block (mirror graph.py:624-627).
   - New nodes `mode_dispatch`, `conversational_turn1`, `out_of_scope_turn1`, `template_deflect`; new conditional-edge function `dispatch_route`.
   - Modify `create_graph` (graph.py:952-1015): register the 4 new nodes; replace the `START → first_turn_init` edge (graph.py:973) with `START → mode_dispatch` and add the `dispatch_route` conditional edges to the 4 turn-1 handlers.
3. `conversational-search/.../agent/prompts.py` — add composition-specific instruction blocks (4 product compositions + chat-takeover + deflection templates), analogous to `COMPACT_PRODUCTS_INSTRUCTION` (prompts.py:151).
4. `conversational-search/conversational-proxy/app/schemas/conversation_schema_v2.py` (ConverseRequest, line 52) — add `is_engagement_of_preview: bool`, `chat_takeover_trigger: bool`, `fork_card_filter_value: str | None`.
5. NEW artifacts: `agent/guidebook/v{N}.yaml`, `agent/support/{shop_id}.yaml` (handoff brief §7/§8a).

**Second-order chains (per the architect's required walk):**

- **Interface change → callers → failure isolation:** `first_turn_init`'s return dict gains keys; `create_graph`'s edge from `START` changes target. `route_entry` is unchanged (still gates on `is_first_turn`), but its `first_turn` branch now lands on `mode_dispatch` not `first_turn_init` — any test asserting `START → first_turn_init` (search the test suite) breaks. `emit_metrics` remains the single funnel; the 4 new turn-1 handlers MUST all edge to `emit_metrics` or the `llm_call_count` metric (graph.py:948) under-counts.
- **New sentinel/protocol → who-recognizes-it:** the 3 new `ConverseRequest` fields are a new FE↔proxy↔agent wire contract. The proxy `conversation_service.converse` (the converse entry, conversation_router_v2.py:71) must thread them into the LangGraph run config so the agent state sees them. This is a **cross-repo coupling** (proxy ← agent enum replication already exists per the digest's ep-14 cluster — TIER/COMPOSITION/CLASSIFIER_PATH enums are replicated proxy-side with parity tests). Adding `mode` to the wire extends that replication surface.
- **New module → connection-graph edges:** the new guidebook/support YAML loaders create `shared-file-read` edges (graph node `mode_dispatch` reads `agent/guidebook/*.yaml` + `agent/support/*.yaml`). The composition instruction blocks create a `compile-time-import` edge `graph.py → prompts.py` (already exists, graph.py:35-42 — extended, not new). No genuinely-new cross-subsystem connection-graph node is introduced within `conversational-search`; the cross-repo proxy↔agent enum coupling is pre-existing.

---

## Rejected Alternatives

1. **Composition as a dedicated graph node (after `first_turn_init`).** *Axis: boundary placement.* Rejected because a separate composition node that re-invokes the LLM to "shape" the response would be a **second turn-1 LLM call** — a direct §3 violation. Even a no-LLM composition node is rejected: it would have to re-serialize the LLM's already-streamed output, fighting the existing single-stream emit (graph.py:250-251). Folding composition into the system prompt keeps the call count at 1. *Specific failure mode: 2 calls on turn-1.*

2. **A dedicated turn-1 intent-classifier LLM call (third `asyncio.gather` coroutine in `first_turn_init`).** *Axis: sync-vs-async / call budget.* This was **already evaluated and rejected in EP5** (research digest §5) — message-shape restructure was chosen instead. Re-proposing it would violate §3 and re-litigate a settled decision. *Specific failure mode: 2 calls on turn-1; contradicts a recorded decision.*

3. **Separate turn-2+ detection engine (a parallel dispatcher for refinement turns).** *Axis: ownership / coupling.* Rejected because mode detection (esp. mid-flow `comparison`) uses the *same* guidebook keyword rules as turn-1; a second engine duplicates the rule set and guarantees drift between the two. The shared-engine-with-entry-signal design (§6) gives turn-2+ what it actually needs (entry-kind + inherited tier) without duplicating detection. *Specific failure mode: rule drift between two detectors; double-maintenance.*

4. **Infer turn-2+ entry-kind from message content (NLP heuristic) instead of a frontend signal.** *Axis: in-band vs side-channel.* Rejected because the FE already knows which UI element the user touched (chip vs text box vs chat link) — re-deriving it from text is strictly lossier (a typed query identical to a chip label is indistinguishable in-band). The FE-owned side-channel (`is_engagement_of_preview` etc.) is authoritative and free. *Specific failure mode: chip-click vs typed-identical-text ambiguity; re-fires preview path, violating §3 engagement-of-preview inheritance.*

5. **Tier classifier as an LLM call (or LLM second-opinion on every query).** *Axis: coupling locus / call budget.* Rejected for turn-1: the signals (`top_share_max`, `axis_entropy`, etc.) are all arithmetic over data already on the wire — an LLM adds cost and latency for a computation that is deterministic. The `llm-second-opinion` path (canonical_enums.py:54) is a documented *future* Phase-6 lever for ambiguous cases written back to a cache, not a turn-1 default. *Specific failure mode: needless turn-1 LLM call for a pure arithmetic decision.*

6. **Do-nothing baseline (keep the binary `is_first_turn` gate, push all mode/tier logic into the prompt).** *Axis: boundary placement.* Rejected because the proxy needs the structured `mode`/`tier`/`composition` fields to drive the FE renderer (§4 — "composition drives which affordances are populated server-side"); a prose-only response gives the FE nothing to switch on, and the `null`-means-do-not-render contract cannot be enforced. *Specific failure mode: FE cannot render structured compositions; the entire §6 composition system is unreachable.*

---

## Operator Decisions Needed

> Each lists the **recommended default** so implementation can proceed on the default while the operator confirms asynchronously. **Deep** on the product_search spine (1, 7); the rest are lighter "confirm the default" calls.

1. **Tier boundary calibration** (handoff brief §11.1). The `80 / 2000 / 12000` + entropy thresholds are intuition-derived. *Recommended default:* ship §5's numbers in **shadow mode** (log tier + signals to turn_events, don't switch composition) for the spine's first deploy; calibrate against logged traffic before going live. *Rationale: the classifier is observable before it is load-bearing; calibration needs real data the shadow run produces.*
2. **Turn-2 hatch contract for `browse_intent`** (§11.2 / §10.3). *Recommended default:* chat-takeover with vibe-anchored quick replies (the prototype's current behavior). *Rationale: matches the shipped prototype; lowest surprise.*
3. **Conversation persistence surface** (§11.3). *Recommended default:* server-side `conversation_id` + thread_metadata (already how `mode_stack` etc. persist). *Rationale: the envelope already lives server-side; no new surface needed.*
4. **Brand-safety for `advice` mode — may the agent discourage a specific SKU?** (§11.4). *Recommended default:* NO (advisory framing only, never "don't buy X"); merchandising decision deferred. *Rationale: safest default; reversible upward.*
5. **`is_first_turn` multi-turn bug fix as a Phase-5 prerequisite** (§6 / digest open-gap). *Recommended default:* fix by deriving turn number from `conversation_turn` (len human messages) before the dispatcher's turn-2+ behaviors ship; the product_search turn-1 spine does NOT need it first. *Rationale: dispatcher mid-flow + tier-inheritance require a correct turn boundary; the spine does not.*
6. **Comparison `mode_stack` depth** (§11.8). *Recommended default:* LIFO depth 3 (the prototype assumption, canonical_enums.py mode_stack note). *Rationale: matches frozen-enum design + prototype; "confirm" was the ask.*
7. **Mis-dispatch fallback target — `product_search` or `out_of_scope`?** (§11.7). *Recommended default:* `product_search`. *Rationale: §3/§9-Phase-5 states mis-dispatch must be cheaper than today's wrong-axis chip row; routing a borderline query to product search degrades gracefully, routing it to out_of_scope refuses a possibly-valid shopper.*
8. **Chat-affordance routing schema — fresh conversation vs extend existing** (§11.9). *Recommended default:* extend the existing turn sequence with `prior_search_context` injected (the one-shot thread_metadata field, canonical_enums.py:72). *Rationale: inherits context without a thread-create round-trip.*
9. **Gift-mode anchored chips per-shop configurability** (§11.10). *Recommended default:* shop-configurable like support config (`agent/guidebook/{shop_id}` override of the 4 default anchors). *Rationale: the brief explicitly flags a bookstore wants different anchors; mirror the support-config pattern.*
10. **`facets_csv_capped` interaction with the adaptive picker** (§11.5). *Recommended default:* if the CSV cap fires and axes are lost, fall back to single-axis chips (do not refuse to emit). *Rationale: degraded chips beat no chips; consistent with the count-only classifier fallback.*
11. **Multi-language tone anchors** (§11.6). *Recommended default:* ship English guidebook anchors; gate non-EN compositions behind a native-speaker pass. *Rationale: out of this design's scope (content, not structure); flag so it isn't forgotten.*
12. **Second-LLM-call tension — confirmed NONE on turn-1.** This design introduces zero new turn-1 LLM calls (Model-call accounting). *No decision needed; recorded so the operator can confirm the §3 commitment is held.* The only residual tension is the **future** Phase-6 `llm-second-opinion` tier path, which is explicitly out of scope here.

---

## Implementation Sequencing

Mapped to the handoff brief's phases (§9), as ordered, independently-shippable slices. **Highest-value first slice is the product_search spine.**

**SLICE 1 — Product_search spine (handoff brief Phase 3 + the 4 product compositions). HIGHEST VALUE; start here.**
- State fields: `tier`, `composition`, `classifier_path`, `tier_signals` (state.py).
- `classify_tier` + (tier→composition) map; wire into `first_turn_init` after the existing search parse (graph.py:238).
- 4 composition instruction blocks in prompts.py; `composition` template var in `compile_system_prompt`.
- Proxy: emit `tier`/`composition` in the LBJSON v2 (enum replication already exists proxy-side).
- Ships **without** the dispatcher — every turn-1 is `product_search` (the current default), now tier-aware. Independently shippable, shadow-mode first (Operator Decision #1).

**SLICE 2 — Mode dispatcher (Phase 5).**
- `mode_dispatch` node + `dispatch_route` edge; `mode`/`dispatch_rationale_token`/`confidence_signal`/`triggering_keyword` state fields.
- `template_deflect` (support/unsafe — no LLM) + guidebook/support YAML loaders.
- Re-point `START → mode_dispatch` (graph.py:973).
- Depends on Slice 1's state plumbing. Ships keyword-layer-only; LLM-guard is the existing single call.

**SLICE 3 — Conversational modes (gift/comparison/advice — Phase 5 cont.).**
- `conversational_turn1` + `out_of_scope_turn1` nodes; chat-takeover composition; `must_ask_before_recommending`.
- `mode_stack` push/pop; `browse_intent`; chat-takeover state fields.
- **Requires** the `is_first_turn` bug fix (Operator Decision #5) for mid-flow comparison.
- Carries the most unresolved product decisions (treated at interface level here).

**SLICE 4 — Turn-2+ entry-kind wiring (Phase 2 + 5).**
- Proxy `ConverseRequest` fields (`is_engagement_of_preview`, `chat_takeover_trigger`, `fork_card_filter_value`); FE wire.
- Turn-2+ inheritance of tier/composition; "← Change the question" pivot.

**SLICE 5 — Pre-computed signatures (Phase 6). OUT OF SCOPE for this design; named for completeness.** The `precomputed` + `llm-second-opinion` classifier paths.

---

Pre-emission self-audit: 9 citations verified, 9 sections present, 3 contradictions checked (R2-vs-R4 tier vocabulary, superseded turn-1 decision records vs live code, tier_signal_computer.py presence claim in digest vs this worktree).

Findings emission self-check: 5 discoveries, 5 emissions.
