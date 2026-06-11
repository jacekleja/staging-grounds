## Pre-Flight Gate Assessment

**Artifact:** docs/v2-design/v2-mockup-ux-fidelity-report.md
**Generator type:** research
**Round:** 1 of 1
**Verdict:** REQUEST-CHANGES

### Flags (2)
- **[severity: medium]** A6/claim-5 price-fallback evidence contradicts code: the report (l.96 row A6, l.96 prioritized-divergence #5) asserts "Price fallback labels are hardcoded Czech." The cited consumer `turn1_selector.py (_try_price_fallback)` l.196–206 routes every price chip label through `_t(raw_language, "price_chip_below"/"price_chip_range"/"price_chip_above")` — they ARE language-resolved, not hardcoded Czech. Defect site: report l.96 evidence cell + prioritized item #5. Downstream consumer: the operator making fix decisions on the 5 divergences. Mis-action: operator schedules a localization fix for price chips that are already localized, or distrusts the (correct) categorical-chip half of the same finding. The A6 PARTIAL verdict itself survives on the verified categorical-chip evidence (l.138 `{"label": v["value"], "filter_value": v["value"]}`), so the row verdict is NOT wrong — only this sub-claim is false. [promote: gotcha]
  evidence: turn1_selector.py l.196-206 (_try_price_fallback): `"label": _t(raw_language, "price_chip_below").format(price=p33)`
- **[severity: medium]** Executive-summary PARTIAL count contradicts the tally and per-row tables. l.3 states "Three items are PARTIAL" and enumerates (chat affordance, type-it-out, "unsafe template has been fixed"). But the Verdict Tally (l.106) and rows show FOUR PARTIAL: A6, A8, A9, A10. The summary's third enumerated item — the unsafe template fix — is graded MATCH in both the mode table (l.44) and copy table (l.67), not PARTIAL; and A6 + A10 are omitted from the headline entirely. Defect site: report l.3 executive summary. Downstream consumer: operator/orchestrator presenting this as the authoritative answer reads the headline first. Mis-action: a reader trusting the headline under-counts the open partials (3 vs 4) and mis-attributes a resolved (MATCH) unsafe item as an open partial, skewing fix triage. [promote: gotcha]
  evidence: report l.3 ("Three items are PARTIAL") vs l.106 tally (PARTIAL | 4 | A6, A8, A9, A10) and l.44/l.67 (unsafe = MATCH)

### Clean Justification
N/A — verdict is REQUEST-CHANGES.

### Summary
The 5 load-bearing verdict citations (1 DIVERGENT + 4 PARTIAL) all resolve and genuinely support their verdicts — the report's substantive findings are sound and safe to act on. Two non-blocking defects remain: a false evidence sub-claim ("price fallback labels hardcoded Czech" — code shows them localized via `_t()`), and an internal-consistency error where the executive summary says "Three PARTIAL" while the tally and rows show four and mislabels the unsafe MATCH as a partial. Both are important, neither critical; a single fix round closes them.

Findings emission self-check: 2 flags, 2 annotation-paired, 2 consequence-named.
