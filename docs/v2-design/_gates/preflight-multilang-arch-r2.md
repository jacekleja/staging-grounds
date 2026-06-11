## Pre-Flight Gate Assessment

**Artifact:** docs/v2-design/multilingual-mode-detection-architecture.md
**Generator type:** design
**Round:** 2 of (cap)
**Verdict:** PASS

### Per-defect resolution (Round-1 request-changes defects)

- **DEFECT 1 (HIGH — false "plan absent" premise):** RESOLVED. The plan `docs/v2-design/plan-v2-final-state-gap-closure.md` demonstrably exists (1018 lines). Verified the artifact's re-grounding citations resolve exactly: Subtask 2 at :568 (matches "Design generalized multi-language detection layer"), Gap 7 at :334-382, checklist C-10/C-11/C-12 at :54-56, expected-output at :584, the 7 design questions at :350-358, and the safety-priority list at :360-365. The head-matter now carries a positive Grounding note (line 5) citing the real plan; no "does NOT exist" / "absent plan" / "interpretation note" string survives anywhere (grep clean). Unknown U1 is now "Slovak/Czech phrases" (renumbered), and U4 (the issue-queue unknown) is reconciled-and-dropped per the Revision Log, not left dangling.

- **DEFECT 2 (MED — per-language support normalization):** RESOLVED. Integration Point 5 (lines 277-282) now specifies the FOUR-point coupled change explicitly: `_SupportPattern` gains parallel `detect_by_language` + `normalized_detect_by_language` dict fields; `_validate_support_config` precomputes per-language normalized tuples mirroring graph.py:421; `_match_support_pattern` selects the per-language pair and keeps the SAME `zip(raw, normalized, strict=True)` loop matching NORMALIZED / reporting RAW; YAML carries only raw. Verified against code: `_SupportPattern` (graph.py:276-284) has the parallel `detect`/`normalized_detect` tuples; `_match_support_pattern` (graph.py:568-571) does `zip(pattern.detect, pattern.normalized_detect, strict=True)`, matches normalized (571), reports `raw_keyword` (572); `_validate_support_config` precomputes at line 421. The artifact's strict-zip-per-language invariant ("never raw from one language zipped against normalized from another", line 281) is exactly right.

- **DEFECT 3 (MED — `dispatch_rationale_token` as breaking change):** RESOLVED. The artifact now treats the token change as a position-affecting / mid-segment-insertion BREAKING change (Audit-field preservation lines 149-162; Migration Constraints line 294; second-order surfaces line 288), specifies the exact per-mode token grammar under grammars A/B (lines 157-160), names the live unsafe token `unsafe_keyword:{phrase}` with phrase at colon-index 1, and instructs the implementer to grep all consumers starting with turn_events_writer.py. Verified the live token at graph.py:641 is `f"unsafe_keyword:{_normalize_dispatch_text(unsafe_keyword)}"` (phrase at index 1 — exact). Verified all four named in-repo consumers ARE pass-through: turn_events_writer.py:55 (opaque `str | None` dataclass field, real path app/service/), turn_events_repo.py:38/48 (verbatim column insert), conversation_service.py:71 (`raw.get(...)` key pass-through), langgraph_client.py:355 (`payload.get(...)` key pass-through). Independent corroboration: test fixtures hard-assert exact-string `"unsafe_keyword:build a bomb"` (test_conversation_service.py:1105/1175, test_turn_events_repo.py:197), confirming a grammar change has a real downstream break surface — which makes the breaking-change reframe correct, not over-cautious.

### Safety short-circuit ordering (must remain clean)

STILL CLEAN — and verified NOT disturbed by the revision. `_dispatch_for_query` (graph.py:631-644) has the unsafe check as its FIRST statement (636), returning immediately on hit (638-644) before `matches: list` is even allocated (646). The artifact's `## Safety Short-Circuit Ordering` (lines 209-220) and Failure-Mode F6 (line 194) preserve this exactly, changing only the vocabulary source (`_UNSAFE_KEYWORDS` -> `language_config.vocabularies["unsafe"].normalized_detect`). The 0-LLM-call property is grounded: deflect path never reaches `regular_turn`. The revision touched the token grammar around the unsafe return but did NOT reorder the short-circuit.

### Clean Justification

(a) Declared goal: H1 "Multilingual Mode-Detection Architecture (Gap 7 detection layer)" and Subtask-2 expected output "...with chosen data shape, code surfaces, safety short-circuit ordering, language propagation proof, test matrix, and migration constraints" (plan:584, quoted verbatim). Deliverable fulfils it: the artifact contains a Proposed Approach (data shape: per-language YAML registry + dataclasses), Integration Points (code surfaces), a Safety Short-Circuit Ordering section, a Language-Propagation Proof, a Test Matrix, and Migration Constraints — every promised element present and in-lane. No title/content drift.
(b) Unverified/Inferred tags: NONE present (grep clean). The doc uses named Assumptions A1-A5 and Unknowns U1-U3, each with an explicit resolution owner — no load-bearing claim left as a bare hedge tag.
(c) Scope breadth: matches Subtask 2 exactly (input dispatch detection; output localization explicitly deferred to Subtask 8 / Gate A1, line 3 and A4). No overrun; no in-scope concern skipped.
(d) Citations resolved: plan citations (:568/:334-382/:54-56/:584/:350-358/:360-365) PASS; code-surface citations (graph.py:631-644, :276-284, :565-572, :399-445, :586-591, :238) PASS; token-consumer citations (turn_events_writer.py:55, turn_events_repo.py:38, conversation_service.py:71, langgraph_client.py:355) PASS. Two `[verified: observed behavior]` and proxy-path citations recorded as unverifiable-but-well-formed (not flagged). Net: ~18 file-anchor citations re-traced, all pass.
(e) Canonical design failure mode (no rejected-alternatives / unstated assumptions / nearby-easier-question drift): NOT present. Six substantive Rejected Alternatives (R1-R6) each name a distinct rejection axis; five Assumptions and three Unknowns are explicit. The doc answers the actual hard question (generalized language-keyed layer + safety) not a nearby-easier one.
(f) Design-type structural elements: Proposed Approach + Rejected Alternatives both present and substantive.
(g) T3 web citations: none (N/A).

### Summary

PASS. All three prior request-changes defects are genuinely resolved (not merely re-worded): the false-premise retraction is real (plan exists, citations resolve), the per-language support normalization is specified as a precise four-point strict-zip-preserving change, and the token change is correctly reframed as a position-breaking grammar change with an instructed consumer sweep. The safety short-circuit ordering remains clean and was not disturbed. The artifact is fit for the Subtask-5 safety-critical implementer to consume.

Findings emission self-check: 0 flags, 0 annotation-paired, 0 consequence-named.
