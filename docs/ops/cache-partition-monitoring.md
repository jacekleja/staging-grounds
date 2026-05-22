# cache-partition-monitoring.md

Operator runbook for `turn1_signature_cache` — the HASH-partitioned Postgres table
introduced by Subtask 7.1 (Axis B R1 §Postgres-Schema Design Sub-Deliverable).

---

## Partition Inventory

Query the live row count across all 16 partitions:

```sql
SELECT relname, n_live_tup
FROM pg_stat_user_tables
WHERE relname LIKE 'turn1_signature_cache%'
ORDER BY relname;
```

**Expected steady-state:** each of the 16 partitions holds approximately 1/16 of the
total cache size.  A partition that exceeds **4× the per-partition average** is a
hot-shop hash collision indicator — one `tracker_id` value dominates that partition's
hash bucket.  Remediation: identify the offending shop via
`SELECT tracker_id, COUNT(*) FROM turn1_signature_cache_pN GROUP BY 1 ORDER BY 2 DESC LIMIT 5;`
(substitute the hot partition name).  If the imbalance is sustained, consider bumping
the modulus to 32 in a v3 migration.

---

## Index Usage

Confirm both indexes are being exercised in production:

```sql
SELECT indexrelname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE relname LIKE 'turn1_signature_cache%'
ORDER BY indexrelname;
```

- `idx_turn1_signature_cache_prompt_fingerprint` — should accumulate scans as the
  Subtask 7.3 invalidation worker fires on Axis F staleness signals.  Zero scans after
  Axis F traffic starts is a signal the worker is not running.
- `idx_turn1_signature_cache_expires_at` — should accumulate scans on every TTL
  sweep by the Subtask 7.3 invalidation worker.

---

## TTL Vacuum Lag

Check for rows that have passed their TTL ceiling but have not yet been evicted:

```sql
SELECT COUNT(*)
FROM turn1_signature_cache
WHERE expires_at < now();
```

A non-zero result after the Subtask 7.3 invalidation worker has been running indicates
the worker is stalled or behind on vacuum.  Steps:
1. Check the worker process logs for errors.
2. Confirm `AUTOVACUUM` is not suppressed on these tables (`pg_stat_user_tables.last_autovacuum`).
3. Issue a manual `VACUUM ANALYZE turn1_signature_cache;` if lag exceeds one worker
   cycle window.

---

## PgBouncer Pool Sizing

The existing PgBouncer pool was originally sized for proxy authentication lookups and
`turn_events` writes (per Subtask 4.1 digest §Unresolved Threads).

Adding the cache introduces approximately **1 additional query per turn-1 cache HIT**
(a `SELECT … WHERE cache_key = $1 AND tracker_id = $2`) — same order of magnitude as
the existing auth lookup.  This doubles per-turn read queries at a 100% hit rate;
at the expected 70% hit rate the increase is ~0.7 queries/turn.

**Action required before M7 traffic ramp:** re-budget PgBouncer `max_client_conn` and
`default_pool_size` with the ops team.  Confirm pool headroom absorbs the additional
read load without connection queuing under peak traffic.

---

## Rollback Procedure

1. Confirm no application code references the table:
   ```sh
   grep -rn 'turn1_signature_cache' conversational-search/conversational-proxy/app/
   ```
   Expected: zero matches.  If Subtask 7.2 has already shipped, coordinate with the
   engineering team before proceeding — the cache writer will need to be disabled first.

2. Apply the rollback DDL:
   ```sh
   psql "$DATABASE_URL" -f conversational-search/migrations/v2_create_turn1_signature_cache.rollback.sql
   ```
   `DROP TABLE … CASCADE` removes the parent table and all 16 partitions (p0..p15)
   together with their indexes in one statement.

3. Verify cleanup:
   ```sql
   SELECT relname FROM pg_stat_user_tables WHERE relname LIKE 'turn1_signature_cache%';
   ```
   Expected: zero rows.

4. Check for stranded references in downstream code:
   - `conversational-search/conversational-proxy/app/` — Subtask 7.2 cache key composer
     (not yet written at v2.0; will land at M7 traffic ramp).
   - Any Subtask 7.3 invalidation worker process — must be stopped before the table
     is dropped to avoid SQL errors in flight.

---

## Capacity Planning — Cold-Start Cost Spike on Cache Invalidation

When `composition_model_version` (from Axis A) bumps, the entire cache invalidates.
This is the intended behavior (correctness over hit-rate) but ops must prepare for
approximately one day of MISS-dominant traffic per Axis A version bump.  During this
window the per-turn cost reverts to the pre-cache baseline (~$0.012–$0.022).  Alert on
`turn_events.cache_hit = FALSE` rate exceeding 90% for more than 15 minutes after a
known A-version bump; this is expected, not an incident.

---

*Source: axis-b-r1.md §Postgres-Schema Design Sub-Deliverable items 3–5;
firing-mode-toggle-synthesis.md §Subtask 7.1–7.4.*
