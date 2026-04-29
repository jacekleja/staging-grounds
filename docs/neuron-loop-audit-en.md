# Neuron Loop — Missing Parts Audit

**Date:** 2026-04-27  
**Implementation status:** POC with 1 agent (Search Quality)  
**Baseline:** `neuron-loop-design.md` (2026-04-15)

---

## Critical Blockers

Things that must be in place before any production deployment.

### 1. Missing `/playbooks/*` endpoints in Gateway

The design requires agents to fetch playbooks through the gateway. The current implementation bypasses this — agents read playbooks directly from disk (`domain/` folder). In production, playbooks must live in S3.

**Related missing pieces in gateway:**
- `services/s3.py` — S3 client for playbooks + eval datasets
- `services/cache.py` — in-memory cache for playbooks (TTL 1h)
- `routes/playbooks.py` — endpoint handler

**Missing endpoints:**
- `GET /playbooks/index`
- `GET /playbooks/{path}`
- `POST /playbooks/select`

### 2. Structured LLM output via tool-use not implemented

`llm/tool_schemas.py` is marked as `"future"`. The design (section 13.1) explicitly states this is **mandatory before production** — without a `report_insights` tool schema, the LLM will return free-form JSON that will break constantly in an automated loop running thousands of times per day.

Requirement: Bedrock tool-use / function-calling for `Insight` and `Recommendation` output. If the LLM doesn't conform to the schema, the tool call fails cleanly and the run is retried.

### 3. Scheduler / multi-tenant execution does not exist

The design describes `scheduler/runner.py` (APScheduler or LangGraph native cron). Currently only `scripts/invoke_agent.py` exists for manual invocation.

### 4. Missing `POST /profiles/{tracker_id}/seed-baselines`

A cold-start endpoint where the gateway internally calls analytics-lens and computes 90-day baselines. Without it, new clients get generic thresholds on their first run instead of learned ones.

**Related missing pieces:**
- `services/analytics_lens.py` in gateway (used only for this single call)

### 5. Analytics-lens endpoints for the remaining agents don't exist

The design doc (section 4) itself states: *"The analytics-lens team should review which of these endpoints already exist, which can be extended, and which need to be built."*

`lens_client.py` likely only covers what Search Quality needs. The other 9 agents require endpoints on both the analytics-lens service side and in `lens_client.py`:

| Endpoint group | Required by agent |
|---|---|
| `GET /plp/*` | Category Navigation, Catalog Gaps, Pricing Intelligence |
| `GET /recommender/*` | Recommender Performance |
| `GET /sessions/funnel` | Funnel Health, Engagement & Retention |
| `GET /sessions/segments` | Engagement & Retention |
| `GET /sessions/ab-tests` | AB Test Monitor |
| `GET /redirects/metrics` | Redirect Effectiveness |
| `GET /products/*` | Catalog Gaps (tool-based) |
| `GET /assistants/*` | Shopping Assistant Flow |

**This is the single biggest risk to the Phase 1 timeline** — without a clear answer from the analytics-lens team on which endpoints exist, planning is blocked.

---

## Important — Not Immediate Blockers

### 6. 9 agents are missing (POC has 1)

Per the rollout plan (section 12):

| Phase | Agents | Status |
|-------|--------|--------|
| Phase 1 | Search Quality | ✓ implemented (POC) |
| Phase 1 | Catalog Gaps, Funnel Health | ✗ missing |
| Phase 2 | Pricing Intelligence, Redirect Effectiveness, Category Navigation | ✗ missing |
| Phase 3 | Recommender Performance, AB Test Monitor | ✗ missing |
| Phase 4 | Shopping Assistant Flow, Engagement & Retention | ✗ missing |

The current structure (`graph/builder.py`, `graph/nodes.py`) is monolithic for a single agent. Supporting 10 agents requires a refactor toward the `agents/base.py` BaseAgent pattern from the design (section 10.2).

### 7. Tools for tool-based agents don't exist

The Catalog Gaps agent is tool-based (get_product_interactions, get_product_appearances, get_query_results). The `tools/` directory from the design (`tools/lens_tools.py`, `tools/registry.py`) does not exist in the current implementation.

### 8. L1 Domain Knowledge is incomplete

| Category | Design specifies | Current | Missing |
|----------|-----------------|---------|---------|
| `playbooks/` | 5 files | 3 | `facet-configuration.md`, `autocomplete-tuning.md`, `recommendation-setup.md` |
| `metrics/` | 4 files | 4 | `conversion-rate.md` (also `multilingual-synonyms.md` is present but not in the design) |
| `verticals/` | 4 verticals | 1 | `fashion.md`, `electronics.md`, `automotive.md`, `general-retail.md` |
| `testing/` | 3 files | 0 | `ab-test-methodology.md`, `what-to-test.md`, `interpreting-results.md` |
| `seasonal/` | 3 files | unknown | `black-friday.md`, `seasonal-trends.md`, `sale-events.md` |

### 9. LangSmith Prompt Hub integration is missing

Prompts are hardcoded in `llm/prompts.py`. The design requires each agent to fetch the current prompt from Prompt Hub (`pull_prompt()`) at the start of every run. Without this:
- Prompts cannot be changed without a redeploy
- The LangSmith evaluation gate before deployment doesn't work

### 10. Recommendation lifecycle closure

The `PATCH /recommendations/{id}` endpoint exists. Missing:
- **Expiration policy** — auto-expire after N days (minimum viable fallback per design section 13.6)
- **Slack integration** for CSM workflow (longer term)

### 11. LangSmith service in gateway is missing

`services/langsmith_client.py` in the gateway (for eval result push + annotation webhook processing) does not exist. The webhook endpoint (`routes/webhooks.py`) exists, but the backing logic is likely incomplete.

---

## Should Be Addressed, Not a Blocker

### 12. Evidence validation in persist_layer

After the LLM produces insights, `persist_layer` should validate that referenced queries/identities actually appear in `enriched_data` from the query layer. Without this, the LLM can hallucinate entities and write fabricated insights to the database.

### 13. Insight fatigue caps

Per-agent caps (max 10 insights/run/tracker) and severity-based delivery are not implemented. Without them the CSM dashboard will be overwhelmed with low-priority insights.

### 14. Concurrency limiter in scheduler

Design (section 13.2): max 10 parallel tracker executions, per-tracker throttling, circuit breaker on the analytics-lens client. The base HTTP client has a circuit breaker; the scheduler-level concurrency limiter is missing — and the scheduler itself is yet to be built.

---

## Open Design Questions (section 14 of design — still unresolved)

1. **Who writes playbooks?** CSMs, product team, or AI-drafted with human review?
2. **Cross-client learning** — whether and when to promote working patterns into domain knowledge
3. **Recommendation conflict detection** — if two recommendations interact (e.g. ranking change + synonym change), how is the conflict detected?
4. **Analytics-lens endpoint gaps** — which endpoints exist vs. need to be built (requires coordination)
5. **Minimum viable CSM dashboard** — without a UI, insights will go unactioned

---

## Prioritized Summary

```
CRITICAL (blocker for any production deployment)
├── Structured LLM output (tool-use / function-calling schema)
├── /playbooks/* gateway endpoints + S3 integration
├── Scheduler for multi-tenant execution
└── Analytics-lens endpoint gaps → coordinate with lens team ← biggest single risk

PHASE 1 COMPLETION (to validate end-to-end pipeline)
├── seed-baselines endpoint + analytics_lens client in gateway
├── Catalog Gaps + Funnel Health agents
├── BaseAgent refactor for shared graph structure
└── Recommendation expiration policy

GRADUALLY
├── LangSmith Prompt Hub integration
├── Evidence validation in persist_layer
├── Insight fatigue caps (per-agent limit + severity routing)
├── L1 knowledge store completion (playbooks, verticals, testing, seasonal)
└── Concurrency limiter + per-tracker throttling in scheduler
```
