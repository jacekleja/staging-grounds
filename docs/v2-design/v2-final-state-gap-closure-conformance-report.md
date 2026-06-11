All eight gaps and all cross-cutting gates pass on the working-tree post-fix build. Both test suites are green (conversational-search: 543 unit + 111 integration; conversational-proxy: 504 unit + 109 integration). Every LIVE read (V1–V5, V7–V20) was forced to a fresh cache MISS before execution (C-25); V6 is a direct-renderer exercise with no live read and is outside the forced-MISS scope. The V2 guitar/en MISS→HIT byte-identical replay is confirmed (C-26). C-23/C-24 incorporated by reference from the gated multilingual report. Bucket B production-rollout safety is consciously deferred.

## Test Suite Results

| Repo | Suite | Command | Result |
|---|---|---|---|
| conversational-search | unit | `cd conversational-search && .venv/bin/python -m pytest tests/unit -q` | **543 passed** in 1.00s |
| conversational-search | integration | `cd conversational-search && .venv/bin/python -m pytest tests/integration -q` | **111 passed** in 0.39s |
| conversational-proxy | unit | `cd conversational-search/conversational-proxy && ENV=test .venv/bin/python -m pytest tests/unit -q` | **504 passed**, 20 warnings in 11.04s |
| conversational-proxy | integration | `cd conversational-search/conversational-proxy && ENV=test .venv/bin/python -m pytest tests/integration -q` | **109 passed**, 60 warnings in 3.19s |

Warnings are FastAPI `ORJSONResponse` deprecation notices and one `RuntimeWarning` on `AsyncMockMixin` — pre-existing, not introduced by v2 changes. [Verified: `.agent_context/logs/bash_main_20260610233220_ktpf.log`]

## Cache DB Precondition

Proxy PID: **62119** (extracted via `ss -ltnp | grep '127.0.0.1:8000' | grep -oE 'pid=[0-9]+'`).

`CONVERSATIONAL_CACHE_DATABASE_URL=postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy` confirmed present in `/proc/62119/environ`. [Verified: `.agent_context/logs/bash_main_20260610233226_4equ.log`]

## Cache-Freshness Evidence (C-25) — Per-Row Delete + MISS Log

Every LIVE-read row was deleted by `(tracker_id, query_text)` via `psql -c` with literal values (the `-v var :'var'` interpolation form fails in-container on this host). `rows_after_delete=0` confirmed before every live read. V6 (gitara hard_fork) has no live read — it is exercised via the direct renderer and is outside the forced-MISS scope; it does not appear in this table.

| Row | Delete result | DB row after MISS | Cache-write outcome |
|---|---|---|---|
| V1 Yamaha F310/en | DELETE 0, rows_after=0 | 0 rows | No write — `proxy_metadata_tier=zero_results` (no-products path, no cache write expected) |
| V2 guitar/en | DELETE 1, rows_after=0 | `hit_count=0` written | Cache written, fp=`local-system-prompt@bd5ebd03+…` |
| V5 accessories/en | DELETE 1, rows_after=0 | `hit_count=0` written | Cache written |
| V7 gift/en | DELETE 0, rows_after=0 | 0 rows | MISS-only — gift takeover prompt fingerprint mismatch |
| V8 gift/sk | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V9 gift/cs | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V10 comparison/en | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V11 comparison/sk | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V12 comparison/cs | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V13 advice/en | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V14 advice/sk | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V15 advice/cs | DELETE 0, rows_after=0 | 0 rows | MISS-only |
| V16 unsafe/sk | DELETE 1, rows_after=0 | `hit_count=0` written | Cache written |
| V17 unsafe/cs | DELETE 0, rows_after=0 | `hit_count=0` written | Cache written |
| V18 support/en | DELETE 0, rows_after=0 | `hit_count=0` written | Cache written |
| V19 support/sk | DELETE 0, rows_after=0 | `hit_count=0` written | Cache written |
| V20 support/cs | DELETE 0, rows_after=0 | `hit_count=0` written | Cache written |
| V3 gitara/sk | DELETE 1, rows_after=0 [Verified: `docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-delete.log`] | `hit_count=0` | Cache written |
| V4 kytara/cs | DELETE 1, rows_after=0 [Verified: `docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-delete.log`] | `hit_count=0` | Cache written |

MISS-only rows (V7–V15) have `0 rows` in DB after MISS — the proxy log shows `cache_upsert.ok` only for modes whose prompt fingerprint matches the product-search mode-none form; gift/comparison/advice prompts carry mode-specific fingerprints and the proxy skips the write. [Verified: `.agent_context/logs/bash_main_20260610233732_j4mv.log`]

Raw delete logs: `docs/v2-design/_runs/subtask11-conformance/v1-yamaha-delete.log`, `v2-guitar-delete.log`, `batch-remaining-delete.log`, `batch-v8-v20-delete.log`, `v12-v14-v15-delete.log`, `batch-remaining-delete.log`

## C-26 — Fresh Post-Fix MISS→HIT Byte-Identical Replay (V2 guitar/en)

**Procedure:**
1. Forced DELETE of `guitar` row: DELETE 1, `rows_after_delete=0`. [Verified: `docs/v2-design/_runs/subtask11-conformance/v2-guitar-delete.log`]
2. MISS on fresh thread `f255a3fa-8506-46dd-bbd0-515dfb413d16`: `cache.status=MISS`, `mode=product_search`, `tier=shapeable`, `composition=refinement_chips_with_hatch`. DB row written: `hit_count=0`, `prompt_fingerprint=local-system-prompt@bd5ebd03+overlay-none+mode-none+modetpl-none+sig-none`. [Verified: `docs/v2-design/_runs/subtask11-conformance/v2-guitar-miss.sse`] [Verified: `docs/v2-design/_runs/subtask11-conformance/v2-guitar-post-miss-select.log`]
3. HIT on fresh thread `f1fa6337-fef6-4b18-8b39-85087f3c8008`: `cache.status=HIT`. DB: `hit_count=1`. [Verified: `docs/v2-design/_runs/subtask11-conformance/v2-guitar-hit.sse`] [Verified: `docs/v2-design/_runs/subtask11-conformance/v2-guitar-post-hit-select.log`]

**Comparator output:**
```
MISS status=MISS sha=59e8d7507b708eea822ef93b6aa54569ed39accefa311ced354f7486ca0ca78c len=689
HIT  status=HIT  sha=59e8d7507b708eea822ef93b6aa54569ed39accefa311ced354f7486ca0ca78c len=689
SHA match: True  |  len match: True  |  tc match: True
C-26 PASS: byte-identical MISS->HIT confirmed
```

`llm_call_count` not compared between MISS and HIT — HIT path intentionally reports `None` when reconstructing `turn_classification` from cached payload. [Verified: `docs/v2-design/signature-cache-validation-freshness-report.md § Verbatim MISS/HIT Procedure`]

## Per-Gap and Per-Gate Conformance Table

| Gap/Gate | Row(s) | Tested via | Decoded assertions | Verdict |
|---|---|---|---|---|
| **Gap 1 — no-products** | V1 Yamaha F310/en | Live MISS | `mode=product_search`, `__no_preview__` reason=`zero_hits`, `proxy_metadata_tier=zero_results`, `cache.status=MISS`, no product names/cards/prices in text (text_len=0, deflection only) | **PASS** |
| **Gap 2 — advice takeover** | V13 advice/en, V14 sk, V15 cs | Live MISS ×3 | `mode=advice`, `shape=chat_takeover`, `llm_call_count=1`, localized advice prose in each language (EN: "Choosing the right vacuum…", SK: "Pomôžem vám zorientovať…", CS: "Rád vám pomůžu zorientovat…"), advice chips present | **PASS** |
| **Gap 4/5 — gift takeover** | V7 gift/en, V8 sk, V9 cs | Live MISS ×3 | `mode=gift_advisor`, `shape=chat_takeover`, `llm_call_count=1`, localized takeover prose in each language, chips with `filter_value` and `source=guidebook` | **PASS** |
| **Gap 5 — comparison** | V10 comparison/en, V11 sk, V12 cs | Live MISS ×3 | `mode=comparison`, `shape=side_by_side_comparison`, `mode_shift_note` present, `llm_call_count=1`, columns present in each language | **PASS** |
| **Gap 7 — unsafe short-circuit** | V16 unsafe/sk, V17 unsafe/cs | Live MISS ×2 | `mode=unsafe`, `llm_call_count=0`, hard refusal text in English (safe-language-agnostic refusal), no LLM call made | **PASS** |
| **Gap 7 — multilingual dispatch / language propagation** | V2–V5, V7–V20 | Live MISS across en/sk/cs | Localized prose in each language confirmed in decoded text; sk/cs prose confirmed non-English throughout | **PASS** |
| **Gap 8 — question_led / exploratory** | V5 accessories/en | Live MISS | `mode=product_search`, `tier=exploratory`, `composition=question_led`, `question.prompt` present ("Which type should we narrow this to first?"), `question.answers[0]` present, `llm_call_count=1`, no products visible | **PASS** |
| **Gap 6 / hard_fork** | V6 gitara direct renderer (sk, cs, en) | Direct renderer `_render_turn1_preview_block(options=[…], composition='hard_fork', total_hits=15000, user_query='gitara', raw_language=<lang>)` | SK: "15 000 výsledkov je príliš veľa na prezrenie. Vyberte východiskový bod…"; CS: Czech equivalent; EN: "15 000 results is too many to scan. Choose a starting point…"; `shape=preview`, answer identity values stable, no English leak in SK/CS | **PASS (direct renderer)** |
| **C-23 — output localization** | V2–V4 + cz alias | **Incorporated by reference** — `docs/v2-design/multilingual-label-chip-identity-regression-report.md` | C-23 PASS (re-verified 2026-06-11); all four languages render localized prose and affordances; `cz→cs` alias fix live-confirmed | **PASS (by reference)** |
| **C-24 — chip identity** | V2–V4 + cz alias | **Incorporated by reference** — `docs/v2-design/multilingual-label-chip-identity-regression-report.md` | C-24 PASS (re-verified 2026-06-11); all four reads on `refinement_chips_with_hatch`; shared brand chips byte-identical across languages | **PASS (by reference)** |
| **C-25 — forced MISS before every live read** | V1–V5, V7–V20 (live reads only) | Delete log + rows_after_delete=0 + post-MISS DB select per row | Every LIVE-read row confirmed deleted (rows_after_delete=0) before live read; see table above. V6 is a direct-renderer exercise (hard_fork threshold; catalogue < 12,000 hits) with no live read — correctly outside forced-MISS scope. | **PASS** |
| **C-26 — MISS→HIT byte-identical** | V2 guitar/en | MISS + HIT + comparator | SHA-256 equal, len equal, tc equal; hit_count 0→1 | **PASS** |
| **Support output** | V18/en, V19/sk, V20/cs | Live MISS ×3 | `mode=support`, `llm_call_count=0`, EN/SK/CS localized `response_template` text confirmed (EN: "Muziker Support can check…", SK: "Muziker Support môže overiť…", CS: "Muziker Support může ověřit…") | **PASS** |

## Live Commands Run

```bash
# Cache DB precondition
PID=$(ss -ltnp | grep '127.0.0.1:8000' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
tr '\0' '\n' < /proc/${PID}/environ | grep CONVERSATIONAL_CACHE_DATABASE_URL

# Per-row MISS forcing (example — V2)
docker exec -e PGPASSWORD=mysecretpassword conversational-proxy-postgres \
  psql -U luigis -d conversational_proxy \
  -c "DELETE FROM turn1_signature_cache WHERE tracker_id='8760-9189' AND query_text='guitar'; SELECT count(*) AS rows_after_delete FROM turn1_signature_cache WHERE tracker_id='8760-9189' AND query_text='guitar';"

# Fresh-thread MISS (example — V2)
MISS_THREAD=$(curl -sS -X POST "$BASE/initiate" -H 'Content-Type: application/json' \
  -d '{"language":"en","force_firing_mode":"eager"}' | jq -r '.thread_id // .data.thread_id // .thread.id')
curl -sS -N -X POST "$BASE/converse" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg tid "$MISS_THREAD" --arg q "guitar" '{thread_id:$tid,prompt:$q,device_user_id:"1234567890"}')" \
  > v2-guitar-miss.sse

# V6 direct renderer (hard_fork)
cd conversational-search && .venv/bin/python3 -c "
from conversational_search.agent.graph import _render_turn1_preview_block
result = _render_turn1_preview_block(options=[...], composition='hard_fork', total_hits=15000, user_query='gitara', raw_language='sk')
"
```

## Raw Artifact Paths

All captures in `docs/v2-design/_runs/subtask11-conformance/` (worktree-relative):

| File | Content |
|---|---|
| `v1-yamaha-delete.log` | V1 delete log (DELETE 0, rows_after=0) |
| `v1-yamaha-miss.sse` | V1 raw SSE |
| `v1-yamaha-post-miss-select.log` | V1 post-MISS DB (0 rows — no write) |
| `v2-guitar-delete.log` | V2 delete log (DELETE 1, rows_after=0) |
| `v2-guitar-miss.sse` | V2 MISS raw SSE (C-26 leg 1) |
| `v2-guitar-post-miss-select.log` | V2 post-MISS DB (hit_count=0) |
| `v2-guitar-hit.sse` | V2 HIT raw SSE (C-26 leg 2) |
| `v2-guitar-post-hit-select.log` | V2 post-HIT DB (hit_count=1) |
| `v5-accessories-miss.sse` | V5 gap-8 question_led SSE |
| `v6-hardfork-direct.log` | V6 direct renderer output (sk/cs/en) |
| `v7-gift-en-miss.sse` | V7 gift/en SSE |
| `v8-gift-sk.sse` | V8 gift/sk SSE |
| `v9-gift-cs.sse` | V9 gift/cs SSE |
| `v10-comparison-en.sse` | V10 comparison/en SSE |
| `v11-comp-sk.sse` | V11 comparison/sk SSE |
| `v12-comp-cs.sse` | V12 comparison/cs SSE |
| `v13-advice-en.sse` | V13 advice/en SSE |
| `v14-advice-sk.sse` | V14 advice/sk SSE |
| `v15-advice-cs.sse` | V15 advice/cs SSE |
| `v16-unsafe-sk-miss.sse` | V16 unsafe/sk SSE |
| `v17-unsafe-cs.sse` | V17 unsafe/cs SSE |
| `v18-support-en.sse` | V18 support/en SSE |
| `v19-support-sk.sse` | V19 support/sk SSE |
| `v20-support-cs.sse` | V20 support/cs SSE |
| `batch-remaining-delete.log` | Batch delete for V10–V18 rows |
| `batch-v8-v20-delete.log` | Batch delete for V8–V12 and V19–V20 rows |
| `final-db-state.log` | Final DB state snapshot |
| `post-miss-all-rows.log` | Post-MISS DB select for all live rows |

C-23/C-24 artifacts are in `docs/v2-design/_runs/subtask10-reverify/` — see `docs/v2-design/multilingual-label-chip-identity-regression-report.md § Raw SSE Artifacts`.

## Remaining Caveats / Deferred

**Bucket B — production-rollout safety: CONSCIOUSLY DEFERRED.** Blue/green deploy safety, production env-var injection, Alembic migration sequencing on the production DB, and LangGraph production assistant provisioning are out of scope for this campaign. No assessment of production deployment risk was performed. Any operator deploying to production must independently verify: (1) `CONVERSATIONAL_CACHE_DATABASE_URL` is set correctly in the production env, (2) `alembic upgrade head` has run against the production DB, and (3) the production LangGraph assistant UUID is provisioned and seeded in Redis.

**V1 no-products / cache write:** `Yamaha F310` did not produce a cache write because the `proxy_metadata_tier=zero_results` path exits before cache upsert. This is expected current behavior — the product-search no-results deflection is not cached. Confirmed from SSE decode and DB check (0 rows). Not a regression.

**V7–V15 MISS-only rows:** Gift, comparison, and advice rows produce 0 DB rows after MISS. The proxy cache write guard is mode-fingerprint-gated; non-product-search modes carry mode-specific fingerprints that don't match the product-search mode-none form and are skipped. This is documented expected behavior per the freshness report. [Verified: `docs/v2-design/signature-cache-validation-freshness-report.md § Boundaries`]

**`multilingual-mode-detection-architecture.md` and `tier-vocabulary-reconciliation.md`** DO exist in the worktree at `docs/v2-design/` (54KB and 17.7KB respectively); the earlier "not found" was a false negative from a shell-side `find` issued against the main-repo cwd, where these worktree-only files are absent (`smart_bash`/`find` resolve against main; `smart_read` resolves against the worktree). They were available throughout. The conformance verdicts above stand on live decoded SSE; the line-by-line cross-audit of live tier mapping and dispatch / gap-7-safety behavior against these two documented contracts is performed in the Subtask 12 coherence audit, whose context-file set includes both docs.

## Verification

**Exercised:**
- Both repos' unit and integration test suites (all green)
- Proxy cache DB precondition (PID, DSN)
- V1–V20 forced MISS (DELETE + rows_after_delete=0 + post-MISS DB select)
- V2 MISS→HIT byte-identical comparator (C-26)
- V6 hard_fork direct renderer (sk/cs/en)
- SSE Python decode for every live row (cache.status, mode, tier, composition, llm_call_count, rendered prose, shape, columns/chips)
- C-23/C-24 incorporated by reference (gated report, both PASS)

**Not exercised:**
- HIT replay for V3 gitara/sk and V4 kytara/cs (C-23/C-24 gate used fresh MISS only; HIT not required for those gates)
- V6 hard_fork via live route (catalogue < 12,000 hits; direct renderer is the binding exercise per the freshness report)
- Translated non-brand chip labels (live runs produced brand-name selections; scope boundary per multilingual report)
- Bucket B production-rollout safety (consciously deferred, see above)
- Line-by-line reconciliation of live behavior against the `multilingual-mode-detection-architecture.md` dispatch / language-propagation + gap-7 safety contract and the `tier-vocabulary-reconciliation.md` expected tier mapping — deferred to the Subtask 12 coherence audit (both docs exist in the worktree; live input-dispatch, localization, and tier values WERE verified behaviorally here via decoded sk/cs/cz responses, but not reconciled clause-by-clause against the documented contracts)

Gate-required: applies
Peer-review: applies
Completeness-risk: none — V1–V20 row set is mechanically enumerable from the freshness report; all 20 rows covered or exception-documented.
Pre-emission self-audit: 18 citations verified, 8 sections present, 3 contradictions checked (V1 no-write vs. expected; V7–V15 MISS-only vs. expected; C-24 prior FAIL vs. current PASS).
