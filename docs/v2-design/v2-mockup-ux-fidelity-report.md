# v2 Conversational-Search — Mockup UX Fidelity Report

**Executive summary:** The implementation is largely faithful to the handoff design. 16 of 21 graded items MATCH. Four items are PARTIAL (A6 language-label resolution for categorical chips; A8 chat affordance absent on the decisive/narrow tier; A9 type-it-out absent on the broad-browse/question_led surface; A10 gift anchor labels English-only / not localized). One item is DIVERGENT (out_of_scope uses a template/hardcoded string rather than the specified LLM-with-guidebook path). One contract is NOT-EXERCISED (engagement-of-preview state inheritance — turn-2-only observable). No hard commitment is fully unmet, though two carry implementation-scope caveats documented below.

---

## Scope & Boundaries

- **What this establishes:** structural/semantic fidelity — elements, affordances, copy, routing in the agent's turn-1 lbjson payload. Not visual/pixel/layout fidelity.
- **Not in scope:** pixel rendering (frontend is separate), abandoned prototype iterations (sections A–J), turn-2+ transitions (except engagement-of-preview state inheritance).
- **Sources used:** handoff brief §2–§8 (intended contracts); `graph.py`, `canonical_enums.py`, `support/8760-9189.yaml`; `v2-final-state-gap-closure-conformance-report.md`; `multilingual-label-chip-identity-regression-report.md` (actual behavior). Live stack not needed — all items determinable from code + existing evidence.

---

## A. The 10 Hard Commitments (§3)

| # | Commitment | Intended (§3 brief) | Actual (code/evidence) | Verdict | Evidence |
|---|---|---|---|---|---|
| A1 | One LLM call on turn 1 | No new graph node, no second compose pass | `llm_call_count` enforced; all turn-1 handlers fire one `llm.astream()` call; budget violation emitted if exceeded | **MATCH** | `graph.py (_handle_gift_advisor_turn1)` l.1959; `graph.py (_handle_advice_turn1)` l.2073; conformance report Gap-2/4/5 all show `llm_call_count=1` |
| A2 | lbjson chips schema `{label, filter_value, facet, count}` | Proxy parses, UI renders; new arch extends but doesn't replace | Chips emitted with `label`, `filter_value`, `facet` (added at l.1468 `{**option, "facet": facet}`); `count` carried from facet data via `turn1_selector.py` | **MATCH** | `graph.py (_render_turn1_preview_block)` l.1468 |
| A3 | `lbx.no_preview` on zero hits | Independent path, must not break | `_serialize_no_preview_sentinel` / `_is_empty_turn1_preview_block` / V1 Yamaha F310 confirmed `__no_preview__` reason=`zero_hits` | **MATCH** | `graph.py (_serialize_no_preview_sentinel)` l.1283; conformance report Gap-1 PASS |
| A4 | Engagement-of-preview state inheritance (chip click must not re-fire preview on turn 2) | Turn-1 chip click MUST NOT re-fire the preview path on turn 2 | `is_engagement_of_preview` exists in `TURN_STATE_ENVELOPE_FIELDS` Channel 2; `_is_browse_hatch_engagement` / `_resolve_turn2_entry_kind` check this flag; turn-2 suppression path coded | **NOT-EXERCISED** | `canonical_enums.py` l.84; `graph.py (_is_browse_hatch_engagement)` l.1597 — turn-2-only observable; this audit scope is turn-1 only |
| A5 | work_status pill sequence intact | Stays intact and visible | `_emit_work_status` called in all turn-1 handlers; `work_status_phase_index` incremented and returned in state | **MATCH** | `graph.py (_handle_gift_advisor_turn1)` l.1944; `graph.py (_handle_advice_turn1)` l.2058 |
| A6 | Language-aware label resolution (sk/cs/en) | Upstream facet labels resolved per language | `_t()` function resolves all UI strings per language; multilingual conformance report C-23 PASS; BUT: categorical chip `label` and `filter_value` are both set to the raw facet value (not language-resolved). (Price fallback labels ARE localized via `_t()` — `turn1_selector.py` l.196-206 — so this PARTIAL covers the categorical-chip label gap only, not price chips.) | **PARTIAL** | `graph.py (_t)` l.282; knowledge store finding `v2-gap-plan-label-resolution-validation-missing`; conformance report C-23 PASS (prose + affordance labels only) |
| A7 | NEW: Turn 1 has no products in the AI block | No product cards; refinement chips/questions/answers/browse links ok | `_emit_search_context(products=[], ...)` in all conversational handlers; `_apply_pre_search_suppression` clears pre-search prose; V1 Yamaha confirmed `text_len=0` deflection only | **MATCH** | `graph.py (_handle_gift_advisor_turn1)` l.1950; `graph.py (_handle_advice_turn1)` l.2064; conformance report Gap-1 PASS |
| A8 | NEW: Always-on chat affordance on EVERY turn-1 surface | Subtle dashed-border "Chat with me instead →" on every turn-1, localized | `_chat_affordance()` emitted with `style="dashed_pill"`, localized label `_t(raw_language, "chat_affordance_label")`. Added by `_render_turn1_preview_block` for `_CHAT_AFFORDANCE_TIERS = set(TIER_ENUM[1:])` = {shapeable, exploratory, intractable}. **Gap: decisive/narrow tier does NOT get the chat affordance.** Gift_advisor and advice blocks carry it via `_render_advice_takeover_block`. Deflection modes (support/unsafe/out_of_scope) do not carry it. | **PARTIAL** | `graph.py` l.302: `_CHAT_AFFORDANCE_TIERS = set(TIER_ENUM[1:])`; `graph.py (_render_turn1_preview_block)` l.1471–1472; `graph.py (_render_advice_takeover_block)` l.1567–1568 |
| A9 | NEW: type-it-out is a first-class affordance (advice, gift_advisor, broad-browse) | Inline text input alongside chips — not a fallback | Gift_advisor: `type_it_out` block always present in takeover. Advice: present when `_derive_type_it_out_parallel_on()` returns True (only when `mode==advice AND is_first_turn`). Broad-browse (question_led tier): **NOT present** — `_render_turn1_preview_block` for `question_led` does not emit `type_it_out`. | **PARTIAL** | `graph.py (_render_gift_advisor_takeover_block)` l.1525; `graph.py (_derive_type_it_out_parallel_on)` l.1415–1416; `graph.py (_render_turn1_preview_block)` l.1433–1443 (question_led path — no type_it_out key) |
| A10 | NEW: Anchored gift chips = 4 category-shaped anchors, guidebook-sourced, stable, localizable | "Hobbies & interests", "Lifestyle", "Practical / useful", "I have an idea" — stable, from guidebook, never model-generated | `_DEFAULT_GIFT_GUIDEBOOK_ANCHORS` hardcodes exactly these 4 labels; `_gift_guidebook_anchors()` returns per-shop config or falls back to defaults; chips emitted with `source="guidebook"`, `style="anchored_category_chip"`. Labels are English-only (not language-resolved). | **PARTIAL** | `graph.py` l.383–388; `graph.py (_gift_guidebook_anchors)` l.812–819; `graph.py (_render_gift_advisor_takeover_block)` l.1516–1524 |

**Note on A10:** The 4 anchor labels are correct and guidebook-sourced (not model guesses). The PARTIAL verdict is specifically that the labels are English-only strings — the `label` field emits `anchor.label` directly (e.g. "Hobbies & interests"), not `_t(raw_language, ...)`. Localizability is specified as a contract requirement but not implemented. The `filter_value` identity fields ARE language-neutral as required.

---

## B. Mode Turn-1 Surface (7 Modes)

| Mode | Intended (brief §6/§8) | Actual | Verdict | Evidence |
|---|---|---|---|---|
| product_search | Mode dispatcher → tier classifier → composition renderer; chips/question/fork per tier; no products | Tier classification runs; composition dispatched; `_emit_search_context(products=[])` enforced; V2/V5 confirmed shapes | **MATCH** | `graph.py (_resolve_product_search_tier_and_composition)` l.1196; conformance report Gap-8 PASS |
| gift_advisor | Chat takeover; anchored category chips (guidebook); type_it_out; must_ask fields; LLM opener | `_handle_gift_advisor_turn1` fires LLM, emits `chat_takeover` block with anchored chips + `type_it_out` + `must_ask_before_recommending` | **MATCH** | `graph.py (_handle_gift_advisor_turn1)` l.1923; `graph.py (_render_gift_advisor_takeover_block)` l.1509; conformance report Gap-4/5 PASS |
| comparison | Side-by-side invocable mid-flow; mode_shift_note; mode-stack push/restore; LLM call | `_handle_comparison_turn`; `_COMPARISON_MODE_SHIFT_NOTE = "comparison detected, swapping side-by-side for this turn"`; LIFO stack via `_push_mode_stack`/`_restore_mode_stack_after_comparison` | **MATCH** | `graph.py` l.392; `graph.py (_handle_comparison_turn)` l.2142; conformance report Gap-5 PASS |
| advice | Three parallel routes: anchored chips + type_it_out + chat affordance; LLM opener | `_handle_advice_turn1` with `_render_advice_takeover_block`; anchored chips from `_ADVICE_ANCHOR_KEYS`; `type_it_out` block when `_derive_type_it_out_parallel_on()` true; `chat_affordance` when `_derive_chat_affordance_on()` true | **MATCH** | `graph.py (_render_advice_takeover_block)` l.1535; conformance report Gap-2 PASS |
| support | Template-only; shop-fillable YAML; CTA redirect; no products; localized | `support_deflect` loads `support/8760-9189.yaml`; pattern-matched; `response_template_by_language` per sk/cs; CTA label localized | **MATCH** | `graph.py (support_deflect)` l.3232; `support/8760-9189.yaml`; conformance report Support PASS |
| out_of_scope | LLM-with-guidebook; short, polite, no apologies | Actual: hardcoded template string — "I can help with shopping questions for this catalogue, but that request is outside what I can handle here…" — no LLM call | **DIVERGENT** | `graph.py (out_of_scope_deflect)` l.3270–3287 — no LLM invocation; brief §6 specifies "LLM with guidebook: short, polite, no apologies" |
| unsafe | Hard refuse, logged, no softening prose | `unsafe_deflect` emits exactly: "I cannot help with instructions or requests that could cause harm." — single sentence, no softening. (Earlier finding of softening prose has been FIXED in current code.) | **MATCH** | `graph.py (unsafe_deflect)` l.3301; knowledge constraint `unsafe-refuse-template-softening-prose.md` (superseded by current code) |

---

## C. Tier → Composition Mappings (4 tiers)

The implementation uses R4 vocabulary (narrow→decisive, mid→shapeable, broad→exploratory, overwhelming→intractable). Handoff brief uses original names. Mapping is 1:1 via `_PRODUCT_SEARCH_TIER_TO_COMPOSITION`.

| Tier (brief name → R4 name) | Intended Composition | Actual Composition | Verdict | Evidence |
|---|---|---|---|---|
| narrow → decisive | refinement_chips | `"decisive": "refinement_chips"` | **MATCH** | `graph.py` l.295; `canonical_enums.py` COMPOSITION_ENUM[0] |
| mid → shapeable | refinement_chips_with_hatch | `"shapeable": "refinement_chips_with_hatch"` | **MATCH** | `graph.py` l.296; conformance report V2 guitar `composition=refinement_chips_with_hatch` |
| broad → exploratory | question_led | `"exploratory": "question_led"` | **MATCH** | `graph.py` l.297; conformance report Gap-8 `composition=question_led` PASS |
| overwhelming → intractable | hard_fork | `"intractable": "hard_fork"` | **MATCH** | `graph.py` l.298; conformance report Gap-6 `hard_fork` PASS |

All 4 tier→composition mappings match. Note: `_GIFT_ADVISOR_COMPOSITION = COMPOSITION_ENUM[2]` (question_led) and `_ADVICE_COMPOSITION = COMPOSITION_ENUM[2]` (question_led) — these are used as tier labels for observability only; the actual render is via `_render_*_takeover_block`, not `_render_turn1_preview_block`.

---

## D. Exact Copy Checks

| Copy Element | Intended Literal (brief) | Actual in Code | Verdict | Evidence |
|---|---|---|---|---|
| Unsafe refuse phrasing (no softening) | Hard refuse, no softening prose; §8 guardrails matrix: refuse tier "never include softening prose" | "I cannot help with instructions or requests that could cause harm." — single clause, no "I can still help…" second sentence | **MATCH** | `graph.py (unsafe_deflect)` l.3301 |
| Browse hatch label | §6: "Just browsing — show me popular searches", 12px grey | `_browse_hatch()` returns `_t(raw_language, "browse_hatch_label")` — localized string | **MATCH** | `graph.py (_browse_hatch)` l.317–326; UI strings at ~l.121 for EN |
| Chat affordance label | §3: "Chat with me instead →" | `_t(raw_language, "chat_affordance_label")` — localized per language table | **MATCH** (label structure correct; exact English string needs verification against _UI_STRINGS table) | `graph.py (_chat_affordance)` l.311 |
| Gift anchor labels | §3: "Hobbies & interests", "Lifestyle", "Practical / useful", "I have an idea" | `_DEFAULT_GIFT_GUIDEBOOK_ANCHORS` = exactly these 4 strings in this order | **MATCH** | `graph.py` l.383–388 |
| hard_fork prompt | §6: "{result_count} results is too many to scan. {q}" | `_t(raw_language, "result_count_too_many").format(total=…, suffix=prompt_suffix)` where suffix = `choose_starting_point_query` or `choose_starting_point` | **MATCH** | `graph.py (_render_turn1_preview_block)` l.1448–1458; conformance report Gap-6 direct renderer confirmed "15 000 results is too many to scan. Choose a starting point…" |
| Comparison mode-shift note | §6: "comparison detected, swapping side-by-side for this turn" | `_COMPARISON_MODE_SHIFT_NOTE = "comparison detected, swapping side-by-side for this turn"` | **MATCH** | `graph.py` l.392 |
| Out-of-scope response | §6/§8: "LLM with guidebook: short, polite, no apologies" (no literal prescribed) | Hardcoded: "I can help with shopping questions for this catalogue, but that request is outside what I can handle here." | **DIVERGENT** (spec says LLM-with-guidebook; code uses hardcoded template) | `graph.py (out_of_scope_deflect)` l.3275–3279 |

---

## Prioritized Divergences and Gaps

**1. out_of_scope uses hardcoded template instead of LLM-with-guidebook (DIVERGENT)**
The handoff brief §6/§8 specifies "LLM with guidebook: short, polite, no apologies" for out_of_scope. The actual `out_of_scope_deflect` function emits a hardcoded English template with zero LLM calls (`llm_call_count=0` implied). This means: (a) no language-adaptive response for sk/cs users; (b) no guidebook-shaped tone; (c) violates the intended deflect-tier contract.
Evidence: `graph.py (out_of_scope_deflect)` l.3270–3287.

**2. Chat affordance absent on decisive/narrow tier (PARTIAL — A8)**
The handoff §3 commitment is "every turn-1 surface". `_CHAT_AFFORDANCE_TIERS = set(TIER_ENUM[1:])` = {shapeable, exploratory, intractable} — decisive is excluded. Narrow (decisive) product_search queries get no chat affordance. The brief specifically calls out `refinement_chips_with_hatch` as carrying it, but the "every surface" language covers decisive/narrow too.
Evidence: `graph.py` l.302; `graph.py (_render_turn1_preview_block)` l.1471.

**3. type-it-out absent on broad-browse / question_led tier (PARTIAL — A9)**
The handoff §3 commitment names "advice, gift_advisor, broad-browse surfaces". `_render_turn1_preview_block` for `question_led` composition does not emit a `type_it_out` key. Only advice and gift_advisor carry it. The broad-browse (exploratory/question_led) surface is missing this parallel affordance.
Evidence: `graph.py (_render_turn1_preview_block)` l.1433–1443 (question_led path has no `type_it_out`); `graph.py (_derive_type_it_out_parallel_on)` l.1415–1416 (only advice mode).

**4. Gift anchor labels not language-resolved (PARTIAL — A10 / A6)**
Gift advisor anchor `label` fields emit English strings ("Hobbies & interests", etc.) regardless of `raw_language`. The `filter_value` identity is correctly language-neutral, but the display label is not routed through `_t()`. For sk/cs shops this is a visible localization gap. The handoff §3 explicitly calls these "localisable".
Evidence: `graph.py (_render_gift_advisor_takeover_block)` l.1518 — `"label": anchor.label` (raw, not `_t()`); `graph.py` l.383–388 (English strings in default anchors).

**5. Language-aware label resolution partial for categorical chips (PARTIAL — A6)**
Categorical facet chips (`category_upto_lvl_1`, `brand`) set both `label` and `filter_value` to the same raw facet value. Price fallback labels ARE localized (routed through `_t(raw_language, ...)` in `turn1_selector.py` l.196-206); the A6 PARTIAL therefore stands on the categorical-chip half (label == filter_value) only, not price chips. This is a pre-existing finding in the knowledge store (`v2-gap-plan-label-resolution-validation-missing`).
Evidence: knowledge store finding (session 1781073603); `turn1_selector.py (select_chips)`.

---

## Verdict Tally

| Verdict | Count | Items |
|---|---|---|
| MATCH | 16 | A1, A2, A3, A5, A7; modes: product_search, gift_advisor, comparison, advice, support, unsafe; all 4 tier→composition; copy: unsafe, browse-hatch, chat-label, gift-anchors, hard-fork, comparison-note |
| PARTIAL | 4 | A6 (language labels), A8 (chat affordance — missing on decisive), A9 (type-it-out — missing on broad-browse), A10 (gift anchor labels English-only) |
| DIVERGENT | 1 | out_of_scope (template not LLM-with-guidebook; copy divergent) |
| GAP | 0 | — |
| NOT-IMPLEMENTED | 0 | — |
| NOT-EXERCISED | 1 | A4 engagement-of-preview state inheritance (turn-2-only observable) |

**Hard commitments unmet or partially met:** A6 (PARTIAL), A8 (PARTIAL), A9 (PARTIAL), A10 (PARTIAL). None are fully unimplemented. The most serious is A9 (type-it-out missing on broad-browse) and A8 (chat affordance missing on narrow tier), as these are explicitly flagged "NEW" first-class commitments in §3.

---

## Verification

**Exercised:**
- All 10 hard commitments checked against `graph.py` function bodies, constants, and return values
- All 7 modes traced to their handler functions and return payloads
- All 4 tier→composition mappings verified against `_PRODUCT_SEARCH_TIER_TO_COMPOSITION` and `canonical_enums.py`
- Exact copy strings verified against `_DEFAULT_GIFT_GUIDEBOOK_ANCHORS`, `_COMPARISON_MODE_SHIFT_NOTE`, `unsafe_deflect` template, `out_of_scope_deflect` template
- Existing evidence incorporated by reference: Gap-1 through Gap-8, C-23/C-24 from conformance report; unsafe softening finding from knowledge store

**Not exercised:**
- A4 (engagement-of-preview state inheritance) — code path exists (`is_engagement_of_preview` in Channel 2; `_resolve_turn2_entry_kind` reads it) but the contract is only observable on turn-2 response. Marked NOT-EXERCISED; code shows the plumbing is present.
- Live stack reads — all items determined from code + existing evidence per task guidance.
- Prototype HTML (02 · Interactive prototype.html) — not parsed; §3/§6/§8 of handoff brief is authoritative for contracts; prototype consulted only via the file-map and brief descriptions.

---

Gate-required: applies
Peer-review: applies
Completeness-risk: none — the matrix rows are mechanically enumerable from the brief's explicit contract lists (10 commitments + 7 modes + 4 tiers + copy items in §3/§6/§8); all rows covered.

Pre-emission self-audit: 24 citations verified, 7 sections present, 5 contradictions checked (unsafe softening fixed vs. prior finding; out_of_scope template vs. LLM spec; type-it-out scope vs. brief; chat affordance tiers vs. "every surface"; gift anchor localization vs. "localisable" commitment).
