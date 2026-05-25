# Conversational Search Cost Calculator

`conversational-search-cost-calculator.xlsx` is a live Excel/Google Sheets calculator for estimating the monthly AWS cost of running the Luigi's Box conversational search feature. Version 2 adds firing-mode switching, M7 prompt-cache modeling, a Haiku 4.5 pre-classifier toggle, and baseline-model selection (Sonnet 4.6, GLM 4.7, GLM 4.7 Flash).

## How to open

- **Excel:** open directly. All formulas use standard XLSX syntax.
- **Google Sheets:** File → Import → Upload. Choose "Replace spreadsheet". Dropdowns and formulas survive the import.

## How to use

1. Open the **Calculator** sheet.
2. Change the **yellow cells** in the INPUTS section to match your site. The v1 inputs (rows 4–13) are unchanged; the seven new v2 inputs are in rows 15–21.
3. Read the **green TOTAL MONTHLY COST cell** (row 78, column B).
4. The **Sensitivity Table** (row 80+) shows monthly cost across four visitor levels (10K / 100K / 500K / 1M) and four engagement levels (1% / 5% / 10% / 15%), all using your current per-search cost.
5. The **Comparison Table** (row 88+) shows monthly cost for five canonical scenarios so you can see the savings stack at a glance.

## What each section does

| Section | Purpose |
|---|---|
| INPUTS (yellow) | The variables you control — v1 inputs plus 7 new v2 inputs |
| LOCKED CONSTANTS (gray) | Sonnet 4.6 + GLM 4.7 + GLM 4.7 Flash + Haiku 4.5 rate cards; do not change unless rates update |
| BASELINE MODEL RATES | Derived rows that resolve the active input/output rates from the baseline-model dropdown |
| DERIVED — COST PER SEARCH | Formula-computed per-search costs for Turn 1 (with all v2 toggles applied) and Turn 2+3 |
| MONTHLY TOTALS | Searches/month × net per-search cost; green total cell is B78 |
| SENSITIVITY TABLE | 4×4 grid of monthly costs across visitor × engagement combinations |
| COMPARISON TABLE | Five canonical scenarios side-by-side using your current B4/B5 inputs |

## What's new in v2

Seven new yellow-cell inputs were added (rows 15–21):

1. **Firing mode (B15)** — `eager` (default) fires Turn 1 on every search, matching v1 behavior. `deferred` fires Turn 1 only when the user clicks through (rate set in B16). The classifier is **skipped** in deferred mode — no pre-filter is needed when Turn 1 is opt-in.

2. **Deferred mode click-through % (B16)** — only effective when B15=`deferred`. Default 15%. Presets: LOW=5%, MID=15%, HIGH=30%.

3. **Application cache (M7) enabled (B17)** — whether our Postgres-backed Turn-1 short-circuit cache is consulted. On HIT, the proxy skips the entire LangGraph dispatch. Works for **any** baseline model (Sonnet AND GLM). Default `Yes`.

4. **Application cache (M7) hit rate % (B18)** — fraction of Turn 1 calls that hit a warm M7 cache entry. Default 50%. Presets: LOW=20%, MID=50%, HIGH=70%. This is a **placeholder** — see caveats.

5. **Classifier enabled (B19)** — whether a Haiku 4.5 pre-classifier fires before Turn 1 in eager mode. Default `No` (matches v1 behavior). The per-call cost is a derived constant (700 input + 10 output tokens at Haiku 4.5 rates = ~$0.00075/call). Skipped in deferred mode.

6. **Classifier rejection rate % (B20)** — fraction of queries the classifier rejects, avoiding the Turn 1 LLM cost. Default 40%. This is a **placeholder** — see caveats.

7. **Baseline model (B21)** — dropdown with three options:
   - `Sonnet 4.6` (default) — $3.00/$15.00 per 1M input/output tokens; Bedrock prompt caching supported.
   - `GLM 4.7` — $0.60/$2.20 per 1M input/output tokens; **no** Bedrock prompt caching.
   - `GLM 4.7 Flash` — $0.07/$0.40 per 1M input/output tokens; **no** Bedrock prompt caching.

The calculator distinguishes two cache layers:
1. **AWS Bedrock prompt caching** (provider-side, prefix cache) — automatic when baseline model supports it. Sonnet 4.6 does; GLM 4.7 and GLM 4.7 Flash do not. The calculator shows this as a read-only status row (B22).
2. **Application cache (M7)** — our Postgres-backed Turn-1 short-circuit cache. On hit, skips the entire LangGraph dispatch. Works with any baseline model — including GLM. Toggle via B17 and adjust hit rate via B18.

When you select GLM as the baseline, the Bedrock prefix-cache rows automatically zero out, but the M7 cache continues to apply if enabled. This is the correct cost model: GLM loses the provider-side prefix-cache savings but keeps the application-level short-circuit savings.

The **Comparison Table** shows five scenarios at your current B4/B5 settings:

| Row | Scenario |
|---|---|
| 1 | v1 baseline: eager, no cache, no classifier, Sonnet 4.6 |
| 2 | v2 cache only: eager + cache=Yes + 50% hit rate, Sonnet 4.6 |
| 3 | v2 cache + classifier: eager + cache + 40% rejection, Sonnet 4.6 |
| 4 | v2 deferred + cache: deferred at B16 click-through% + cache, Sonnet 4.6 |
| 5 | Current selected configuration (whatever is dialed in above) |

## Key caveats

- **Guardrail policy mix is unknown.** Production guardrail ID `lxv6kzi6cryg` — check the AWS Console for its policy list. Budget at "Content + PII + Grounding" ($0.95/1K text units) for a safe ceiling, or "Content only" ($0.75/1K) for the floor.
- **Cached prefix is an estimate.** The 4,030-token figure is based on the in-code system prompt (3,098 tokens) + search_products tool schema (932 tokens). The production system prompt is Langfuse-hosted and may differ.
- **M7 cache hit rate is a placeholder.** The 50% default is a reasonable MID estimate (LOW=20%, HIGH=70%). Production-traffic measurement — instrumenting how often the same composition payload is seen within the 5-minute TTL — should refine this number before budgeting at scale.
- **Classifier rejection rate is a placeholder.** The 40% default depends entirely on query mix and classifier tuning. Measure against production query logs before relying on classifier-savings estimates.
- **GLM does not support Bedrock prompt caching (provider-side).** When GLM 4.7 or GLM 4.7 Flash is selected, the Bedrock prefix-cache rows zero out automatically. The application-level M7 cache continues to apply — it is provider-agnostic.
- **Calculator-vs-code divergence on deferred-mode caching.** The calculator assumes the M7 application cache fires whenever Turn-1 actually fires — including click-through requests in deferred mode. The current proxy code (`signature_cache._should_consult_cache`) explicitly suppresses the M7 cache lookup when `firing_mode = deferred`, which means deferred-mode click-through requests today always pay the full Turn-1 cost. If the deferred-mode cost estimates in this calculator are to be relied on for budgeting, the suppression in `_should_consult_cache` should be removed in the proxy so the runtime behavior matches the modeled behavior. The Bedrock prefix cache is not affected — it operates at the provider level and applies automatically whenever Sonnet is the baseline.
- **GLM availability is NOT modeled.** This calculator does not check whether GLM 4.7 is available in your deployment region or whether procurement constraints apply. The operator must verify region availability and procurement separately before relying on GLM cost estimates.
- **Assumes 5-minute cache TTL is hit.** If a conversation resumes after 5 minutes, the cache-write cost is charged again.
- **LLM-calls-per-turn default is conservative.** B10=1.1 leaves headroom over the benchmark median (~1 call for the happy path, ~2 on retry). Set B10=1 to match the median; the cost delta is ~10%.
- **Chars-per-token = 4 is approximate.** This heuristic is used only for guardrail text-unit math, not for LLM token billing. JSON/code content has a lower ratio.
- **Verifier LLM call not modeled.** `verify_search_intent` runs per turn as a separate Bedrock call (~670 token input). If enabled and material, add ~$0.0001/turn manually.

## Where to look in the AWS Console (guardrail precision)

1. Go to **Amazon Bedrock → Guardrails** in the production region.
2. Find guardrail ID `lxv6kzi6cryg`.
3. Check which policy types are enabled: Content Filters, Sensitive Information (PII), Contextual Grounding Check.
4. Update the **Guardrail policy mix** dropdown in the calculator accordingly.

## Pricing sources (fetched 2026-05-14)

- AWS Bedrock pricing: https://aws.amazon.com/bedrock/pricing/
- Anthropic prompt-caching docs: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- GLM 4.7 model card (AWS Bedrock): https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-zai-glm-4-7.html

For full methodology, assumption rationale, and open questions, see the **Notes** sheet inside the workbook.
