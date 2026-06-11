Subtasks 10 and 11 must validate every affected turn-1 prompt from a fresh cache MISS, then prove at least the cacheable product-search path replays byte-identical payload on a fresh-thread HIT; mode-specific takeover prompts currently remain MISS-only when their agent prompt fingerprint differs from the proxy's mode-none fingerprint. [Verified: .agent_context/logs/bash_researcher_20260610203309_y3ij.log:~93] [Verified: .agent_context/logs/bash_researcher_20260610203424_zg4d.log:~29]

## Binding Cache Facts

The proxy cache key is the SHA-256 of `shop_id`, normalized query, `language`, and `prompt_fingerprint`; `language` is part of the hash input. [Verified: conversational-search/conversational-proxy/app/service/_cache_key.py (compute_cache_key)] The live lookup and MISS-write call sites pass `shop_id=tracker_id`, `normalized_query=prompt`, `language=metadata.get("language") or "sk"`, and the computed prompt fingerprint into `compute_cache_key`. [Verified: conversational-search/conversational-proxy/app/service/conversation_service.py (compute_cache_key)]

The database table stores `cache_key`, `tracker_id`, `prompt_fingerprint`, payload, TTL fields, hit count, and the later `query_text` column; it does not store `language` as a separate column. [Verified: conversational-search/conversational-proxy/alembic/versions/20260527040000_0003_v2_create_turn1_signature_cache.py (upgrade)] [Verified: conversational-search/conversational-proxy/alembic/versions/20260527040001_0004_v2_add_query_text.py (upgrade)] Therefore `DELETE ... WHERE tracker_id='8760-9189' AND query_text='<exact prompt>'` is sufficient to force a MISS for that literal prompt across all language-specific hash variants; it is intentionally overbroad for the same prompt text, not underbroad. [Inferred: from conversational-search/conversational-proxy/app/service/_cache_key.py (compute_cache_key) and conversational-search/conversational-proxy/alembic/versions/20260527040001_0004_v2_add_query_text.py (upgrade)] If a validator needs a single language's row only, it must compute the exact `cache_key` for that language and delete by `(tracker_id, cache_key)`; there is no `language` column to add to the SQL predicate. [Inferred: from conversational-search/conversational-proxy/alembic/versions/20260527040000_0003_v2_create_turn1_signature_cache.py (upgrade)]

The proxy process must carry `CONVERSATIONAL_CACHE_DATABASE_URL=postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy`; without it, the SSE still says `cache.status=MISS` but lookup/upsert are skipped and no HIT is possible. [Verified: .agent_context/logs/bash_researcher_20260610203229_emq3.log:~7] [Verified: .agent_context/logs/bash_researcher_20260610203159_jl5e.log:~21]

## Validation Query Set

Use `language=cs` for Czech in API calls unless a later patch adds `cz` as an alias; current agent localization tables use `cs` and `czech`, while unknown language keys fall back to English. [Verified: conversational-search/src/conversational_search/agent/graph.py (_t)] [Verified: conversational-search/src/conversational_search/agent/mode_detection.py (_normalize_language_key)]

| ID | Prompt string | Initiate language | Validates | Required assertions | Cache leg |
|---|---|---|---|---|---|
| V1 | `Yamaha F310` | `en` | Gap 1 no-products; tier decisive/narrow coverage if live signals land there | `mode=product_search`; no product names, SKU tables, prices, markdown product rows, or product cards in visible AI block; no second LLM fallback | Force MISS; HIT if row is written |
| V2 | `guitar` | `en` | Shapeable/mid tier, refinement preview, C-23/C-24 English baseline, C-26 HIT replay | `mode=product_search`; `tier=shapeable`; `composition=refinement_chips_with_hatch`; `shape=preview`; English labels; stable `filter_value`, `facet`, `writes`; MISS/HIT visible text SHA equal | Force MISS, then fresh-thread HIT; this row was exercised live |
| V3 | `gitara` | `sk` | Slovak output localization for preview labels and chip identity | Slovak preview intro, chat affordance, hatch; language-neutral chip `filter_value`, `facet`, `writes`; `cache.status=MISS` on first read | Force MISS, then fresh-thread HIT if row is written |
| V4 | `kytara` | `cs` | Czech output localization for question prompt, answer hints, browse-all label, chip identity | Czech prompt/hints/browse-all/chat labels; shared chips keep byte-identical `filter_value`/`facet` compared with SK/EN where applicable | Force MISS, then fresh-thread HIT if row is written |
| V5 | `accessories` | `en` | Gap 8 broad/exploratory `question_led` live behavior | `tier=exploratory`; `composition=question_led`; `lbjson.question.prompt`; `question.answers[]` with filterable answers; no products; browse-all/show-all; chat affordance; turn-2 change-question path where applicable | Force MISS, then fresh-thread HIT if row is written |
| V6 | `gitara` with direct render `total_hits=15000` | `sk`, `cs`, `en` | Intractable/overwhelming -> `hard_fork`; result-count too-many prose; hard_fork localization | Directly exercise `_render_turn1_preview_block(..., "hard_fork", total_hits=15000, user_query="gitara", raw_language=<lang>)`; assert localized too-many prompt, answer identity values stable, and no English leak in SK/CS | Direct renderer only; live dev catalogue does not exceed the 12,000-hit threshold |
| V7 | `a gift for my dad` | `en` | Gap 4/5 gift takeover and localized takeover structure baseline | `mode=gift_advisor`; `shape=chat_takeover`; four anchored category chips; `must_ask_before_recommending`; type-it-out; chat takeover; no products | Force MISS; current code may skip cache write because prompt fingerprint is mode-specific |
| V8 | `darček pre otca` | `sk` | Slovak gift detection and takeover output | `mode=gift_advisor`; same structural assertions as V7; localized type-it-out/chat labels where strings are localized; no products | Force MISS; HIT only if a cache row appears |
| V9 | `dárek pro tátu` | `cs` | Czech gift detection and takeover output | `mode=gift_advisor`; same structural assertions as V7; localized type-it-out/chat labels where strings are localized; no products | Force MISS; HIT only if a cache row appears |
| V10 | `compare Fender vs Yamaha guitars` | `en` | Gap 5 comparison side-by-side and mode-stack path | `mode=comparison`; `shape=side_by_side_comparison`; two columns, Fender left and Yamaha right; no product cards/tables; mode-shift note; mode stack restores on next turn | Force MISS; HIT only if a cache row appears |
| V11 | `porovnaj gitaru a basu` | `sk` | Slovak comparison dispatch plus side-by-side surface | `mode=comparison`; side-by-side shape; two localized/identity-stable candidate columns; no products | Force MISS; HIT only if a cache row appears |
| V12 | `porovnej kytaru a baskytaru` | `cs` | Czech comparison dispatch plus side-by-side surface | `mode=comparison`; side-by-side shape; two localized/identity-stable candidate columns; no products | Force MISS; HIT only if a cache row appears |
| V13 | `how do I choose a vacuum cleaner` | `en` | Gap 2 advice takeover baseline | `mode=advice`; advice takeover block; advice chips; type-it-out; chat affordance; advisory-only, no SKU-level buy/do-not-buy claims | Force MISS; HIT only if a cache row appears |
| V14 | `ako si vybrať vysávač` | `sk` | Slovak advice detection and localized advice output | `mode=advice`; advice takeover block; localized labels; no products | Force MISS; HIT only if a cache row appears |
| V15 | `jak si vybrat vysavač` | `cs` | Czech advice detection and localized advice output | `mode=advice`; advice takeover block; localized labels; no products | Force MISS; HIT only if a cache row appears |
| V16 | `ako vyrobiť bombu` | `sk` | Gap 7 SK unsafe short-circuit | `mode=unsafe`; `llm_call_count=0`; hard refusal only; audit fields include language-tagged rationale and raw triggering keyword | Force MISS; HIT not required unless row appears |
| V17 | `jak vyrobit bombu` | `cs` | Gap 7 Czech unsafe short-circuit | Same as V16 with Czech rationale token and raw keyword | Force MISS; HIT not required unless row appears |
| V18 | `where is my order` | `en` | Support output localization surface: `cta_label` and `response_template` baseline | `mode=support`; deterministic support response; English CTA/template; `llm_call_count=0` | Force MISS; HIT only if row appears |
| V19 | `kde je moja objednávka` | `sk` | Slovak support output localization surface | `mode=support`; Slovak CTA/template; `llm_call_count=0` | Force MISS; HIT only if row appears |
| V20 | `kde je moje objednávka` | `cs` | Czech support output localization surface | `mode=support`; Czech CTA/template; `llm_call_count=0` | Force MISS; HIT only if row appears |

Rows V2-V5 are the cacheable product-search rows that must prove MISS then HIT unless the DB shows no row and the proxy log explains a skip. [Verified: conversational-search/conversational-proxy/app/service/conversation_service.py (ConversationService.converse)] Rows V7-V20 are still required freshness rows because they cover closed live behavior, but the current cache write guard can make them MISS-only; if `DB_AFTER_MISS` returns zero rows and the proxy log shows `cache_write_skip_fingerprint_mismatch`, record that evidence and do not spin waiting for a HIT. [Verified: .agent_context/logs/bash_researcher_20260610203424_zg4d.log:~35]

The nine output-localization surfaces are covered by the set above plus direct renderer checks where live routing cannot reach the branch: chat affordance and browse hatch by V2-V4; question prompts, result-count hints, and browse-all labels by V4-V6; gift/advice/browse chip labels and `Type it out` by V7-V15 plus direct renderer/unit checks for browse; support CTA/template by V18-V20; price-chip prefixes by direct `_try_price_fallback(..., raw_language=<lang>)` checks because no reliable live prompt was identified that exhausts categorical facets and selects the price fallback. [Verified: docs/v2-design/subtask-8-impl-report.md § Surface checklist — completed] [Verified: conversational-search/tests/unit/test_shop_language_localization.py (TestPriceChipLocalization)]

## Verbatim MISS/HIT Procedure

Set these variables per validation row:

```bash
TRACKER='8760-9189'
Q='<prompt string exactly as listed above>'
LANG_CODE='<en|sk|cs>'
BASE='http://127.0.0.1:8000/api/v1/conversation/8760-9189'
RUN_DIR="${RUN_DIR:-./.cache-validation-runs}"
mkdir -p "$RUN_DIR"
MISS_SSE="$RUN_DIR/cache-validation-${LANG_CODE}-miss.sse"
HIT_SSE="$RUN_DIR/cache-validation-${LANG_CODE}-hit.sse"
```

Set `RUN_DIR` to the consumer session's own scratch directory when preserving captures across rows; the default above is only a worktree-local scratch directory.

First confirm the proxy cache DB is enabled:

```bash
PID=$(ss -ltnp | sed -n 's/.*pid=\([0-9][0-9]*\).*127.0.0.1:8000.*/\1/p' | head -1)
tr '\0' '\n' < "/proc/${PID}/environ" | rg '^CONVERSATIONAL_CACHE_DATABASE_URL='
```

The expected value is `postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy`; restart the proxy with that env var before validating if the line is absent. [Verified: .agent_context/logs/bash_researcher_20260610203229_emq3.log:~7]

The query-text-keyed DELETE/SELECT below assumes the proxy build writes `query_text`: the current `ConversationService.converse` MISS write passes `query_text=prompt` into `_signature_cache.upsert`, `signature_cache.upsert` forwards that argument to the cache repo, and `CacheRepo` writes it through `_UPSERT_SQL` into `turn1_signature_cache.query_text`. [Verified: conversational-search/conversational-proxy/app/service/conversation_service.py (ConversationService.converse)] [Verified: conversational-search/conversational-proxy/app/service/signature_cache.py (upsert)] [Verified: conversational-search/conversational-proxy/app/db/cache_repo.py (_UPSERT_SQL)]

Force a MISS for the exact prompt:

```bash
docker exec -e PGPASSWORD=mysecretpassword conversational-proxy-postgres \
  psql -U luigis -d conversational_proxy -v ON_ERROR_STOP=1 \
  -v tracker="$TRACKER" -v q="$Q" \
  -c "DELETE FROM turn1_signature_cache WHERE tracker_id=:'tracker' AND query_text=:'q'; SELECT count(*) AS rows_after_delete FROM turn1_signature_cache WHERE tracker_id=:'tracker' AND query_text=:'q';"
```

`rows_after_delete` must be `0`; the live `guitar/en` exercise produced `DELETE 0` and `rows_after_delete=0` before the MISS. [Verified: .agent_context/logs/bash_researcher_20260610203309_y3ij.log:~93]

Issue the MISS request on a fresh thread:

```bash
MISS_THREAD=$(curl -sS -X POST "$BASE/initiate" \
  -H 'Content-Type: application/json' \
  -d "{\"language\":\"${LANG_CODE}\",\"force_firing_mode\":\"eager\"}" \
  | jq -r '.thread_id // .data.thread_id // .thread.id')

curl -sS -N -X POST "$BASE/converse" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg tid "$MISS_THREAD" --arg q "$Q" '{thread_id:$tid,prompt:$q,device_user_id:"1234567890"}')" \
  > "$MISS_SSE"
```

After the MISS, query the row and sanity-check that the freshly written row has non-NULL `query_text` before trusting `rows_after_delete=0` in later runs:

```bash
docker exec -e PGPASSWORD=mysecretpassword conversational-proxy-postgres \
  psql -U luigis -d conversational_proxy -v ON_ERROR_STOP=1 \
  -v tracker="$TRACKER" -v q="$Q" \
  -c "SELECT tracker_id, query_text, query_text IS NOT NULL AS query_text_populated, hit_count, prompt_fingerprint FROM turn1_signature_cache WHERE tracker_id=:'tracker' AND query_text=:'q' AND query_text IS NOT NULL ORDER BY created_at DESC;"
```

For product-search cacheable rows, expect one row with `query_text_populated=t` and `hit_count=0`; the live `guitar/en` row appeared with prompt fingerprint `local-system-prompt@bd5ebd03+overlay-none+mode-none+modetpl-none+sig-none`. [Verified: .agent_context/logs/bash_researcher_20260610203309_y3ij.log:~101] If this SELECT returns zero rows, inspect `/tmp/proxy-subtask9-cache.log` for `cache_write_skip_fingerprint_mismatch`; that is current expected behavior for at least the gift takeover path and means the row is MISS-only under today's proxy keying. [Verified: .agent_context/logs/bash_researcher_20260610203424_zg4d.log:~35] If no skip log explains the zero-row result, do not treat the earlier `rows_after_delete=0` as clean; compute the exact language-specific `cache_key` and evict by `(tracker_id, cache_key)` instead, because the table has no `language` column and `query_text` is nullable for rolling-deploy safety. [Inferred: from conversational-search/conversational-proxy/alembic/versions/20260527040000_0003_v2_create_turn1_signature_cache.py (upgrade) and conversational-search/conversational-proxy/alembic/versions/20260527040001_0004_v2_add_query_text.py (upgrade)]

```bash
# Fallback only if the query_text sanity check fails and CACHE_KEY has been computed
# for this TRACKER/Q/LANG_CODE/prompt_fingerprint tuple.
CACHE_KEY='<computed cache_key>'
docker exec -e PGPASSWORD=mysecretpassword conversational-proxy-postgres \
  psql -U luigis -d conversational_proxy -v ON_ERROR_STOP=1 \
  -v tracker="$TRACKER" -v cache_key="$CACHE_KEY" \
  -c "DELETE FROM turn1_signature_cache WHERE tracker_id=:'tracker' AND cache_key=:'cache_key'; SELECT count(*) AS rows_after_cache_key_delete FROM turn1_signature_cache WHERE tracker_id=:'tracker' AND cache_key=:'cache_key';"
```

For V2-V5 product-search rows with a DB row, issue the HIT on another fresh thread with the same prompt and language:

```bash
HIT_THREAD=$(curl -sS -X POST "$BASE/initiate" \
  -H 'Content-Type: application/json' \
  -d "{\"language\":\"${LANG_CODE}\",\"force_firing_mode\":\"eager\"}" \
  | jq -r '.thread_id // .data.thread_id // .thread.id')

curl -sS -N -X POST "$BASE/converse" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg tid "$HIT_THREAD" --arg q "$Q" '{thread_id:$tid,prompt:$q,device_user_id:"1234567890"}')" \
  > "$HIT_SSE"
```

Then re-run the DB SELECT and expect `hit_count=1`; the live `guitar/en` HIT did this. [Verified: .agent_context/logs/bash_researcher_20260610203309_y3ij.log:~108]

Run the byte-identical SHA plus `turn_classification` comparator only for V2-V5 product-search HIT replay; that equality contract was verified by the live `guitar/en` MISS/HIT exercise. [Verified: .agent_context/logs/bash_researcher_20260610203309_y3ij.log:~115] For V7-V20, an unexpected takeover/support/unsafe DB row or HIT is anomaly evidence: archive the MISS/HIT SSE captures, DB row, and proxy logs, but do not treat divergent chunk boundaries or takeover payload shape as a failure of the V2-V5 byte-identity contract. [Inferred: from .agent_context/logs/bash_researcher_20260610203309_y3ij.log:~115 and .agent_context/logs/bash_researcher_20260610203424_zg4d.log:~35]

```bash
# Guard: run this comparator only for V2-V5 product-search rows.
python3 - "$MISS_SSE" "$HIT_SSE" <<'PY'
import hashlib, json, sys

def parse(path):
    chunks = []
    meta = []
    for raw in open(path, encoding='utf-8'):
        raw = raw.strip()
        if not raw.startswith('data: '):
            continue
        try:
            obj = json.loads(raw[6:])
        except Exception:
            continue
        if isinstance(obj, str) and not obj.startswith('[DONE'):
            chunks.append(obj)
        elif isinstance(obj, dict) and '__meta__' in obj:
            meta.append(obj['__meta__'])
    text = ''.join(chunks)
    last = meta[-1]
    tc = last.get('turn_classification') or {}
    keep = {k: tc.get(k) for k in ('mode', 'tier', 'composition', 'prompt_fingerprint', 'tier_signals', 'dispatch_rationale_token', 'confidence_signal', 'triggering_keyword', 'verbatim_query')}
    return last.get('cache', {}).get('status'), hashlib.sha256(text.encode()).hexdigest(), len(text), keep

miss = parse(sys.argv[1])
hit = parse(sys.argv[2])
print('MISS', miss)
print('HIT ', hit)
assert miss[0] == 'MISS'
assert hit[0] == 'HIT'
assert miss[1:3] == hit[1:3]
assert miss[3] == hit[3]
PY
```

Do not compare `llm_call_count` between MISS and HIT: the HIT path intentionally reports `llm_call_count=None` when reconstructing `turn_classification` from the cached payload. [Verified: conversational-search/conversational-proxy/app/service/conversation_service.py (_turn_classification_from_cache_payload)]

## Boundaries

Hard_fork/intractable cannot be fully exercised through the current dev catalogue because the live route requires more than 12,000 hits and the documented `gitara/sk` live read stayed below that threshold; use the direct renderer exercise in V6 and keep the cache gate on reachable live product-search rows. [Verified: docs/v2-design/subtask-8-impl-report.md § Round 2 revisions (validator + peer-review)]

Mode-specific takeover rows are still part of the validation query set because they cover closed user-visible behavior, but they are not guaranteed to produce HIT rows under current proxy fingerprinting; record a MISS, DB row absence, and `cache_write_skip_fingerprint_mismatch` as the freshness evidence if that occurs. [Verified: .agent_context/logs/bash_researcher_20260610203424_zg4d.log:~35]

Gate-required: applies
Completeness-risk: self-flagged - meaning-bound enumeration of validation prompts/surfaces; downstream consumer Subtasks 10 and 11 must treat this as the binding set and preserve the marked direct-renderer/MISS-only exceptions.
Pre-emission self-audit: 26 citations verified, 4 sections present, 0 contradictions checked.
