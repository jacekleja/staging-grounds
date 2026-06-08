---
name: v3-apparatus
description: "Protocol V3 (Unbraked Deepening) apparatus — orchestrator use only. Invoke when running a V3 investigation cycle: per-cycle step ordering, MAO trigger conditions, NNN pre-allocation, apparatus-surface scope, termination triggers."
argument-hint: "[cycle-dispatch|terminate]"
allowed-tools:
  - mcp__context-tools__smart_read
  - mcp__context-tools__smart_grep
  - mcp__context-tools__smart_glob
  - mcp__context-tools__smart_write
  - mcp__context-tools__knowledge
  - mcp__context-tools__findings
  - mcp__context-tools__issues
  - mcp__context-tools__smart_bash
caller-allowlist: [main, driller]
---

# PROTOCOL-V3 — Operational Rules

This file is the authoritative operational spec for PROTOCOL-V3. It is read by the orchestrator when running a V3 (Unbraked Deepening) investigation cycle. The required delegation-prompt tokens (validated by the `v3-protocol-schema-gate.py` PreToolUse hook) are listed in §3.

The synthesizer's post-drill MAO verification procedure (Stages 0 → 0.5 → 1 → 2) lives in `.claude/knowledge/reference/v3-mao-verification-procedure.md`. This skill governs the orchestrator-side cycle dispatch only.

## Evidence Tier Taxonomy

V3 drillers classify each finding they emit as Tier-1, Tier-2, or Tier-3 against the rubric below. The orchestrator passes this rubric into every drill via the `drill_tier_rubric` delegation field. Tier governs (a) which MAO forms are permitted autonomously (see §2), and (b) whether the finding counts toward termination trigger (c) (see §6).

- **Tier-1 — Direct, replayable evidence.** The finding cites a specific file path with a line range or grep-replayable substring, a tool-call return value, an artifact field, or a sentinel-file presence/absence. A second agent can re-execute the same read and reproduce the citation byte-for-byte. Examples: "the hook returns exit code 2 at `cycle-hook.py:332`"; "the synthesizer report at `session-dir/cycle-3-digest.md § MAO Telemetry` lists 4 skipped drillers".
- **Tier-2 — Synthesized inference from Tier-1 evidence.** The finding draws a conclusion that no single citation establishes but that follows from two or more Tier-1 observations the driller documents inline. Recognition test: the inference step is named, and each input citation is Tier-1. The synthesizer marks Tier-2 findings `confirmed` when its own Stage-1 grep-replay reproduces every cited input; `unconfirmed` otherwise.
- **Tier-3 — Pattern recognition without replayable grounding.** The finding describes a behavior the driller noticed across multiple observations but cannot cite to a specific file/line/return-value. Recognition test: replace the claim with "I have a hunch that…" — if the meaning survives, it is Tier-3. Tier-3 findings are NOT a basis for autonomous MAO; they may seed a Form-4 settling experiment that gathers the missing Tier-1 evidence.

## 0. Per-Cycle Step Ordering

Each V3 apparatus cycle proceeds through these steps in order:

- **Step 0 — Oracle question generation.** The oracle reads the prior cycle's MAO telemetry (issues filed, findings emitted, settling experiments dispatched, synthesizer conclusions, as recorded in the prior cycle's synthesizer digest at `{session_dir}/cycle-{N-1}-digest.md`) and generates the questions drillers will investigate. On cycle 1, when no prior MAO telemetry exists, the oracle reads the user-supplied investigation frame instead.
- **Step 1 — Driller dispatch.** The orchestrator dispatches one `driller` per oracle-generated question, using the question text as the drill scope and binding the required delegation-prompt tokens listed in §3. The orchestrator does NOT decompose the investigation frame into its own axes.
- **Steps 2–5 — Drill execution.** Each driller investigates its assigned question, classifying each finding it emits as Tier-1, Tier-2, or Tier-3 per §Evidence Tier Taxonomy.
- **Step 6 — Critic verdict.** The critic reviews each drill output and emits one of four verdicts: `accept`, `accept-with-caveats`, `request-redrill`, or `route-back-instruction`. The last form names a specific sub-question or tool-call sequence the critic wants re-drilled, scoped narrower than the original drill (see §5 for the narrow-redrill recovery path it enables).
- **Step 6.5 — MAO (Mandatory Action-Output Step).** After the critic-verdict pass, drillers with `accept` or `accept-with-caveats` execute MAO per §1.
- **Step 7 — Synthesizer.** The synthesizer aggregates all accepted drill outputs, MAO actions, and critic verdicts into `{session_dir}/cycle-{N}-digest.md` and advances the run state.

**Drillers drill oracle-generated questions, not orchestrator-decomposed axes.** Without oracle mediation, the orchestrator's natural decomposition tendency produces axes derived from the investigation frame's surface structure rather than from prior-cycle MAO signal. Oracle-generated questions are grounded in what the apparatus has already learned; orchestrator-decomposed axes are not.

## 1. Mandatory Action-Output Step (MAO)

**Protocol position:** Step 6.5 — after the critic-verdict pass, before synthesizer dispatch.

### MAO trigger conditions (any one fires MAO)

1. The drill names a settling experiment as a falsifiability criterion.
2. The drill identifies a single-call-blockable fix with no existing issue or campaign entry.
3. The drill assigns an exit condition to "an external actor" without naming a behavioural falsifier the apparatus can observe (escape-clause anti-pattern).
4. The drill identifies a constraint that would change downstream agent behaviour (rule-file candidate).

### Permitted action-output forms (closed enum)

1. **Form-1** — `issues(action='file', ...)` with `dedupe_key`, severity, summary, `suggested_approach`, `origin_agent`. The synthesizer verifies the issue ID via deterministic hash recomputation using `created_at` + `dedupe_key`.
2. **Form-2** — `findings(...)` with `tags=['should-be-issue']`. Existence is verified via audit-trace cross-reference; drill-scope attribution uses the `tool_use_id` tag (see §1.y).
3. **Form-3** — New file or section replacement under `.claude/knowledge/constraints/`. Existence verified by the triple cross-reference of change-log + filesystem-scan + audit-trace. Subject to the M1–M6 mechanical pre-flight (see §1.z).
4. **Form-4** — Settling-experiment dispatch: an artifact at `unbraked-deepening/settling-experiments/exp-NNN.md` plus a dispatch-tracker entry in `unbraked-deepening/settling-experiments/INDEX.md`. NNN is apparatus-allocated (see §5).
5. **Form-5** — Investigation-termination artifact written to `unbraked-deepening/V3-RUN-NNN-TERMINATION.md` citing the met precondition (see §6).

### Escape valve

A driller MAY skip MAO by emitting `findings(...)` with `tags=['mao-skip-rationale']`. The finding's content must name either (a) why no trigger fired, or (b) why evidence is Tier-3-only and termination is not yet justified. MAO does not recursively re-fire on the skip-rationale finding within the same drill.

### §1.x Settling-experiment validity gate

When MAO trigger (1) fires, the settling-experiment predicate MUST satisfy one of two admissible forms:

- **Grep-stable** — a literal-substring check verifiable via `grep -c` on a specific file path. The predicate names the file and the expected count (e.g., "`grep -c form-2-attribution-missing .agent_context/audit/v3-block-trace.jsonl` returns 0 over the next 3 cycles").
- **Behavioral-rate** — a metric + threshold + observation window (e.g., "Form-2 attribution-missing rate drops below 5% over the next 3 cycles").

A predicate that satisfies neither form fails the validity gate. Additionally, the predicate MUST contain at least one of (a) a comparison operator, (b) a tool-output reference, or (c) a closed-enum verdict-string — tautological text ("the behavior continues as before") is semantically vacuous and invalid regardless of form.

### §1.y Form-2 attribution requirement

Every Form-2 emission produced during a drill MUST include the tag `drill-tool-use-id:<tool_use_id>` in its `tags[]` array, where `<tool_use_id>` is the `tool_use_id` of the driller dispatch that produced the finding. This lets the synthesizer uniquely attribute Form-2 findings across multiple drillers in the same cycle.

Absence of this tag is treated as MAO-INCOMPLETE with the typed reason `form-2-attribution-missing`. The synthesizer rejects the finding from drill-scope accounting and flags the driller — `MAO-INCOMPLETE` is the synthesizer-side counter that contributes to the skip-rate detection logic in trigger (b) (see §6).

### §1.z Form-3 mechanical pre-flight (M1–M6)

Before LLM acceptance of a Form-3 emission, the synthesizer applies six mechanical checks. Any FAIL flips Form-3 to MAO-INCOMPLETE with the typed reason `form-3-vacuous-{kind}`. The full rubric lives in `bin/test_rule_substance_lint.py`.

| Check | Pass criterion |
|---|---|
| M1 non-emptiness | Word count ≥30, at least one bullet or heading, at least one citation or `<!-- no-citation -->` marker |
| M2 path-glob match | The declared path-glob in frontmatter matches the file being written |
| M3 citation resolvability | `bin/test_citation_anchor_lint.py` exits 0 on the proposed content |
| M4 frontmatter completeness | All required frontmatter fields present (`audience:`, `path-glob:`, `verified:`) |
| M5 shingle-overlap | Jaccard similarity ≤0.45 against existing knowledge files in the same subsystem |
| M6 contradiction probe | Soft warn only — no automatic FAIL; synthesizer reviews flagged contradictions |

## 2. Action-Authority Routing

Routing is determined by the tier of the finding the action is based on. The table below names which MAO forms a drill MAY emit autonomously at each tier, which forms require a settling experiment first, and which forms require human-in-loop authorization.

| Tier of finding | Autonomous (Set A + Set B-i) | Requires settling experiment | Requires human-in-loop |
|---|---|---|---|
| Tier-1 | Forms 1, 3, 4 | — | Form-5 |
| Tier-2 (synthesizer-confirmed) | Form-1 (severity capped at `med`), Form-4 | Form-3 | Form-5 |
| Tier-3 | Form-4 only | Forms 1, 3 | Form-5 |

When a drill tries to emit Form-1 or Form-3 from Tier-3 evidence without a prior settling experiment producing Tier-1 grounding, the synthesizer flags the emission `apparent-pattern-not-evidence` and routes it into the human-in-loop column. The flag is the apparatus's primary guard against acting on hunches — Tier-3 is recognizable pattern without replayable evidence (see §Evidence Tier Taxonomy), and a constraint or issue grounded only in pattern recognition cannot be distinguished from coincidence without the settling-experiment step.

## 3. Apparatus-Surface Scope and Write Discipline

The apparatus may write autonomously to two disjoint path sets and no others:

- **Set A — session-scoped artifacts:** `unbraked-deepening/**`. Drillers, the critic, and the synthesizer write freely here; this is where settling experiments, INDEX.md, and the termination artifact live.
- **Set B-i — autonomous-eligible knowledge writes:** `.claude/knowledge/constraints/**`. Form-3 emissions land here. Writes here are subject to the M1–M6 pre-flight (see §1.z) and the tier-routing rule (see §2).

Writes outside Set A ∪ Set B-i are not autonomous — they require either a settling-experiment basis or human-in-loop authorization per §2, or fall under the absolute prohibitions in §4.

### Required delegation-prompt tokens

The `v3-protocol-schema-gate.py` PreToolUse hook fires on every `Agent` dispatch into `critic-driller` or `synthesizer` and checks for these literal snake_case tokens via word-boundary regex:

- `v3_apparatus` — gates whether the hook engages at all (without it, the hook returns silently).
- `cycle_number`, `mao_trigger`, `source_finding_ids`, `drill_tier_rubric` — universal required fields; missing any fails the gate.
- `assigned_exp_nnn` — conditionally required when the prompt also contains `form_4_eligible: true` (critic-driller only).
- `parent_drill_tool_use_id` — required for every critic-driller dispatch (links the critic to the driller it reviews).

Human-prose paraphrases ("cycle number", "tier-classification rubric") do NOT satisfy the gate — emit the literal tokens. The hook hard-blocks (exit 2) on missing fields; emergency dispatch uses the bypass token `<!-- V3-SCHEMA-BYPASS: <reason> -->` as a top-of-prompt line within the first 20 lines.

## 4. Prohibited Autonomous Action Classes

The apparatus NEVER autonomously:

- Modifies `PROTOCOL-V3.md` or `V3-arch.md` (the protocol-design files themselves).
- Modifies any file under `.claude/agents/` (the agent registry is operator-curated).
- Closes its own issues (issue closure requires human-in-loop or a settling experiment whose predicate explicitly authorizes the closure).

These prohibitions apply at every tier. A Form-3 or Form-1 emission that would touch any of the above is rejected regardless of evidence strength or settling-experiment grounding — only a Form-5 termination artifact + human-in-loop authorization may unlock these paths.

## 5. Form-4 NNN Allocation Rule

The orchestrator pre-allocates `assigned_exp_nnn` for each form-4-eligible drill at dispatch time. The driller MUST use this exact NNN when writing the `exp-NNN.md` artifact and the INDEX.md entry. Allocation is monotonic-increasing across cycles; collision is mechanically impossible.

A driller that self-assigns a different NNN produces a `form-4-unallocated-nnn` synthesizer rejection, counted as MAO-INCOMPLETE.

### Narrow-redrill recovery path

When the critic emits verdict `request-redrill`, a full re-drill is the default recovery path. A **narrow-redrill** is a permitted alternative when the critic also emits a `route-back-instruction` whose scope is bounded to a single sub-question or a single tool-call sequence within the original drill. The narrow-redrill re-runs only the bounded scope, reusing the rest of the original drill's accepted output.

The orchestrator chooses between the two paths from the critic's `route-back-instruction` field:
- Field absent or scope covers the full drill → full re-drill.
- Field present and scope is sub-question-bounded → narrow-redrill permitted.

Narrow-redrills preserve the original drill's `tool_use_id` for Form-2 attribution (the original `drill-tool-use-id:<tool_use_id>` tag continues to apply to findings from both the original and narrow re-run).

## 6. Termination Triggers

The apparatus terminates when ANY of these conditions is met. On any trigger firing, the synthesizer emits a `termination-trigger-met` finding tagged with the trigger ID and writes the Form-5 artifact. Human-in-loop review is post-termination.

- **(a) Cycle-wide skip with experiments awaiting.** All drillers in the cycle emit `mao-skip-rationale` AND the rationale states no Tier-1 evidence was produced AND at least one settling experiment from a prior cycle is awaiting observation.
- **(b) Skip-rate threshold.** Skip-rate is the per-cycle ratio of drillers emitting `mao-skip-rationale` to total drillers. The cold-start floor is `>80%` over 2 consecutive cycles (used exclusively in cycles 1–3). From cycle 4 onward, three augmentation layers also apply: distribution-based (skip-rate exceeds rolling p95 or run baseline +2σ), multi-window (sliding median over last 5 cycles exceeds the cold-start floor), and cumulative-pattern (a plateau just below the floor, or oscillation above and below it, or a spike-and-reset within 3 cycles). The full detection logic lives in `.claude/knowledge/reference/v3-mao-verification-procedure.md § Skip-Rate Detection Logic`.
- **(c) Tier-1 + Tier-2 absence.** Three consecutive cycles produce zero novel Tier-1 findings AND zero synthesizer-confirmed Tier-2 findings (per the synthesizer's Stage-1 grep-replay verification — see §Evidence Tier Taxonomy).
- **(d) Stale-experiment accumulation.** Two or more settling experiments simultaneously reach `experiment-stale` status AND staleness is `apparatus-owner-attributable`. The two terms are tracked as fields on each experiment's INDEX.md entry: `status: experiment-stale` is set by the synthesizer when an experiment's observation-window deadline passes with no decisive observation; `attribution: apparatus-owner-attributable` is set when the apparatus failed to gather the evidence (driller skip, synthesizer rejection cascade, or settling-experiment infrastructure failure). Experiments with `attribution: human-owner-attributable` (waiting on operator input) do NOT count toward (d); three simultaneous human-owner waits instead emit a `human-owner-bottleneck` finding tagged for operator attention without firing termination.
- **(e) Operator-directed apparatus-revision-required.** The operator explicitly states that the current V3 run has produced a sufficient apparatus-revision signal to warrant an implementation run (V3.1+). This is the only trigger that is not mechanically computable. Abuse-prevention floor: minimum 1 completed cycle before (e) may fire (the discarded-run scenario — operator terminates after cycle 1 with confirmed findings — is a legitimate use case).

### §6.(e) authorization basis

The Form-5 artifact written under trigger (e) MUST cite three sources of authorization:
1. The operator's verbatim termination statement (quoted in the Form-5 header, with session-id + episode-id of the statement's origin).
2. The TERMINATION.md artifact itself (the `unbraked-deepening/V3-RUN-NNN-TERMINATION.md` path being written).
3. `.claude/knowledge/decisions/v3.1-apparatus-revision-meta-authorization.md` (the meta-authorization decision file that pre-authorizes apparatus-revision writes; read it via `knowledge(action='read', path='decisions/v3.1-apparatus-revision-meta-authorization.md')` when composing the Form-5).

Authorization is grounded in the meta-authorization decision file plus the operator's statement, NOT in trigger (e) itself.

### §6.(e).1 Form-5 (e) required structure

The Form-5 artifact for trigger (e) MUST contain these 6 sections in order. The synthesizer rejects a Form-5 (e) that is missing any required section, or whose §3 is empty.

1. **Header** — 8 fields: `apparatus-version`, `run-id`, `cycle-count` (integer, includes discarded cycles), `operator-direction` (verbatim quote + session-id + episode-id), `trigger-fired` (must be `(e) operator-directed apparatus-revision-required`), `date`, `session-id`, `episode-id`. The `run-id` and `apparatus-version` are read from the run's initial `cycle-0-bootstrap.md` artifact; `session-id` and `episode-id` are the current session and episode at termination time.
2. **§1 Yield Summary** — a metrics table: novel Tier-1 findings count, synthesizer-confirmed Tier-2 findings count, issues filed (open/resolved breakdown), knowledge promotions count, settling experiments dispatched/resolved/stale breakdown. Each row is an integer.
3. **§2 Apparatus Findings Crystallized** — a prose or bullet summary of the highest-signal findings from the run, suitable as primary input for a V3.1 designer. MUST name at least one specific apparatus-behavioural gap.
4. **§3 Changeset Proposal** — REQUIRED, NOT optional. Must contain at least one scope item describing a proposed change to the apparatus. This is the primary deliverable of a trigger-(e) termination — it is the structured input to V3.1 (or V3.2+) apparatus-revision. An empty §3 is a Form-5 rejection.
5. **§4 Honest Accounting — Unrun Cycles** — explicit statement of the cost/yield ratio for cycles that would have been run if (e) had not fired. MUST state: estimated remaining cycles (integer or "unknown"), estimated additional yield at current run rate, one-sentence operator-facing rationale for early termination.
6. **§5 Carry-Forward Items + §6 Cross-references** — open issues or settling experiments that survive the run's termination, plus links to the meta-authorization decision file, the V3.1 scope-synthesis (if it exists), and any directly-downstream V3.1 implementation plan.

<!-- v3-apparatus self-audit sentinel — wholesale rewrite for fix-skill-v3-apparatus addressing surface-gate critical + important findings; tier taxonomy inlined, Posture-A replaced with self-defining write-discipline language, R6 and friction-asymmetry references stripped, route-back-instruction added to verdict enum, dangling sentinels (experiment-stale, apparatus-owner-attributable, human-owner-bottleneck, apparent-pattern-not-evidence) defined inline, trigger-(e) authorization basis stated inline -->
