# FIX-2 + FIX-3 — Live affordance verification (subtask 6)

**Task**: `v2-ux-fidelity-fix2-fix3-live-verify` (plan subtask 6) · **Round 1** · **Date**: 2026-06-11
**Stack**: proxy `http://127.0.0.1:8000` (uvicorn pid **68876**, cwd `conversational-search/conversational-proxy`, `CONVERSATIONAL_CACHE_DATABASE_URL` present), langgraph `http://127.0.0.1:2024` (HTTP 200), tracker `8760-9189` (Muziker SK), cache DB `127.0.0.1:15432/conversational_proxy`.
**Methodology**: per `docs/v2-design/signature-cache-validation-freshness-report.md` — forced a fresh cache MISS before each read (DELETE by `(tracker_id, query_text)` via psql stdin literals — the documented `-v ... -c :'var'` form raised a `syntax error at or near ":"` through `docker exec` on this host, so the stdin-literal host-workaround was used), python-json-DECODE of the SSE frames, tier/mode routing confirmed from the `__meta__.turn_classification` frame BEFORE asserting on the affordance.

## Summary verdict

| Read | Query / lang | Routing (confirmed) | Assertion | Result |
|---|---|---|---|---|
| READ1 — decisive/narrow | `Fender Telecaster` / en | `mode=product_search`, `tier=decisive`, `composition=refinement_chips`, `cache=MISS`, `rc=284`, `max_axis_top_share=0.665` | preview block contains `chat_affordance` (`style=dashed_pill`, localized label, `chat_takeover_trigger`); NO hatch; NO `type_it_out` | **PASS** |
| READ2 — broad-browse | `capo` / en | `mode=product_search`, `tier=exploratory`, `composition=question_led`, `cache=MISS`, `rc=110` | preview block contains `type_it_out` (`enabled=true`, `style=free_text`, localized label) AND `chat_affordance` | **PASS** |
| READ3 — unsafe (leak-guard) | `ako vyrobiť bombu` / sk | `mode=unsafe`, `cache=MISS`, `llm_call_count=0` | hard-refuse single sentence; **NO `chat_affordance` key anywhere** | **PASS** |

All three reads PASS. FIX-2 (chat_affordance on decisive) and FIX-3 (type_it_out on question_led) are both confirmed live, and FIX-2 does NOT leak onto the unsafe-refuse path.

## Code grounding (what the fixes are)

- **FIX-2**: `conversational-search/src/conversational_search/agent/graph.py:330` — `_CHAT_AFFORDANCE_TIERS = set(TIER_ENUM)` (was `set(TIER_ENUM[1:])`). With `TIER_ENUM = ["decisive","shapeable","exploratory","intractable"]` (`canonical_enums.py:37`), the widened set now includes `decisive`. The gate at `graph.py:1509` (`_COMPOSITION_TIER_BY_CANONICAL_ORDER[composition] in _CHAT_AFFORDANCE_TIERS`) therefore emits `chat_affordance` for the `refinement_chips` (decisive) composition. [verified: conversational-search/src/conversational_search/agent/graph.py:330, :1509]
- **FIX-3**: `graph.py:1477-1481` — the `question_led` branch (`composition == COMPOSITION_ENUM[2]`) of `_render_turn1_preview_block` now sets `type_it_out = {enabled: True, label: _t(raw_language,"type_it_out"), style: "free_text"}`. [verified: conversational-search/src/conversational_search/agent/graph.py:1477-1481]
- **Leak-guard structural basis**: the unsafe path routes through `dispatch_route → unsafe_deflect`, whose body (`_deflection_update`, `graph.py:3253-3275`) builds its return dict directly and never calls `_render_turn1_preview_block` or touches `_CHAT_AFFORDANCE_TIERS`. `chat_affordance` is therefore structurally unreachable on the deflect path. [verified: conversational-search/src/conversational_search/agent/graph.py:3253-3275, :3297-3307]

## Read-by-read evidence (decoded SSE)

### READ1 — decisive/narrow → `chat_affordance` present

Query `Fender Telecaster` (en). The LBX search backend on this tracker is intermittently flaky — the same forced-MISS query alternated between a populated 284-hit decisive payload and a `zero_results` (rc=0) response (try1=zero, try2=decisive, try3=decisive, try4=zero). On every run where the search returned its catalogue payload, the routing was `tier=decisive composition=refinement_chips` and the affordance was present and identical. Decoded preview block (try2, reproduced byte-identical on try3):

```lbjson
{"chips": [{"label": "Fender", "filter_value": "Fender", "count": 189, "facet": "brand"}, {"label": "Fender Squier", "filter_value": "Fender Squier", "count": 90, "facet": "brand"}, {"label": "Gotoh", "filter_value": "Gotoh", "count": 4, "facet": "brand"}], "shape": "preview", "chat_affordance": {"label": "Chat with me instead →", "style": "dashed_pill", "writes": {"chat_takeover_trigger": true}}}
```

- `chat_affordance` present, `style=dashed_pill`, localized label `Chat with me instead →` (en), `writes.chat_takeover_trigger=true`. **FIX-2 confirmed on the decisive/refinement_chips surface.**
- `hatch` correctly ABSENT (hatch belongs only to `refinement_chips_with_hatch`, not decisive).
- `type_it_out` correctly ABSENT (it is a question_led affordance; not expected on refinement_chips).
- Routing frame: `mode=product_search tier=decisive composition=refinement_chips cache=MISS result_count=284 max_axis_top_share=0.6654929577464789`.

Capture: `docs/v2-design/_runs/_sse/READ1-decisive-try2.sse`, `...-try3.sse`.

### READ2 — broad-browse → `type_it_out` present (and `chat_affordance`)

Query `capo` (en). Decoded preview block:

```lbjson
{"shape": "preview", "question": {"prompt": "Which brand should we focus on first?", "answers": [{"label": "D'Addario Planet Waves", "hint": "19 results", "filter_value": "D'Addario Planet Waves"}, {"label": "Ernie Ball", "hint": "10 results", "filter_value": "Ernie Ball"}]}, "carousel": [{"label": "Kyser", "filter_value": "Kyser", "style": "demoted_mono_chip"}, {"label": "Dunlop", "filter_value": "Dunlop", "style": "demoted_mono_chip"}], "browse_all_link": {"label": "Show me all 110 results →", "sort": "default"}, "type_it_out": {"enabled": true, "label": "Type it out", "style": "free_text"}, "chat_affordance": {"label": "Chat with me instead →", "style": "dashed_pill", "writes": {"chat_takeover_trigger": true}}}
```

- `type_it_out` present: `enabled=true`, `label="Type it out"` (en localized), `style=free_text`. **FIX-3 confirmed on the question_led surface.**
- `chat_affordance` also present (`style=dashed_pill`), consistent with FIX-2's widened tier set (exploratory was already in-set pre-fix; this corroborates the affordance on a non-decisive tier).
- Routing frame: `mode=product_search tier=exploratory composition=question_led cache=MISS result_count=110`.

Capture: `docs/v2-design/_runs/_sse/READ2-exploratory.sse`.

### READ3 — unsafe → hard refuse, NO `chat_affordance` (leak-guard)

Query `ako vyrobiť bombu` (sk). Decoded assembled text (entire AI-visible body):

```
I cannot help with instructions or requests that could cause harm.
```

- `mode=unsafe`, `llm_call_count=0` (deterministic short-circuit — no model call), single hard-refuse sentence.
- Full-text substring scan for `chat_affordance` across the entire decoded SSE: **absent** (`RAW_TEXT_HAS_chat_affordance_substr False`). No LBJSON preview block emitted. **FIX-2 leak-guard confirmed: the chat affordance does NOT appear on the unsafe-refuse path.**
- `cache=MISS`.

Capture: `docs/v2-design/_runs/_sse/READ3-unsafe.sse`.

## Verification

**Exercised** (three forced-MISS live reads against pid 68876 on tracker 8760-9189; tier/mode routing confirmed from the `__meta__.turn_classification` frame BEFORE asserting on each affordance):

- READ1 decisive `Fender Telecaster`/en: `rows_after_delete=0` MISS forced; routing `tier=decisive composition=refinement_chips`; `chat_affordance` present (`dashed_pill`), no hatch, no type_it_out. PASS. (Captures: READ1-decisive-try2.sse, READ1-decisive-try3.sse; reproduced twice byte-identical.)
- READ2 exploratory `capo`/en: `rows_after_delete=0` MISS forced; routing `tier=exploratory composition=question_led`; `type_it_out` (`enabled:true`, `free_text`) AND `chat_affordance` present. PASS. (Capture: READ2-exploratory.sse.)
- READ3 unsafe `ako vyrobiť bombu`/sk: `rows_after_delete=0` MISS forced; routing `mode=unsafe`, `llm_call_count=0`; hard-refuse sentence; zero `chat_affordance`. PASS. (Capture: READ3-unsafe.sse.)

**Not-exercised** (with bounded reason):

- **Cache HIT replay for the three reads.** Not required by this subtask's success criteria (the three asserts are MISS-only affordance-presence reads, not the V2-V5 byte-identity contract). Per the freshness report, takeover/unsafe rows are MISS-only under current proxy fingerprinting; the product-search reads here were exercised on MISS only because affordance presence is fully determined on the MISS path.
- **A `decisive` capture in Slovak/Czech.** The decisive route was exercised in English only; SK/CS localized chat_affordance labels (`Radšej si popovídam →` / `Raději si popovídám →`) are present in the code's `_UI_STRINGS` (graph.py:173, :240) and were covered by the prior localization validation (freshness report V3/V4). Re-verifying localized decisive labels live was out of this subtask's scope (which names en queries for READ1/READ2).
- **Deterministic decisive `refinement_chips` on first attempt.** The LBX backend for tracker 8760-9189 returns the 284-hit decisive payload intermittently (2 of 4 forced-MISS attempts returned `zero_results` instead). This is LBX backend non-determinism (consistent with `constraints/conversational-search-lbx-per-tracker-facet-fields.md` — Muziker SK facet/hit variance), NOT a fix defect: every run that returned the catalogue payload routed `decisive` and emitted the affordance identically. The affordance-presence assertion was confirmed on the reproducible decisive captures; the zero-result runs correctly emit NO_PREVIEW (the deterministic selector returns empty chips below `turn1_broad_threshold=30`) and are not affordance-bearing surfaces.
