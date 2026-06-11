## Pre-Flight Gate Assessment

**Artifact:** docs/v2-design/signature-cache-validation-freshness-report.md
**Generator type:** research
**Round:** 2 of 2
**Verdict:** PASS

### Prior-defect clearance

1. **[CRITICAL] Hardcoded authoring-session-id — CLEARED.** `smart_grep` for `1781106672` across the artifact returns zero matches. Capture paths now derive from a consumer-set `RUN_DIR` (lines 51-54: `RUN_DIR="${RUN_DIR:-./.cache-validation-runs}"`, `mkdir -p "$RUN_DIR"`, `MISS_SSE`/`HIT_SSE` composed under it); line 57 instructs the consumer to point `RUN_DIR` at its own scratch dir. No literal session-id survives; the wiring is runnable as copy-pasted (relative default dir, clean variable composition).

2. **[IMPORTANT] Unstated query_text precondition — CLEARED.** Precondition stated at line 68 with the full write chain `converse -> signature_cache.upsert -> repo.upsert -> _UPSERT_SQL`, which I re-traced end-to-end: `_UPSERT_SQL` (conversational-search/conversational-proxy/app/db/cache_repo.py:46-56) names `query_text` in the INSERT column list AND the ON CONFLICT update; both `upsert` signatures forward `query_text` [verified: conversational-search/conversational-proxy/app/db/cache_repo.py (upsert)] [verified: conversational-search/conversational-proxy/app/service/signature_cache.py (upsert)]. Non-NULL sanity check present at lines 95-104 (`query_text IS NOT NULL AS query_text_populated` selected before trusting `rows_after_delete=0`). `(tracker_id, cache_key)` fallback present at lines 104-114, gated on the sanity-check failing. Nullable-for-rolling-deploy rationale matches the migration verbatim [verified: conversational-search/conversational-proxy/alembic/versions/20260527040001_0004_v2_add_query_text.py (upgrade)].

3. **[IMPORTANT] SHA/turn_classification equality scoping — CLEARED.** Line 132 scopes byte-identical SHA + turn_classification equality to "V2-V5 product-search HIT replay" and directs consumers to treat an unexpected V7-V20 takeover/support/unsafe HIT as anomaly evidence, not a byte-identity failure. The comparator block carries an inline guard comment (line 135: "run this comparator only for V2-V5 product-search rows").

### Independent re-check (no new consumption-blocker)

- Comparator `keep` dict (line 157) — every key (`mode`, `tier`, `composition`, `prompt_fingerprint`, `tier_signals`, `dispatch_rationale_token`, `confidence_signal`, `triggering_keyword`, `verbatim_query`) is present in the reconstruction [verified: conversational-search/conversational-proxy/app/service/conversation_service.py (_turn_classification_from_cache_payload)]; no phantom field. `llm_call_count` is correctly excluded from `keep` because the HIT path reports it as `None` (function returns `llm_call_count: None` in both branches, lines 63 and 80).
- Code fences balanced: 16 backtick-fence lines = 8 complete blocks. No broken fence.
- Variable ordering: `TRACKER`/`Q`/`LANG_CODE`/`RUN_DIR`/`MISS_SSE`/`HIT_SSE` defined before use; `CACHE_KEY` fallback block placed after the sanity-check that triggers it, with a guard comment. No reference-before-definition.
- DELETE/SELECT predicates use real columns (`tracker_id`, `query_text`, `cache_key`) — all confirmed against schema/migration.
- Language claim `cs` (not `cz`) for Czech verified [verified: conversational-search/src/conversational_search/agent/mode_detection.py (_normalize_language_key)] (`"czech": "cs"`).
- `BASE` URL hardcodes tracker `8760-9189` matching `TRACKER`; consistent, documented live tracker, not a session-id.
- No drafting residue (TODO/FIXME/WIP/HTML-comment/editorial-question): zero matches.

### Clean Justification

Declared goal (artifact opening line, verbatim): "Subtasks 10 and 11 must validate every affected turn-1 prompt from a fresh cache MISS, then prove at least the cacheable product-search path replays byte-identical payload on a fresh-thread HIT." The deliverable that fulfils it: a copy-paste-runnable MISS/HIT runbook — per-row variable block, DB-enable check, query_text-keyed MISS-force, fresh-thread initiate/converse curls, non-NULL sanity SELECT, cache_key fallback, V2-V5-scoped SHA comparator, and V1-V20 query set with cacheable-vs-MISS-only legs marked. Title and deliverable match; the body would survive introduction to a fresh consumer session as "this is what was promised." Attacks run and why each failed: (a) [Unverified]/[Inferred] tags — 3 [Inferred:] tags, all ancillary (schema-derived overbroad-DELETE reasoning, no load-bearing decision rests on an unverified claim; all rest on migrations I confirmed). (b) Scope breadth — artifact stays in-lane (runbook for subtasks 10/11 cache freshness); no overrun/underrun vs the two consumer subtasks. (c) Citations resolved: 7 code-anchor citations re-traced and PASS (cache_repo upsert + _UPSERT_SQL, signature_cache upsert, conversation_service _turn_classification_from_cache_payload, migration 0004, mode_detection _normalize_language_key); the bash-log `.agent_context/logs/**` citations are session-log capture evidence, not locally retraceable, recorded in scope_not_covered. (d) Research canonical failure mode (thorough-looking prose answering a nearby-easier question, e.g. a non-executable design doc): not present — every claim maps to a runnable step a consumer copies verbatim. (e) Research structural elements: executive summary present (opening line), citation-bearing claims throughout, all four sections present.

### Summary
All three prior defects (hardcoded session-id, query_text precondition, SHA-equality scoping) are cleared and re-traced to source. Independent pass surfaced no new consumption-blocker: fences balanced, no reference-before-definition, comparator fields all real, language code correct. The runbook is consumption-ready as written for subtasks 10 and 11.

Findings emission self-check: 0 flags, 0 annotation-paired, 0 consequence-named.
