# Shop Agent UX — Handoff Pack

Three artifacts in this folder, in suggested reading order.

---

## 1. Handoff doc (.html)

**Start here.** Self-contained, opens in any browser, works offline.

Long-form vision + architecture rationale, with every proposal rendered
inline as a live React component (not a screenshot). 14 sections:

1. The diagnosis — why today's chip row fails on broad queries
2. The reframe — dispatcher → tier → composition
3. **Compositions by tier** — four rendered product-search tiers (narrow / mid / broad ★ / overwhelming)
4. **Modes** — the 7-mode dispatcher table
5. **Turn 1 → turn 2+ contracts** — 5 filmstrips showing the transitions per mode
6. Guidebook — YAML structure
7. Guardrails — 4-tier allow/redirect/deflect/refuse matrix
8. Tier classifier — where count fails, query signatures, pre-compute architecture
9. Schema — `lbjson` v2 sketch
10. Hard commitments — including the three new ones (no products turn 1, always-on chat, type-it-out first-class)
11. Phased rollout — 7 phases, cheapest first
12. First-week tasks
13. Open questions
14. File map

## 2. Interactive prototype (.html)

The design canvas where it all came together. Six sections, ~40 artboards,
pan/zoom navigation, tweaks panel for switching anchor queries
(guitar / vacuum / camera / dress / book).

Sections, in order:

- A–F · Chip row variants (the first iteration)
- G–J · Directions that abandon the chip metaphor
- K · Adaptive composition (the recommended policy)
- Signal · Query signature architecture (beyond count)
- Conversational · OOD (modes, guidebook, guardrails)
- **Turn 1 minimal · transitions** (the latest, with the no-products-on-turn-1 constraint)

Every artboard has an annotation panel beneath it naming **purpose,
strategy, what breaks, and prod commitments at risk**.

## 3. Handoff brief (.md)

A condensed engineering brief — the same content as the doc but in
plain markdown for editing, diff-tracking, and grep. Phased rollout
table, schema sketch, support-config schema, and the open-questions list.

Use this if you want to copy-paste sections into tickets, planning
documents, or PRs. The HTML doc is the primary reference; the markdown
is for working with.

---

## Three principles to anchor implementation

1. **No products in the AI block on turn 1.** Products are a turn-2+ surface, after the user has expressed enough intent to disambiguate. The shop's native catalogue list below is unaffected.
2. **Always offer a chat takeover.** Every turn-1 surface includes a subtle "Chat with me instead →" link. The user can opt out of the structured flow at any point and route to free-form conversation that inherits the search context.
3. **Type-it-out is first-class, not a fallback.** It sits next to chip rows, not behind a separate button. Users who want to describe their situation in their own words can do so on every interactive surface (turn 1 and turn 2+).

---

## Recommended sequencing (TL;DR)

1. **Phase 1** — Deepen the facet stack. One-line priority edit. Gated on a catalogue probe across the top 50 queries.
2. **Phase 2** — Add the "just browsing" hatch. One new chip variant + the `browse_intent` turn-2 contract.
3. **Phase 3** — Tier classifier in the proxy (hot-path version). Shadow mode first.
4. **Phase 4** — Question-led composition for broad tier.
5. **Phase 5** — Mode dispatcher (the largest surface; shadow + A/B + fallback).
6. **Phase 6** — Pre-computed signatures.
7. **Phase 7** — Guidebook + guardrails as versioned artifacts.

See section 11 of the handoff doc for cost/leverage notes per phase.
