# Handoff Brief — Plan: v2 Conversational-Search to Final-State Sketch Realization

**Authored:** 2026-06-09 | **Session:** `1781009183-6469-01cc30565818`
**For:** Next session whose job is to produce the implementation plan

---

## Your task (next session)

Produce a thorough, phased **implementation plan** that takes the v2 conversational-search agent + proxy build from its current state (documented below) to the fully-realized intended final state defined in the handoff-pack design sketches. The plan is the deliverable — do not implement anything. The plan must cover: which gaps to close and in what order, the change surface for each gap, testing/validation approach, and how each gap maps to the design's 7-phase rollout sequence. Before producing the plan, resolve the four framing questions below with the operator on your first turn — their answers branch the scope significantly.

---

## OPEN FRAMING QUESTIONS — resolve with operator on turn 1

These branch the plan materially. Do not skip them.

1. **Scope of "final state":** Full 7-phase sketch realization (tier classifier with design vocabulary, question-led composition, pre-computed signatures, guidebook + guardrails as versioned artifacts) — or close only the 8 conformance gaps identified this session? The two are compatible but the plan structure and effort differ by ~3x.

2. **Multi-language in scope?** The product serves `.sk` shops (`language: sk`). Detection is English-only static keywords. Slovak/Czech `unsafe` prompts bypass the hard-refuse short-circuit (filed: `iss_3712bb402a94`, med). Is Slovak/Czech mode detection required for final state? And if yes: quick keyword extension (config + code, lowest blast radius) or generalized multi-language detection layer (changes the keyword-only dispatch contract — architecture work)?

3. **Tier vocabulary — which is truth?** ~~Live emits `exploratory` / `shapeable` / `zero_results`. Design specifies `narrow` / `mid` / `broad` / `overwhelming`. Signals are computed correctly; only the vocabulary/mapping differs. Adopt the design enum (rename live values) or ratify the live vocabulary and update the design?~~ **RESOLVED** — ratified live/R4 vocabulary as canonical; see `docs/v2-design/tier-vocabulary-reconciliation.md`.

4. **Composition-fidelity bar:** For `gift_advisor` and `comparison`, how exactly must the realized build match the prototype's specific UI compositions? Pixel-matched (anchored category chips, side-by-side table) vs. functionally equivalent (correct mode + no-products-on-turn-1 + conversational)? This determines whether gap 4 and gap 5 are template edits or structural work.

---

## Intended final state

Source: `docs/_handoff-pack/03 · Handoff brief.md`, `docs/_handoff-pack/README.md`, `docs/_handoff-pack/01 · Handoff doc.html`.

### Architecture

```
Turn-1 request
      │
      ▼
MODE DISPATCHER  (keyword + LLM-guarded detection)
  product_search │ gift_advisor │ comparison │ advice
  support ──► template
  out_of_scope ──► LLM
  unsafe ──► refuse
      │ (product_search only)
      ▼
TIER CLASSIFIER  (signature lookup or hot-path derivation)
  narrow │ mid │ broad │ overwhelming
  (live/R4: decisive │ shapeable │ exploratory │ intractable — see tier-vocabulary-reconciliation.md)
      │
      ▼
COMPOSITION RENDERER  (tier-specific UI, NO products on turn 1)
  refinement_chips │ refinement_chips_with_hatch │ question_led │ hard_fork
```

### Three anchoring principles (must hold everywhere)

1. **No products in the AI block on turn 1.** Product cards are never turn-1 content. The shop's native catalogue list below is unaffected.
2. **Always-on chat takeover.** Every turn-1 surface carries a dashed-border "Chat with me instead →" link.
3. **Type-it-out is first-class.** An inline text input sits alongside chip rows — not behind a separate button.

### Hard commitments (from §3 of the engineering brief)

- One LLM call on turn 1; no second compose pass.
- `lbjson` schema with `chips:[{label, filter_value, facet, count}]` — extended but not replaced.
- `lbx.no_preview` custom event on zero hits — must not break.
- Engagement-of-preview state inheritance — turn-1 chip click must not re-fire preview on turn 2.
- `work_status` pill sequence stays intact.
- Language-aware label resolution (Slovak, Czech, English at minimum).
- **NEW:** turn 1 no products in AI block.
- **NEW:** always-on chat affordance.
- **NEW:** anchored gift chips (category-shaped: "Hobbies & interests", "Lifestyle", "Practical / useful", "I have an idea") — NOT model-generated personality guesses.

### Compositions per tier (design spec)

> **Vocabulary note:** the design corpus uses R2 (count-coded) tier names; live/R4 code uses shape-coded names. The mapping is 1:1 — see `docs/v2-design/tier-vocabulary-reconciliation.md` for the full ratified table. The "Live value" column below is a quick reference.

| Tier (design alias) | Live value (R4) | Composition | What it renders |
|---|---|---|---|
| `narrow` | `decisive` | `refinement_chips` | 2–4 inline axis-prefixed chips; no browse hatch; skip below 30 results |
| `mid` | `shapeable` | `refinement_chips_with_hatch` | 4 chips (deepened stack: lvl_2→lvl_1→brand→price) + "Just browsing" quiet link + always-on chat affordance |
| `broad` | `exploratory` | `question_led` | One diagnostic question + 2 answer cards + sub-search carousel (demoted) + "Show all N →" + chat affordance + "← Change the question" on turn 2 |
| `overwhelming` | `intractable` | `hard_fork` | 2 strong fork cards + "Show all N sorted by popularity" link; no carousel |

### Conversational compositions (§6)

- **gift_advisor:** turn-1 chips are anchored categories (guidebook-stable, not LLM-generated); `must_ask_before_recommending`; type-it-out always present.
- **comparison:** detectable mid-flow on `vs`/`or`/`compare` tokens in any turn; swaps to side-by-side for one turn then restores prior mode (LIFO mode stack, max depth 3); inline mode-shift note on detection.
- **advice:** three parallel routes on turn 1 — anchored chips, type-it-out, AND chat link.
- **support / out_of_scope / unsafe:** template (support, unsafe) or LLM-with-guidebook (out_of_scope); thin UI; no product surface even if LLM tries.

### 7-phase rollout (design sequencing)

| Phase | Description | Design signal |
|---|---|---|
| 1 | Deepen facet stack (lvl_2→lvl_1→brand→price chip priority) | `kytara` emits Electric/Acoustic/Classical/Bass |
| 2 | "Just browsing" hatch + `browse_intent` turn-2 contract | Variant E pattern |
| 3 | Tier classifier hot-path in proxy; shadow mode first | `tier` + `composition` in `lbjson` v2 |
| 4 | Question-led composition for broad tier | `question:{prompt, answers[]}` server event |
| 5 | Mode dispatcher (keyword → LLM fallback; shadow + A/B + fallback) | New `mode` field in `lbjson` v2 |
| 6 | Pre-computed signatures (nightly top-10K job; ~400-token blob in system prompt) | Faster/cheaper turn 1 |
| 7 | Guidebook + guardrails as versioned artifacts (`agent/guidebook/v{N}.yaml`) | Compliance audit trail |

---

## Current state (evidence-backed, English — confirmed this session)

**Primary evidence source:** `.agent_context/sessions/1781009183-6469-01cc30565818/demo-artifacts/mode-conformance-report-english.md`

**Caution on the Slovak report:** `demo-artifacts/mode-conformance-report.md` (Slovak) UNDER-REPORTED mode-detection capability because the keyword detectors are English-only. Slovak queries collapsed `gift_advisor`, `comparison`, and `advice` to `product_search` — masking that those modes work correctly in English. Use the English report as authoritative.

### Branches under test (confirmed correct, NOT prod)

| Repo | Branch | Commit |
|---|---|---|
| `conversational-search` (agent) | `feat/v2-campaign-rebased` | `0d33694` |
| `conversational-search/conversational-proxy` | `reconcile/proxy-v2-the-rest-on-origin-master` | `b8ca055` (49 commits ahead of origin/master) |

Path: `/home/fanderman/projects/luigis-box/conversational-search`

### Mode dispatch conformance (English)

| Mode | Verdict | Evidence |
|---|---|---|
| `product_search` broad (`guitar`) | **MATCH** — chips only, no products, chat affordance + hatch | English conformance report §comparison table |
| `product_search` narrow (`Yamaha F310`) | **DIVERGENT** — full product table with prices in turn-1 AI block (10 SKUs, markdown tables) | Confirmed language-independent; same in Slovak |
| `gift_advisor` | **PARTIAL** — dispatches correctly; opens with clarifying question, NOT anchored category chips | Confirmed distinct mode in English; composition wrong |
| `comparison` | **PARTIAL** — dispatches correctly; qualitative prose, NOT side-by-side composition | Confirmed distinct mode in English; composition wrong |
| `advice` | **DIVERGENT** — collapses to `product_search`; keyword list too narrow (`"should I"`, `"is X worth"` miss `"how do I choose"`) | Genuine implementation gap, not language issue |
| `support` | **MATCH** — 0 LLM calls, deterministic template, sub-second | CONFIRMED working English; diagnosis report §controlled passing path |
| `out_of_scope` | **MATCH** — 0 LLM calls, clean deflection | CONFIRMED working English |
| `unsafe` | **PARTIAL** — dispatches correctly, 0 LLM calls, BUT template contains prohibited softening: _"I can still help with safe shopping questions."_ | Design §3: "softening trains users to expect a softer wall" |

### Tier vocabulary divergence (confirmed)

| Live value | Design spec value | Notes |
|---|---|---|
| `shapeable` | `narrow` or `mid` | Both broad and narrow product_search showed `shapeable` |
| `exploratory` | `broad` | gift_advisor, comparison |
| `zero_results` | (no direct design equivalent; advice fallback path) | Signals computed correctly; vocabulary only differs |

Tier signals ARE computed (`result_count`, `top_share_max`, `axis_entropy`, `filled_axes` all present). Only the vocabulary/mapping to the design enum differs.

### The signature-cache HIT path — correction

The prior session's handoff brief (v2-showcase) presented the signature cache / cache-HIT as "the freshest, most demoable v2 win." **The operator clarified this session: the cache HIT path is already live on main — it is NOT a v2 differentiator.** Do not present it as a v2 feature in the plan.

### Multi-language (Slovak) — scope status

Detection is English-only static keyword/substring matching (`_normalize_dispatch_text`, `_match_static_keyword`, `_match_support_pattern` in `graph.py`). Slovak prompts fall through to `product_search` fallback for ALL non-product modes. This was **deliberately excluded from the prior campaign's scope** (plan of record states this 3 times). It is NOT a regression.

Safety consequence: Slovak `unsafe` prompts bypass the hard-refuse short-circuit (LLM call made; mode = `product_search`). Filed as **`iss_3712bb402a94`** (med severity).

---

## The gap set — delta to close

Ordered by severity/design-commitment weight:

| # | Gap | Design commitment violated | Evidence |
|---|---|---|---|
| 1 | **Products in turn-1 AI block on narrow `product_search`** | Strictest hard rule; "turn 1 has no products in the AI component" | English conformance report §3b; language-independent |
| 2 | **`advice` keyword-coverage gap** — collapses to `product_search` | Mode dispatcher must route all 7 modes | English conformance report §3a, §per-mode table |
| 3 | **`unsafe` refuse-template softening prose** — "I can still help with safe shopping questions" | §3 hard commitment: "refuse-tier responses never include softening prose" | English conformance report §3c |
| 4 | **`gift_advisor` composition** — opens with clarifying question; design requires anchored category chips as turn-1 chips | §3 "anchored gift chips, not personality guesses"; §6 gift_advisor specifics | English conformance report §comparison table |
| 5 | **`comparison` composition** — qualitative prose; design requires side-by-side handler | §6 comparison specifics; §4 mode table in handoff doc | English conformance report §comparison table |
| 6 | **Tier vocabulary reconciliation** — live (`exploratory`/`shapeable`/`zero_results`) vs. design enum (`narrow`/`mid`/`broad`/`overwhelming`) | Schema §4 specifies the design enum in `lbjson` v2 | English conformance report §3d |
| 7 | **Multi-language detection** incl. safety-critical `unsafe` bypass | §3 "language-aware label resolution"; safety angle on `unsafe` | Diagnosis report; `iss_3712bb402a94` |
| 8 | **Broad-tier `question_led` composition + per-composition affordance set** — not verified against design §3/§6/§7 | §6 question_led spec; guardrails matrix §7 | Not exercised this session; verification needed |

Gaps 1–7 are CONFIRMED. Gap 8 is an open verification needed before the planner can assess it.

---

## Code loci for the planner

### Agent (`conversational-search` repo)

| File | Relevant to |
|---|---|
| `src/conversational_search/agent/graph.py` | `_dispatch_for_query` (mode detection logic); `_OUT_OF_SCOPE_KEYWORDS` (~line 245); `_match_static_keyword` (~586); `_match_support_pattern` (~565); `dispatch_route` (~2932); `create_graph` (~3282) |
| `src/conversational_search/agent/support/8760-9189.yaml` | Per-tracker support deflection config — `detect` phrases (English-only); redirect targets; response prose |

### Proxy (`conversational-search/conversational-proxy` repo — independent nested git repo)

| File | Relevant to |
|---|---|
| `app/service/tier_signal_computer.py` | Tier signal computation (live vocabulary source) |
| `app/service/conversation_service.py` | Turn routing; composition selection |
| `app/service/signature_cache.py` | Turn-1 signature cache (already on main; not a v2 differentiator) |
| `app/clients/langgraph_client.py` | Reads `mode`/`tier`/`composition` from agent's `lbx.turn_classification` event; does not synthesize mode |

**Topology note:** `conversational-proxy` is an independent nested git repo under `conversational-search`. Changes to each repo are tracked and committed separately. The plan must account for this.

---

## How to run it live (canonical bring-up reference)

Single authoritative source: `.agent_context/sessions/1781001505-6469-00ebdec6795a/cache-hit-live-verification-report.md` §2.

Essentials (re-confirmed working this session):

```
# Agent (LangGraph), port 2024
cd /home/fanderman/projects/luigis-box/conversational-search
nohup poetry run langgraph dev --host 127.0.0.1 --port 2024 --no-browser > langgraph.log 2>&1 &

# Proxy, port 8000 — psycopg+ DSN form is load-bearing (alembic + runtime both require it)
cd /home/fanderman/projects/luigis-box/conversational-search/conversational-proxy
export ENV=development
export CONVERSATIONAL_CACHE_DATABASE_URL='postgresql+psycopg://luigis:<PW>@127.0.0.1:15432/conversational_proxy'
nohup poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 > proxy.log 2>&1 &

# Redis seed (tracker → assistant mapping; catalog access)
ENV=development poetry run python scripts/setup_dev.py
```

**Dependencies:**
- Docker container `conversational-proxy-postgres` on host port 15432 (db: `conversational_proxy`, user: `luigis`)
- Native redis-server on port 6390 (proxy `.env`: `REDIS_URL=redis://localhost:6390/1`)
- AWS Bedrock creds in `~/.aws` + `AWS_BEARER_TOKEN`
- Schema: `poetry run alembic upgrade head` (NOT `setup_dev.py` — setup_dev.py is Redis-only)

**Query contract:** `POST /api/v1/conversation/{tracker_id}/initiate` (body: `{"language":"sk"}`) → `thread_id` → `POST .../converse` (body: `{"prompt":"...", "device_user_id":"<10-25 digit string>"}`)

Tracker `8760-9189` is the seeded development tracker.

---

## Reusable references from the prior campaign

Plan of record (all subtasks completed): `docs/v2-design/plan-v2-ux-followup-fixes.md`

Two structural patterns from that plan are reusable for the new plan:
- **Parallelization graph** (§ "Parallelization Graph") — the coupling analysis + parallel/sequential subtask grouping approach.
- **Housekeeping pattern** — the cycling-terminal / session-audit / commit+push / knowledge-hygiene trailing subtasks pattern.

Prior defect set (A/B/C/D/F/G/H all completed) is the baseline this session confirmed against.

---

## Caveats and operator-dialogue corrections

1. **Cache HIT is NOT a v2 differentiator.** The prior handoff brief framed `signature_cache` / cache-HIT as the headline v2 win. The operator clarified this session: it is already live on main. Do not include it as a gap to close or a v2 milestone.

2. **Slovak is deliberately out of scope for the prior campaign.** The prior campaign plan explicitly excluded Slovak-language detection 3 times. The conformance collapse on Slovak is NOT a regression — it is an acknowledged gap carried forward. It has a safety consequence (unsafe bypass) that may make it in-scope for final state, but that is the framing question #2 above.

3. **Branch confirmed correct.** The agent is on `feat/v2-campaign-rebased` @ `0d33694` and the proxy is on `reconcile/proxy-v2-the-rest-on-origin-master` @ `b8ca055`. The prior campaign's showcase session confirmed these branches. This session re-confirmed. Do not assume main or origin/master.

4. **English is the correct test language.** The Slovak conformance report (`demo-artifacts/mode-conformance-report.md`) masked 2 of 7 modes as "collapsed" because detection is English-only. The English report (`mode-conformance-report-english.md`) is the authoritative current-state verdict.

5. **`advice` gap is English-confirmed.** The prior brief inherited from the showcase session did not surface the `advice` collapse explicitly. This session confirmed it is a genuine English keyword-coverage gap (phrase `"how do I choose"` not in static list), not a language issue.

---

## Source / evidence map

| What | Path |
|---|---|
| **Primary conformance verdict (English)** | `.agent_context/sessions/1781009183-6469-01cc30565818/demo-artifacts/mode-conformance-report-english.md` |
| **Slovak report (under-reported — cautionary)** | `.agent_context/sessions/1781009183-6469-01cc30565818/demo-artifacts/mode-conformance-report.md` |
| **Root-cause diagnosis (English-only detection)** | `.agent_context/sessions/1781009183-6469-01cc30565818/demo-artifacts/support-deflection-diagnosis.md` |
| **Raw SSE captures (per-mode)** | `.agent_context/sessions/1781009183-6469-01cc30565818/demo-artifacts/modes/` |
| **Engineering brief (final architecture + compositions + phases)** | `docs/_handoff-pack/03 · Handoff brief.md` |
| **3 anchoring principles + 7-phase TL;DR** | `docs/_handoff-pack/README.md` |
| **Full vision doc (§3 compositions, §4 dispatcher table, §7 guardrails)** | `docs/_handoff-pack/01 · Handoff doc.html` (grep sections; 1.8 MB — do not read whole file) |
| **Prior campaign plan of record (all done)** | `docs/v2-design/plan-v2-ux-followup-fixes.md` |
| **Live bring-up (commands, ports, env)** | `.agent_context/sessions/1781001505-6469-00ebdec6795a/cache-hit-live-verification-report.md` §2 |
| **Prior handoff brief (showcase session — structural template)** | `.agent_context/sessions/1781001505-6469-00ebdec6795a/v2-showcase-handoff-brief.md` |
| **Knowledge: cache signature / bringup digest** | `.claude/knowledge/decisions/conversational-search-v2-cache-signature-cache-bringup-digest.md` |
| **Knowledge: proxy DSN constraint** | `.claude/knowledge/constraints/conversational-proxy-cache-dsn-postgresql-psycopg.md` |
| **Knowledge: proxy structural (nested git repo)** | `.claude/knowledge/constraints/conversational-proxy-structural.md` |
| **Knowledge: LangGraph dev server + store persistence** | `.claude/knowledge/constraints/langgraph-dev-server-store-persistence.md` |
| **Open issue: Slovak unsafe bypass** | `iss_3712bb402a94` (med severity) |
