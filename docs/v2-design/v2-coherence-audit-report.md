# V2 Final-State Gap-Closure — Coherence Audit (Subtask 12)

**Verdict: APPROVE.** Cross-artifact coherence holds. 25/27 checklist IDs PASS with a substantiating-artifact pointer; 2 are forward obligations (commit) not yet violable. Bucket B confirmed consciously deferred in both plan and conformance report. Zero cross-artifact contradictions. One benign topology observation (mode_detection file+dir) resolved as intended structure with no import ambiguity.

This audit is completeness/coherence only — no live tests re-run, no code-quality review (that is owned by `validate-subtask-4.json` and `validate-subtask-7.json`, both `approve`).

---

## 1. Implementation-Checklist Grading (C-01 .. C-27)

| ID | Verdict | Evidence pointer |
|---|---|---|
| C-01 | PASS | conformance Gap-1 row V1 Yamaha (`text_len=0`, deflection only, no products); validate-subtask-4 traces graph.py:2278/2325/2380-2382 + renderer 1145-1206 |
| C-02 | PASS | en.yaml `advice.detect` carries `"how do i choose"` (gap-2 phrase) — confirmed on disk |
| C-03 | PASS | conformance Gap-7 unsafe row: hard refusal text, no softening/redirection |
| C-04 | PASS | conformance Gap-4/5 gift row: `mode=gift_advisor`, chips with `filter_value`, `source=guidebook` |
| C-05 | PASS | conformance Gap-4/5 row: `shape=chat_takeover`, no products turn-1, `llm_call_count=1` |
| C-06 | PASS | conformance Gap-5 comparison row: `shape=side_by_side_comparison`, columns present |
| C-07 | PASS | conformance Gap-5 row: `mode_shift_note` present (one-turn shift + restoration) |
| C-08 | PASS | tier-vocabulary-reconciliation.md ratified table; validate-subtask-7 traces brief patches |
| C-09 | PASS | reconciliation doc: `shapeable` observed both narrow/mid-like; `zero_results` extra-state; `decisive`/`intractable` documented canonical (code-proven) |
| C-10 | PASS | `_resolve_request_language` + per-language en/sk/cs YAMLs in mode_detection.py; conformance multilingual-dispatch row PASS |
| C-11 | PASS | conformance Gap-7 row: sk/cs unsafe, `llm_call_count=0`, audit fields; matches architecture contract A4 |
| C-12 | PASS | per-language YAML registry (generalized layer, not ad-hoc tuples) per architecture contract Integration Points |
| C-13 | PASS | gap-8-question-led-verification.md is the early gate (BRANCH A divergence recorded) |
| C-14 | PASS | conformance Gap-8 row V5 accessories: `question.prompt`/`answers[0]` present; validate-subtask-4 traces config.py:86 + exploratory->question_led table |
| C-15 | PASS | validate-subtask-4: chips extended not replaced (else-branch 1197-1201 preserves `chips:[{...,facet}]`) |
| C-16 | PASS | validate-subtask-4: `lbx.no_preview` sentinel path 2380-2382 intact |
| C-17 | PASS | validate-subtask-4: turn-2 question_led pivot (graph.py:2469-2474 + inheritance test) |
| C-18 | PASS | validate-subtask-4: `work_status` sequence unchanged (TestWorkStatusOrderingInvariant) |
| C-19 | PASS | validate-subtask-4: <=1 LLM call turn-1; conformance llm_call_count=1 on intended paths |
| C-20 | PASS (forward) | gap-8 report + conformance: live bring-up with `postgresql+psycopg://` DSN, Alembic upgrade, Redis seed, LangGraph :2024, proxy :8000 all evidenced |
| C-21 | PASS (forward-obligation) | separate per-repo commit histories present (agent @ feat/v2-campaign-rebased, proxy @ reconcile/proxy-v2-the-rest-on-origin-master); Subtask-15 commit not yet run, nothing staged — obligation not yet violable |
| C-22 | PASS (forward-obligation) | nothing staged in either repo; stray artifacts (agent_diff.txt, runs/, dump.rdb, proxy.log, uv.lock.local-pre-ff-2026-06-01) remain untracked |
| C-23 | PASS | multilingual-label-chip-identity-regression-report.md (re-verified 2026-06-11): en/sk/cs + cz-alias localized prose; `cz->cs` alias fix live |
| C-24 | PASS | same report: shared brand chips byte-identical `filter_value`/`facet=brand` across languages on matched composition |
| C-25 | PASS | conformance C-25 table: every V1-V20 row `rows_after_delete=0` before live read |
| C-26 | PASS | conformance C-26: V2 guitar MISS->HIT byte-identical (SHA equal, len equal, `hit_count` 0->1) |
| C-27 | PASS | shop-language output producer live (localized takeover/deflect/support prose en/sk/cs in conformance rows; identity fields language-neutral per C-24) |

**Tally: 25 PASS (artifact-pointed), 2 forward-obligation (C-21/C-22, commit subtask not yet run), 0 FAIL, 0 deferred-with-rationale at checklist level.** Bucket B is deferred at the package level (below), not a checklist ID.

---

## 2. Deferred Clause-by-Clause Reconciliations (the work the conformance report handed here)

### 2a. Live tier values vs `tier-vocabulary-reconciliation.md` — CONFORM

| Ratified mapping (reconciliation doc) | Live observation (conformance) | Match |
|---|---|---|
| `shapeable` -> `refinement_chips_with_hatch` | V2 guitar/en + all four multilang reads | YES |
| `exploratory` -> `question_led` | V5 accessories/en | YES |
| `intractable` -> `hard_fork` | V6 gitara direct renderer (sk/cs/en) | YES (renderer-direct) |
| `zero_results` (extra-state, NOT 5th tier) | V1 Yamaha `proxy_metadata_tier=zero_results` | YES |
| `decisive` -> `refinement_chips` | NOT observed live | No divergence — doc itself flags this as a probe-coverage gap (code-proven reachable), not a reachability gap |

No tier-mapping divergence. `decisive` non-observation is explicitly anticipated by the reconciliation doc's open flag (Unknowns #1).

### 2b. Live dispatch/localization/gap-7-safety vs `multilingual-mode-detection-architecture.md` — CONFORM

- **C-11 unsafe safety (contract A4 + Safety Short-Circuit):** contract says the unsafe refusal routes correctly but renders English prose even for sk/cs input (output prose is out of the dispatch contract's scope). Conformance Gap-7 row confirms verbatim: `mode=unsafe`, `llm_call_count=0`, "hard refusal text in English (safe-language-agnostic refusal)". Exact match.
- **C-10/C-12 language-aware dispatch:** `_resolve_request_language`, `_load_language_config`, and per-language `en/sk/cs.yaml` present in `mode_detection.py` — matches contract "New module + data layout" (lines 35-41) and Integration Points.
- **Output localization (apparent tension, resolved):** the conformance report shows localized sk/cs takeover/deflect prose. The architecture contract (A4, line 260) explicitly scopes output prose OUT and delegates it to Subtask 8 / Gate A1 / C-27. These are **complementary** (input dispatch contract vs output producer), not contradictory. C-27 is the producer that makes the localized prose appear; it does not violate the dispatch contract.

---

## 3. Cross-Artifact Contradiction Sweep — NONE

- **gap-8 BRANCH-A "implementation required" vs conformance Gap-8 PASS:** correctly time-ordered, NOT a contradiction. The gap-8 report is the early verification gate (C-13) that found `exploratory` divergent (graph overrode `question_led`->`refinement_chips_with_hatch`). Subtask 4 then closed it (C-14), validated by `validate-subtask-4.json` (config.py:86 `composition_table_live` flip + `exploratory->question_led` table). The conformance report observes the post-fix state. This is the intended early-gate -> fix -> reverify flow, not stale-resolved drift.
- No value contradiction across the six reports (test counts, fingerprints, cache states, tier/composition values all consistent: e.g. `local-system-prompt@bd5ebd03+...` fingerprint cited identically in conformance and multilingual reports).

---

## 4. Cross-Repo / Topology Risks

| Concern | State |
|---|---|
| Three separate git repos | CONFIRMED: outer luigis-box worktree (docs/), agent `conversational-search` @ `feat/v2-campaign-rebased`, nested `conversational-proxy` @ `reconcile/proxy-v2-the-rest-on-origin-master` |
| Proxy as nested-repo boundary | CONFIRMED: `conversational-proxy/` shows as untracked from the agent repo (correct separate-repo boundary) |
| Separate per-repo commit requirement (C-21) | Forward obligation. Both repos carry distinct v2 commit histories; nothing staged yet (Subtask-15 commit has not run — this audit is Subtask 12, which precedes it). Not yet violable. |
| Stray-artifact exclusion (C-22) | `agent_diff.txt`, `runs/`, `dump.rdb`, `proxy.log`, `uv.lock.local-pre-ff-2026-06-01` all untracked and unstaged. Subtask-15 must continue to exclude them (targeted staging only). |
| `mode_detection.py` (file) + `mode_detection/` (dir) coexistence | INTENDED structure, NO import ambiguity. The directory contains only data files (`cs.yaml`, `en.yaml`, `sk.yaml`) and **no `__init__.py`**, so it is not a Python package and cannot shadow the `.py` module — `import mode_detection` resolves unambiguously to the module. Matches architecture contract lines 35-41 exactly. |

---

## 5. Bucket B (Production Rollout Safety) — Consciously Deferred (CONFIRMED)

Recorded as a conscious operator-accepted boundary in **both**:
- Plan `## Deferred (with rationale)`, line 1010 (Y_1 cross-repo deploy sequencing, Y_3 feature-flag/shadow-mode/fallback, Y_2 production cache rollout; "branches conformant + dev-stack validated" is the explicit stopping point).
- Conformance report `## Remaining Caveats / Deferred`, line 150 (blue/green, prod env-var injection, prod Alembic sequencing, LangGraph prod assistant provisioning) with a three-item operator handoff checklist.

NOT graded as a failure.

---

## 6. Scope Not Covered (by delegation)

Live test re-execution, live SSE re-reads, and code-quality review are out of scope by delegation. Graph-edge connection-store dimensions and the dual-layer knowledge sweep are N/A for an artifact-vs-plan completeness audit. The findings-rot-sweep found 0 prior `knowledge-drift` findings (empty scope this round).

---

*Sidecar (canonical JSON): `docs/v2-design/_gates/coherence-audit-subtask12.json`*
