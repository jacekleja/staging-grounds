## Pre-Flight Gate Assessment

**Artifact:** docs/v2-design/plan-v2-ux-fidelity-fixes.md
**Generator type:** plan
**Round:** 1 of 1
**Verdict:** REQUEST-CHANGES (operator token: concerns)

### Flags (3)

- **[severity: high]** FIX-4 WIRED-branch has no clean code-authoring owner and falls outside the validation/verification dependency sets: The main plan routes FIX-4 to subtask 4 (Agent: **researcher**) titled "investigate + conditional-wire", while subtask 7 (Agent: implementer) is explicitly "a documentation deliverable, **not a code change**" (subtask-7.md l.9). Subtask-4.md l.11 then states the WIRED code "is a small in-subtask code+test step the researcher hands to the orchestrator as a WIRED disposition, **OR** the orchestrator inserts an implementer follow-up" — two contradictory provisions. On the WIRED branch this means either a researcher lands production code+tests, or an unplanned implementer subtask appears that is absent from the Subtask Summary Table. Downstream: subtask 8 (validator; `Depends On: 2, 3, 7`) validates "any FIX-4 wire from subtask 4 **recorded in subtask 7**" — but subtask 7 is doc-only and carries no code diff, and an inserted follow-up subtask is in neither subtask 8's nor subtask 9's dependency set; subtask 9 (LIVE verify FIX-4, `Depends On: 7`) would then verify a wire whose code-landing subtask is not in its dependency chain, so it can run before the wire is committed. The orchestrator/validator chain is mis-routed precisely on the conditional branch the plan exists to keep clean. [promote: gotcha]
  evidence: docs/v2-design/plan-v2-ux-fidelity-fixes-subtask-4.md l.11 ("OR the orchestrator inserts an implementer follow-up") vs. plan-v2-ux-fidelity-fixes-subtask-7.md l.9 ("documentation deliverable, not a code change") vs. plan line 64 (subtask 9 Depends On: 7) and line 84 ("any FIX-4 wire from subtask 4 recorded in subtask 7")

- **[severity: low]** CL-9 (live re-verification) is mapped to subtasks (5, 6, 9) but to no Completion Criterion by name: CC-2/CC-3/CC-4 each embody live re-verification under `docs/v2-design/_runs/`, so CL-9 is covered by substance, but the pre-emission self-audit (l.224) asserts "all 9 checklist items map to ≥1 subtask + ≥1 completion criterion" — for CL-9 that mapping is implicit, not explicit. Downstream: the coherence-auditor (subtask 10) auditing CL-9 finds no `(CL-9)`-tagged CC and must infer coverage from the `_runs/` artifacts cited under CC-2/3/4; recoverable, but the self-audit overstates explicit traceability.
  evidence: docs/v2-design/plan-v2-ux-fidelity-fixes.md l.42 (CL-9) and l.211-217 (Completion Criteria CC-1..CC-7, none names CL-9) and l.224 (self-audit completeness claim)

- **[severity: low]** Subtask 9 → subtask 10 skip-propagation semantic is asserted but unstated: The self-audit (l.225) claims subtask 9 "degrades cleanly to skip on DEFERRED without breaking the chain", and functionally it does — on DEFERRED, CL-5 routes to subtask 7's deferral doc audited by subtask 10, and CC-4 covers both WIRED and DEFERRED branches. But subtask 10's `Depends On: 5, 6, 8, 9` does not state that a skipped subtask 9 counts as a satisfied dependency. Downstream: an orchestrator applying literal blocking semantics could stall subtask 10 waiting for a subtask 9 that will never run.
  evidence: docs/v2-design/plan-v2-ux-fidelity-fixes.md l.65 (subtask 10 Depends On: 5, 6, 8, 9) and l.77 (9 "conditional on subtask 4's WIRED disposition") and l.225 (clean-degradation claim)

### Summary
The plan is structurally strong — DAG is a valid strict topological order (no forward refs, no cycles), CL-1..CL-8 map cleanly to subtasks and completion criteria, both operator decisions (D1 FULL LLM-with-guidebook; D2 A10-deferred / A6-conditional / no fabricated translations) are faithfully reflected, the English-keyword-only out_of_scope constraint is correctly carried into FIX-1 live verification (subtask 5 / CC-2), and all four cited `graph.py` function anchors verify exactly. One important defect blocks a clean hand-off: the FIX-4 WIRED branch lacks a properly-typed code-authoring subtask wired into the subtask-8/9 dependency sets. Verdict: concerns (request-changes) — fixable in a plan revision; not an upstream block.

Findings emission self-check: 3 flags, 3 annotation-paired, 3 consequence-named.
