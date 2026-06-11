# Gap 8 — Broad-Tier `question_led` Verification Gate

**Executive summary:** BRANCH A (confirmed by corrected methodology). The live stack does NOT produce `question_led` composition for `exploratory`-tier queries. The tier classifier correctly classifies and emits `composition: "question_led"` inside `tier_signals`, but the graph-level composition dispatcher overrides it to `refinement_chips_with_hatch`. The composition table live-switch is inactive (`composition_table_live: false`). Implementation is required in Subtask 4.

> **Prior Round 1 Branch A verdict:** Superseded (methodology error — tested `guitar`/`shapeable` tier, not `exploratory` tier). The Round 1 Branch A verdict happened to be correct in conclusion (implementation required) but for the wrong reason. See §Prior Round 1 content below; do NOT rely on the Round 1 tier-is-broad rationale.

---

## 1. Branch Confirmation

| Repo | Branch | HEAD |
|---|---|---|
| conversational-search | feat/v2-campaign-rebased | 0d33694 |
| conversational-search/conversational-proxy | reconcile/proxy-v2-the-rest-on-origin-master | b8ca055 |

[Verified: observed behavior — `git log --oneline -3` run against both repos]

---

## 2. Stack Bring-Up

### Commands run

```
# LangGraph agent, port 2024
cd /home/fanderman/projects/luigis-box/conversational-search
nohup poetry run langgraph dev --host 127.0.0.1 --port 2024 --no-browser > langgraph.log 2>&1 &
# PID: 1710

# Alembic schema (psycopg+ DSN form is load-bearing)
cd /home/fanderman/projects/luigis-box/conversational-search/conversational-proxy
export ENV=development
export CONVERSATIONAL_CACHE_DATABASE_URL='postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy'
poetry run alembic upgrade head
# Output: INFO [alembic.runtime.migration] Context impl PostgresqlImpl. Will assume transactional DDL.

# Proxy, port 8000
nohup poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 > proxy.log 2>&1 &
# PID: 3504

# Redis seed
ENV=development poetry run python scripts/setup_dev.py
# Output: ✅ Successfully seeded 1 throttling rules
```

### Credential resolution

- DSN password (`mysecretpassword`) resolved from `docker inspect conversational-proxy-postgres` → `POSTGRES_PASSWORD`.
- `CONVERSATIONAL_CACHE_DATABASE_URL` form `postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy` per constraint `constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md`.
- AWS Bedrock: `~/.aws` credentials present in environment; LangGraph came up healthy without explicit AWS_BEARER_TOKEN override.

### Health checks

```
GET http://127.0.0.1:2024/ok  →  {"ok":true}          # LangGraph healthy
uvicorn running on http://127.0.0.1:8000               # Proxy healthy (serving routes; /health not registered)
```

[Verified: observed behavior — curl responses above]

---

## 3. Query Selection — Why `guitar` Is Broad/Exploratory

Query used: **`guitar`**

Rationale: a single bare category noun with no brand, model, price, or feature qualifier. The catalogue returns 2,767 results (`result_count: 2767`), `axis_entropy: 0.87`, `diversity_score_D: 0.63`, `filled_axes: 2`. These signals match the design description of a "broad/exploratory" query: high result count, diffuse across many facet values, no disambiguating tokens. The tier classifier labels it `shapeable` (the live vocabulary equivalent of the designed `broad` tier, per the tier-vocabulary divergence confirmed in the prior English conformance sweep).

[Verified: `__meta__.turn_classification.tier_signals` in the live SSE — `gap8_broad_guitar_en_sse.txt`]

---

## 4. Cache Eviction (Gate A2 — MANDATORY)

**Method:** Direct DELETE on the `turn1_signature_cache` partitioned table via `docker exec` into the Postgres container, targeting `query_text = 'guitar'`.

**Command:**
```sql
docker exec conversational-proxy-postgres psql -U luigis -d conversational_proxy -c "
DELETE FROM turn1_signature_cache WHERE query_text='guitar';
SELECT 'post-delete guitar rows: ' || count(*) FROM turn1_signature_cache WHERE query_text='guitar';
"
```

**Evidence:**
```
DELETE 1
post-delete guitar rows: 0
```

The deleted row had `cache_key = f14c719c163a1468b68020abf112c05590110bc43ce2b25a1025f2a4dc6dfdd0`, `tracker_id = 8760-9189`, `hit_count = 0`.

**Live read confirms MISS:** `__meta__.cache.status = "MISS"` in the SSE response captured below.

[Verified: observed behavior — docker exec psql output + `__meta__` in live SSE]

---

## 5. SSE Artifact Path

```
/home/fanderman/projects/luigis-box/.agent_context/sessions/1781106672-6469-2465b2ba685c/demo-artifacts/gap8_broad_guitar_en_sse.txt
```

(Round 1 artifact — `guitar` / `shapeable` tier. See §CORRECTED RE-VERIFICATION below for the Round 2 `exploratory`-tier artifact.)

```
data: {"__work_status__": {"kind": "lbx.work_status", "step": "query_received", "phase_index": 1, "source_node": "reset_tool_call_count"}}

data: {"__work_status__": {"kind": "lbx.work_status", "step": "composing_response", "phase_index": 2, "source_node": "handle_regular_turn"}}

data: {"__work_status__": {"kind": "lbx.work_status", "step": "composing_response", "phase_index": 3, "source_node": "handle_regular_turn"}}

data: "Choose an option below to narrow your search.\n\n```lbjson\n{\"chips\": [{\"label\": \"Ernie Ball\", \"filter_value\": \"Ernie Ball\", \"count\": 271, \"facet\": \"brand\"}, {\"label\": \"PSD Guitars\", \"filter_value\": \"PSD Guitars\", \"count\": 143, \"facet\": \"brand\"}, {\"label\": \"DR Strings\", \"filter_value\": \"DR Strings\", \"count\": 123, \"facet\": \"brand\"}, {\"label\": \"Fender\", \"filter_value\": \"Fender\", \"count\": 121, \"facet\": \"brand\"}], \"shape\": \"preview\", \"chat_affordance\": {\"label\": \"Chat with me instead →\", \"style\": \"dashed_pill\", \"writes\": {\"chat_takeover_trigger\": true}}, \"hatch\": {\"label\": \"Just browsing — show me popular searches\", \"style\": \"quiet_12px_grey_link\", \"writes\": {\"browse_intent\": true}}}\n```"

data: {"__meta__": {"turn_classification": {"mode": "product_search", "tier": "shapeable", "composition": "refinement_chips_with_hatch", "tier_signals": {"result_count": 2767, "top_share_max": 0.464, "axis_entropy": 0.870, "diversity_score_D": 0.625, "composition_table_live": false, "composition_table_fallback": false}}, "cache": {"status": "MISS"}}}

data: {"__work_status__": {"step": "done", "phase_index": 4, "source_node": "proxy"}}

data: "[DONE conversational_run_019eb245-0814-79b3-8b93-033a26fecb28]"
```

[Verified: observed behavior — full SSE in artifact file above]

---

## 6. Gap 8 Functional Contract — Per-Criterion Pass/Fail

| # | Criterion | Required value | Observed value | Result |
|---|---|---|---|---|
| 1 | `tier` maps to broad/exploratory | broad/exploratory (or live-vocab equivalent) | `shapeable` — this IS the broad tier live label (2,767 results, high entropy) | PASS (tier is broad) |
| 2 | `composition` == `question_led` | `question_led` | `refinement_chips_with_hatch` | **FAIL** |
| 3 | AI block has NO products | no product SKUs in lbjson | Brand chips only, no product SKUs | PASS |
| 4 | `lbjson.question.prompt` exists | key present | Absent — lbjson has `chips`, `shape`, `chat_affordance`, `hatch`; no `question` key | **FAIL** |
| 5 | `lbjson.question.answers[]` has ≥2 filterable answers | ≥2 answer objects | Absent — no `question.answers` key | **FAIL** |
| 6 | Demoted carousel present (if supported) | carousel block | Absent — composition does not include a carousel node | **FAIL** |
| 7 | show-all link exists | browse-all or equivalent | Absent as a named `browse_all_link`; a `hatch` link exists ("Just browsing — show me popular searches") but writes `browse_intent: true`, not a direct show-all catalogue link | **FAIL** (hatch is not the designed show-all link) |
| 8 | chat affordance exists | `chat_affordance` block | Present: `{"label": "Chat with me instead →", "style": "dashed_pill", "writes": {"chat_takeover_trigger": true}}` | PASS |
| 9 | turn-2 "Change the question" affordance reachable | follow-up turn emits change-the-question path | Not tested — turn-1 composition is wrong; `question_led` pivot path requires correct turn-1 first | UNTESTABLE (blocked by criterion 2 fail) |

**Summary: 3 PASS, 5 FAIL, 1 UNTESTABLE → BRANCH A**

The composition is entirely `refinement_chips_with_hatch`, not `question_led`. The `question.prompt` and `question.answers[]` keys required by the Gap 8 contract do not exist in the lbjson payload. The demoted carousel and show-all link are absent. The chat affordance is present but is part of the wrong composition.

> **NOTE (Round 2):** The Round 1 pass/fail table used `guitar`/`shapeable` as the probe query, which was the wrong tier for Gap 8. The Round 2 re-verification (§CORRECTED RE-VERIFICATION below) uses `accessories`/`exploratory` — the correct tier — and reaches the same BRANCH A verdict for the right reason.

---

## 7. Verdict

**BRANCH A — divergent/absent. Implementation required (Subtask 4).**

The live stack does not produce `question_led` composition for broad/exploratory product_search queries. The tier classifier correctly classifies `guitar` as broad (`shapeable`), but the composition mapper routes it to `refinement_chips_with_hatch` rather than `question_led`. The `question`, `carousel`, and `browse_all_link` keys required by the Gap 8 design contract are absent from the lbjson payload.

### Exact implementation surface for Subtask 4

Per `docs/v2-design/plan-v2-final-state-gap-closure.md § Gap 8` (lines 390–393):

1. **`conversational-search/graph.py`** — `_PRODUCT_SEARCH_TIER_TO_COMPOSITION` mapping table (maps `shapeable`/broad tier to `question_led`), `_render_turn1_preview_block` (must emit `question.prompt` + `question.answers[]` shape), `handle_regular_turn` (composition dispatch), `_emit_turn2_pivot` (turn-2 "Change the question" affordance).
2. **`conversational-search/turn1_selector.py`** — tier-to-composition selection logic.
3. **`conversational-search/conversational-proxy/app/service/langgraph_client.py`** — only if `question`, `carousel`, or `browse_all_link` are not forwarded/hydrated correctly once the agent emits them.

The `composition_table_live: false` and `composition_table_fallback: false` flags in the live tier_signals confirm the composition table override path is not active — the live-switch mechanism exists but is not triggered for this query.

[Verified: `__meta__.turn_classification.tier_signals.composition_table_live` in live SSE]

---

## 8. Stack Handoff

| Service | PID(s) | Status |
|---|---|---|
| LangGraph dev server (port 2024) | 1710, 1711 | Running — `GET /ok → {"ok":true}` |
| Proxy uvicorn (port 8000) | 3504, 3505 | Running — uvicorn serving |

**Stack is LEFT RUNNING for downstream subtasks.**

**DSN password resolution:** `docker inspect conversational-proxy-postgres` → env var `POSTGRES_PASSWORD=mysecretpassword`. Full DSN: `postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy`.

**AWS creds:** `~/.aws` present; LangGraph came up without explicit override.

**Cache eviction method for downstream subtasks:** DELETE on `turn1_signature_cache` by `query_text`:
```
docker exec conversational-proxy-postgres psql -U luigis -d conversational_proxy -c \
  "DELETE FROM turn1_signature_cache WHERE query_text='<your-query>';"
```
Confirm eviction by checking `SELECT count(*) FROM turn1_signature_cache WHERE query_text='<your-query>'` returns 0. Then confirm MISS by checking `__meta__.cache.status` in the live SSE response.

---

## Verification

**Exercised:**
- Stack bring-up: LangGraph 2024 + proxy 8000 both healthy.
- Cache eviction via `DELETE FROM turn1_signature_cache WHERE query_text='guitar'` — confirmed `DELETE 1`, post-delete count 0.
- Live MISS confirmed: `__meta__.cache.status = "MISS"` in fresh SSE response.
- Gap 8 functional contract evaluated against live `guitar` turn-1 payload.
- Composition `refinement_chips_with_hatch` confirmed on fresh MISS (not a stale cache artifact).

**Not exercised, and why:**
- Turn-2 "Change the question" affordance: cannot exercise because turn-1 composition is wrong (`refinement_chips_with_hatch` not `question_led`); the turn-2 pivot path requires a conformant turn-1 first.
- `question.answers[]` click/filter follow-up: key absent from lbjson; untestable until Subtask 4 implements the composition.
- Demoted carousel: not present in current composition; untestable until implemented.
- Slovak/multilingual broad query: out of scope for this subtask (English is the authoritative baseline per task spec).

---

---

## CORRECTED RE-VERIFICATION (Round 2)

**Executive summary:** BRANCH A confirmed by corrected methodology. An `exploratory`-tier query (`accessories`, `top_share_max=0.25`, `diversity_score_D=0.723`) produces `tier="exploratory"` at the classifier level but `composition="refinement_chips_with_hatch"` at the graph dispatch level — not `question_led`. The tier_signals payload itself contains `composition: "question_led"` (the correct mapping), confirming the classifier computed the right answer; however the graph-level dispatcher ignores it and applies `refinement_chips_with_hatch`. The composition table live-switch is inactive. Implementation required in Subtask 4.

> **Prior Round 1 Branch A is superseded.** Round 1 tested `guitar` (tier=`shapeable`), which is the wrong tier for Gap 8. The `shapeable` → `refinement_chips_with_hatch` mapping is CORRECT per design; the Round 1 "fail" on that tier was not a Gap 8 violation. The correct target is `exploratory` → `question_led`.

---

### R2.1 — Threshold Analysis: `exploratory` vs `shapeable`

[Verified: conversational-proxy/app/service/tier_signal_computer.py:64-68 (threshold constants)]

The classifier boundary rules in order of evaluation:

| Branch | Condition | Tier |
|---|---|---|
| Degenerate | `result_count == 0` | `zero_results` |
| Degenerate | `result_count == 1` | `decisive` |
| F3 anchor | `has_brand_token AND has_model_token AND count < 200` | `decisive` |
| D4 protect | `result_count > 12,000 AND max_axis_top_share < 0.60` | `intractable` |
| F1 primary | `max_axis_top_share >= 0.60` | `decisive` |
| F1 primary | `max_axis_top_share >= 0.45` | `shapeable` |
| F2 tie-break | `result_count > 12,000 AND diversity_score_D >= 0.75` | `intractable` |
| **F2 tie-break** | **`diversity_score_D >= 0.50`** | **`exploratory`** |
| Default | _(none of the above)_ | `shapeable` |

**`exploratory` is ONLY reachable via the F2 tie-break branch**, which requires ALL of:
1. `max_axis_top_share < 0.45` (F1 shapeable threshold not met — no strong single-axis signal)
2. `result_count <= 12,000` (D4 operator-protection ceiling not exceeded)
3. `diversity_score_D >= 0.50` (F2 exploratory floor met)

Diversity score formula: `D = 0.4*(1-top_share_max) + 0.3*axis_entropy + 0.2*(filled_axes/8) + 0.1*indicator(price_spread>3.0)` [Verified: conversational-proxy/app/service/tier_signal_computer.py:205-223 (_compute_diversity_score_D)]

The `guitar` probe (`top_share_max=0.464`) hit the F1 shapeable branch at line 396 before reaching F2. It never entered F2. `exploratory` requires a more diffuse catalogue slice.

[Verified: conversational-proxy/app/service/tier_signal_computer.py:306-445 (_compute_inner)]

---

### R2.2 — Exploratory Query: `accessories`

**Query:** `accessories`

**Rationale:** A generic cross-category English noun with no brand or model signal. The seeded music/instrument catalogue returns a small cross-category result set with very even brand distribution, producing `max_axis_top_share = 0.25` — well below the F1 shapeable threshold of 0.45.

**Observed tier_signals (formal MISS read):**

```json
{
  "result_count": 38,
  "top_share_max": 0.25,
  "max_axis_top_share": 0.25,
  "axis_entropy": 0.9097,
  "filled_axes": 2,
  "diversity_score_D": 0.7229,
  "tier": "exploratory",
  "composition": "question_led",
  "classifier_path": "hot-path",
  "composition_table_live": false,
  "composition_table_live_switch_applied": false
}
```

**Classifier path verification:**
- F3 anchor: `has_brand_token=false` → SKIP
- D4 protect: `38 > 12,000` → FALSE → SKIP
- F1 decisive: `0.25 >= 0.60` → FALSE → SKIP
- F1 shapeable: `0.25 >= 0.45` → FALSE → SKIP
- F2 intractable: `38 > 12,000 AND D>=0.75` → FALSE → SKIP
- **F2 exploratory: `D=0.723 >= 0.50` → TRUE → tier=`exploratory`** ✓

[Verified: observed behavior — `__meta__.turn_classification.tier_signals` in `gap8_exploratory_accessories_en_sse.txt`]

---

### R2.3 — Cache Eviction (Gate A2)

**Command:**
```sql
docker exec conversational-proxy-postgres psql -U luigis -d conversational_proxy -c "
DELETE FROM turn1_signature_cache WHERE query_text='accessories';
SELECT 'post-delete accessories rows: ' || count(*) FROM turn1_signature_cache WHERE query_text='accessories';
"
```

**Evidence:**
```
DELETE 1
post-delete accessories rows: 0
```

Cache eviction confirmed: 1 prior row deleted, post-delete count = 0. [Verified: observed behavior — docker exec psql output]

**Live read confirms MISS:** `__meta__.cache.status = "MISS"` in the formal SSE response below. [Verified: observed behavior — SSE artifact]

---

### R2.4 — SSE Artifact Path (Round 2)

```
/home/fanderman/projects/luigis-box/.agent_context/sessions/1781106672-6469-2465b2ba685c/demo-artifacts/gap8_exploratory_accessories_en_sse.txt
```

Key event lines from the formal MISS read:

```
data: {"__meta__": {"turn_classification": {
  "mode": "product_search",
  "tier": "exploratory",
  "composition": "refinement_chips_with_hatch",
  "tier_signals": {
    "result_count": 38,
    "top_share_max": 0.25,
    "axis_entropy": 0.9097,
    "diversity_score_D": 0.7229,
    "tier": "exploratory",
    "composition": "question_led",
    "composition_table_live": false,
    "composition_table_live_switch_applied": false
  }},
  "cache": {"status": "MISS"}
}}
```

Note the split: `tier_signals.composition = "question_led"` (tier_signal_computer output — correct) vs. `turn_classification.composition = "refinement_chips_with_hatch"` (graph dispatch output — incorrect). This split is the exact locus of the Gap 8 implementation gap.

[Verified: observed behavior — full SSE in artifact file above]

---

### R2.5 — Gap 8 Functional Contract: Per-Criterion Pass/Fail (Round 2, `exploratory` tier)

| # | Criterion | Required value | Observed value | Result |
|---|---|---|---|---|
| 1 | `tier` == `exploratory` (live vocab for design `broad`) | `exploratory` | `exploratory` | **PASS** |
| 2 | `composition` == `question_led` | `question_led` | `refinement_chips_with_hatch` (graph level) | **FAIL** |
| 3 | AI block has NO products | no product SKUs in lbjson | Category chips only (Art, Zľavy a kupóny, Hudobné nástroje, Pomôcky na modelovanie), no product SKUs | PASS |
| 4 | `lbjson.question.prompt` exists | key present | Absent — lbjson has `chips`, `shape`, `chat_affordance`, `hatch`; no `question` key | **FAIL** |
| 5 | `lbjson.question.answers[]` has ≥2 filterable answers | ≥2 answer objects | Absent — no `question.answers` key | **FAIL** |
| 6 | Demoted carousel present (if supported) | carousel block | Absent — composition does not include a carousel node | **FAIL** |
| 7 | show-all link exists | browse-all or equivalent | Absent as a named `browse_all_link`; a `hatch` link exists but writes `browse_intent: true`, not a direct show-all catalogue link | **FAIL** |
| 8 | chat affordance exists | `chat_affordance` block | Present: `{"label": "Chat with me instead →", "style": "dashed_pill", "writes": {"chat_takeover_trigger": true}}` | PASS |
| 9 | turn-2 "Change the question" affordance reachable | follow-up turn emits change-the-question path | Not tested — turn-1 composition is wrong; `question_led` pivot path requires correct turn-1 first | UNTESTABLE (blocked by criterion 2 fail) |

**Summary: 3 PASS, 5 FAIL, 1 UNTESTABLE → BRANCH A (confirmed)**

---

### R2.6 — Exact Divergence Point

The `tier_signals` block emitted by the proxy's `TierSignalComputer` contains:
- `tier_signals.tier = "exploratory"`
- `tier_signals.composition = "question_led"` ← classifier computed this correctly

But the graph-level `turn_classification.composition = "refinement_chips_with_hatch"` — the agent graph is applying a different (hardcoded or fallback) composition mapping rather than consuming the composition from the tier_signal_computer output.

Additional confirming flags:
- `composition_table_live: false` — live composition-table override is not active
- `composition_table_live_switch_applied: false` — the switch that would route `exploratory` → `question_led` was evaluated and not triggered

[Verified: `__meta__.turn_classification.tier_signals` in `gap8_exploratory_accessories_en_sse.txt`]

---

### R2.7 — Corrected Verdict

**BRANCH A — divergent/absent. Implementation required in Subtask 4.**

An `exploratory`-tier query (`accessories`) is reachable with the seeded catalogue and correctly classified by `TierSignalComputer`. The classifier correctly emits `composition="question_led"` in `tier_signals`. However the graph dispatch layer overrides this to `refinement_chips_with_hatch`. The `question.prompt`, `question.answers[]`, demoted carousel, and `browse_all_link` keys are absent from the lbjson payload. The composition table live-switch exists but is inactive.

### Exact implementation surface for Subtask 4 (refined from Round 1)

1. **`conversational-search/graph.py`** — the composition dispatch in `handle_regular_turn` (and/or `_PRODUCT_SEARCH_TIER_TO_COMPOSITION` map) must route `exploratory` → `question_led`. The `_render_turn1_preview_block` must emit `question.prompt` + `question.answers[]`. The `_emit_turn2_pivot` must produce a "Change the question" affordance.
2. **`conversational-search/turn1_selector.py`** — tier-to-composition selection logic for the `exploratory` tier.
3. **`conversational-search/conversational-proxy/app/service/langgraph_client.py`** — only if `question`, `carousel`, or `browse_all_link` are not forwarded/hydrated correctly once the agent emits them.

The `tier_signals.composition = "question_led"` field confirms the proxy-side classifier is already computing the right answer — the fix is in the graph-side dispatch, not the classifier.

---

## Verification (Round 2)

**Exercised:**
- Threshold analysis: `exploratory` reachability conditions verified against `_compute_inner` boundary-rule sequence in `tier_signal_computer.py:306-445`.
- Query `accessories` confirmed to reach `tier=exploratory` via F2 tie-break (`max_axis_top_share=0.25 < 0.45`, `diversity_score_D=0.723 >= 0.50`, `result_count=38 <= 12,000`).
- Cache eviction: `DELETE FROM turn1_signature_cache WHERE query_text='accessories'` — confirmed `DELETE 1`, post-delete count 0.
- Live MISS confirmed: `__meta__.cache.status = "MISS"` in formal SSE read.
- Gap 8 functional contract evaluated against live `accessories`/`exploratory` turn-1 payload.
- Composition split documented: `tier_signals.composition="question_led"` vs. `turn_classification.composition="refinement_chips_with_hatch"`.

**Not exercised, and why:**
- Turn-2 "Change the question" affordance: cannot exercise because turn-1 composition is wrong; the `question_led` pivot path requires a conformant turn-1 first.
- `question.answers[]` click/filter follow-up: key absent from lbjson; untestable until Subtask 4 implements the composition.
- Demoted carousel: not present in current composition; untestable until implemented.
- Slovak/multilingual `exploratory` query: out of scope for this subtask (English is the authoritative baseline per task spec).
- `decisive` tier probe: not run (out of scope for Gap 8; verified code-reachable in tier-vocabulary-reconciliation.md).

---

Pre-emission self-audit: 8 citations verified, 14 sections present, 2 contradictions checked (Round 1 vs Round 2 tier mapping; tier_signals.composition vs turn_classification.composition split).
