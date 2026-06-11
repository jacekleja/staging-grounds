# v2 UX-Fidelity Campaign — Conscious Deferrals, Follow-ups, and Disposition Notes

**Purpose:** Records every gap NOT fully closed by this campaign and every documented
non-blocking follow-up, so nothing is silent. Feeds the coherence-auditor (subtask 13
verifies CL-5/CL-6/CL-7 against this document).

**Campaign scope:** Turn-1 lbjson payload fidelity. Subtasks 1–10.

---

## 1. FIX-4 / A6 — Categorical Chip Localization: DEFERRED

**Verdict: DEFERRED — upstream data-architecture change required.**

### Disposition

Categorical chip labels (`category_upto_lvl_1`, `brand`) are NOT localized by this
campaign. The `label` and `filter_value` fields in every categorical chip are both set to
the raw facet `value` from the LBX API response:

```python
# turn1_selector.py (select_chips) — line 138
{"label": v["value"], "filter_value": v["value"], "count": v["hits_count"]}
```

This is not an oversight — it is the only correct behaviour given the upstream feed.

### Why localization is impossible without upstream support

The LBX Search API facet bucket schema carries exactly two fields:

```json
{ "value": "Gitarové efekty", "hits_count": 247 }
```

There is no `label`, `display_name`, or any locale-keyed display field distinct from the
identity `value`. The full code trace is:

1. **LBX API → `search_products`:** returns `{value, hits_count}` per bucket — no
   additional fields. [Verified: docs.luigisbox.com/quickstart/search/building-custom-ui/
   § Facet Response Fields]
2. **`_compact_facets`:** for non-price facets calls `out.append(facet)` unchanged —
   there is nothing to transform. [Verified: tools.py (_compact_facets) l.112]
3. **`select_chips`:** reads `v["value"]` because it is the only label-like field
   available. [Verified: turn1_selector.py (select_chips) l.138]
4. **Test fixtures + live run data:** all model facet buckets as `{value, hits_count}` —
   no extra fields. Category chips in live sweeps show `label == filter_value` throughout
   (e.g. `{"label": "Gitarové efekty", "filter_value": "Gitarové efekty"}`).
   [Verified: fix4-a6-upstream-investigation.md § Observed Live Data]

### Catalogue DATA vs. UI STRING — binding constraint

Category and brand names in `v["value"]` are **live catalogue data**: arbitrary
per-merchant, per-query strings uploaded into the LBX index. They are NOT a fixed UI-
string set. This distinction is critical:

- **Placeholder translations MUST NOT be used here.** The R2 reversal that re-enabled
  hardcoded placeholder translations for *fixed* UI strings (the `_UI_STRINGS`/`_t`
  mechanism) does **not** license fabricating translations for arbitrary catalogue names.
  Pre-authoring placeholder sk/cs strings for category names like "Gitarové efekty" or
  "Yamaha" would fabricate catalogue data, which is a correctness violation.
- **Fixed UI strings** (e.g. gift-anchor labels, browse-hatch label, chat-affordance
  label) ARE appropriate targets for `_UI_STRINGS`/`_t` placeholder wiring — they form
  a closed, known set authored by this codebase.
- **Catalogue-data labels** are an open set authored by merchants; only the merchant can
  supply their localized equivalents, via a separate upstream field.

### What the future fix requires

The only valid path to localized categorical chip labels is upstream data-architecture
support: the merchant must upload locale-keyed category taxonomies into LBX under a
separate field (e.g. `display_label_sk`, `display_label_cs`), and the LBX API must
expose those fields in the facet bucket response. Once such a field exists, `select_chips`
can be updated to read `v.get("display_label_{lang}", v["value"])` instead of `v["value"]`.

This is outside the scope of this campaign, which operates entirely within the existing
feed contract.

**Plumbing point:** `turn1_selector.py (select_chips)` — the `label` assignment at
line 138 is the single change point once upstream locale fields exist.

**Source investigation:** `fix4-a6-upstream-investigation.md` (session
1781106672-6469-2465b2ba685c) — DEFER-RECOMMENDED disposition, full code trace and
catalogue-data/UI-string distinction documented there.

---

## 2. A10 / FIX-5 — Gift-Anchor Label Localization: WIRED WITH PLACEHOLDERS (non-blocking follow-up, NOT a deferral)

**Verdict: FIX SHIPPED — sk/cs placeholder strings wired; native-speaker polish is the
outstanding follow-up.**

### What was fixed (subtask 7)

The 4 fixed gift-advisor anchor labels are now fully routed through `_UI_STRINGS`/`_t`
with language-specific strings present for `en`/`english`, `sk`/`slovak`, `cs`/`czech`,
and the `cz` alias. The `filter_value` field is untouched (language-neutral, as required).

**Plumbing point:** `graph.py (_render_gift_advisor_takeover_block)` — line ~1565
changed from `"label": anchor.label` to `"label": _t_gift_anchor_label(raw_language,
anchor)`, routed through the new `_t_gift_anchor_label` helper (lines ~1542–1554).

**`_UI_STRINGS` keys added** (one per anchor × three language sections):

| Key | en | sk | cs |
|---|---|---|---|
| `gift_anchor_hobbies_and_interests` | "Hobbies & interests" | "Záľuby a záujmy" | "Záliby a zájmy" |
| `gift_anchor_lifestyle` | "Lifestyle" | "Životný štýl" | "Životní styl" |
| `gift_anchor_practical_useful` | "Practical / useful" | "Praktické / užitočné" | "Praktické / užitečné" |
| `gift_anchor_i_have_an_idea` | "I have an idea" | "Mám nápad" | "Mám nápad" |

The `cz` alias (`_UI_STRINGS["cz"] = _UI_STRINGS["cs"]`) at line ~279 covers Czech
automatically.

**Location of placeholder strings:** `graph.py`, in the `_UI_STRINGS` dict, within
the `"sk"`/`"slovak"` and `"cs"`/`"czech"` sections. Each non-English entry carries the
inline comment `# PLACEHOLDER — native-speaker polish pending`.

### Non-blocking follow-up: native-speaker polish

The sk/cs strings above are **machine-generated placeholders** — semantically plausible
but not reviewed by a native speaker. They are flagged with `# PLACEHOLDER — native-
speaker polish pending` in every non-English language block in `_UI_STRINGS`.

**Follow-up action required (non-blocking):** A native Slovak and native Czech speaker
should review and, if needed, revise the 4 sk and 4 cs strings in `graph.py` (`_UI_STRINGS`
`"sk"`/`"slovak"` and `"cs"`/`"czech"` sections) before these labels are considered
production-quality. The fix itself — the wiring and the routing through `_t()` — is
correct and complete. This is a translation-quality pass, not a structural fix.

**Tests:** 6 new unit tests in `tests/unit/test_shop_language_localization.py`
(`TestGiftAnchorLabelLocalization`) verify that sk/cs labels differ from English,
all 4 fixed anchors are covered per language, unknown languages fall back to English,
and `filter_value` is language-neutral. 555 unit tests pass with 0 regressions.

---

## 3. A4 — Engagement-of-Preview State Inheritance: NOT-EXERCISED (plumbing present, turn-2 verification deferred)

**Verdict: PLUMBING PRESENT; turn-2 verification not exercised by this campaign.**

### What the contract requires

A4 (Hard Commitment §3): when a user clicks a categorical chip on the turn-1 preview,
the turn-2 response must NOT re-fire the preview path — it must treat the chip click as
an engagement of the existing preview, not a fresh query.

### Current code state

The plumbing for this contract exists:

- `canonical_enums.py` l.84 — `is_engagement_of_preview` field in
  `TURN_STATE_ENVELOPE_FIELDS` Channel 2
- `graph.py (_is_browse_hatch_engagement)` l.1597 — reads the flag to detect browse-hatch
  engagements
- `graph.py (_resolve_turn2_entry_kind)` — reads `is_engagement_of_preview` to determine
  turn-2 routing; the suppression path that prevents preview re-fire on chip-click
  engagement is coded

### Why turn-2 verification was not exercised

This campaign is turn-1-scoped: all live verification, unit testing, and fidelity
checking targets the turn-1 lbjson payload. A4's contract is *only* observable on the
turn-2 response (i.e., after the user has clicked a chip and the agent processes that
second turn). There is no unit test that fires a turn-2 chip-click engagement; such a
test would require a full two-turn conversation fixture with state passing between turns.

The fidelity report (v2-mockup-ux-fidelity-report.md § A4) marks this NOT-EXERCISED
explicitly: "turn-2-only observable; this audit scope is turn-1 only."

### Disposition

This is a conscious, bounded non-exercise — not a finding of absence. The code shows the
plumbing is in place; what is unverified is the *integration contract* under a real turn-2
dispatch. A recommended optional verification (outside this campaign's scope) would be:

1. Author a two-turn conversation fixture where turn-1 emits chips and turn-2 carries
   `is_engagement_of_preview=True` in the state envelope.
2. Assert that the turn-2 handler routes through the engagement path (not preview re-fire).
3. Assert the lbjson turn-2 payload does not contain a preview block.

Until that test exists, A4 is **implemented-unverified**: the plumbing is present and
the code path reads the flag, but no automated guard prevents a future regression from
silently breaking the suppression logic.

---

## Summary

| Item | Disposition | Status | Follow-up required |
|---|---|---|---|
| FIX-4 / A6 (categorical chip labels) | **DEFERRED** — upstream field absent | Persistent gap | Upstream data-architecture: locale-keyed LBX facet fields |
| A10 / FIX-5 (gift-anchor labels) | **WIRED** — placeholder sk/cs shipped | Non-blocking follow-up | Native-speaker polish of `_UI_STRINGS` sk/cs sections in `graph.py` |
| A4 (engagement-of-preview) | **IMPLEMENTED-UNVERIFIED** — plumbing present | Turn-2 verification deferred | Optional: two-turn fixture test (see §3 above) |
