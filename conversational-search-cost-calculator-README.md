# Conversational Search Cost Calculator

`conversational-search-cost-calculator.xlsx` is a live Excel/Google Sheets calculator for estimating the monthly AWS cost of running the Luigi's Box conversational search feature.

## How to open

- **Excel:** open directly. All formulas use standard XLSX syntax.
- **Google Sheets:** File → Import → Upload. Choose "Replace spreadsheet". Dropdowns and formulas survive the import.

## How to use

1. Open the **Calculator** sheet.
2. Change the **yellow cells** in the INPUTS section (rows 4-10) to match your site:
   - **Monthly site visitors** — your expected monthly traffic.
   - **Engagement rate (CTR, %)** — percentage of visitors who start a conversational-search session (default 5%).
   - **Avg input tokens per LLM call** — advanced knob; default 6,000 is the measured median across a typical 3-turn conversation.
   - **Avg output tokens per turn** — default 1,450 is the Turn-2 benchmark median.
   - **LLM calls per turn** — default 3 is a conservative buffer over the benchmark median (~2 LLM calls/turn: 1 tool-decision call + 1 final-response call). The `search_products` invocation itself is a Luigi's Box Search API call, not a Bedrock LLM call. Set to 2 to match the documented median; cost delta is ~32%.
   - **Guardrails enabled** — Yes/No dropdown.
   - **Guardrail policy mix** — Content only / Content + PII / Content + PII + Grounding.
3. Read the **green TOTAL MONTHLY COST cell** (row 40, column B).
4. The **Sensitivity Table** at the bottom shows monthly cost across four visitor levels (10K / 100K / 500K / 1M) and four CTR levels (1% / 5% / 10% / 15%), all using your current per-conversation cost.

## What each section does

| Section | Purpose |
|---|---|
| INPUTS (yellow) | The variables you control |
| LOCKED CONSTANTS (gray) | Sonnet 4.6 rate card + cache prices — do not change unless rates update |
| DERIVED — PER CONVERSATION | Formula-computed cache, input, output, and guardrail costs per conversation |
| MONTHLY TOTALS | Conversations/month and total monthly cost |
| SENSITIVITY TABLE | 4x4 grid of monthly costs across visitor x CTR combinations |

## Key caveats

- **Guardrail policy mix is unknown.** The production guardrail ID is `lxv6kzi6cryg`. Its policy list is not in the codebase — check the AWS Console. Until known, budget at "Content + PII + Grounding" ($0.95/1K text units) for a safe ceiling, or "Content only" ($0.75/1K) for the floor.
- **Cached prefix is an estimate.** The 3,500-token figure is based on a comparable Bedrock Sonnet 4.6 agent. The actual production system prompt is Langfuse-hosted and has not been directly measured.
- **`search_products` tool-result JSON size varies.** The result payload (driven by the `size` parameter and number of matched products) dominates dynamic input tokens. Production-traffic sampling would tighten the 6,000-token input default — until then, the default is the blended-median estimate from the research artifact.
- **LLM-calls-per-turn default is conservative.** Default 3 leaves headroom; the documented benchmark median is ~2 (tool-decision call + final-response call). Set B8=2 to match the benchmark median. The cost delta is ~32%.
- **Chars-per-token = 4 is approximate.** This heuristic is used only for guardrail text-unit math, not for LLM token billing. JSON/code content has a lower ratio.
- **Assumes 5-minute cache TTL is hit.** If a conversation is interrupted and resumes after 5 minutes, the cache-write cost is charged again.

## Where to look in the AWS Console (guardrail precision)

1. Go to **Amazon Bedrock → Guardrails** in the production region.
2. Find guardrail ID `lxv6kzi6cryg`.
3. Check which policy types are enabled: Content Filters, Sensitive Information (PII), Contextual Grounding Check.
4. Update the **Guardrail policy mix** dropdown in the calculator accordingly.

## Pricing sources (fetched 2026-05-14)

- AWS Bedrock pricing: https://aws.amazon.com/bedrock/pricing/
- Anthropic prompt-caching docs: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

For full methodology, assumption rationale, and open questions, see the **Notes** sheet inside the workbook.
