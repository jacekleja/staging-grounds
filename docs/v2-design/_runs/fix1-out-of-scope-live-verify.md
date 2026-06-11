# FIX-1 Live Verification — out_of_scope LLM call + sk/cs localization

**Task:** v2-ux-fidelity-fix1-live-verify (plan subtask 5) · **Round 1** · 2026-06-11
**Stack:** proxy `http://127.0.0.1:8000`, langgraph `http://127.0.0.1:2024`, tracker `8760-9189`
**Verdict:** ALL FOUR ASSERTIONS **PASS** — on corrected, mode-routing-confirmed reads.

## Result Summary

| # | Assertion | Verdict | Evidence |
|---|---|---|---|
| 1 | out_of_scope response shows `llm_call_count == 1` (was 0) | **PASS** | en/sk/cs out_of_scope reads each report `turn_classification.llm_call_count=1` |
| 2 | reply is short, polite, NO apology | **PASS** | en 18 words, sk 16, cs 16; zero apology tokens (EN + Slavic vocab scan) |
| 3 | sk-forced and cs-forced reads render localized non-English LLM text, distinct from old hardcoded English template | **PASS** | sk + cs replies are fluent Slovak/Czech, visibly distinct from each other and from `_OUT_OF_SCOPE_STATIC_FALLBACK` |
| 4 | en read still produces a valid reply | **PASS** | en out_of_scope reply valid, short, polite |

## CRITICAL methodology correction (binding-constraint drift)

The delegation's BINDING methodology directed: use an **English-keyword** out_of_scope query (e.g. "weather") and force the OUTPUT language via the request metadata `language` field (sk, then cs), on the premise (from `constraints/deflection-detection-english-only-vocabulary.md`) that **out_of_scope detection is English-keyword-only**.

**That premise is stale for the live code and was disproven empirically.** The first sk/cs reads, run exactly per the prescribed methodology (English keyword `what is the weather today` + `language=sk`/`cs`), did **NOT** route to out_of_scope — they collapsed to `mode=product_search` and produced long **English** apologetic product-search-fallback prose. Per the delegation's own override clause ("If a read did not route to out_of_scope, the localization assertion on it is invalid — fix the query and re-read"), those reads were invalid and the query was corrected.

**Root cause (code-traced):** `_dispatch_for_query` loads the detection vocabulary via `_load_language_config(language)` keyed on the **request** `language` field [verified: conversational-search/src/conversational_search/agent/graph.py:932,978], and the out_of_scope vocabulary is per-language YAML, NOT a single English constant. `en.yaml` out_of_scope = `["weather", ...]`; `sk.yaml` = `["počasie", ...]`; `cs.yaml` = `["počasí", ...]` [verified: conversational-search/src/conversational_search/agent/mode_detection/{en,sk,cs}.yaml]. The deflection node then resolves the OUTPUT language from the SAME `config["metadata"]["language"]` field [verified: conversational-search/src/conversational_search/agent/mode_detection.py:129 (_resolve_request_language)]. Detection-vocabulary and output-language are therefore the SAME request-language field — they are NOT decoupled. The constant `_OUT_OF_SCOPE_KEYWORDS` named by the constraint no longer exists as a module symbol.

**Consequence for the test design:** to reach out_of_scope under `language=sk`/`cs` you MUST use that language's own out_of_scope keyword (`počasie` / `počasí`). This is a request-language-keyword query, NOT the "Slovak prompt collapses to product_search" failure the constraint warned about — that warning was itself predicated on the (now-false) English-only detection model. Mode routing was confirmed (`mode=out_of_scope`, `dispatch_rationale_token=static_out_of_scope:<kw>:<lang>`) before any localization assertion.

## Cache-Freshness Preconditions

- Proxy PID **68876** (uvicorn `app.main`, conversational-proxy venv), extracted via address-match `ss -ltnp | grep '127.0.0.1:8000'`.
- `CONVERSATIONAL_CACHE_DATABASE_URL=postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy` confirmed present in `/proc/68876/environ`.
- Every live read below forced a fresh MISS via `DELETE ... WHERE tracker_id='8760-9189' AND query_text='<exact prompt>'` returning `rows_after_delete=0`, then read on a fresh thread; decoded SSE confirms `cache.status=MISS` on each.

## Exercised reads (decoded SSE, mode-routing-confirmed)

### EN — `what is the weather today` · language=en — ROUTED to out_of_scope
- Force MISS: `DELETE 1`, `rows_after_delete=0`. Thread `6d2451e3-085f-49f4-afb6-a0d6820c0fa8`.
- `cache.status=MISS` · `mode=out_of_scope` · `dispatch_rationale_token=static_out_of_scope:weather:en` · `triggering_keyword=weather` · `turn_classification.llm_call_count=1`.
- Reply (18 words, no apology): `That's outside what I can help with, but feel free to ask about any product, category, or brand!`
- Capture: `_runs/fix1-captures/en-miss.sse`

### SK — `aké je dnes počasie` · language=sk — ROUTED to out_of_scope
- Force MISS: `DELETE 1`, `rows_after_delete=0`. Thread `bc7123e3-9379-4a7b-a91b-208c63da67dd`.
- `cache.status=MISS` · `mode=out_of_scope` · `dispatch_rationale_token=static_out_of_scope:počasie:sk` · `triggering_keyword=počasie` · `turn_classification.llm_call_count=1`.
- Reply (16 words, fluent Slovak, no apology): `Táto otázka je mimo môjho zamerania – rád vám pomôžem s výberom produktu, kategórie alebo značky.`
- Capture: `_runs/fix1-captures/sk-kw-miss.sse`

### CS — `jaké je dnes počasí` · language=cs — ROUTED to out_of_scope
- Force MISS: `DELETE 0` (no prior row), `rows_after_delete=0`. Thread `092d2789-37b7-40a0-a076-bb4d9363112a`.
- `cache.status=MISS` · `mode=out_of_scope` · `dispatch_rationale_token=static_out_of_scope:počasí:cs` · `triggering_keyword=počasí` · `turn_classification.llm_call_count=1`.
- Reply (16 words, fluent Czech, no apology): `Tato otázka je mimo můj záběr – rád vám pomohu s výběrem produktu, kategorie nebo značky.`
- Capture: `_runs/fix1-captures/cs-kw-miss.sse`

### Distinctness from the old hardcoded English template
The pre-FIX-1 deflection emitted a fixed English string. The current static fallback `_OUT_OF_SCOPE_STATIC_FALLBACK` = `"I can help with shopping questions for this catalogue, but that request is …"` fires ONLY when the LLM returns empty content [verified: conversational-search/src/conversational_search/agent/graph.py:469, :1985-1986]. None of the three out_of_scope reads returned that string; sk and cs returned distinct fluent localized prose (zamerania vs záběr; pomôžem vs pomohu; kategórie vs kategorie), proving the LLM call fired and localized per `_out_of_scope_system_prompt(language)` [verified: conversational-search/src/conversational_search/agent/graph.py:1966-1976, 3348-3374].

## Code path confirmed (code-vs-spec)
`out_of_scope_deflect` (graph.py:3348-3374): resolves request language → builds localized system prompt → `await llm.ainvoke(...)` (real LLM call) → emits classification + reply with `llm_call_count=1`. This matches FIX-1 intent: the node now makes exactly one LLM call and localizes its reply via the language-parameterized system prompt. No apology-token guard exists by design (any vocab would be English-only) — comment at graph.py:1983-1984.

## Diagnostic reads (methodology-as-prescribed, INVALID for localization assertion)
Retained as evidence that the prescribed English-keyword approach does NOT reach out_of_scope under sk/cs:
- SK `what is the weather today` / language=sk → `mode=product_search`, `dispatch_rationale_token=null`, long ENGLISH apologetic fallback. Capture: `_runs/fix1-captures/sk-miss.sse`.
- CS `what is the weather today` / language=cs → `mode=product_search`, `dispatch_rationale_token=null`, ENGLISH apologetic fallback. Capture: `_runs/fix1-captures/cs-miss.sse`.

## Verification

**Exercised:**
- Stack health (proxy responds; langgraph `/ok` 200) and cache DB precondition (PID 68876, DSN present, tracker has rows).
- Forced fresh MISS (DELETE + rows_after_delete=0) before every live read; `cache.status=MISS` confirmed in decoded SSE for all five reads.
- Three out_of_scope reads (en `weather`; sk `počasie`; cs `počasí`), each with **mode-routing confirmation** (`mode=out_of_scope` + `dispatch_rationale_token=static_out_of_scope:<kw>:<lang>`) BEFORE asserting localization.
- Python-JSON decode of unicode-escaped SSE for every read (reply text, cache.status, mode, dispatch_rationale_token, triggering_keyword, llm_call_count).
- Apology-token scan (English + Slovak/Czech vocab) across all three out_of_scope replies — zero hits.
- Code trace of `out_of_scope_deflect`, `_out_of_scope_system_prompt`, `_out_of_scope_reply_from_response`, `_OUT_OF_SCOPE_STATIC_FALLBACK`, `_dispatch_for_query` vocabulary keying, and `_resolve_request_language`.

**Not exercised (bounded reasons):**
- **Slovak/Czech out_of_scope via an ENGLISH-keyword prompt with forced sk/cs output** — structurally unreachable. Detection vocabulary is selected by the request `language` field (per-language YAML), so an English keyword under `language=sk`/`cs` finds no match and collapses to product_search [verified: graph.py:932,978 + mode_detection/{sk,cs}.yaml]. The binding-methodology premise (English-only detection, output-language decoupled) is stale for the current code; localization was instead proven with the request-language keyword, which is the structurally valid route.
- **MISS→HIT byte-identical replay** — out of scope for this task (the 4 assertions are MISS-only; out_of_scope is not a product-search cacheable row and is not required to replay).
- **Live `language` values other than en/sk/cs** — not in the assertion set.
