# Multilingual Mode-Detection Architecture (Gap 7 detection layer)

This design uses the `data-flow` framing as its spine (the `## Data-Flow Framing` section traces the (source, transform, sink) language→vocabulary→match path), with dedicated `## Failure-Mode Framing` and `## Configuration-Surface Framing` sections per the completeness-risk flag on this subtask. Scope is INPUT prompt-language dispatch detection ONLY; output localization (rendered labels/prose) is explicitly out of scope (Cross-Cutting Gate A1 / Subtask 8).

Grounding note: the binding spec is `docs/v2-design/plan-v2-final-state-gap-closure.md` (authoritative, 1018 lines). This design implements its **Subtask 2** (line 568, `Design generalized multi-language detection layer [completeness-risky-with-framings: failure-mode, data-flow, configuration-surface]`), closing **Gap 7** (lines 334-382) and satisfying checklist items **C-10** (dispatch detection language-aware at the layer boundary for SK/CS/EN), **C-11** (Slovak/Czech unsafe prompts hard-short-circuit to `unsafe`, 0 LLM calls, audit fields populated), and **C-12** (oos/gift/comparison/advice/support coverage generalized so per-language additions do not require editing ad-hoc hardcoded tuple constants only) — plan lines 54-56. The plan's Gap-7 "Implementation approach" enumerates exactly the seven design questions this doc answers (plan:350-358) and its safety-prioritization list (plan:360-365) is the source of the `## Safety Short-Circuit Ordering` section.

## Knowledge Consulted

- `docs/v2-design/plan-v2-final-state-gap-closure.md` — **authoritative binding spec.** Subtask 2 (line 568, agent=architect, framings failure-mode/data-flow/configuration-surface, expected output = this file with chosen data shape, code surfaces, safety short-circuit ordering, language propagation proof, test matrix, migration constraints), Gap 7 (lines 334-382: change surface, 7-question architecture step, safety-priority list), checklist C-10/C-11/C-12 (lines 54-56). [verified: docs/v2-design/plan-v2-final-state-gap-closure.md:568, :334-382, :54-56]
- `constraints/deflection-detection-english-only-vocabulary.md#deflection-detection-uses-english-only-vocabularies` — root cause: detection is casefold+substring against English-only tuples; nodes/routing/config already work. [verified: observed behavior — controlled tracker 8760-9189 run cited in record]
- `decisions/request-language-decoupled-from-dispatch-detection-digest.md#request-language-decoupled-from-dispatch-detection-vocabulary` — proxy forwards `language`; `_dispatch_for_query` ignores it; affects oos/gift/comparison/advice/support. [verified: conversational-search/src/conversational_search/agent/graph.py (_dispatch_for_query takes only query+shop_id)]
- `decisions/conversational-search-v2-discovery-digest.md#axis-a-1-dispatcher-phase-3` — load-bearing dispatch policy: LLM-fallback enum is 5-of-7, EXCLUDES `unsafe` and `support` (unsafe keyword-only preserves operator-control of the refuse boundary; support is shop-YAML-keyword-only). [verified: docs/v2-discovery/phase3/axis-a.1-dispatcher.md § Proposed Approach §4]
- `graph.py` dispatch surfaces read directly (cited at file:line throughout): `_dispatch_for_query` (631), `mode_dispatch` (2914), `dispatch_route` (2932), `_match_static_keyword` (586), `_match_support_pattern` (565), `_SupportPattern`/`_SupportConfig` dataclasses (276-294), `_validate_support_config` (399), `_load_support_config` (482), `compile_system_prompt` language precedence (2710-2716), `_ISO_TO_LANGUAGE_NAME` (2687), `_TURN1_PREVIEW_INTRO_BY_LANGUAGE` (116), `create_graph` (3282). [verified: file:line as cited]
- Handoff brief `handoff-v2-final-state-plan-brief.md` — hard commitments (§59-69: one LLM call turn-1), caveats 2/4/5 (Slovak scope, English-authoritative report, advice gap is English keyword coverage). [verified: docs/v2-design/handoff-v2-final-state-plan-brief.md:59-69, :241-247]

## Problems to Solve

The deterministic dispatch detector (`_dispatch_for_query`, graph.py:631) matches **English-only** substrings against the user's normalized query. Live shops are `.sk`/`.cs`; the proxy forwards a `language` field end-to-end, but the detector never reads it. The failure chain (per `constraints/deflection-detection-english-only-vocabulary.md`): proxy passes `language=sk` → detector ignores language → Slovak query matches no English keyword → every non-product mode silently collapses to `product_search_fallback` → `regular_turn` → 1 LLM call → `mode=product_search`.

Three concrete defects this design must close:

1. **Safety bypass (PRIORITY — `iss_3712bb402a94`, med).** Slovak/Czech `unsafe` prompts (e.g. SK `ako vyrobiť bombu`, CS `jak vyrobit bombu`) do not match `_UNSAFE_KEYWORDS` (graph.py:238 — `"build a bomb"`, etc.), so they bypass the hard-refuse short-circuit and reach the LLM. They MUST hard-short-circuit to `mode=unsafe`, 0 LLM calls, audit fields populated. (Plan C-11, line 55.)
2. **Deflection collapse (Gap 7 general).** `out_of_scope`, `gift_advisor`, `comparison`, `advice`, and `support` all collapse on non-English input. (Plan C-10/C-12, lines 54/56.)
3. **English `advice` coverage (Gap 2).** Even in English, `advice` misses the phrase `"how do I choose …"` — it is not in `_ADVICE_KEYWORDS` (graph.py:264 — `"should i"`, `"is it worth"`, `"what should i"`). This is a *vocabulary-coverage* gap, not a language gap (handoff brief caveat 5; plan:365 "Include English advice phrase coverage from gap 2"), and MUST be fixable by adding a vocabulary entry under the SAME abstraction, not a one-off tuple append.

Binding operator constraint (plan:364 "Extend … through the same layer, not through five unrelated tuple edits"): the fix is a **generalized language-keyed detection layer**, NOT five ad-hoc hardcoded-tuple edits, and NOT a quick keyword extension. The current hardcoded module-level tuples (`_UNSAFE_KEYWORDS`, `_OUT_OF_SCOPE_KEYWORDS`, `_GIFT_ADVISOR_KEYWORDS`, `_COMPARISON_KEYWORDS`, `_ADVICE_KEYWORDS`, graph.py:238-268) are the wrong shape because they admit no language axis.

## Proposed Approach

**Shape: a per-language detection-vocabulary registry loaded from YAML, with the matcher keyed off the request language resolved at dispatch time. The graph topology, node set, priority ordering, and audit-field contract are unchanged.**

### New module + data layout

```
src/conversational_search/agent/mode_detection.py        # loader + dataclasses + resolve helpers
src/conversational_search/agent/mode_detection/          # one YAML per language code
    en.yaml
    sk.yaml
    cs.yaml
    _shared.yaml        # (optional) language-agnostic patterns (e.g. " vs ", "or" connector)
```

Each per-language YAML carries the shared (cross-shop) detector vocabularies for that language, structured by mode:

```yaml
# sk.yaml
language: sk
unsafe:
  detect: ["vyrobiť bombu", "ako vyrobiť bombu", "zabiť", "ublížiť niekomu", "otráviť"]
out_of_scope:
  detect: ["počasie", "predpoveď počasia", "cena akcie", "napíš mi báseň", "lekárska diagnóza"]
gift_advisor:
  detect: ["darček pre", "darčekový nápad", "darček na"]
comparison:
  detect: ["porovnaj", "alebo", "verzus"]
advice:
  detect: ["mám si kúpiť", "oplatí sa", "ako si vybrať", "ktorý si vybrať"]
```

```yaml
# en.yaml  (the migration target for today's hardcoded tuples + the Gap-2 advice phrase)
language: en
unsafe:
  detect: ["build a bomb", "make a bomb", "poison someone", "kill someone", "hurt someone"]
out_of_scope:
  detect: ["weather", "weather forecast", "stock price", "write me a poem", "homework answer", "medical diagnosis"]
gift_advisor:
  detect: ["gift for", "present for", "gift idea", "gift ideas"]
comparison:
  detect: ["compare", "versus", " vs "]
advice:
  detect: ["should i", "is it worth", "what should i", "how do i choose"]   # Gap-2 phrase added here
```

### Dataclasses (mode_detection.py)

```python
@dataclass(frozen=True)
class _ModeVocabulary:
    mode: str                      # canonical 7-mode enum value
    detect: tuple[str, ...]        # raw phrases (audit preserves these verbatim)
    normalized_detect: tuple[str, ...]   # _normalize_dispatch_text() applied at load

@dataclass(frozen=True)
class _LanguageDetectionConfig:
    language: str                  # normalized key, e.g. "sk"
    vocabularies: dict[str, _ModeVocabulary]   # keyed by mode
```

The `_ModeVocabulary` keeps `detect` (raw) and `normalized_detect` (precomputed `_normalize_dispatch_text`) as PARALLEL tuples, mirroring the existing `_SupportPattern.detect` / `_SupportPattern.normalized_detect` pair (graph.py:279-280). The matcher consumes `normalized_detect` for the substring test and reports the corresponding `detect` entry verbatim as `triggering_keyword` — exactly the strict-zip discipline used by `_match_support_pattern` (graph.py:568-571). See Integration Points 5/6 for the parallel change required on the support side.

### Language resolution (the critical wiring point)

`mode_dispatch` (graph.py:2914) already receives `state`, `runtime`, AND `config`. It MUST resolve the request language using the **same precedence chain that `compile_system_prompt` documents (graph.py:2710-2716)**: language lives in `config["metadata"]` (langgraph merges proxy thread-metadata there), NOT in `runtime.context`. The resolver:

```python
def _resolve_request_language(runtime, config) -> str:
    metadata = (config.get("metadata") if config else None) or {}
    context = runtime.context if runtime is not None else None
    raw = metadata.get("language") or (context.get("language") if isinstance(context, dict) else None) or "sk"
    return _normalize_language_key(raw)   # casefold; map ISO/full-name via existing _ISO_TO_LANGUAGE_NAME inverse + the _TURN1_PREVIEW_INTRO_BY_LANGUAGE key convention
```

The default is `"sk"` — identical to `compile_system_prompt`'s fallback (graph.py:2725) — because live shops are `.sk`. `_normalize_language_key` accepts BOTH ISO-2 codes (`sk`, `cs`, `en`) and full names (`slovak`, `czech`, `english`), mirroring the existing `_TURN1_PREVIEW_INTRO_BY_LANGUAGE` dual-key convention (graph.py:116-128) and reusing `_ISO_TO_LANGUAGE_NAME` (graph.py:2687).

### Detector signature change

`_dispatch_for_query(query, shop_id)` → `_dispatch_for_query(query, shop_id, language)`. The body changes from referencing module-level tuples to loading `_LanguageDetectionConfig` for `language` (with English fallback if a language file is absent — see Failure-Mode F3) and matching against `config.vocabularies[mode].normalized_detect`. The matcher (`_match_static_keyword`, graph.py:586) is UNCHANGED — it already takes a `keywords` tuple; we pass the per-language `detect` tuple instead of the module constant. Priority constants (`_MODE_RECOGNIZER_PRIORITIES`, unsafe=1000, support=800+, oos=600) are UNCHANGED.

### Per-mode detection strategy (deterministic vs. guarded-LLM)

| Mode | Strategy | Rationale |
|---|---|---|
| `unsafe` | **deterministic translated patterns ONLY** (no LLM classifier added) | Operator-control of the refuse boundary; LLM-fallback enum is deliberately 5-of-7 excluding `unsafe` (`decisions/conversational-search-v2-discovery-digest.md` Axis A.1; plan:356 "Whether unsafe remains deterministic-only"). Preserves the one-LLM-call commitment. |
| `support` | **deterministic, per-SHOP YAML detect** (now language-aware, see below) | Already shop-YAML-keyword-only; LLM would over-route. |
| `out_of_scope` | deterministic translated patterns | Static keyword list translates cleanly. |
| `gift_advisor` | deterministic translated patterns | Short phrase set. |
| `comparison` | deterministic translated patterns + shared connectors | `vs`/`versus` are near-universal; `or`-connector regex is language-specific (`alebo`/`nebo`) and goes in the per-language file. |
| `advice` | deterministic translated patterns | Gap-2 phrase added as a vocabulary entry, not a tuple append. |

No mode gets a NEW turn-1 LLM classifier. The existing LLM-fallback path (5-of-7, the `regular_turn`/dispatcher LLM call) is unchanged and still fires only when deterministic detection returns `product_search_fallback` — i.e. the single existing turn-1 LLM call. This preserves the hard one-call commitment (handoff brief §61; plan:376 "Confirm no new second turn-1 LLM call was introduced").

### Per-shop support phrases coexisting with shared language vocabularies

Today support detect lives in `agent/support/{shop_id}.yaml` (graph.py:130, `_load_support_config`:482), English-only. Support is *shop-specific* (URLs, team names), so it stays shop-keyed but gains a **language sub-key**:

```yaml
# support/8760-9189.yaml  (extended, backward-compatible)
support:
  team_name: "Muziker Support"
  patterns:
    order_status:
      detect:                          # EN preserved at top level for back-compat (treated as the en bucket)
        - "order status"
        - "where is my order"
      detect_by_language:              # NEW optional block
        sk: ["kde je moja objednávka", "stav objednávky", "sledovať objednávku"]
        cs: ["kde je moje objednávka", "stav objednávky"]
      ...
```

`_match_support_pattern` (graph.py:565) gains a `language` argument and matches the per-language NORMALIZED detect list if present, falling back to the top-level `detect` (treated as English). This keeps the shop-config file the single owner of shop-specific phrases while the shared modes (oos/gift/comparison/advice/unsafe) live in the language-level files. Two-tier ownership: **language-level files own shop-agnostic vocabularies; shop files own shop-specific support phrases, now language-bucketed.** The precise dataclass + loader change that preserves the strict-zip normalized-match invariant is specified in Integration Points 5/6 — read it before touching `_match_support_pattern`.

### Audit-field preservation

`mode_dispatch` returns `dispatch_rationale_token`, `confidence_signal`, `triggering_keyword`, `verbatim_query` (graph.py:2923-2929). All preserved. Two refinements:

- `triggering_keyword` carries the **raw matched phrase verbatim** (the `detect` entry, e.g. `"vyrobiť bombu"`) — not the normalized form. The dataclass keeps both raw and normalized; the matcher returns the raw.
- `dispatch_rationale_token` gains a language tag so the matched language is auditable. **This is NOT a safe additive-prefix change — it is a mid-segment insertion that shifts existing segment positions, and the implementer MUST treat it as a breaking grammar change.** The live unsafe token is built as `f"unsafe_keyword:{_normalize_dispatch_text(unsafe_keyword)}"` (graph.py:641) → grammar `unsafe_keyword:{phrase}`, with the phrase at **colon-segment index 1**. Inserting language between as `unsafe_keyword:{language}:{phrase}` moves the phrase to **index 2**, breaking any position-based consumer that does `token.split(":")[1]`. The prefix string `unsafe_keyword` is unchanged, so prefix-equality and `startswith` consumers are unaffected; only *position-indexing* consumers break.

  **Required of the implementer (worked example: the safety-relevant unsafe token):** before changing the format, grep ALL `dispatch_rationale_token` consumers — the proxy persistence path (`turn_events_writer.py` → `turn_events_repo.py`), `langgraph_client.py`, `conversation_service.py`, and any analytics/dashboard parser — and classify each as pass-through (opaque string) or position-parsing. Then choose ONE of:

  - **(A) Non-position-breaking grammar (preferred).** Append language as a TRAILING segment so existing indices are stable: `unsafe_keyword:{phrase}:{language}` (phrase stays at index 1, language is the new index 2). Or carry language as a SEPARATE audit field rather than inside the token string. This is the lower-blast-radius choice and is the design's recommendation.
  - **(B) Mid-segment insertion + update consumers.** Use `unsafe_keyword:{language}:{phrase}` only if every position-parsing consumer is found and updated in the same change.

  Exact per-mode token grammars the implementer must pin (whichever of A/B is chosen, apply it uniformly so all modes share one position convention):
  - `unsafe` — current `unsafe_keyword:{normalized_phrase}` → grammar A: `unsafe_keyword:{normalized_phrase}:{language}`.
  - `out_of_scope` — current `static_out_of_scope:{normalized_phrase}` → grammar A: `static_out_of_scope:{normalized_phrase}:{language}`.
  - `gift_advisor` / `comparison` / `advice` — current `{mode}_recognizer:{normalized_phrase}` → grammar A: `{mode}_recognizer:{normalized_phrase}:{language}`.
  - `support` — current `support_pattern:{pattern_name}` (the pattern name, not a phrase) → grammar A: `support_pattern:{pattern_name}:{language}` (the `{pattern_name}` segment stays at index 1).

  The audit goal — "which language fired" must be recoverable post-hoc (Failure-Mode F7) — is satisfied by either grammar; the choice is purely about not silently breaking a position-indexing consumer. `verbatim_query` already preserves the raw query untouched.

### Positive tradeoff (what this shape gives up)

This shape spends **a config-file-per-language maintenance surface and a YAML-load + dict-lookup per dispatch** to buy a single extensible abstraction that closes all five collapsing modes AND the English advice gap under one mechanism. The cost is real: adding a language is now a file-authoring task (someone must translate the vocabularies — a human-judgement step the design cannot eliminate), and the detector now has a load path that can fail (missing/malformed YAML) where the hardcoded tuples could not. We accept this over the lower-blast-radius keyword-extension because the operator decision is binding (generalized layer required) AND because the keyword-extension shape cannot express the language axis without exactly the ad-hoc tuple proliferation the operator forbade. We do NOT buy runtime language *detection* (we trust the proxy-supplied `language`) — that is a deliberate non-goal (see Rejected Alternatives).

## Data-Flow Framing

Tracing the (source → transform → sink) path of the language signal and the detection decision. This is the design spine.

| # | Source | Transform | Sink |
|---|---|---|---|
| DF-1 | Client `POST /initiate {"language":"sk"}` | `InitiateRequest.language` (proxy `schemas/conversation_schema_v2.py:34`) | `conversation_service.initiate(language=...)` |
| DF-2 | `conversation_service.initiate` arg | written to thread metadata field 3 (`conversation_service.py:179` `"language": language`) | langgraph thread metadata |
| DF-3 | thread metadata | langgraph-api merges metadata into `config["metadata"]` (NOT into `runtime.context` — documented at `compile_system_prompt` graph.py:2710-2716) | agent run config |
| DF-4 | `config["metadata"]["language"]` | **NEW** `_resolve_request_language(runtime, config)` in `mode_dispatch` (graph.py:2914) | normalized language key (e.g. `"sk"`) |
| DF-5 | normalized language key + `verbatim_query` | **NEW** `_dispatch_for_query(query, shop_id, language)` (graph.py:631) loads `_LanguageDetectionConfig` | per-language `_ModeVocabulary` set |
| DF-6 | `_ModeVocabulary.normalized_detect` + `_normalize_dispatch_text(query)` | `_match_static_keyword` (graph.py:586) / `_match_support_pattern` (graph.py:565, now language-aware) | `_DispatchMatch` (mode, priority, raw `triggering_keyword`) |
| DF-7 | `_DispatchMatch` | `_resolve_dispatch_match` priority arbitration (graph.py:604) | `state["mode"]` + audit fields (graph.py:2923) |
| DF-8 | `state["mode"]` | `dispatch_route` (graph.py:2932) | `unsafe_deflect` / `support_deflect` / `out_of_scope_deflect` / `regular_turn` |

Branch point: DF-5 forks on `language`. If a language file is absent, the loader falls back to `en.yaml` (Failure-Mode F3) — the dispatch still produces a decision; it just uses English vocabulary. Data-flow sink for audit (`triggering_keyword`, `dispatch_rationale_token` with language tag, `verbatim_query`) is the `turn_classification` event emitted by the deflect nodes (`_emit_deflection_classification`, graph.py:2866).

## Failure-Mode Framing

Enumerating (triggering condition → resulting wrong/absent behavior) pairs the implementer MUST handle.

- **F1 — language metadata absent / empty.** Trigger: `config["metadata"]` has no `language` (older thread, direct LangGraph invocation, test harness omission). Behavior without handling: `KeyError`/`None` → crash or null match. Required: resolver defaults to `"sk"` (matches `compile_system_prompt`:2725). Consequence of wrong default: English `unsafe` would still match (en is loadable), but Slovak prompts would be tested against Slovak vocab — correct for the live shop base.
- **F2 — language code unrecognized (e.g. `fr`, or a full name `"French"`).** Trigger: proxy forwards a language with no YAML file. Behavior: must NOT crash. Required: fall back to `en.yaml`. Consequence: a French unsafe prompt would bypass — accepted limitation, surfaced in Unknowns; the design does NOT claim coverage beyond authored languages.
- **F3 — language YAML file missing or malformed.** Trigger: deploy ships `sk.yaml` deleted or with bad YAML. Behavior: `_load_language_config` must (a) fall back to English for a missing file, and (b) for a malformed file, FAIL LOUDLY at startup via a `_validate_all_language_configs()` called from `create_graph` (mirroring `_validate_all_support_configs`, graph.py:498). Rationale: a silently-broken safety vocabulary is the worst outcome; fail-fast at load beats fail-open at dispatch.
- **F4 — unsafe phrase present but only in a non-authored language.** Trigger: Polish unsafe prompt, no `pl.yaml` safety section. Behavior: bypass (same as F2). This is the residual safety surface; the design narrows it from "all non-English" to "all non-authored-language", and the test matrix asserts SK+CS+EN unsafe coverage explicitly.
- **F5 — substring false-positive across languages.** Trigger: a Slovak detect phrase is a substring of an unrelated word, or an English phrase accidentally matches Slovak text. Behavior: over-routing (e.g. spurious `out_of_scope`). Mitigation: matching is keyed to the resolved language file ONLY (we do NOT union all languages), so cross-language false-positives are structurally prevented. Within a language, the existing word-boundary padding in `_match_static_keyword` (graph.py:587, `f" {normalized_query} "`) is preserved.
- **F6 — unsafe NOT first.** Trigger: a query matches both an unsafe phrase and a support/oos phrase. Behavior if mis-ordered: refuse boundary leaks. Required: unsafe short-circuit returns BEFORE any other match is even computed (see `## Safety Short-Circuit Ordering`). This is the load-bearing invariant.
- **F7 — audit field loses language or raw phrase.** Trigger: refactor normalizes `triggering_keyword`, OR a position-indexing token consumer mis-reads the language tag after the grammar change. Behavior: post-hoc audit cannot tell which language/phrase fired. Required: `triggering_keyword` = raw verbatim phrase; `dispatch_rationale_token` carries the language segment under ONE pinned position convention (Audit-field preservation, grammar A/B), and the consumer sweep confirms no position-parser silently mis-reads it.

## Configuration-Surface Framing

Enumerating the named, externally-settable values that change detection behavior.

- **CS-1 — `mode_detection/{lang}.yaml` files.** The primary surface. Adding a language = adding a file. Adding/removing a phrase for a mode = editing a `detect` list. This is where the Gap-2 advice phrase (`"how do i choose"`) is added — a one-line edit to `en.yaml`'s `advice.detect`, under the abstraction, satisfying the operator's "same abstraction not a tuple append" constraint (plan:364-365).
- **CS-2 — `support/{shop_id}.yaml` `detect_by_language` blocks.** Per-shop, per-language support phrases. Backward-compatible: top-level `detect` is the implicit English bucket. The loader precomputes a parallel per-language normalized list (Integration Points 5/6).
- **CS-3 — request `language` field** (`POST /initiate`). The runtime selector. Already a configuration surface (proxy schema); this design newly *consumes* it in dispatch. Values: ISO-2 codes or full names; normalized by `_normalize_language_key`.
- **CS-4 — priority constants** (`_MODE_RECOGNIZER_PRIORITIES` graph.py:269; unsafe=1000, support=800+priority, oos=600). UNCHANGED by this design; listed because they govern arbitration when multiple modes match within a language. They are NOT moved into YAML (keeping cross-language priority invariant is safer than per-file priority drift — see Rejected Alternatives R4).
- **CS-5 — default language** (`"sk"`, in `_resolve_request_language`). A single constant; changing it changes the fallback for metadata-absent threads. Pinned to match `compile_system_prompt`.
- **CS-6 — `_normalize_language_key` alias table.** The ISO/full-name → canonical-key mapping. Reuses `_ISO_TO_LANGUAGE_NAME` (graph.py:2687) and the `_TURN1_PREVIEW_INTRO_BY_LANGUAGE` dual-key convention.
- **CS-7 — `dispatch_rationale_token` grammar (position convention).** The chosen A/B grammar (Audit-field preservation) is itself a configuration surface: it is a contract between the agent emitter and every token consumer. Changing it later re-incurs the consumer-sweep obligation. Pinned once, applied uniformly across all modes.

## Safety Short-Circuit Ordering

**The unsafe match MUST win BEFORE any support/oos/gift/comparison/advice match is computed.** This is already the structure in `_dispatch_for_query` (graph.py:636-644): the unsafe keyword check is the FIRST statement and returns immediately on a hit, before the `matches: list` is even allocated (graph.py:646). The design PRESERVES this exact ordering — only the vocabulary source changes from `_UNSAFE_KEYWORDS` (module constant) to `language_config.vocabularies["unsafe"].normalized_detect`. (This ordering is verified correct and is NOT altered by this revision.)

Explicit required ordering inside `_dispatch_for_query(query, shop_id, language)`:

1. Resolve `language_config = _load_language_config(language)` (English fallback if absent).
2. **`unsafe` check FIRST** — `_match_static_keyword(language_config.vocabularies["unsafe"].normalized_detect, normalized_query)`. On hit: `return _DispatchMatch(mode="unsafe", priority=1000, dispatch_rationale_token=<unsafe token per the pinned grammar A: f"unsafe_keyword:{normalized_phrase}:{language}">, triggering_keyword=raw_phrase)` — **immediately, with 0 LLM calls**, before any other vocabulary is consulted.
3. Only if no unsafe hit: compute support (priority 800+), out_of_scope (600), gift_advisor (300), comparison (200), advice (100) matches and arbitrate via `_resolve_dispatch_match` (graph.py:604).
4. Fallback: `product_search_fallback` → `regular_turn` (the single existing LLM call).

This guarantees: a Slovak `unsafe` prompt → `dispatch_route` (graph.py:2940) returns `"unsafe_deflect"` → `unsafe_deflect` (graph.py:3000) emits the refusal with `llm_call_count=0` (`_deflection_update`, graph.py:2900) and populated audit fields (`_emit_deflection_classification`, graph.py:2866). No lower-priority match can preempt it because unsafe returns before they are computed. The 0-LLM-call property holds because the deflect path never reaches `regular_turn`. (Closes plan C-11, line 55.)

## Language-Propagation Proof

**Verdict: NO proxy edit is required. `language` already propagates end-to-end from client to agent dispatch-time runtime. PROVEN below.** (Plan Gap-7 change surface, plan:343-346, flags the proxy edit as conditional — "only if language is not already visible … prove before editing proxy"; this section is that proof.)

1. Client → proxy: `InitiateRequest.language: str` accepted at `POST /initiate` [verified: conversational-search/conversational-proxy/app/schemas/conversation_schema_v2.py:34; conversation_schema.py:30].
2. Router → service: `conversation_service.initiate(language=body.language, ...)` [verified: conversational-search/conversational-proxy/app/router/v1/conversation_router_v2.py:41; conversation_router.py:35].
3. Service → thread metadata: `language` is written as field 3 of the 14-field Channel-1 thread-metadata schema [verified: conversational-search/conversational-proxy/app/service/conversation_service.py:179 `"language": language`].
4. Thread metadata → agent run config: langgraph-api merges thread metadata into `config["metadata"]` (i.e. `run_metadata`) but does NOT merge it into `runtime.context`. This is documented verbatim in the agent [verified: conversational-search/src/conversational_search/agent/graph.py:2710-2716 (compile_system_prompt docstring)].
5. Agent already reads it: `compile_system_prompt` resolves `_raw_language = run_metadata.get("language") or context.get("language") or "sk"` [verified: conversational-search/src/conversational_search/agent/graph.py:2725]. The `ContextSchema` declares `language: str` [verified: conversational-search/src/conversational_search/agent/state.py:15] and `canonical_enums.py` documents it as "thread-create; proxy /initiate sets, agent compile_system_prompt reads" [verified: conversational-search/src/conversational_search/agent/canonical_enums.py (language envelope comment)].
6. `mode_dispatch` already has the handles: its signature is `mode_dispatch(state, runtime, config)` [verified: conversational-search/src/conversational_search/agent/graph.py:2914-2918] — it already receives both `runtime` and `config`, so it can call `_resolve_request_language(runtime, config)` with zero plumbing changes. The ONLY gap is that `_dispatch_for_query(query, shop_id)` [verified: conversational-search/src/conversational_search/agent/graph.py:631] does not take `language`.

**Conclusion:** the entire change is agent-side. The proxy already forwards `language` correctly. Do NOT edit the proxy.

## Test Matrix

Modes × languages. Each cell asserts: resolved `mode`, `llm_call_count`, and audit fields (`triggering_keyword` raw-verbatim, `dispatch_rationale_token` with language tag). EN is the backward-compatibility baseline; SK/CS are the new coverage. (Plan testing matrix, plan:369-376.)

| Mode | English (baseline) | Slovak | Czech |
|---|---|---|---|
| `unsafe` | `"build a bomb"` → mode=unsafe, llm=0 | `"ako vyrobiť bombu"` → mode=unsafe, llm=0 (PRIORITY — closes `iss_3712bb402a94`) | `"jak vyrobit bombu"` → mode=unsafe, llm=0 |
| `support` | `"where is my order"` → mode=support, llm=0, token=`support_pattern:order_status:en` | `"kde je moja objednávka"` → mode=support, llm=0 | `"kde je moje objednávka"` → mode=support, llm=0 |
| `out_of_scope` | `"weather"` → mode=out_of_scope, llm=0 | `"aké je dnes počasie"` → mode=out_of_scope, llm=0 | `"jaké je dnes počasí"` → mode=out_of_scope, llm=0 |
| `gift_advisor` | `"gift for my dad"` → mode=gift_advisor | `"darček pre otca"` → mode=gift_advisor | `"dárek pro tátu"` → mode=gift_advisor |
| `comparison` | `"compare X vs Y"` → mode=comparison | `"porovnaj X a Y"` / `"X alebo Y"` → mode=comparison | `"porovnej X a Y"` → mode=comparison |
| `advice` | `"how do I choose a vacuum"` → mode=advice (Gap-2; previously collapsed) | `"ako si vybrať vysávač"` → mode=advice | `"jak si vybrat vysavač"` → mode=advice |
| (regression) `product_search` | `"red running shoes"` → mode=product_search, llm=1 | `"červené bežecké topánky"` → mode=product_search, llm=1 | `"červené běžecké boty"` → mode=product_search, llm=1 |

The `support` row's `token=support_pattern:order_status:en` cell uses grammar A (trailing language segment); if the implementer chooses grammar B the cell becomes `support_pattern:en:order_status`. The matrix asserts whichever grammar was pinned — the test author reads the chosen convention from the code, not the other way around.

Additional ordering assertions (Safety Short-Circuit): a Slovak prompt containing BOTH an unsafe phrase and an oos phrase resolves to `unsafe` (proves F6). A language-absent (no metadata) unsafe English prompt still refuses (proves F1 default).

Phrase note: the Slovak/Czech phrases above are *illustrative* and MUST be reviewed by a native speaker before ship (Unknowns U2). The implementer authors the YAML; phrase correctness is a human-judgement gate, not a code property.

## Assumptions

- **A1 — the proxy always forwards a usable `language`.** If wrong: F1 default (`"sk"`) catches it; dispatch still functions, just on the Slovak vocabulary. Verified that the field exists and is written (Language-Propagation Proof); NOT verified that every production caller populates it non-empty.
- **A2 — the 7-mode canonical enum and priority ordering are stable.** Per `decisions/conversational-search-v2-discovery-digest.md`, the canonical list is `product_search, gift_advisor, comparison, advice, support, out_of_scope, unsafe`, and LLM-fallback is 5-of-7 (excludes unsafe+support). If wrong (enum renamed): the YAML mode keys and `dispatch_rationale_token` prefixes would need a coordinated rename. Stated as a hard downstream constraint.
- **A3 — `_match_static_keyword`'s substring+boundary semantics are correct for Slavic morphology.** Slovak/Czech are inflected; `"objednávka"` vs `"objednávku"` differ by case ending. The `detect` lists must include inflected variants OR rely on substring matching the stem. If wrong: under-matching (a real inflection misses). Mitigation: author stems where safe (`"objednávk"` substring-matches both), but this is a phrase-authoring judgement, flagged for the safety-critical implementer.
- **A4 — output localization is genuinely separable.** The deflect-node response prose (e.g. `out_of_scope_deflect` English text, graph.py:2985) is OUT of scope here (Gate A1 / Subtask 8). This design only sets `mode`; the rendered prose is a separate concern. If wrong (the two were coupled): a Slovak unsafe refusal would route correctly but render English prose — acceptable for THIS subtask's boundary, owned by Subtask 8.
- **A5 — `dispatch_rationale_token` has no current position-parsing consumer in the proxy persistence path.** Verified that `turn_events_writer.py` carries the token as an opaque `str | None` field (`turn_events_writer.py:55`) and `turn_events_repo.py` inserts it verbatim (`turn_events_repo.py:38`); `langgraph_client.py:355` and `conversation_service.py:71` also pass it through by key. If wrong (an analytics/dashboard parser NOT in this repo splits-and-indexes the token): grammar A (trailing language segment) protects index 1; grammar B would break it. This is the residual risk the implementer's consumer sweep must close before choosing B.

## Unknowns

- **U1 — correct Slovak/Czech detection phrases.** The phrases in the YAML examples and test matrix are illustrative. Resolved by: native-speaker review of the authored `sk.yaml`/`cs.yaml` before ship (a human gate, owned by the implementer + reviewer, NOT this design).
- **U2 — inflection coverage strategy** (stem-substring vs. enumerated variants). Resolved by: the safety-critical implementer's choice during YAML authoring, informed by A3; the design permits either since `_match_static_keyword` does plain substring.
- **U3 — out-of-repo `dispatch_rationale_token` consumers.** The in-repo consumers are all pass-through (A5); whether any analytics dashboard or downstream BI job outside this repo position-parses the token is not knowable from this codebase. Resolved by: the implementer's consumer grep + a decision to default to grammar A (trailing segment), which is position-safe for the known consumers regardless.

## Integration Points

Files the implementer (Subtask 5) modifies, in order:

1. **NEW `src/conversational_search/agent/mode_detection.py`** — dataclasses (`_ModeVocabulary`, `_LanguageDetectionConfig`), `_load_language_config`, `_validate_all_language_configs`, `_resolve_request_language`, `_normalize_language_key`. First, because everything below imports it. `_ModeVocabulary` precomputes `normalized_detect` at load (`tuple(_normalize_dispatch_text(item) for item in detect)`), mirroring `_validate_support_config:421`.
2. **NEW `src/conversational_search/agent/mode_detection/en.yaml`, `sk.yaml`, `cs.yaml`** — migrate today's hardcoded tuples into `en.yaml` (exact phrases from graph.py:238-268) + add the Gap-2 advice phrase; author SK/CS (U1 gate).
3. **`graph.py:631` `_dispatch_for_query`** — add `language` param; replace module-tuple references with `_load_language_config(language).vocabularies[...]`; preserve unsafe-first ordering (graph.py:636) and audit-field shape; add the language segment to `dispatch_rationale_token` under the pinned grammar (Audit-field preservation, grammar A recommended). The live unsafe token construction at graph.py:641 (`f"unsafe_keyword:{_normalize_dispatch_text(unsafe_keyword)}"`) is the worked example — change it to grammar A `f"unsafe_keyword:{_normalize_dispatch_text(unsafe_keyword)}:{language}"`.
4. **`graph.py:2914` `mode_dispatch`** — call `_resolve_request_language(runtime, config)`; pass `language` into `_dispatch_for_query`. (No signature change to `mode_dispatch` — it already has `runtime` + `config`.)
5. **`graph.py:565` `_match_support_pattern` + the `_SupportPattern` dataclass (graph.py:276-284) + `_validate_support_config` (graph.py:399) + `_load_support_config` (graph.py:482).** This is a coupled four-point change, NOT a single signature edit. Today the matcher iterates `zip(pattern.detect, pattern.normalized_detect, strict=True)` (graph.py:568-571), matching the NORMALIZED keyword (`normalized_keyword in normalized_query`, line 571) while reporting the RAW `raw_keyword` (line 572). To make support language-aware while PRESERVING that strict-zip-normalized invariant:
   - **`_SupportPattern` (graph.py:277-284):** add two PARALLEL per-language fields alongside today's `detect` / `normalized_detect` — e.g. `detect_by_language: dict[str, tuple[str, ...]]` (raw) and `normalized_detect_by_language: dict[str, tuple[str, ...]]` (precomputed). Each language's two tuples MUST be the same length so the strict-zip holds per language.
   - **`_validate_support_config` (graph.py:399-445):** where it builds `normalized_detect = tuple(_normalize_dispatch_text(item) for item in detect)` (line 421), add a parallel block that reads the optional `detect_by_language` mapping, validates each language's list is a non-empty list of non-empty strings (same checks as lines 416-420), and precomputes `normalized_detect_by_language[lang] = tuple(_normalize_dispatch_text(item) for item in raw_list)`. Construct the `_SupportPattern` (graph.py:439-445) with both new fields populated (empty dicts when the block is absent — back-compat).
   - **`_load_support_config` (graph.py:482):** no body change beyond what flows through `_validate_support_config`; the mtime-keyed cache (graph.py:486-494) already re-validates on file change, so the precomputed per-language normalized tuples are cache-correct.
   - **`_match_support_pattern` (graph.py:565):** add a `language` param. Select the per-language pair when `pattern.normalized_detect_by_language.get(language)` is present, ELSE fall back to the top-level `(detect, normalized_detect)` English pair. Keep the SAME `zip(raw, normalized, strict=True)` loop (line 568) and the SAME normalized-match test (line 571) — match NORMALIZED phrases, report RAW. The strict-zip MUST be over the selected language's parallel raw+normalized tuples, never a raw list from one language zipped against normalized from another.
   - Update its two callers: `_dispatch_for_query` (graph.py:650) and `support_deflect` (graph.py:2956) to pass `language`.
6. **`support/8760-9189.yaml`** — add `detect_by_language` blocks under each pattern (backward-compatible; top-level `detect` stays as the English bucket). The implementer authors only RAW phrases in YAML; the parallel `normalized_detect_by_language` is precomputed by the loader (point 5) — the YAML never carries normalized forms.
7. **`graph.py:3282` `create_graph`** — call `_validate_all_language_configs()` at graph build (mirroring `_validate_all_support_configs`) for fail-fast on malformed YAML (F3).
8. **The module-level tuples (graph.py:238-268)** — removed (migrated to `en.yaml`) OR kept as the English-fallback default if the implementer prefers a code-resident baseline; either is acceptable, but DO NOT leave them as the live source once YAML is wired (dead-code / drift risk).

Second-order surfaces:
- **`dispatch_rationale_token` consumers — token grammar is a BREAKING change, not additive-prefix.** The token gains a language segment that, in the naive `unsafe_keyword:{language}:{phrase}` form, shifts the phrase from colon-index 1 to index 2 (the live unsafe token at graph.py:641 puts the phrase at index 1). Verified in-repo consumers and their parse style: `turn_events_writer.py:55` (opaque `str | None` field — pass-through, position-safe), `turn_events_repo.py:38` (inserts verbatim — pass-through), `langgraph_client.py:355` (key pass-through), `conversation_service.py:71` (key pass-through). ALL known in-repo consumers are pass-through, so grammar A (trailing language segment, `unsafe_keyword:{phrase}:{language}`) is fully back-compatible AND grammar B would also not break these — but any out-of-repo position-parsing analytics consumer (U3) WOULD break under B. Implementer obligation: grep all consumers (start with `turn_events_writer.py`), confirm pass-through, default to grammar A. (Connection-graph edge: `graph.py` `_dispatch_for_query` → `turn_events` schema, a behavioral-contract edge via the rationale-token string format.)
- **No graph-topology change.** `create_graph` node set and edges (graph.py:3293-3343) are unchanged; `dispatch_route` (graph.py:2932) is unchanged (it reads `state["mode"]`, which is mode-string-agnostic). No new connection-graph edge of the compile-time-import kind beyond `graph.py` → new `mode_detection.py` module.

## Migration Constraints

- **One LLM call on turn 1 — preserved.** No new turn-1 LLM classifier is introduced for any mode. Deterministic detection short-circuits to deflect nodes (0 LLM) or falls through to the single existing `regular_turn` LLM call (handoff brief §61; plan:376).
- **Audit fields preserved + extended.** `triggering_keyword`, `verbatim_query`, `dispatch_rationale_token`, `confidence_signal` all still emitted (graph.py:2923-2929). The token gains a language segment, but this is a **position-shifting grammar change, not a no-op additive-prefix** — the prefix string is unchanged but a naive mid-insertion moves later segments. The implementer pins ONE grammar (A recommended: trailing language segment) and sweeps token consumers before the change (Audit-field preservation; Integration Points second-order surfaces).
- **Backward-compatible with English baseline.** `en.yaml` is the migration of today's exact tuples; the regression row in the test matrix asserts unchanged English behavior. Support `detect_by_language` is optional — existing shop YAMLs work untouched (the loader populates empty per-language dicts when the block is absent).
- **No proxy edit.** Proven; the proxy already forwards `language`. The proxy nested-git-repo topology (handoff brief §188) is therefore not touched by this subtask.
- **Fail-fast on config error.** Malformed language YAML fails at `create_graph` (F3), not silently at dispatch — a deliberate fail-fast choice for a safety-bearing vocabulary.

## Rejected Alternatives

- **R1 — Quick keyword extension (append Slovak/Czech phrases to the existing module-level tuples).** Rejected on the binding operator decision (plan:364) AND on the axis of *extensibility*: the tuples have no language axis, so multilingual support would mean either unioning all languages into one tuple (causing cross-language false-positives, F5) or proliferating `_UNSAFE_KEYWORDS_SK`, `_UNSAFE_KEYWORDS_CS`, … — exactly the five ad-hoc hardcoded-tuple edits the operator forbade. Differs from the chosen shape on the **configuration-surface** axis (no externalized per-language file).
- **R2 — Add a non-English LLM safety classifier for `unsafe`.** Rejected on the **sync/LLM-call-budget** axis: it would add a second turn-1 LLM call (violating the hard commitment) OR replace the deterministic refuse with a probabilistic one (weakening operator-control of the refuse boundary, which `decisions/conversational-search-v2-discovery-digest.md` Axis A.1 deliberately keeps keyword-only at 5-of-7). The safety mode must stay deterministic (plan:356, "Whether unsafe remains deterministic-only"). Differs on **failure-semantics** (fail-deterministic vs. fail-probabilistic).
- **R3 — Runtime language *detection* (auto-detect the query language, e.g. langdetect/fastText) instead of trusting proxy `language`.** Rejected on the **data-flow-source** axis: the proxy already supplies an authoritative `language` (Language-Propagation Proof), so detection would add a dependency + latency + a misdetection failure mode (short queries detect poorly) for a signal we already have. Differs on **coupling locus** — chosen shape couples to the proxy-supplied field; this would couple to a new ML dependency.
- **R4 — Move priority constants into the per-language YAML.** Rejected on the **invariant-locus** axis: cross-language priority MUST be invariant (unsafe always beats support always beats oos), and per-file priorities would let one language's file drift and silently re-order arbitration. Keeping `_MODE_RECOGNIZER_PRIORITIES` in code (graph.py:269) places the priority invariant in one owner. Differs on **ownership** (single code-owner vs. per-file multi-owner).
- **R5 — One combined `mode_detection.yaml` keyed `{mode: {language: [...]}}` instead of one file per language.** Rejected on the **payload-density / reversibility** axis: a single dense file couples all languages into one edit surface (a Slovak phrase edit risks an English diff; merge conflicts across language authors), and is harder to add/remove a language atomically. One-file-per-language is cheaper to evolve and review per-language. This is a genuine structural alternative (same boundary, different file granularity), not a reskin — the rejection is about edit-locality, not naming.
- **R6 — Carry language ONLY inside the `dispatch_rationale_token` string (mid-segment) and let consumers re-parse.** Rejected on the **in-band vs. side-channel** axis combined with the consumer-breakage cost: encoding language as `unsafe_keyword:{language}:{phrase}` shifts the phrase's colon-index and silently breaks any position-parsing consumer. The chosen approach pins a position-safe grammar (trailing segment) — or, if richer structure is ever needed, carries language as a separate audit field. Differs from R6 on **payload-density** (one overloaded position-fragile string vs. a stable-position or separate-field encoding).

## Verification

**Exercised:**
- Re-grounded on the REAL plan `docs/v2-design/plan-v2-final-state-gap-closure.md` (1018 lines): Subtask 2 (:568), Gap 7 (:334-382), checklist C-10/C-11/C-12 (:54-56). Confirmed the design substance (data shape, code surfaces, safety ordering, language-propagation proof, test matrix, migration constraints) matches the plan's expected output for Subtask 2 (:584) and its 7-question architecture step (:350-358).
- Read and cited at file:line every dispatch surface named in the delegation: `_dispatch_for_query` (631), `mode_dispatch` (2914), `dispatch_route` (2932), `_match_static_keyword` (586), `_match_support_pattern` (565), `_SupportPattern`/`_SupportConfig` (276-294), `_validate_support_config` (399-445), `_load_support_config` (482), `_UNSAFE_KEYWORDS`/`_OUT_OF_SCOPE_KEYWORDS`/`_ADVICE_KEYWORDS` (238-268), `create_graph` (3282), `compile_system_prompt` (2710-2716), `_ISO_TO_LANGUAGE_NAME` (2687), `_TURN1_PREVIEW_INTRO_BY_LANGUAGE` (116).
- Verified the support strict-zip invariant precisely: `_match_support_pattern` iterates `zip(pattern.detect, pattern.normalized_detect, strict=True)` (graph.py:568-571), matches normalized (line 571), reports raw (line 572); `_validate_support_config` precomputes `normalized_detect` (line 421). This grounds the Integration-Points-5 four-point coupled change.
- Verified the live unsafe token grammar: `f"unsafe_keyword:{_normalize_dispatch_text(unsafe_keyword)}"` (graph.py:641) → phrase at colon-index 1; mid-insertion of language shifts it to index 2.
- Verified `dispatch_rationale_token` in-repo consumers are all pass-through: `turn_events_writer.py:55` (opaque `str | None`), `turn_events_repo.py:38`, `langgraph_client.py:355`, `conversation_service.py:71`. No in-repo position-parser found.
- Proved the language-propagation path end-to-end through proxy (`conversation_schema_v2.py:34`, `conversation_router_v2.py:41`, `conversation_service.py:179`) and agent (`state.py:15`, `graph.py:2710-2716`, `:2725`), establishing NO proxy edit is needed.
- Confirmed the unsafe short-circuit is already structurally first in `_dispatch_for_query` (graph.py:636-644) and that the deflect path emits `llm_call_count=0` (`_deflection_update`, graph.py:2900). This ordering is verified correct and PRESERVED unchanged.

**Not exercised, and why:**
- Did not run the agent live or execute any test from the matrix — this is a design pass, no code edits (delegation constraint); the test matrix is a spec for Subtask 5, not executed here.
- Did not author the actual Slovak/Czech phrase lists — phrase correctness is a native-speaker human gate (U1), out of scope for the architecture decision.
- Did not enumerate out-of-repo `dispatch_rationale_token` consumers (analytics/BI) — not knowable from this codebase (U3); flagged for the implementer's consumer sweep, mitigated by recommending position-safe grammar A.

## Revision Log (Round 2)

Pre-flight-gate returned `request-changes` before Subtask-5 (safety-critical implementer) consumption. The safety short-circuit ordering PASSED clean and is verified correct — it was NOT disturbed. Three substantive defects + one low note fixed:

- **DEFECT 1 (HIGH — false premise retracted).** The round-1 doc claimed `docs/v2-design/plan-v2-final-state-gap-closure.md` did NOT exist and reconstructed the spec from secondary sources (an interpretation note at the head, Unknown U1, and a Verification "did not read the absent plan" line). The plan DOES exist (1018 lines, authoritative). Re-grounded on it: read Subtask 2 (:568), Gap 7 (:334-382), checklist C-10/C-11/C-12 (:54-56); confirmed the design substance matches the real spec. Removed the false head-matter interpretation note (replaced with a positive Grounding note citing the real plan), deleted Unknown U1 (the absent-plan unknown; remaining unknowns renumbered U1-U3), removed the "Did not read the absent plan file" Verification line, and added the real plan as the first Knowledge-Consulted citation plus inline `plan:NNN` citations at Problems-to-Solve, the per-mode strategy table, the test matrix, and the Language-Propagation Proof.
- **DEFECT 2 (MEDIUM — per-language support normalization specified).** Integration Points 5/6 previously described the `detect_by_language` YAML block and the `_match_support_pattern` signature change but omitted the precompute-parallel-normalized requirement. Rewrote Integration Point 5 as an explicit FOUR-point coupled change (`_SupportPattern` dataclass at graph.py:277-284 gains parallel `detect_by_language` + `normalized_detect_by_language` fields; `_validate_support_config` at graph.py:399-445 precomputes the per-language normalized tuples mirroring line 421; `_load_support_config` cache is already correct; `_match_support_pattern` at graph.py:565 selects the per-language pair and keeps the SAME `zip(raw, normalized, strict=True)` loop matching NORMALIZED, reporting RAW). Stated the strict-zip-per-language invariant explicitly and that YAML carries only RAW phrases (normalized precomputed by the loader).
- **DEFECT 3 (MEDIUM — token change reframed as breaking mid-segment insertion).** The round-1 doc framed the token change as "additive language segment, prefix unchanged" (Audit-field preservation, Migration Constraints, second-order surfaces). Corrected: the live unsafe token `unsafe_keyword:{phrase}` (graph.py:641) puts the phrase at colon-index 1, and inserting language mid-string shifts it to index 2 — breaking any position-based consumer. Specified the EXACT new token grammar per mode under a pinned position convention; offered grammar A (trailing language segment, position-safe, recommended) vs. grammar B (mid-insertion + update consumers); made the safety-relevant unsafe token the worked example. Verified the in-repo proxy consumers (`turn_events_writer.py:55` et al.) are pass-through-safe, and instructed the implementer to grep all token consumers (esp. `turn_events_writer.py`) before changing the format. Added Assumption A5, Unknown U3 (out-of-repo consumers), Configuration-Surface CS-7 (token grammar as a contract), and Rejected Alternative R6 (token-only mid-segment encoding).
- **LOW (U4 reconciled).** Round-1 Unknown U4 asked whether `iss_3712bb402a94` exists in the live issue queue (a session query returned 0 records). The orchestrator confirms the handoff brief references it as filed (med) and the brief's description is sufficient spec. Reconciled rather than left open: U4 was dropped from Unknowns (the issue is treated as a known med-severity spec input, cited at Problems-to-Solve #1 and the test-matrix unsafe row); no design decision depended on the live-queue record.

Pre-emission self-audit: 42 citations verified, 18 sections present, 4 contradictions checked.
Findings emission self-check: 3 discoveries, 3 emissions.
