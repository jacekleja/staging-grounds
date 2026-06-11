# Subtask 4: FIX-4 investigate — A6 categorical-chip localization (upstream-feed; data-vs-UI-string)

**Description**: PURE INVESTIGATION (no code authoring — the wire, if warranted, is authored by the properly-typed implementer subtask 8). Investigate whether the upstream facet feed already carries REAL localized category/brand labels, then RECOMMEND wire-or-defer. Today `turn1_selector.py (select_chips)` sets categorical-facet (`category_upto_lvl_1`, `brand`) chips' `label` AND `filter_value` to the same raw `v["value"]` — no localization. The question: does the LBX facet response shape (the buckets `select_chips` iterates) include a localized display name distinct from the identity value? Investigate by: (1) reading `turn1_selector.py (select_chips)` and the facet-bucket shape it consumes (trace back to `tools.py search_products` / `_compact_facets` which produce the facet buckets); (2) determining from the facet response schema / a live MISS facet read whether buckets carry a localized-label field (e.g. a display name vs. the raw value) for sk/cs.

**Catalogue-DATA vs. UI-STRING distinction (binding, R2):** categorical chip labels are LIVE CATALOGUE DATA (arbitrary per-query category/brand names), NOT a fixed UI-string set. The R2 placeholder-translation reversal does NOT license fabricating placeholder translations here — pre-authoring placeholder translations for arbitrary catalogue category names would fabricate CATALOGUE data. So the ONLY path to a wire is REAL upstream localized labels; absent those, DEFER. State this in the finding so no downstream subtask mistakes the reversal as permitting fabricated catalogue-name translations.

**Decision rule (the investigation RECOMMENDS; subtask 8 acts on it):**
- **If real upstream localized labels EXIST** → recommend WIRE. Record what field carries the localized name, for sk and cs, with an evidence citation (code-trace or live MISS read). Subtask 8 will set chip `label` from the upstream localized name while keeping `filter_value` language-neutral, respecting the three-table coupling and the turn1_selector↔graph circular-import constraint.
- **If NO real upstream localized labels** → recommend DEFER. Do NOT fabricate sk/cs translations. Record the investigation finding (what the facet feed does/does not carry) so subtask 8 emits a no-op DEFER disposition and subtask 11 documents the gap. No code change anywhere.

Emit the recommended disposition explicitly in the return message — subtasks 8 and 11 route on it.

**Agent**: researcher (the investigation IS the entire deliverable; this subtask authors NO production code and hands NO code to the orchestrator — the conditional wire is owned solely by implementer subtask 8).

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md` (§ label/data-resolution validation missing — the A6 gap statement)
- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md` (§ turn1_selector imports graph._t via function-level import; § three language-keyed tables)

**Dependencies**: --

**Context files**:
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-mockup-ux-fidelity-report.md` — A6 PARTIAL verdict (divergence item 5) the investigation scopes against.
- `/home/fanderman/projects/luigis-box/docs/v2-design/signature-cache-validation-freshness-report.md` — IF a live facet MISS read is needed to inspect bucket shape, the freshness + decode methodology + host workarounds to apply.

**Expected output**: A finding report at `{session_dir}/fix4-a6-upstream-investigation.md` stating: what the facet feed carries (with a live or code-traced evidence citation), the recommended WIRE-or-DEFER disposition, and the catalogue-data-vs-UI-string note. NO diff and NO code — recommendation only. Return message states the recommendation in one line: `FIX-4: WIRE-RECOMMENDED — <field carrying localized name>` or `FIX-4: DEFER-RECOMMENDED — <one-line reason: no upstream localized label>`.

**active_rubrics**: ["generator-preflight"]

**Design phase**: no with reason research-only-no-design-decision — the decision rule is given; the deliverable is the upstream-feed fact + the rule-determined recommendation.

**UX phase**: no — backend label-resolution investigation; no user-facing surface layout. No new IA surface.
