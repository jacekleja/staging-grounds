# Static Code Validation Report — v2 UX-Fidelity Fixes (plan subtask 12)

**Target:** `conversational-search/src/conversational_search/agent/graph.py` (combined landed diff: FIX-1, FIX-2+3, FIX-5)
**Rubrics:** `code-vs-spec`, `constraint-compliance`
**Round:** 1 of 1
**Date:** 2026-06-11

## Verdict

**approve.** All six success criteria trace to live code with file:line evidence; the full unit suite passes (569 passed, 0 failed); the only subsystem-matching constraint is the delegation-acknowledged stale `deflection-detection-english-only-vocabulary.md`, and the code's divergence from it is the documented-correct behavior, not a violation. No critical or important gaps.

## Rubric: code-vs-spec

### CL-1 — out_of_scope_deflect single LLM call + dual-write (PASS)

`out_of_scope_deflect` (graph.py:3348-3374) makes exactly one model call: `await llm.ainvoke(llm_messages, config={"tags": [TAG_NOSTREAM]}, ...)` (3360-3364). The dual-write is present and correct:
- `_emit_deflection_classification(..., llm_call_count=1)` (3371) — the emitted telemetry path; helper now threads an optional `llm_call_count: int = 0` param into `_emit_turn_classification` (3231-).
- `_deflection_update(..., llm_call_count=1)` (3374) — the state-update path; helper returns `"llm_call_count": llm_call_count` (was hardcoded 0).

The old hardcoded English template survives ONLY as `_OUT_OF_SCOPE_STATIC_FALLBACK` (graph.py:466-474), consumed by `_out_of_scope_reply_from_response` (1979-1988) on empty/non-text response. `support_deflect` (3345) and `unsafe_deflect` (3390) call the two shared helpers WITHOUT the new param, so both keep the default `llm_call_count=0`. The failure mode this prevents: telemetry under-reporting the deflect LLM call as 0 (the validation-locus chain B in the design sketch) — verified closed at both write sites.

### CL-3 — chat affordance NOT on any deflect path, including unsafe-refuse (PASS)

All three deflect nodes read in full carry zero reference to `chat_affordance` or `_render_turn1_preview_block`:
- `support_deflect` (3318-3345): classification + `_emit_deflection_text` + `_deflection_update`.
- `out_of_scope_deflect` (3348-3374): LLM call + classification + text + update.
- `unsafe_deflect` (3377-3390): classification + static refusal text + update.

The affordance gate (`if ... in _CHAT_AFFORDANCE_TIERS: block["chat_affordance"] = _chat_affordance(...)`) appears only inside the preview-render path — `_render_turn1_preview_block` (1509-1510) and the takeover block (1619-1620), reached via `handle_regular_turn` (2120-2126). FIX-2 widened `_CHAT_AFFORDANCE_TIERS = set(TIER_ENUM)` (line 330, was `TIER_ENUM[1:]`); that constant is consumed only by the preview gate (`_derive_chat_affordance_on` 1444-1445, and 1509), never by the deflect nodes. The failure mode this prevents: the unsafe-refuse bubble sprouting a "Chat with me instead" CTA that invites continued engagement on a harmful request — verified the deflect nodes never touch the gate.

### CL-4 — question_led type_it_out shape (PASS)

`_render_turn1_preview_block`, `question_led` branch (COMPOSITION_ENUM[2], 1462-1482), emits:
```
"type_it_out": {"enabled": True, "label": _t(raw_language, "type_it_out"), "style": "free_text"}
```
(1477-1481) — matches the advice/gift `{enabled: true, label: _t(...), style: "free_text"}` shape. Test `test_question_led_preview_full_shape` (test_graph_emit.py) asserts `type_it_out` in the key set and `enabled is True` / `style == "free_text"`.

### CL-6 — gift-anchor label localization via correct table (PASS)

`_t_gift_anchor_label(raw_language, anchor)` (1516-1527) derives `key = f"gift_anchor_{anchor.value}"` and routes through `_t()` only when the key is registered in `_UI_STRINGS_EN`; YAML-sourced custom anchors (key absent) fall through to `anchor.label` unchanged. The 4 fixed default anchor values — `hobbies_and_interests`, `lifestyle`, `practical_useful`, `i_have_an_idea` (`_DEFAULT_GIFT_GUIDEBOOK_ANCHORS` 411-416) — each map to a `gift_anchor_*` key present in `_UI_STRINGS` across en/english/sk/slovak/cs/czech (108-273). The sk and cs sections carry the inline `# PLACEHOLDER — native-speaker polish pending` comment (199, 232). `_render_gift_advisor_takeover_block` (1567 area) sets `"label": _t_gift_anchor_label(raw_language, anchor)` while keeping `"filter_value": anchor.value` (language-neutral identity). Unknown-language fallback to English is exercised by `test_absent_language_falls_back_to_english`. The English fallback is preserved (`_UI_STRINGS["en"]` keys present). The failure mode this prevents: anchor labels rendering English for sk/cs shoppers OR the `filter_value` identity drifting per-language and breaking chip click-through — both verified closed.

### FIX-4 — stayed deferred, no categorical-chip code landed (PASS)

`turn1_selector.py select_chips` (138) still assigns `{"label": v["value"], "filter_value": v["value"], "count": v["hits_count"]}` — raw facet `value` for both fields, no localization, no `display_label`/locale-keyed read, no fabricated catalogue-data translations. The deferral is documented in `docs/v2-design/v2-ux-fidelity-deferrals.md` §1 (DEFERRED — upstream data-architecture change required), with the catalogue-DATA-vs-UI-STRING binding constraint spelled out (placeholder translations forbidden for arbitrary merchant catalogue names). No FIX-4 wire landed, so the three-localization-table coupling and turn1_selector circular-import constraints are not exercised — correctly so.

### D-edge-cases / D-error-paths / D-integration-points / D-consistency / D-sketch-adherence

- **Edge cases:** empty/non-text LLM content -> `_OUT_OF_SCOPE_STATIC_FALLBACK` (1985-1987); `len(options) < 2` -> empty preview block (1463-1464, 1484-1485); unknown language -> English fallback throughout `_t` and `_t_gift_anchor_label`.
- **Error paths:** single awaited `ainvoke`, no new try/except, no swallowed exceptions; propagation contract unchanged.
- **Integration points:** `out_of_scope_deflect` wired in `create_graph` (node 3672, route 3688, edge-to-END 3704). The defaulted-param change to the two shared helpers is backward-compatible — `support_deflect`/`unsafe_deflect` call sites unchanged (verified, exactly 3 callers each).
- **Consistency:** out_of_scope helpers mirror the gift-advisor system-prompt/opener idioms; `ainvoke + TAG_NOSTREAM` matches the established deflection-channel idiom (not `astream`, which would leak onto `messages/partial`).
- **Sketch adherence:** implementation follows `fix1-out-of-scope-design.md` Proposed Approach — system-prompt-only Option B (no new guidebook artifact), `ainvoke + TAG_NOSTREAM` per streaming-channel chain A, no apology-token guard per A3. No unjustified deviation.

### D-untested / D-exercise-evidence

- **Untested:** the new branches are covered by `test_ux_fidelity_fixes.py` (FIX-1/2/3) and `test_shop_language_localization.py::TestGiftAnchorLabelLocalization` (FIX-5); the static fallback, llm_call_count=1, and language-injection assertions are present.
- **Exercise evidence:** the impl-report `## Verification` section (`impl-v2-ux-fidelity-fix1-implement.md` 23-44) is present and non-degenerate — `### Exercised` cites the proxy SSE grep (langgraph_client.py:323-340 deflection_text-only routing), a caller audit, the `test_llm_call_count_is_1` assertion, and the Slovak/Czech language-injection assertions; `### Not exercised, and why` gives a bounded disclosure (live end-to-end SSE deferred to subtask 5; streaming-protocol suppression confirmed by reading the proxy handler). Non-degenerate confirmed.

## Rubric: constraint-compliance

Globbed `.claude/knowledge/constraints/**/*.md`. The only constraint whose subsystem/`covers` matches the changed file `conversational-search/src/conversational_search/agent/graph.py` is `deflection-detection-english-only-vocabulary.md`.

Per the delegation (and corroborated live), this constraint is **stale / contradicted-by-code**: detection vocabulary is actually per-language YAML keyed on the request `language` field, not an English-only constant. Confirmed at `graph.py:978` — `out_of_scope_vocab = lang_config.vocabularies["out_of_scope"]` with `dispatch_rationale_token=...:{language}` (987). The FIX-1 code's design decision A3 — `_out_of_scope_reply_from_response` (1979-1988) deliberately applies NO apology-token guard precisely because such a guard would be English-only — is the CORRECT divergence: it avoids re-introducing the very English-only-vocabulary bug the stale doc describes. This is a compliance PASS, not a violation. The stale doc is surfaced as a knowledge-drift signal below, per the delegation note that the finding was already filed this session.

All other constraint files (platform, etc.) classified does-not-apply: their `globs:` do not match the conversational-search agent path.

## Scope Covered

- `code-vs-spec` dimensions: D-spec-coverage, D-edge-cases, D-error-paths, D-integration-points, D-consistency, D-sketch-adherence, D-untested, D-exercise-evidence — all run.
- `constraint-compliance` D-constraint-compliance — run.
- graph.py ranges read: 108-273, 330/333-340, 411-416, 466-474, 568-572, 978-987, 1444-1513, 1966-1988, 3231-3275, 3318-3390.
- turn1_selector.py: 101-144 (`select_chips`).
- Combined diff `.agent_context/v2-ux-fidelity-combined-code.diff` (full, including test deltas).
- Design contract `fix1-out-of-scope-design.md` (full).
- Deferral note `docs/v2-design/v2-ux-fidelity-deferrals.md` (full).
- Impl report `impl-v2-ux-fidelity-fix1-implement.md` (full, incl. `## Verification`).
- CL-8 runtime: `cd conversational-search && .venv/bin/python -m pytest tests/unit -q` -> **569 passed in 1.16s**.
- Knowledge consulted: `constraints/deflection-detection-english-only-vocabulary.md#deflection-detection-uses-english-only-vocabularies` [verified: graph.py:978 lang_config.vocabularies — contradicts the doc]; `decisions/conversational-search-v2-marathon-findings-digest.md#multilingual-output-localization-mode-dispatch-language-8` [verified: graph.py:411-416, 108-273 three-table localization confirmed]; `decisions/request-language-decoupled-from-dispatch-detection-digest.md#request-language-decoupled-from-dispatch-detection-vocabulary` [verified: observed behavior — consulted for cross-subsystem context, not in static scope].

## Scope Not Covered

- D-concurrency-idempotency — N/A (`no-shared-state`): the deflect/render paths read state and emit events; no locks, retries, timeouts, or file-locking in the changed lines.
- D-security — N/A (`no-privilege-boundary`): no auth, secrets, untrusted-input parsing, or injection vectors in the changed paths; LLM prompt strings are codebase-authored constants.
- Live SSE / full-stack behavior — out of this static pass's scope (delegation: live behavior covered by separate live-verify validators; dev stack on ports 8000/2024 left untouched).
- FIX-4 three-table-coupling / turn1_selector circular-import constraints — not exercised because no FIX-4 wire landed (correctly deferred).

## Knowledge-Drift Annotation

- **[severity: medium]** Stale constraint contradicted by code: `deflection-detection-english-only-vocabulary.md` asserts deflection detection uses English-only vocabularies, but the live agent reads per-language YAML keyed on the request `language` field. [promote: constraint]
  evidence: [verified: conversational-search/src/conversational_search/agent/graph.py:978 (out_of_scope_vocab = lang_config.vocabularies["out_of_scope"]; dispatch_rationale_token includes :{language} at :987)]

Findings emission self-check: 1 discovery, 1 [promote:] annotation.
