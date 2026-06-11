# Tier Vocabulary Reconciliation — Design Alias ↔ Live Value ↔ Composition

**Authored:** 2026-06-10 · **Owner:** solution-designer (Wave 1 / Subtask 3) · **Status:** ratified mapping, one live-confirmation flag open
**Scope:** Resolves the 3-vs-4 tier vocabulary mismatch (handoff-pack/brief R2 names vs. live/R4 canonical enum). **Docs/design decision only — NO code renames.**

Framing: this is a vocabulary-reconciliation mapping, not a classifier redesign. The binding operator decision (plan Scope Commitments) is to **RATIFY the LIVE/R4 tier vocabulary** and reconcile the design docs to it — never the reverse.

---

## Problems to Solve

1. The design corpus (`docs/_handoff-pack/03 · Handoff brief.md`, `docs/_handoff-pack/README.md`, `docs/v2-design/handoff-v2-final-state-plan-brief.md`) names tiers `narrow | mid | broad | overwhelming` (R2 count-coded vocabulary). The live code froze the R4 shape-coded vocabulary `decisive | shapeable | exploratory | intractable` [verified: conversational-search/src/conversational_search/agent/canonical_enums.py:37]. Operators reading design docs and live telemetry see two different vocabularies for the same four states.
2. Live probes (English conformance) observed only a subset of tiers, raising an operator question: is `decisive` actually reachable, or does `shapeable` collapse design `narrow`+`mid` in practice?
3. `zero_results` appears in live telemetry but is not one of the four design tiers — its disposition (where it maps, what it is) must be documented so it is not mistaken for a fifth tier.

This is not a trivial one-bullet subtask: three distinct reconciliation questions (vocabulary mapping, decisive reachability, zero_results disposition) plus an exact docs-to-patch list for a downstream implementer.

---

## Proposed Approach

**Ratify the live/R4 vocabulary as the canonical tier names.** Publish this reconciliation doc as the single mapping authority, then patch the design docs to carry a design-alias → live-value → composition table inline (not a rename of live values).

### Final mapping table (RATIFIED — all four rows code-proven)

| Design alias (R2) | Live/canonical value (R4) | Composition (renderer key) | Code proof |
|---|---|---|---|
| `narrow` | `decisive` | `refinement_chips` | enum `TIER_ENUM[0]` [verified: canonical_enums.py:37]; map [verified: tier_signal_computer.py:53] |
| `mid` | `shapeable` | `refinement_chips_with_hatch` | enum `TIER_ENUM[1]` [verified: canonical_enums.py:37]; map [verified: tier_signal_computer.py:54] |
| `broad` | `exploratory` | `question_led` | enum `TIER_ENUM[2]` [verified: canonical_enums.py:37]; map [verified: tier_signal_computer.py:55] |
| `overwhelming` | `intractable` | `hard_fork` | enum `TIER_ENUM[3]` [verified: canonical_enums.py:37]; map [verified: tier_signal_computer.py:56] |

The four composition values are the closed `COMPOSITION_ENUM` [verified: canonical_enums.py:42-47] and the `_TIER_TO_COMPOSITION` 1:1 dict [verified: tier_signal_computer.py:52-59]. The agent-side enum and proxy-side copy are kept in parity by a test that asserts string-level equality [verified: conversational-proxy/tests/unit/test_tier_signal_computer.py — `test_decisive_maps_to_refinement_chips` (:577) and the `(tier,composition)` parametrize block (:589)].

### `zero_results` disposition (RATIFIED)

`zero_results` is **NOT a fifth design tier.** It is a degenerate/observability state the classifier emits on the `result_count == 0` branch [verified: tier_signal_computer.py:330-338, `tier="zero_results"` at :335], listed in `TIER_EXTRA_STATES = ["zero_results", "no_facet_config"]` and explicitly annotated "observability-only, not in the 4-state machine" [verified: canonical_enums.py:38]. For rendering/observability it maps to `question_led` [verified: tier_signal_computer.py:58] — i.e. **safe question-led recovery**, the same composition as `exploratory`/`broad`. This is the "safe question-led recovery path" disposition named in plan Gap 6, confirmed by code (NOT the zero-hit/no-preview path). The `lbx.no_preview` custom event on zero hits (handoff-brief hard commitment) is a separate FE concern and is not contradicted by this mapping.

`no_facet_config` is the sibling extra-state (no live disposition asserted here; out of this subtask's scope — flagged under Unknowns).

### Decisive-reachability resolution (RATIFIED reachable; one narrow live-confirmation flag)

**`decisive` IS reachable — proven by code, not collapsed by `shapeable`.** The classifier has independent branches that return `decisive`:
- `result_count == 1` degenerate [verified: tier_signal_computer.py:340-348]
- F3 anchor short-circuit (`has_brand_token and has_model_token` with count < 200) [verified: tier_signal_computer.py:351-360]
- F1 primary gate (`max_axis_top_share >= 0.60`) [verified: tier_signal_computer.py:385-395]
- and these are directly exercised by unit tests asserting `result.tier == "decisive"` [verified: conversational-proxy/tests/unit/test_tier_signal_computer.py:223-226 (Case 2, share=0.95), :263-265 (explicit), :501-520 (F3 anchor)].

`shapeable` is the F1 moderate band (`0.45 <= max_axis_top_share < 0.60`) [verified: tier_signal_computer.py:396-406] and the diffuse default [verified: tier_signal_computer.py:436-445]. It does NOT subsume `decisive`; the two occupy disjoint `max_axis_top_share` bands. So design `narrow` and `mid` are distinct live tiers (`decisive` and `shapeable`), not a collapsed pair.

**Open flag (live-data, not code):** the English conformance probes did not happen to issue a query landing in the `decisive` band. That is a *coverage gap in the probe set*, not a reachability gap in the classifier. The docs should state: "`decisive` is reachable and unit-test-proven; whether it is *observed* in a given live probe run depends on query mix — exercise one high-`top_share`/anchor query (e.g. brand+model) to observe it live." This is the only "needs LIVE confirmation" item, and it is bounded to observation, not existence.

### What this approach SPENDS (positive tradeoff)

- **Cost — design-corpus divergence is now permanent, not healed.** By ratifying live and patching docs to carry an alias table (rather than renaming code to the design vocabulary), the R2 names `narrow|mid|broad|overwhelming` survive in the design corpus as *aliases* forever. Every future reader of `docs/_handoff-pack/*` must consult the alias table to translate to live telemetry. The alternative (rename code) would have unified the vocabulary at one cost spike; this approach trades that one-time cost for a permanent translation tax on doc-readers. The operator decision accepts this tax deliberately — renaming a frozen closed-enum that G's `turn_events.tier` column, H's prompt segments, and the parity test all depend on is the larger, riskier blast radius [verified: discovery-digest "TIER_ENUM membership changes" finding — propagates to A.4 canonical_enums, G turn_events.tier column, H prompt segments, test fixtures].
- **Cost — `zero_results`/`exploratory` share `question_led`, so composition alone is not tier-identifying.** Because both map to `question_led`, any observability consumer that keys off *composition* cannot distinguish a zero-hit recovery from a genuine broad/exploratory result. Consumers must read the `tier` field, not infer tier from composition. This is already true in code; the docs must say so to prevent a downstream consumer from collapsing them.

### Docs-to-patch list (EXACT — for Subtask 7 implementer)

Chosen target(s): **patch the two `docs/v2-design/` planning references in-place, and add NO edits to `docs/_handoff-pack/` source.** Rationale below under Rejected Alternatives (the handoff-pack is frozen prototype-handoff provenance; editing it rewrites historical artifacts). The handoff-pack is reconciled *by reference* — this doc is the authority, and the active planning doc links to it.

1. **`docs/v2-design/handoff-v2-final-state-plan-brief.md`** — PATCH. It is the active planning reference (plan Gap 6 names it conditionally "if it remains the active planning reference" — it is). Specifically:
   - The "Compositions per tier (design spec)" table (lines ~71-78): add a leading "Live value" column OR a one-line header pointing to this reconciliation doc, so the R2 tier column is annotated with its live/R4 equivalent.
   - The architecture ASCII block (lines ~45-50): the `narrow | mid | broad | overwhelming` line should carry a parenthetical or footnote to the live names.
   - Framing question 3 (line ~22): mark RESOLVED — "ratified live/R4 vocabulary; see tier-vocabulary-reconciliation.md."
2. **`docs/v2-design/tier-vocabulary-reconciliation.md`** (this file) — already the authority; no patch needed beyond what Subtask 7 may cite back.

**Explicitly NOT patched (state for the implementer):**
- `docs/_handoff-pack/03 · Handoff brief.md` and `docs/_handoff-pack/README.md` — left untouched (frozen provenance). If the operator later wants the handoff-pack itself annotated, that is a separate, explicitly-authorized edit; default is no-touch.
- All code/test files (`canonical_enums.py`, `tier_signal_computer.py`, both test modules) — reference evidence only, NEVER renamed.

Subtask 4 consumes the **tier-alias mapping table** above (design alias ↔ live value) directly.

---

## Assumptions

1. **`docs/v2-design/handoff-v2-final-state-plan-brief.md` is the active planning reference.** Consequence if wrong: if a different doc is the live planning authority, Subtask 7 patches the wrong file and the reconciliation is not surfaced where readers look. Mitigation: plan Gap 6 line 302 names this file as the conditional target; the condition holds because the in-flight plan references it.
2. **The operator wants the handoff-pack left frozen.** Consequence if wrong: if the operator wanted `docs/_handoff-pack/*` annotated in-place, Subtask 7's patch set is incomplete by two files. Mitigation: stated as an explicit choice with rationale; the operator can override by naming the handoff-pack files as in-scope.
3. **The worktree code matches what will ship.** The marathon digest warns v2 tier code was "absent from this worktree" on a *different* branch [verified: marathon-findings-digest "v2 detection/tier/composition code absent" finding]. Consequence if wrong: if the shipping branch's `tier_signal_computer.py` differs, the line citations drift. Mitigation: I read the files at the ABSOLUTE main path the delegation specified, and they exist with the asserted content; the delegation ratifies the LIVE reality, so the read code IS the truth being ratified.
4. **`question_led` is an acceptable observability composition for `zero_results`.** Consequence if wrong: if FE expects a distinct zero-hit composition, the `_TIER_TO_COMPOSITION` entry is a latent UI mismatch. Mitigation: code already does this [verified: tier_signal_computer.py:58]; ratifying live means accepting it.

---

## Unknowns

1. **Live observation of `decisive`.** Code-proven reachable; not yet observed in an English conformance probe run. Resolution path for the implementer/validator: issue one anchor query (brand+model token, e.g. a specific product model) or one high-`top_share` query against the live stack per the bring-up reference (handoff-brief §"How to run it live") and confirm `tier=decisive` in the SSE `lbx.turn_classification` event. If the live stack is unavailable, document as "unreachable-to-exercise this run; unit-test-proven" per plan Gap 6 validation guidance.
2. **`no_facet_config` disposition.** It is the sibling `TIER_EXTRA_STATES` member [verified: canonical_enums.py:38] but the delegation scoped only `zero_results`. Resolution: out of this subtask's scope; if a consumer needs it, grep `tier_signal_computer.py` for `no_facet_config` / `no-facet-config` (it appears in `CLASSIFIER_PATH_ENUM` [verified: canonical_enums.py:51-57], suggesting it is a classifier-path observability value, not a tier). Escalate to a follow-up if Subtask 4 needs it.

---

## Integration Points

**Files to be modified, in order (docs only):**
1. `docs/v2-design/tier-vocabulary-reconciliation.md` (this file) — created this subtask.
2. `docs/v2-design/handoff-v2-final-state-plan-brief.md` — patched by Subtask 7 (compositions-per-tier table, architecture block, framing-question-3 resolution).

**Consumers affected (second-order chains):**
- **Subtask 4** consumes the alias table. Second-order: if Subtask 4 keys any logic on the R2 names, it must translate through this table; the design-alias column exists precisely so Subtask 4 never hard-codes a live value it cannot find in the design corpus.
- **Subtask 7** consumes the docs-to-patch list. Second-order: Subtask 7 must NOT touch code or `docs/_handoff-pack/*` — the "explicitly NOT patched" list is its guard against scope creep.
- **Observability consumers** (G's `turn_events.tier` column, telemetry dashboards): second-order chain — because `zero_results` and `exploratory` both render `question_led`, any dashboard inferring tier from composition is wrong. The reconciliation doc states tier must be read from the `tier` field. Naming this prevents a dashboard-level collapse bug.
- **Parity test** `conversational-proxy/tests/unit/test_tier_signal_computer.py` and `conversational-search/tests/unit/test_canonical_enums.py`: NOT modified, but they are the live contract this doc ratifies. If a future agent renames live values, `test_tier_enum_no_r2_names` [verified: test_canonical_enums.py:25-31] fails — that test is the active guard enforcing "do not rename to R2 names," which is exactly the operator decision this doc records.

---

## Rejected Alternatives

1. **Rename live code values to `narrow|mid|broad|overwhelming` (adopt design enum).** Rejected on the **ownership/source-of-truth axis** (which vocabulary is canonical) — the binding operator decision (plan Scope Commitments) forbids it, and the existing `test_tier_enum_no_r2_names` guard [verified: test_canonical_enums.py:25-31] actively prevents it. Failure mode it would cause: a closed-enum rename cascading to G's `turn_events.tier` accepted-value set, H's prompt segments, the proxy parity copy, and all fixtures [verified: discovery-digest "TIER_ENUM membership changes" finding] — a large, multi-repo blast radius for a cosmetic naming preference.
2. **Patch the `docs/_handoff-pack/*` source files in place.** Rejected on the **artifact-provenance axis** (frozen historical handoff vs. live planning doc) — the handoff-pack is the prototype-handoff record of what was designed; rewriting its tier names rewrites history and breaks any external link/reference into it. Failure mode: provenance drift, where the "design intent" doc no longer reflects what was actually handed off. The chosen approach reconciles by reference (this doc is the authority) instead, leaving provenance intact.
3. **Treat `zero_results` as a fifth tier in the mapping table.** Rejected on the **state-machine-membership axis** (4-state tier machine vs. observability extras) — the code explicitly separates `TIER_ENUM` (4) from `TIER_EXTRA_STATES` [verified: canonical_enums.py:37-38, "observability-only, not in the 4-state machine"]. Failure mode: a fifth-tier framing would make Subtask 4 and downstream consumers expect a fifth composition and a fifth design alias that do not exist, inventing a phantom UI state.

---

## Verification

**Exercised:**
- All four tier→composition mappings against `_TIER_TO_COMPOSITION` and `COMPOSITION_ENUM` [verified: tier_signal_computer.py:52-59; canonical_enums.py:42-47].
- `decisive` reachability against three classifier branches + three unit tests [verified: tier_signal_computer.py:340,351-360,385-395; test_tier_signal_computer.py:223,263,501].
- `zero_results` disposition against the `result_count==0` branch and observability annotation [verified: tier_signal_computer.py:330-338,58; canonical_enums.py:38].
- R2-vs-R4 split corroborated against knowledge digests (marathon-findings, discovery-digest).

**Not exercised, and why:**
- **Live SSE probe for `decisive`** — not run; this is a docs/design subtask (no live stack bring-up authorized in scope, and the delegation forbids branch switching). Flagged as the one open live-confirmation item under Unknowns #1.
- **`no_facet_config` disposition** — not resolved; out of the delegation's `zero_results`-only scope. Flagged under Unknowns #2.
- **Running `test_canonical_enums.py` / `test_tier_signal_computer.py`** — not executed; plan Gap 6 lists this under Subtask 7's testing step, and this subtask is docs-only. The tests' *content* was read as reference evidence; their *pass/fail* is Subtask 7's validation gate.

---

## Knowledge Consulted

- `.claude/knowledge/decisions/conversational-search-v2-marathon-findings-digest.md#v2-conversational-search-marathon-agentdetectiontiercomposition-findings` — R2-vs-R4 split between handoff brief and canonical_enums; "v2 code absent from this worktree" branch caveat [verified: observed behavior — knowledge search].
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md#axis-a2-r4--diversity-primary-tier-classifier-phase-3-r4` — TIER_ENUM membership change count-coded→shape-coded and its downstream propagation set [verified: observed behavior — knowledge search].
- Code as reference evidence: `canonical_enums.py`, `tier_signal_computer.py`, `test_canonical_enums.py`, `test_tier_signal_computer.py` (citations inline above) [verified: file:line].

Pre-emission self-audit: 24 citations verified, 9 sections present, 3 contradictions checked.
Findings emission self-check: 2 discoveries, 2 emissions.
