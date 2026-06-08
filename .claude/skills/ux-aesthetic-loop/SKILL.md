---
name: ux-aesthetic-loop
description: "UX wrapper around `visual-work-loop`: renders the ux-designer's prototype HTML to PNGs, then delegates iteration to `visual-work-loop` (critic = `ux-aesthetic-critic`, fix-author = `ux-designer`), then writes the `Aesthetic-Approval: M{M}-stopped` seal to the sketch frontmatter on STOP. Invoked by the orchestrator AFTER `ux-designer` emits an initial sketch + inline `ux-rubric.yaml` + `## Prototype HTML` AND `bootstrap-config.json` has both `ux_aesthetic_designer_loop_enabled: true` and `prototype_render_enabled: true`. Skipped silently when either flag is false or the sketch carries a prior `Aesthetic-Approval:` seal."
audience: subagent
caller-allowlist: [main]
allowed-tools:
  - mcp__context-tools__smart_read
  - mcp__context-tools__smart_write
  - mcp__context-tools__smart_bash
  - mcp__context-tools__knowledge
  - mcp__context-tools__findings
  - Skill
---

## Purpose

You drive the design-time aesthetic loop for one UX subtask. The work splits in two: the UX-specific pieces live here, the shared iteration mechanics live in `visual-work-loop`.

You own:

- **Rasterizing the prototype.** `bin/ux_prototype_render.py` against the designer-emitted `## Prototype HTML` produces the PNGs the critic sees as pixel evidence.
- **Wiring the loop.** Pass the rendered PNGs, the designer-emitted inline rubric, `ux-aesthetic-critic` as the critic, `ux-designer` as the fix-author, and `cap_M: 3` into `visual-work-loop` via `Skill(skill='visual-work-loop', ...)`.
- **Sealing the sketch on STOP.** Write `Aesthetic-Approval: M{final_M}-stopped` to the sketch frontmatter once the loop returns `outcome: stop` (`final_M` is the loop's last iteration counter, carried on the return envelope). The redesigned `ux-designer` body explicitly disclaims this responsibility ([verified: .claude/agents/ux-designer.md:78]); the seal is yours.
- **Surfacing `attention_signal` upward.** Pass the loop's `attention_signal` envelope through to the orchestrator unchanged. See § Operator-attention routing for the consumption contract.

`visual-work-loop` owns (do NOT re-implement at this layer): rubric attachment to the critic, criterion-score 1:1 validation, the five-enum verdict routing (`STOP | CONTINUE | BACKTRACK | RESTART | ESCALATE`), the `M ≤ 3` cap, the pixel-never invariant, the `bin/extract-critic-verdict.py` helper invocation, designer fix-author dispatch on non-STOP verdicts, and `attention_signal` aggregation from `criterion_scores[]` [verified: .claude/skills/visual-work-loop/SKILL.md § Purpose].

## When invoked

The orchestrator invokes you ONCE per UX subtask, AFTER `ux-designer` has produced an initial sketch + inline rubric + prototype HTML AND `pre-flight-gate` has approved the sketch. Confirm all preconditions before doing any work:

- `.claude/bootstrap-config.json` has BOTH `ux_aesthetic_designer_loop_enabled: true` AND `prototype_render_enabled: true` [verified: .claude/orchestrator-prompt.md:494]. If either is false, halt at Step 0 — return `outcome: skip` per § Loop exit with `skip_reason: bootstrap-config-disabled`. The orchestrator advances the subtask without aesthetic approval.
- Three designer-produced artifacts exist at the canonical paths:
  - `{session_dir}/{TASK}-R{N}-ux-sketch.md` carrying a non-empty `## Prototype HTML` section.
  - `{session_dir}/{TASK}-R{N}-ux-rubric.yaml` (the designer's inline rubric per [verified: .claude/agents/ux-designer.md § Inline rubric authoring]).
  - `{session_dir}/{TASK}-R{N}-ux-prototype.html` (the rendered prototype HTML the designer emitted from `## Prototype HTML`).
  Missing any of these → halt at Step 0 with `skip_reason: designer-artifacts-missing` and emit `findings(topic='ux-aesthetic-loop-designer-artifacts-missing', tags=['constraint'])` naming which path was absent. Do not synthesize the missing artifact.
- The sketch frontmatter does NOT already carry `Aesthetic-Approval: M<k>-stopped`. A sealed sketch means a prior loop run completed — do not re-enter. Halt at Step 0 with `skip_reason: sketch-already-sealed`.

`{TASK}` is the full `task_id` envelope value (no slug); `{N}` is the `round` envelope value. The wrapper does not re-derive these from the filesystem.

You are not invoked on a fix-pass — there is no second-dispatch posture for this skill. The orchestrator dispatches you exactly once per UX subtask; everything iterative happens inside the `visual-work-loop` call at Step 2 of § Procedure.

## Procedure

### Step 0 — Confirm preconditions

Run the gate checks from § When invoked. On any failure, halt and return the named skip envelope from § Loop exit. Emit findings as named there.

### Step 1 — Render the prototype HTML to PNGs

Invoke `bin/ux_prototype_render.py` via `smart_bash`:

```
bin/ux_prototype_render.py \
  --session-dir {session_dir} \
  --task {TASK} \
  --round {N} \
  --subround 0 \
  --prototype-html {session_dir}/{TASK}-R{N}-ux-prototype.html \
  --viewports 1440,768,375
```

The renderer writes one PNG per viewport at `{session_dir}/{TASK}-R{N}-M0-prototype-render-{width}px.png` (three paths on default viewports). Capture the list as `prototype_render_paths`.

**Why `--subround 0` is hardcoded.** `visual-work-loop`'s `image_paths_refresh` shell-mode contract requires the re-render at each iteration to overwrite the SAME paths the prior iteration produced [verified: .claude/skills/visual-work-loop/SKILL.md § When invoked]. The renderer embeds its `--subround` value into the output filename, so fixing it to `0` here gives stable paths the loop driver can reuse unchanged across iterations. The critic's `iteration_M` (the loop counter) is a separate concept owned by `visual-work-loop` and unrelated to this argument.

Exit codes from the renderer: `0` = success; `1` = playwright unavailable (treat as `outcome: skip` with `skip_reason: renderer-unavailable`); `2` = render error (treat as `outcome: skip` with `skip_reason: renderer-failed` and emit `findings(topic='ux-prototype-render-failed', tags=['constraint'])` carrying the stderr).

Viewport choice rationale: `1440,768,375` covers desktop / tablet / mobile breakpoints — three of the six-image critic cap. Narrowing the list when the sketch's `Platform` field is single-form-factor (e.g., `native-desktop` only) is a future refinement; the default is safe and stays inside the cap.

### Step 2 — Delegate iteration to `visual-work-loop`

Invoke `Skill(skill='visual-work-loop', ...)` with this envelope. Field-by-field shape is defined at [verified: .claude/skills/visual-work-loop/SKILL.md § When invoked]; the values you pass are:

```yaml
session_dir: {session_dir}
task_id: {TASK}
round_N: {N}
subagent_type: ux-aesthetic-critic
model_route: claude    # the same-family route per [verified: .claude/agents/ux-aesthetic-critic.md § Pixel transport]; cross-family is Phase-3+
helper_kind: ux
rubric_path: {session_dir}/{TASK}-R{N}-ux-rubric.yaml
image_paths: <list from Step 1 — the three prototype-render-*.png paths>
cap_M: 3
extra_critic_envelope:
  prototype_render_paths: <same list from Step 1>
  ux_sketch_path: {session_dir}/{TASK}-R{N}-ux-sketch.md
fix_author_subagent_type: ux-designer
fix_author_envelope_template:
  session_dir: {session_dir}
  task_id: {TASK}
  round: {N}
  target_artifact_path: {session_dir}/{TASK}-R{N}-ux-sketch.md
image_paths_refresh:
  mode: shell
  command: "bin/ux_prototype_render.py --session-dir {session_dir} --task {TASK} --round {N} --subround 0 --prototype-html {session_dir}/{TASK}-R{N}-ux-prototype.html --viewports 1440,768,375"
canonical_verdict_path: "{session_dir}/{TASK}-R{N}-aesthetic-verdict-M{M}.md"
```

Three coupling notes about the values above:

- `rubric_path` points at the designer's per-task inline rubric YAML, not a shared hardcoded checklist. The pre-rewrite skill assumed six fixed dimensions (`hierarchy, copy, layout, state-clarity, accessibility, brand-fit`); that hardcoding is retired. The rubric `criteria[]` are whatever the designer authored per [verified: .claude/agents/ux-designer.md § Inline rubric authoring].
- `model_route` is the pixel-transport route the dispatch tool uses to attach the PNGs to the critic. Phase-2 default is the same-family Claude route (the value the critic body documents as the one its `tools:` frontmatter is sized for at [verified: .claude/agents/ux-aesthetic-critic.md § Pixel transport]). Cross-family routes (`gemini | gpt`) require the dispatch-tool `image_paths` field to be wired AND the critic body to be cross-family-validated; both are Phase-3+ concerns. The exact route-value string is owned by the dispatch-tool surface — pass through whatever value the source-of-truth pixel-transport section names; do not invent a new one here.
- `fix_author_subagent_type: ux-designer` plus `prior_critic_action: CONTINUE | BACKTRACK | RESTART` (overlaid by `visual-work-loop` per its § Verdict branching) is how non-STOP verdicts re-dispatch the designer as a fix-author. The designer's frontmatter at [verified: .claude/agents/ux-designer.md § Fix-author dispatch (visual-work-loop iteration)] accepts these three slugs and only these three; you do not re-implement the dispatch yourself.

Wait for the `Skill(...)` return — that IS the loop's full execution; you receive the envelope shape defined at [verified: .claude/skills/visual-work-loop/SKILL.md § Loop exit].

### Step 3 — Branch on the loop return

Read the returned `outcome:` field and route per § Verdict branching. There is no per-iteration work at this layer — every iteration ran inside Step 2.

## Verdict branching

`visual-work-loop` owns the per-iteration five-enum routing (`STOP | CONTINUE | BACKTRACK | RESTART | ESCALATE`) internally — by the time you read the return envelope, all iterations have run and the inner loop has collapsed to ONE of three outer outcomes: `stop`, `escalate`, `skip`. Route once at this layer:

- **`outcome: stop`** — the critic emitted STOP on the final iteration. The design is approved. Seal the sketch frontmatter per § Sealing the sketch on STOP below, then return the clean envelope from § Loop exit. There is NO extra designer dispatch on STOP — the pre-rewrite skill re-dispatched `ux-designer` one more time to write the seal; the redesigned designer disclaims that responsibility at [verified: .claude/agents/ux-designer.md:78] and the seal is yours to write directly.

- **`outcome: escalate`** — the loop terminated abnormally. The `escalation_reason` slug on the return envelope names the failure (`loop-cap-hit`, `critic-dispatch-failed`, `critic-verdict-malformed`, `verdict-image-receipt-mismatch`, `image_paths_refresh-failed`, or a critic-emitted slug per [verified: .claude/skills/visual-work-loop/SKILL.md § Verdict branching]). Do NOT seal the sketch. Pass the envelope through to the orchestrator per § Loop exit — the orchestrator surfaces escalation to the operator and the subtask cannot advance to solution-designer without operator input.

- **`outcome: skip`** — `visual-work-loop` halted at entry-time (envelope-incomplete, malformed `image_paths_refresh`). This should not happen if Step 2's envelope is composed per the spec above; if it does, surface the `skip_reason` per § Loop exit and emit `findings(topic='ux-aesthetic-loop-envelope-rejected-by-driver', content=<skip_reason + the envelope you passed>, tags=['gotcha'])` so the wrapper-to-driver contract drift can be tightened.

### Sealing the sketch on STOP

The seal is one line added inside the sketch's YAML frontmatter block: `Aesthetic-Approval: M{final_M}-stopped` where `{final_M}` is the integer `final_M` from the loop return envelope. The seal records which iteration the critic emitted STOP on (M=0 means STOP on first inspection; M=3 means STOP on the cap-iteration).

Mechanical procedure:

1. `smart_read` the sketch at `{session_dir}/{TASK}-R{N}-ux-sketch.md`.
2. Locate the frontmatter block — content between the leading `---` (line 1) and the next `---` line.
3. Insert `Aesthetic-Approval: M{final_M}-stopped` as a new line at the END of the frontmatter block (just before the closing `---`).
4. `smart_write` the modified file back to the same path.

If the sketch lacks a frontmatter block (no leading `---`), do NOT silently invent one — emit `findings(topic='ux-sketch-frontmatter-missing', content=<sketch path>, tags=['constraint'])` and return the escalation envelope from § Loop exit with `escalation_reason: sketch-frontmatter-missing`. The sketch shape is the designer's contract; the wrapper does not repair structural anomalies in it.

The seal is what downstream validator passes (screenshot-diff round, implementation-match scoring) read to confirm aesthetic approval happened before implementation began. A sealed sketch is also the precondition that gates re-entry into this skill (§ When invoked) — the seal is permanent within the session.

## Loop exit

Return ONE envelope to the orchestrator. The shape mirrors `visual-work-loop`'s return [verified: .claude/skills/visual-work-loop/SKILL.md § Loop exit] with two UX-specific additions: the sealed-sketch path on clean exit, and the surfaced `attention_signal` on every exit shape that the loop driver itself populated.

**Clean exit (`outcome: stop` after the seal is written):**

```yaml
outcome: stop
final_M: <integer from the loop return>
verdict: STOP
sealed_sketch_path: {session_dir}/{TASK}-R{N}-ux-sketch.md
canonical_verdict_path: {session_dir}/{TASK}-R{N}-aesthetic-verdict-M{final_M}.md
attention_signal: <pass-through from the loop return; see § Operator-attention routing>
```

The orchestrator passes the sealed sketch path to `solution-designer` and `implementer` as the approved UX surface.

**Escalation exit (`outcome: escalate` — every cause including loop-internal slugs and the seal-write failure):**

```yaml
outcome: escalate
final_M: <integer reached>
verdict: ESCALATE
escalation_reason: <slug — loop-cap-hit | critic-dispatch-failed | critic-verdict-malformed | verdict-image-receipt-mismatch | image_paths_refresh-failed | sketch-frontmatter-missing | <critic-emitted slug>>
canonical_verdict_path: <from the loop return — last verdict file written, may be a placeholder>
attention_signal: <pass-through with escalation_context: true per the loop's contract>
```

The orchestrator MUST NOT advance the subtask past this point without operator input. There is no per-skill escalation file at this layer — the loop driver writes the canonical verdict, the wrapper writes nothing else. (The pre-rewrite skill referenced `{session_dir}/{TASK}-R{N}-aesthetic-escalation.md` as a critic-authored escalation file — that path is retired; the loop driver's canonical verdict file carries the escalation rationale via the embedded YAML envelope's `escalation_reason` and `verdict_rationale` fields.)

**Skip exit (entry-time bail or driver-rejected envelope):**

```yaml
outcome: skip
skip_reason: <slug — bootstrap-config-disabled | designer-artifacts-missing | sketch-already-sealed | renderer-unavailable | renderer-failed | <driver-emitted slug>>
```

No iteration ran (or no critic dispatch fired). The orchestrator advances the subtask without aesthetic approval — the sketch stays unsealed, downstream validators read the absence as "no aesthetic loop ran this round" and treat it as a non-event (NOT as an aesthetic failure). The findings emitted at the halt site carry the diagnostic the operator needs to decide whether the skip was correct.

## Operator-attention routing

`visual-work-loop` aggregates designer-side (`discipline: rubric-uncertain` + `score: concern`) and critic-side (`score: unevaluable`) rubric-uncertainty from the per-iteration `criterion_scores[]` into a single `attention_signal` block on its return envelope [verified: .claude/skills/visual-work-loop/SKILL.md § Operator-attention emission]. You do not aggregate or filter — pass the block through unchanged on every exit shape in § Loop exit that names it.

The orchestrator-side consumption contract for `attention_signal`:

- When `attention_signal.triggered: true`, the orchestrator surfaces `attention_signal.summary` (a one-paragraph prose roll-up) and the `attention_signal.affected_criteria[]` list (each entry's `criterion_id`, `source`, `score`, and trimmed `prose`) to the operator BEFORE dispatching the next planner subtask.
- When `attention_signal.triggered: false`, the orchestrator advances without surfacing.
- On escalation exits, the loop driver's contract carries `attention_signal.escalation_context: true` so the operator can see the channel fired because of cap-exhaustion rather than normal completion. The wrapper does not set or strip this flag — pass it through verbatim.

The two source semantics the operator sees (defined by the loop driver's aggregation rule, included here so the wrapper layer makes the contract visible to its consumers without forcing a cross-file load):

- `source: designer` — the designer flagged the criterion as `discipline: rubric-uncertain` AND the critic emitted `concern` on it. The criterion's `threshold.concern` prose (designer-authored) is the operator-facing message.
- `source: critic` — the critic emitted `score: unevaluable`, meaning the criterion could not be measured against the pixel evidence (e.g., the criterion checks a UI element the prototype does not render). The critic's `criterion_scores[].evidence` prose (critic-authored) is the operator-facing message.

`discipline: deferred-ceiling` + `score: concern` does NOT contribute to `attention_signal` — that combination is the by-design feature-deferral outcome of the motion-quality-ceiling-aspiration pattern, not an attention signal. The filter is the loop driver's, not yours; this paragraph documents the boundary so the wrapper's consumers can read this section as the routing contract without chasing references.

This skill does NOT add UX-specific attention logic. The channel is mechanism-level (loop driver) not domain-level (UX vs Asset); Phase-3's asset-phase rewrite will consume the same envelope contract symmetrically.
