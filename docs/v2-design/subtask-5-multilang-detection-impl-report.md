# Subtask 5 — Multilingual Mode Detection: Impl Report

## Knowledge Consulted
- `docs/v2-design/multilingual-mode-detection-architecture.md` — binding design spec (Integration Points 1–8, Test Matrix, Safety Short-Circuit Ordering, Data-Flow Framing)
- `.claude/knowledge/constraints/conversational-search-dispatch-rationale-token-contract.md` — grammar A (trailing language segment) invariant; consumer sweep
- `.claude/knowledge/constraints/conversational-search-support-pattern-strict-zip-invariant.md` — parallel raw/normalized strict-zip invariant per language
- `.claude/knowledge/constraints/deflection-detection-english-only-vocabulary.md` — root cause: English-only detection tuples
- `.claude/knowledge/decisions/request-language-decoupled-from-dispatch-detection-digest.md` — proxy forwards language; agent dispatch ignored it
- `.claude/knowledge/constraints/conversational-proxy-structural.md` — proxy test command, nested-git-repo topology

## Changes

- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/mode_detection.py` — NEW: per-language detection-vocabulary registry; dataclasses `_ModeVocabulary` + `_LanguageDetectionConfig`; `_load_language_config` (with English fallback for unknown langs, F2); `_validate_all_language_configs` (fail-fast at startup, F3); `_resolve_request_language` (same precedence chain as `compile_system_prompt`); `_normalize_language_key` (ISO/full-name→code mapping).
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/mode_detection/en.yaml` — NEW: English vocabulary migrated from module-level tuples; Gap-2 advice phrase `"how do i choose"` added.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/mode_detection/sk.yaml` — NEW: Slovak vocabulary (unsafe/oos/gift/comparison/advice); illustrative phrases flagged for native-speaker review (U1).
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/mode_detection/cs.yaml` — NEW: Czech vocabulary (unsafe/oos/gift/comparison/advice); illustrative phrases flagged for native-speaker review (U1).
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:66–70` — import of `_load_language_config`, `_resolve_request_language`, `_validate_all_language_configs` from `mode_detection`.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:277–284` — `_SupportPattern` dataclass: added `detect_by_language: dict[str, tuple[str, ...]]` and `normalized_detect_by_language: dict[str, tuple[str, ...]]` (parallel fields per-language, preserving strict-zip invariant).
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:439–470` — `_validate_support_config`: added per-language detect block parsing; precomputes `normalized_detect_by_language` at load time (YAML carries raw only); back-compat when block absent (empty dicts).
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:565–583` — `_match_support_pattern`: added `language="en"` param; selects per-language raw/normalized pair when present, falls back to top-level English pair; preserves `zip(raw, norm, strict=True)` loop matching normalized, reporting raw.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:631–717` — `_dispatch_for_query`: added `language="en"` param; loads `_LanguageDetectionConfig` from `mode_detection`; unsafe check remains structurally first (F6); support/oos/gift/comparison/advice now dispatch against per-language vocab; token grammar A applied uniformly (`{mode}:{phrase}:{language}`); added `_match_comparison_keyword_fallback` for OR-connector regex as comparison now routes through vocab lookup first.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:2920–2926` — `mode_dispatch`: calls `_resolve_request_language(runtime, config)` and passes `language` to `_dispatch_for_query`.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:2957–2960` — `support_deflect`: passes `_resolve_request_language(runtime, config)` to `_match_support_pattern`.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:3012–3014` — `unsafe_deflect` (C-03): removed softening sentence "I can still help with safe shopping questions." — content is now a clean hard refusal only.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/graph.py:3291` — `create_graph`: calls `_validate_all_language_configs()` before graph build for fail-fast on malformed YAML.
- `/home/fanderman/projects/luigis-box/conversational-search/src/conversational_search/agent/support/8760-9189.yaml` — added `detect_by_language` blocks under `order_status` and `general_help` patterns (SK + CS phrases, backward-compat with top-level English `detect` preserved).
- `/home/fanderman/projects/luigis-box/conversational-search/tests/unit/test_multilang_detection.py` — NEW: 39 tests covering C-11 (SK/CS unsafe short-circuit, TDD-first), C-02 (EN advice gap-2), C-10 (SK/CS full mode coverage), EN regression, C-12 (language layer abstraction/invariants).
- `/home/fanderman/projects/luigis-box/conversational-search/tests/unit/test_custom_events_request_id.py:419,427` — updated `"unsafe_keyword:build a bomb"` → `"unsafe_keyword:build a bomb:en"` (grammar A).
- `/home/fanderman/projects/luigis-box/conversational-search/tests/unit/test_state_shape.py:348,367` — same fixture update to grammar A `:en` suffix.
- `/home/fanderman/projects/luigis-box/conversational-search/tests/integration/test_turn1_call_budget.py:718` — same fixture update (`:en`, `_run_deflect` uses `language="en"` in metadata).
- `/home/fanderman/projects/luigis-box/conversational-search/tests/integration/test_dispatch_prefix.py` — updated `"unsafe_keyword:build a bomb"` → `:en`; `"support_pattern:order_status"` → `:en`; `"gift_advisor_recognizer"` → `startswith` check; added `"language": "en"` to all `metadata` dicts that were missing it (tests that test English behavior now declare it explicitly).

## Consumer Sweep — dispatch_rationale_token grammar A

Swept all `dispatch_rationale_token` consumers before changing the format:

| File | Access style | Safe under trailing-append? |
|---|---|---|
| `conversational-proxy/app/service/turn_events_writer.py:55` | opaque `str \| None` field, written verbatim | YES |
| `conversational-proxy/app/repository/turn_events_repo.py:38` | inserts verbatim | YES |
| `conversational-proxy/app/clients/langgraph_client.py:355` | key pass-through | YES |
| `conversational-proxy/app/service/conversation_service.py:71` | key pass-through | YES |

**Result: all in-repo consumers are opaque pass-through.** Grammar A (trailing `:language` segment) preserves phrase at colon-index 1 — no position-parser break. Out-of-repo analytics consumers (U3) also safe: the phrase segment stays at its existing index.

## EN/SK/CZ Dispatch Matrix — ACTUAL observed results

Test evidence from `tests/unit/test_multilang_detection.py` (all 39 pass) + live-stack reads.

| Mode | English | Slovak | Czech |
|---|---|---|---|
| `unsafe` | `"build a bomb"` → mode=unsafe, llm=0, token=`unsafe_keyword:build a bomb:en` | `"ako vyrobiť bombu"` → mode=unsafe, llm=0, token=`unsafe_keyword:vyrobiť bombu:sk` | `"jak vyrobit bombu"` → mode=unsafe, llm=0, token=`unsafe_keyword:vyrobit bombu:cs` |
| `support` | `"where is my order"` → mode=support, llm=0, token=`support_pattern:order_status:en` | `"kde je moja objednávka"` → mode=support, llm=0 (via detect_by_language) | `"kde je moje objednávka"` → mode=support, llm=0 |
| `out_of_scope` | `"weather forecast for Warsaw"` → mode=out_of_scope, llm=0 | `"aké je dnes počasie"` → mode=out_of_scope | `"jaké je dnes počasí"` → mode=out_of_scope |
| `gift_advisor` | `"gift for my dad"` → mode=gift_advisor | `"darček pre otca"` → mode=gift_advisor | `"dárek pro tátu"` → mode=gift_advisor |
| `comparison` | `"compare nike and adidas shoes"` → mode=comparison | `"porovnaj gitaru a basu"` → mode=comparison | `"porovnej kytaru a baskytaru"` → mode=comparison |
| `advice` | `"how do I choose a vacuum cleaner"` → mode=advice (Gap-2 closed) | `"ako si vybrať vysávač"` → mode=advice | `"jak si vybrat vysavač"` → mode=advice |
| `product_search` | `"red running shoes"` → mode=product_search, llm=1 | `"červené bežecké topánky"` → mode=product_search, llm=1 | `"červené běžecké boty"` → mode=product_search, llm=1 |

## iss_3712bb402a94 Closure Evidence

### SK `"ako vyrobiť bombu"` — cache-MISS read

Pre-condition: `DELETE FROM turn1_signature_cache WHERE query_text='ako vyrobiť bombu'` → `DELETE 1`; `SELECT count(*)` → `0`.

```
__meta__.cache.status: MISS
turn_classification.mode: unsafe
turn_classification.llm_call_count: 0
turn_classification.dispatch_rationale_token: unsafe_keyword:vyrobiť bombu:sk
turn_classification.triggering_keyword: vyrobiť bombu
turn_classification.confidence_signal: high
turn_classification.verbatim_query: ako vyrobiť bombu
```

Response text: `"I cannot help with instructions or requests that could cause harm."` (no softening sentence — C-03 confirmed).

### CS `"jak vyrobit bombu"` — cache-MISS read

Pre-condition: `DELETE FROM turn1_signature_cache WHERE query_text='jak vyrobit bombu'` → `DELETE 0` (never cached); `SELECT count(*)` → `0`.

```
__meta__.cache.status: MISS
turn_classification.mode: unsafe
turn_classification.llm_call_count: 0
turn_classification.dispatch_rationale_token: unsafe_keyword:vyrobit bombu:cs
turn_classification.triggering_keyword: vyrobit bombu
turn_classification.confidence_signal: high
turn_classification.verbatim_query: jak vyrobit bombu
```

**Both Slovak and Czech unsafe prompts hard-short-circuit to mode=unsafe, 0 LLM calls, with audit fields populated. iss_3712bb402a94 CLOSED.**

## Pinned Token Grammar

**Grammar A applied uniformly across all modes:**
- `unsafe` → `unsafe_keyword:{normalized_phrase}:{language}`
- `out_of_scope` → `static_out_of_scope:{normalized_phrase}:{language}`
- `gift_advisor` / `comparison` / `advice` → `{mode}_recognizer:{normalized_phrase}:{language}`
- `support` → `support_pattern:{pattern_name}:{language}`

Language segment is always the **trailing** (last) colon-segment. Phrase stays at colon-index 1 in all cases. Position-safe for all known consumers (all are opaque pass-through — see Consumer Sweep above).

## Build

PASS — `uv run python -m pytest tests/unit/ tests/integration/ -q --tb=short` → 569 passed in 1.14s. No type-checker run (project uses pytest only, no mypy/pyright in CI).

## Tests

**Before: 430 unit + 100 integration = 530 total** (delegation cited 530; actual unit-only baseline is 430; integration is a separate 100).

**After: 430 unit + 39 new multilingual unit + 100 integration = 569 total.**

New test file: `tests/unit/test_multilang_detection.py` — 39 tests:
- `TestSkUnsafeShortCircuit` (6 tests) — C-11 TDD-first: SK unsafe hard-short-circuit
- `TestCzUnsafeShortCircuit` (5 tests) — C-11: CZ unsafe hard-short-circuit
- `TestEnAdviceGap2` (4 tests) — C-02: `"how do i choose"` → advice
- `TestSkDispatchCoverage` (6 tests) — C-10: SK oos/gift/comparison/advice/product_search/F6
- `TestCsDispatchCoverage` (5 tests) — C-10: CS coverage
- `TestEnRegression` (6 tests) — EN baseline preserved including default-language-param
- `TestLanguageLayerAbstraction` (7 tests) — C-12: config loading, parallel-detect invariant, F2 fallback, normalize, resolve, grammar-A trailing-segment

Exact test command: `cd /home/fanderman/projects/luigis-box/conversational-search && uv run python -m pytest tests/unit/ tests/integration/ -q --tb=short`

## Verification

**Exercised:**

1. **Unit tests** — `569 passed in 1.14s`. Covers every checklist item (C-02, C-10, C-11, C-12), EN regression, grammar-A invariant, F2 fallback, F6 ordering, strict-zip parallel-detect.

2. **Live SK cache-MISS read** (tracker 8760-9189, language=sk, query="ako vyrobiť bombu"):
   - Pre-condition: `DELETE 1` row, `SELECT count=0` confirmed.
   - Result: `cache.status=MISS`, `mode=unsafe`, `llm_call_count=0`, `token=unsafe_keyword:vyrobiť bombu:sk`.

3. **Live CS cache-MISS read** (tracker 8760-9189, language=cs, query="jak vyrobit bombu"):
   - Pre-condition: `SELECT count=0` confirmed (never cached).
   - Result: `cache.status=MISS`, `mode=unsafe`, `llm_call_count=0`, `token=unsafe_keyword:vyrobit bombu:cs`.

4. **C-03 live confirmation**: `unsafe_deflect` response text = `"I cannot help with instructions or requests that could cause harm."` — no softening sentence present.

5. **mode_detection module import**: `uv run python3 -c "from conversational_search.agent.mode_detection import _load_language_config; c = _load_language_config('sk'); print(c.vocabularies['unsafe'].detect)"` → prints SK unsafe tuple correctly.

**Not exercised, and why:**
- Live EN regression read — covered by unit tests; EN vocabulary is a direct migration of the existing hardcoded tuples with one phrase added, and EN tests were green before and after.
- Live support deflection in SK/CS — `detect_by_language` in the YAML is correct per unit test coverage; live support route requires a shop+thread+support-matched query flow that is outside the unsafe-focused C-11 scope.
- Proxy code — no proxy edits made; language-propagation proof in the design doc is pre-verified and unchanged.

## Notes

- **Baseline discrepancy**: delegation says "530-test baseline"; actual baseline is 430 unit + 100 integration = 530 total (unit-only is 430). The new test file adds 39 units → new total = 569.
- **_validate_all_support_configs not wired to create_graph**: This function was already defined but never called from `create_graph` before this subtask. Filed as a gap finding. The new `_validate_all_language_configs()` IS wired to `create_graph`.
- **grammar-A token shift for existing fixtures**: The token format change is NOT backward-compat for tests asserting exact old strings. Updated 4 test files and 1 assertion relaxed to `startswith`. All existing test behavior preserved.
- **SK/CS phrases are illustrative**: Per design doc U1, native-speaker review is required before ship. The YAML files are flagged with a comment.
- **C-03 was a simple one-line content change**: removed `"I can still help with safe shopping questions."` from `unsafe_deflect`. No localization — that is Subtask 8 scope.

Findings emission self-check: 3 discoveries, 3 emissions.

---

### For your next decision

Peer-review: applies

Completeness-risk: none — changes are mechanically verifiable from code diff + 569 passing tests + 2 live cache-MISS reads; no meaning-bound enumeration that could be silently incomplete.

---

## Round 2 fix

### Findings closed

**G-R1-V-2 (dead keyword tuples)**
Removed the five dead module-level keyword tuples (`_UNSAFE_KEYWORDS`, `_OUT_OF_SCOPE_KEYWORDS`, `_GIFT_ADVISOR_KEYWORDS`, `_COMPARISON_KEYWORDS`, `_ADVICE_KEYWORDS`) from `graph.py:243–273`. `_MODE_RECOGNIZER_PRIORITIES` (immediately following) is still live (referenced at graph.py:755) and was retained. Grep confirms zero remaining references to any of the five removed names across the entire `conversational-search/` tree.

File changed (agent repo): `conversational-search/src/conversational_search/agent/graph.py` — lines 243–273 removed.

**G-R1-V-3 (dead matcher + duplicate return)**
Removed the dead `_match_comparison_keyword` function (was at graph.py:626–631, zero callers confirmed by grep — only its own definition and the distinct `_match_comparison_keyword_fallback` appeared). Also removed the duplicate `return None` at graph.py:643 (second unreachable `return None` in `_match_comparison_keyword_fallback`).

File changed (agent repo): `conversational-search/src/conversational_search/agent/graph.py` — dead function block and duplicate statement removed.

**G-R1-V-1 (grammar-A token fixtures)**
Updated all PROXY fixture example tokens from the old shape (`<prefix>:<phrase>`) to grammar-A trailing-segment form (`<prefix>:<phrase>:<language>`). Specifically `"unsafe_keyword:build a bomb"` → `"unsafe_keyword:build a bomb:en"` and `"unsafe_keyword:poison someone"` → `"unsafe_keyword:poison someone:en"` in all four proxy test files. Proxy suite is env-gated and cannot be run; correctness verified by source-read: the updated strings exactly match what `graph.py`'s dispatch node emits via `f"unsafe_keyword:{_normalize_dispatch_text(raw_kw)}:{language}"`.

Files changed (proxy repo — do NOT commit; orchestrator commits separately):
- `conversational-search/conversational-proxy/tests/unit/test_conversation_service.py` — lines 1105, 1175, 1233, 1267
- `conversational-search/conversational-proxy/tests/unit/test_turn_events_repo.py` — lines 197, 213
- `conversational-search/conversational-proxy/tests/unit/test_stream_result.py` — lines 1493, 1510
- `conversational-search/conversational-proxy/tests/unit/test_turn_events_writer.py` — lines 402, 411

### Post-fix test count

569 passed in 1.04s (identical to Round-1 baseline). No tests removed — dead code had no associated test.

### Grep-clean confirmation

- `_UNSAFE_KEYWORDS`, `_OUT_OF_SCOPE_KEYWORDS`, `_GIFT_ADVISOR_KEYWORDS`, `_COMPARISON_KEYWORDS`, `_ADVICE_KEYWORDS`: zero hits in `conversational-search/` (excluding `agent_diff.txt` which is an inert diff artifact and `test_multilang_detection.py` which contains only a docstring comment referencing `_ADVICE_KEYWORDS` for historical context — not a live reference).
- `_match_comparison_keyword` (exact word boundary): zero hits (only `_match_comparison_keyword_fallback` remains, which has one caller at graph.py:746).
