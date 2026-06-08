---
name: asset-phase
description: "Asset-pipeline wrapper around `visual-work-loop`. Dispatches asset-designer to extract a frozen rubric and produce K candidates, dispatches asset-critic in `mode=verify` DIRECTLY (K-Sort carve-out — visual-work-loop is `mode=check`-only), then delegates the per-item rubric + Simulated-Annotators `mode=check` iteration loop to `visual-work-loop` (critic = `asset-critic`, fix-author = `asset-designer`). Round-cap at `N=3`. Pre-checks `asset_enabled: true` in bootstrap-config and `brand-spec.yaml` existence — skips silently when either gate fails."
audience: subagent
caller-allowlist: [main]
allowed-tools:
  - mcp__context-tools__smart_read
  - mcp__context-tools__smart_bash
  - mcp__context-tools__dispatch_agent
  - mcp__context-tools__dispatch_agent_status
  - mcp__context-tools__knowledge
  - mcp__context-tools__findings
  - Skill
---

## Purpose

You drive the asset-pipeline workflow for one planner subtask carrying `Asset phase: yes`. The work splits in two: the asset-specific pieces live here, the shared `mode=check` critic-iteration mechanics live in `visual-work-loop`.

You own:

- **Pre-flight gating.** `asset_enabled` in `.claude/bootstrap-config.json`, brand-spec existence, and `asset_type` + `target_dimensions` envelope validation. The asset pipeline is opt-in at the project level; you halt cleanly when the project has not opted in.
- **Round-state across the wrapper invocation.** Round counter `N` (cap `N ≤ 3`), `previous_round_winner_path` carried from prior STOP rounds via the delegation envelope, and the frozen-rubric path the asset-designer wrote in Round 1.
- **Round-1 K-candidate generation.** Dispatching `asset-designer` to extract the frozen rubric and produce K candidates via `asset_edit`, then confirming the Tier-1 elimination artifact exists.
- **The K-Sort `mode=verify` carve-out.** Dispatching `asset-critic` in `mode=verify` DIRECTLY (not via `visual-work-loop`). `mode=verify` emits a ranked candidate list with `best_candidate`, not the five-enum verdict — `visual-work-loop` is explicitly out of scope for it [verified: .claude/skills/visual-work-loop/SKILL.md § When invoked].
- **Wiring the `mode=check` loop.** Passing the verify winner, the frozen-rubric path, `asset-critic` as the critic, `asset-designer` as the fix-author, and the asset-pipeline envelope fields into `visual-work-loop` via `Skill(skill='visual-work-loop', ...)`.
- **Surfacing `attention_signal` upward.** Passing the loop's `attention_signal` envelope through to the orchestrator unchanged. See § Operator-attention surfacing for the consumption contract.

`visual-work-loop` owns (do NOT re-implement at this layer): rubric attachment to the critic, criterion-score 1:1 validation, the five-enum verdict routing (`STOP | CONTINUE | BACKTRACK | RESTART | ESCALATE`) inside the `mode=check` iteration loop, the `M ≤ cap_M` cap, the pixel-never invariant, the `bin/extract-critic-verdict.py` helper invocation, asset-designer fix-author dispatch on non-STOP verdicts, and `attention_signal` aggregation from `criterion_scores[]` [verified: .claude/skills/visual-work-loop/SKILL.md § Purpose]. From your perspective the five-enum collapses to three outer outcomes on return: `stop`, `escalate`, `skip`.

You are pixel-never. You never `Read` a candidate path; you pass paths to the critic and the loop driver and consume their text output.

## When invoked

The orchestrator invokes you when a planner subtask carries `Asset phase: yes`. Per the canonical asset-pipeline ordering, this happens AFTER `ux-designer` has fixed the surface structure (so the asset-designer knows target placement, dimensions, and state) and BEFORE `solution-designer` / `implementer` need the asset path.

You are invoked ONCE per Asset wrapper-round. The orchestrator passes the current round counter `N` in the delegation envelope and is responsible for re-dispatching you on a fresh round if a prior invocation returned escalate with a reason that the operator decided to retry. You do not loop multiple wrapper-rounds inside a single invocation — round-state is held by the orchestrator across invocations.

The wrapper passes you these envelope fields:

- `session_dir`, `task_id`, `round` — provenance for output paths. `{TASK}` binds to `task_id` verbatim (full string, no slug); `{N}` binds to `round`.
- `asset_type` — closed enum `mascot | logo | icon | banner | single-character-flat`.
- `target_dimensions` — `{ width, height }` in pixels.
- `previous_round_winner_path` — required when `round > 1` and the prior round emitted STOP; passed through to the asset-designer fix-author overlay so the BACKTRACK branch inside `visual-work-loop` can roll back to it.
- `final_ship_gate` — boolean; when `true`, raises the asset-critic Simulated-Annotator agreement floor from 60% to 80% (see asset-critic.md § Mode: check). Set by the orchestrator on the final asset deliverable round.

Path templates throughout this body bind these envelope fields verbatim. `{N}` is integer; `{TASK}` is the full task_id; `{M}` is `visual-work-loop`'s internal iteration counter and is never set or read here.

The Step 4 `Skill(skill='visual-work-loop', ...)` invocation passes `visual-work-loop`'s `caller-allowlist: [main]` gate even though `asset-phase` is not listed in it. `bin/skill-agent-gate.py` (main) resolves caller identity from the PreToolUse:Skill payload via the chain `subagent_type → agent_type → agent_id → "main"` — NOT from the invoking SKILL's `name:` frontmatter. A wrapper SKILL body executing inside the orchestrator's turn produces a Shape-1 payload (no `subagent_type`, no `agent_type`); the resolver falls through to the literal `"main"` fallback, which the gate's synonym table matches against `[main]` and `[orchestrator]` allowlists [verified: bin/skill-agent-gate.py main(); .claude/knowledge/constraints/skill-agent-gate-main-orchestrator-synonym.md]. `ux-aesthetic-loop`'s § Step 2 — Delegate iteration to `visual-work-loop` is the sibling-symmetric production proof of this contract.

## Pre-flight checks

Run all three gates before any agent dispatch. On any failure, halt and return the named skip envelope from § Round exit.

1. **`asset_enabled` gate.** `smart_read` `.claude/bootstrap-config.json` and check `asset_enabled`. If `false` or absent, halt: emit `findings(topic='asset-phase-skipped-disabled', tags=['constraint'])` and return `outcome: skip` with `skip_reason: asset-pipeline-disabled`. The orchestrator advances the subtask without an asset. The gate is correct-by-design under both flag states: enabled → procedure runs end-to-end; disabled → clean skip with no agent dispatch.
2. **Brand-spec gate.** Confirm `.claude/knowledge/brand/brand-spec.yaml` exists via `smart_bash`. If absent, halt: emit `findings(topic='brand-spec-missing')` and return `outcome: skip` with `skip_reason: brand-spec-missing`.
3. **Envelope-validation gate.** Confirm the delegation envelope names `asset_type` (in the closed enum) and `target_dimensions` (both `width` and `height`). If either is missing or malformed, halt: emit `findings(topic='asset-phase-envelope-invalid', tags=['constraint'])` and return `outcome: skip` with `skip_reason: envelope-invalid`.

Do not auto-retry on any pre-flight failure — the orchestrator surfaces the skip to the operator.

## Round state

You hold three values for the duration of this invocation:

- `N` — the round counter, taken from the delegation envelope's `round` field. Treat it as constant for this invocation; do not increment it. Multi-round semantics live at the orchestrator.
- `previous_round_winner_path` — taken from the delegation envelope when `N > 1`; null on `N = 1`. Passed through to the asset-designer fix-author overlay so `visual-work-loop`'s BACKTRACK branch (inside its `mode=check` iteration loop) can roll back to it [verified: .claude/agents/asset-designer.md § BACKTRACK].
- `frozen_rubric_path` — the path the asset-designer writes in Step 1: `{session_dir}/{TASK}-R{N}-asset-sketch.md`. Preserved across iterations because `visual-work-loop` carries it through unchanged via `rubric_path` (the contract token it does not parse) [verified: .claude/skills/visual-work-loop/SKILL.md § Purpose].

**Round cap.** Enforce `N ≤ 3` at entry-time (Step 0). If `N > 3` on arrival, halt: emit `findings(topic='asset-phase-round-cap-hit', tags=['gotcha'])` and return `outcome: escalate` with `escalation_reason: asset-phase-round-cap-hit`. The cap matches `visual-work-loop`'s internal `cap_M: 3` — three wrapper-rounds × three iteration-loop iterations is the global ceiling before the operator is asked to resume-or-abandon.

## Procedure

### Step 0 — Confirm preconditions

Run the three pre-flight gates from § Pre-flight checks AND the round-cap entry-time check from § Round state. On any failure, halt and return the named skip or escalate envelope from § Round exit. Emit findings as named there. Do not proceed past Step 0 if any gate failed.

### Step 1 — Dispatch asset-designer

Dispatch `asset-designer` via `dispatch_agent` with `subagent_type: asset-designer`, `model_route: claude` (Opus, same-family — see asset-designer.md frontmatter), and a `prompt_body` carrying these envelope fields:

```yaml
session_dir: {session_dir}
task_id: {TASK}
round: {N}
asset_type: <from envelope>
target_dimensions: <from envelope>
prior_critic_verdict: null                  # asset-phase-level; CONTINUE/BACKTRACK/RESTART are visual-work-loop-internal
previous_round_winner_path: <from envelope, or null on N=1>
```

Poll with `dispatch_agent_status` until `complete`. The designer extracts the frozen rubric (Round 1) or recovers it from the prior sketch (`N > 1`), runs the mandatory two-pass cross-check (Round 1 only), and calls `asset_edit` to produce K candidates. Designer output: `{session_dir}/{TASK}-R{N}-asset-sketch.md` with `## Rubric`, `## Generation Parameters`, and `## Candidate Paths` sections [verified: .claude/agents/asset-designer.md § Asset-Sketch Output Format].

Designer escalation (the designer wrote its own escalation file): the dispatch returns non-`complete` or the sketch is missing the `## Rubric` section → halt and return `outcome: escalate` with `escalation_reason: designer-failed`. Do not attempt Tier-1 or any critic dispatch.

### Step 2 — Confirm Tier-1 artifact

The `asset_edit` tool chain runs deterministic Tier-1 programmatic checks (hex ΔE, aspect, resolution, OCR) inside the designer's turn and writes the result to `.agent_context/assets/{TASK}-R{N}-round-{M_designer}-tier1.json`. The asset-designer manages `{M_designer}` (its own sub-round candidate batch counter) internally per asset-designer.md § Iteration Handling — Round 1 emits `M_designer = 1`, but CONTINUE/RESTART branches on wrapper-rounds N>1 may produce any integer. The wrapper does NOT compute or track `{M_designer}` — Step 1 just completed, so the just-written tier1.json is whichever file under this glob has the most recent mtime.

Resolve the path via `smart_bash`:

```
ls -t .agent_context/assets/{TASK}-R{N}-round-*-tier1.json 2>/dev/null | head -n 1
```

Capture stdout (with trailing newline trimmed) as `tier1_json_path` — it is passed verbatim into Step 3 and Step 4. If stdout is empty (no file matched): halt and return `outcome: escalate` with `escalation_reason: tier1-artifact-missing` and emit `findings(topic='tier1-artifact-missing-observed', content=<expected path glob>, tags=['constraint'])`. The critic would re-fire the same escalation; emitting at the wrapper short-circuits the dispatch round-trip.

### Step 3 — Dispatch asset-critic in `mode=verify` (K-Sort carve-out)

**Why this dispatch bypasses `visual-work-loop`.** `mode=verify` emits a ranked candidate list with `best_candidate` — it does NOT emit the five-enum verdict envelope `visual-work-loop`'s helper validates. `visual-work-loop` is `mode=check`-only by explicit scope: its `helper_kind: asset-check` accepts `mode=check` dispatches and the loop driver MUST NOT invoke the `bin/extract-critic-verdict.py` helper for verify-mode [verified: .claude/skills/visual-work-loop/SKILL.md § When invoked]. The wrapper handles verify-mode dispatch directly here.

Dispatch `asset-critic` via `dispatch_agent` with `subagent_type: asset-critic`, `model_route: claude`, and a `prompt_body` carrying:

```yaml
session_dir: {session_dir}
task_id: {TASK}
round: {N}
mode: verify
tier1_json_path: <from Step 2>
candidate_paths: <list from designer sketch ## Candidate Paths>
frozen_rubric_path: {session_dir}/{TASK}-R{N}-asset-sketch.md
reference_image_paths: <brand-spec reference_images paths; optional>
```

Read the designer-emitted candidate paths via `smart_read(mode='section', name='Candidate Paths', file={session_dir}/{TASK}-R{N}-asset-sketch.md)`, one absolute path per non-blank, non-`#` line. Pass paths only — never `Read` the image bytes.

Poll with `dispatch_agent_status` until `complete`. The critic runs Tier-1 elimination on the candidate set, then K-Sort tournament on survivors (degraded shape at K=3/K=2/K=1 per asset-critic.md § Mode: verify). Output: ranked list and `best_candidate` written to `{session_dir}/{TASK}-R{N}-asset-critique.md` (no five-enum verdict — verify-mode omits the `action:` field per asset-critic.md § verify mode output).

Verify-mode escalations:

- The dispatch returned non-`complete`, or the critique file is missing a `best_candidate` field → halt and return `outcome: escalate` with `escalation_reason: verify-dispatch-failed`.
- The critique file's frontmatter carries `reason: pairwise-ambiguous` (K=2 swap-verify disagreed; see asset-critic.md § verify mode output decision-table row 10) → halt and return `outcome: escalate` with `escalation_reason: pairwise-ambiguous` and emit `findings(topic='asset-pairwise-ambiguous', tags=['gotcha'])` so K-Sort sizing rules can be tuned.
- `K=0` survivors after Tier-1 elimination (all candidates eliminated; the critique carries this annotation per asset-critic.md § Critic Step A) → halt and return `outcome: escalate` with `escalation_reason: verify-zero-survivors`.

On success: read `best_candidate` from the critique file via `smart_read(mode='section', name='Verdict', file=...)` and capture it for Step 4.

### Step 4 — Delegate `mode=check` iteration to `visual-work-loop`

Invoke `Skill(skill='visual-work-loop', ...)` with the ten-field envelope from [verified: .claude/skills/visual-work-loop/SKILL.md § Asset-consumer contract]. Pass these values:

```yaml
session_dir: {session_dir}
task_id: {TASK}
round_N: {N}
subagent_type: asset-critic
model_route: claude
helper_kind: asset-check
rubric_path: {session_dir}/{TASK}-R{N}-asset-sketch.md
image_paths: [<best_candidate from Step 3>]
cap_M: 3
extra_critic_envelope:
  mode: check
  tier1_json_path: <from Step 2>
  reference_image_paths: <brand-spec reference_images paths; optional>
  final_ship_gate: <from envelope, default false>
  frozen_rubric_path: {session_dir}/{TASK}-R{N}-asset-sketch.md
fix_author_subagent_type: asset-designer
fix_author_envelope_template:
  asset_type: <from envelope>
  target_dimensions: <from envelope>
  previous_round_winner_path: <from envelope, or null on N=1>
image_paths_refresh:
  mode: sketch-section
  path: {session_dir}/{TASK}-R{N}-asset-sketch.md
  section: Candidate Paths
canonical_verdict_path: {session_dir}/{TASK}-R{N}-asset-critique.md
```

Field-by-field shape rationale (one note per row deserving emphasis):

- `rubric_path` is the frozen rubric the asset-designer wrote in Step 1. `visual-work-loop` carries it through unchanged as a contract token; it does not parse rubric semantics. Asset-phase preserves this path across all iterations of the `mode=check` loop because BACKTRACK/RESTART within `visual-work-loop` re-dispatch the designer with the same `frozen_rubric_path` — the rubric is frozen for the round [verified: .claude/agents/asset-designer.md § CONTINUE].
- `image_paths` is single-element (the verify winner) on entry. The loop driver refreshes it from the designer's re-emitted `## Candidate Paths` section on each fix-author return per the `sketch-section` refresh shape.
- `extra_critic_envelope.mode: check` is what gates asset-critic into per-item rubric + Simulated-Annotators mode (asset-critic.md § Mode: check). Setting `mode: verify` here would route to verify behavior and the helper would reject the output — verify-mode does not emit the five-enum verdict the helper validates.
- `fix_author_envelope_template` is opaque to the loop driver; the driver overlays `prior_critic_action`, `suggested_fix`, and `iteration_M` per `visual-work-loop`'s § Verdict branching shared shape. asset-designer's § Iteration Handling consumes `prior_critic_action ∈ {CONTINUE, BACKTRACK, RESTART}` and the wrapper-provided `previous_round_winner_path`.
- `cap_M: 3` matches asset-phase's wrapper-round cap and asset-critic's decision-table row 3 escalation precondition.
- `model_route: claude` is the same-family Claude route; cross-family routes for asset-critic are a Phase-3+ concern and not wired here.

**Known limitation — `tier1_json_path` is not refreshed across iterations.** `visual-work-loop` passes `extra_critic_envelope` opaquely to the critic on every iteration [verified: .claude/skills/visual-work-loop/SKILL.md § When invoked]; no `tier1_refresh` field exists symmetric with `image_paths_refresh`. On iterations `M > 0`, the asset-designer's fix-author run produces a fresh tier1.json at a new `{M_designer}` batch path (per asset-designer.md § CONTINUE), but the critic receives the `M = 0` snapshot. Operationally the gap is fragility, not breakage at the current operating point — programmatic Tier-1 checks (hex ΔE, aspect, resolution, OCR) on edit-mode refinements within the same brand-spec rarely flip pass→fail. Tightening requires an upstream `visual-work-loop` contract change (a `tier1_refresh` field mirroring `image_paths_refresh`); routes to a follow-on planner subtask. Before composing the envelope above, emit `findings(topic='asset-phase-tier1-refresh-contract-gap', content=<tier1_json_path + cap_M>, tags=['constraint'])` once per invocation so the gap accumulates visibly until the upstream fix lands.

Wait for the `Skill(...)` return — that IS the full `mode=check` loop's execution. You receive the envelope shape defined at [verified: .claude/skills/visual-work-loop/SKILL.md § Loop exit].

### Step 5 — Branch on the loop return

Read the returned `outcome:` field and route per § Verdict branching. There is no per-iteration work at this layer — every `mode=check` iteration ran inside Step 4.

## Verdict branching

`visual-work-loop` owns the per-iteration five-enum routing (`STOP | CONTINUE | BACKTRACK | RESTART | ESCALATE`) internally inside the `mode=check` loop. By the time you read the return envelope, all iterations have run and the inner loop has collapsed to ONE of three outer outcomes: `stop`, `escalate`, `skip`. Route once at this layer:

- **`outcome: stop`** — the asset-critic emitted STOP on the final iteration. The asset is approved. Resolve `best_candidate` per § Round exit (clean) — the loop envelope does not carry it directly; the wrapper re-reads it from the asset-sketch's `## Candidate Paths` section. Capture `canonical_verdict_path` from the loop return. Exit per § Round exit (clean).

- **`outcome: escalate`** — the loop terminated abnormally. The `escalation_reason` slug on the return envelope names the failure: `loop-cap-hit`, `critic-dispatch-failed`, `critic-verdict-malformed`, `verdict-image-receipt-mismatch`, `image_paths_refresh-failed`, or a critic-emitted slug (e.g., `oscillation`, `simulated-annotator-disagreement`, `round-cap-hit`) per [verified: .claude/skills/visual-work-loop/SKILL.md § Verdict branching]. Pass the envelope through to the orchestrator per § Round exit (escalation) — the orchestrator surfaces escalation to the operator and the subtask cannot advance to solution-designer without operator input. Do NOT auto-retry and do NOT increment `N` — multi-round semantics are the orchestrator's choice on operator input.

- **`outcome: skip`** — `visual-work-loop` halted at entry-time (envelope-incomplete, malformed `image_paths_refresh`). This should not happen if Step 4's envelope is composed per the spec above; if it does, surface the `skip_reason` per § Round exit (skipped) and emit `findings(topic='asset-phase-loop-envelope-rejected', content=<skip_reason + the envelope you passed>, tags=['gotcha'])` so the wrapper-to-driver contract drift can be tightened.

## Round exit

Return ONE envelope to the orchestrator. The shape mirrors `visual-work-loop`'s return [verified: .claude/skills/visual-work-loop/SKILL.md § Loop exit] with two asset-pipeline-specific additions: the `best_candidate` path on clean exit (the orchestrator passes it to `solution-designer` / `implementer`), and the `frozen_rubric_path` on every exit shape that ran past Step 1 (downstream validators read this to confirm rubric provenance).

**Clean exit (`outcome: stop`):**

The loop's return envelope does NOT carry `best_candidate_path` — visual-work-loop's exit contract is generic and asset-specific candidate-path tracking happens at this wrapper layer [verified: .claude/skills/visual-work-loop/SKILL.md § Loop exit]. Resolve `best_candidate` by re-reading the asset-sketch's `## Candidate Paths` section AFTER the loop returns: the asset-designer's fix-author runs rewrite that section on each iteration (per asset-designer.md § CONTINUE / § BACKTRACK / § RESTART), so the section's content at loop-return time IS the refined path family for the converged round. On iterations that ran zero designer fix-authors (STOP on the first inspection), the section still contains the verify winner Step 3 captured. Read via `smart_read(mode='section', name='Candidate Paths', file={session_dir}/{TASK}-R{N}-asset-sketch.md)` and take the first non-blank, non-`#` line as the single `best_candidate`.

```yaml
outcome: stop
round_N: {N}
final_M: <integer from the loop return>
verdict: STOP
best_candidate: <first path from `## Candidate Paths` of {session_dir}/{TASK}-R{N}-asset-sketch.md, read after loop return>
frozen_rubric_path: {session_dir}/{TASK}-R{N}-asset-sketch.md
canonical_verdict_path: {session_dir}/{TASK}-R{N}-asset-critique.md
attention_signal: <pass-through from the loop return; see § Operator-attention surfacing>
```

**Escalation exit (`outcome: escalate` — every cause including wrapper-level pre-flight escalation, Step 1–3 escalation, and loop-internal slugs):**

```yaml
outcome: escalate
round_N: {N}
final_M: <integer from the loop return, or null if escalation happened before Step 4>
verdict: ESCALATE
escalation_reason: <slug — asset-phase-round-cap-hit | designer-failed | tier1-artifact-missing | verify-dispatch-failed | pairwise-ambiguous | verify-zero-survivors | loop-cap-hit | critic-dispatch-failed | critic-verdict-malformed | verdict-image-receipt-mismatch | image_paths_refresh-failed | <critic-emitted slug>>
frozen_rubric_path: {session_dir}/{TASK}-R{N}-asset-sketch.md  # or null if escalation happened before Step 1 completed
canonical_verdict_path: <from the loop return — last verdict file written, may be null if escalation pre-empted Step 4>
attention_signal: <pass-through with escalation_context: true per the loop's contract, or { triggered: false } if pre-loop escalation>
```

The orchestrator MUST NOT advance the subtask past this point without operator input. No auto-retry. The escalation-reason taxonomy collapses three sources into one slug field: wrapper-level pre-flight and Step 1–3 dispatch failures, asset-critic's own escalation reasons (oscillation, annotator-disagreement, etc., which surface inside `mode=check` and bubble up via the loop's `critic-emitted` pass-through), and the loop driver's own structural escalations.

**Skipped exit (`outcome: skip` — pre-flight failure or driver-rejected envelope):**

```yaml
outcome: skip
round_N: {N}
skip_reason: <slug — asset-pipeline-disabled | brand-spec-missing | envelope-invalid | <driver-emitted slug>>
```

No agent dispatch fired (or the loop driver bailed at entry-time). The orchestrator advances the subtask without an asset — the absence of `best_candidate` is the routing signal. Findings emitted at the halt site carry the diagnostic the operator needs to decide whether the skip was correct.

## Operator-attention surfacing

`visual-work-loop` aggregates designer-side (`discipline: rubric-uncertain` + `score: concern`) and critic-side (`score: unevaluable`) rubric-uncertainty from the per-iteration `criterion_scores[]` into a single `attention_signal` block on its return envelope [verified: .claude/skills/visual-work-loop/SKILL.md § Operator-attention emission]. You do not aggregate or filter — pass the block through unchanged on every exit shape in § Round exit that names it.

The orchestrator-side consumption contract for `attention_signal`:

- When `attention_signal.triggered: true`, the orchestrator surfaces `attention_signal.summary` (a one-paragraph prose roll-up) and the `attention_signal.affected_criteria[]` list (each entry's `criterion_id`, `source`, `score`, and trimmed `prose`) to the operator BEFORE dispatching the next planner subtask.
- When `attention_signal.triggered: false`, the orchestrator advances without surfacing.
- On escalation exits, the loop driver's contract carries `attention_signal.escalation_context: true` so the operator can see the channel fired because of cap-exhaustion rather than normal completion. The wrapper does not set or strip this flag — pass it through verbatim.

The two source semantics the operator sees (defined by the loop driver's aggregation rule, included here so the wrapper layer makes the contract visible to its consumers without forcing a cross-file load):

- `source: designer` — the asset-designer flagged the criterion as `discipline: rubric-uncertain` AND the asset-critic emitted `concern` on it. The criterion's `threshold.concern` prose (designer-authored, grounded in brand-spec) is the operator-facing message.
- `source: critic` — the asset-critic emitted `score: unevaluable`, meaning the criterion could not be measured against the pixel evidence (e.g., the criterion checks a brand element the candidate's resolution or framing does not let the VLM resolve). The critic's `criterion_scores[].evidence` prose is the operator-facing message.

`discipline: deferred-ceiling` + `score: concern` does NOT contribute to `attention_signal` — that combination is the by-design deferral pattern for ceiling-aspiration criteria, filtered by the loop driver [verified: .claude/skills/visual-work-loop/SKILL.md § Aggregation rule].

This skill adds NO asset-specific attention logic. The channel is mechanism-level (loop driver), not domain-level (UX vs Asset); the contract is symmetric with `ux-aesthetic-loop`.

## Findings triggers

Asset-phase emits findings only on wrapper-owned failure modes — the loop driver and the dispatched agents emit their own findings on driver-internal and agent-internal failures.

- **`asset-phase-skipped-disabled`** — pre-flight gate 1 found `asset_enabled: false` or absent. Tag `constraint`. Emitted on every disabled-state invocation so the operator-attention channel surfaces the production-state flag if it stays off too long.
- **`brand-spec-missing`** — pre-flight gate 2 found no `.claude/knowledge/brand/brand-spec.yaml`. Tag `constraint`. Asset pipeline is non-functional without it; the orchestrator routes the subtask without an asset and the operator either curates the brand-spec or accepts the unassetted path.
- **`asset-phase-envelope-invalid`** — pre-flight gate 3 found `asset_type` or `target_dimensions` missing or malformed. Tag `constraint`. Includes the missing field name so the orchestrator-side delegation prompt can be corrected.
- **`asset-phase-round-cap-hit`** — entry-time check found `N > 3`. Tag `gotcha`. The cap was reached without the operator approving a STOP; surfaces alongside the prior rounds' verdicts for context.
- **`tier1-artifact-missing-observed`** — Step 2 confirmation failed. Tag `constraint`. Includes the expected path so the `asset_edit` chain's tier1-emission contract can be tightened if the absence is recurring.
- **`asset-pairwise-ambiguous`** — Step 3 K=2 swap-verify produced disagreement. Tag `gotcha`. The K-Sort sizing rules at K=2 (single pair, single swap-verify) can be tuned only if the K=2 frequency is observable.
- **`asset-phase-tier1-refresh-contract-gap`** — Step 4 entry (once per invocation). Tag `constraint`. `visual-work-loop`'s opaque `extra_critic_envelope` pass-through means iterations `M > 0` of `mode=check` read the `M = 0` Tier-1 snapshot rather than the designer's fresh-batch tier1.json. Emission keeps the gap visible until visual-work-loop ships a `tier1_refresh` field symmetric with `image_paths_refresh`. See § Procedure Step 4 § Known limitation for the routing rationale.
- **`asset-phase-loop-envelope-rejected`** — `outcome: skip` returned from `visual-work-loop` (envelope-incomplete or `image_paths_refresh` malformed). Tag `gotcha`. Should not fire if Step 4 is composed per the envelope spec; emission means the wrapper-to-driver contract drifted.
