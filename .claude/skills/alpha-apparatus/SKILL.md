---
name: alpha-apparatus
description: "Drives an α-investigation: planner → leaf×N → synthesizer dispatch sequence through the alpha-pipeline MCP server. Off-by-default — only available when --pipeline alpha is active. Token + span telemetry lands in {session_dir}/alpha-*.jsonl."
audience: dev
caller-allowlist: [orchestrator]
---

## Purpose

You invoke this skill when a planner subtask carries `Alpha phase: yes`. It drives one α-investigation: a planner fan-out into N leaf workers, then a synthesizer fold over the leaf outputs. Each step is a single `mcp__alpha-pipeline__alpha_dispatch` call; the wrapper writes per-call telemetry to `{session_dir}/alpha-*.jsonl` without your involvement.

Each dispatch is one-shot — no retry inside this skill body. If a step fails, the skill body either folds the partial set (Step 2 failures) or surfaces the failure to the orchestrator (Step 1 / Step 3 failures). The orchestrator decides whether to redispatch with a different framing.

## Three-step dispatch sequence

The pseudocode below is the SHAPE of the dispatch — instructions for you to follow, not code to execute. Each `mcp__alpha-pipeline__alpha_dispatch` call is one MCP tool invocation; the wrapper handles spawn, envelope parsing, JSONL writes, and timing. Leaf dispatches in Step 2 are sequential — call the next leaf after the previous one returns. The harness MAY emit them in one assistant turn (the model decides emit-shape); this skill body does not instruct parallelism.

**`campaign_id` write-time rule.** In the skill body, leave `campaign_id` as the empty string unless the orchestrator can verify its own `session_id` equals `CLAUDE_SESSION_ID` at write-time. `CLAUDE_SESSION_ID` is a process-env var the orchestrator can inspect via a one-shot `Bash` invocation (`echo "$CLAUDE_SESSION_ID"`); if no such inspection has run this session, take the empty-string fallback. The wrapper-derived value in `{session_dir}/alpha-*.jsonl` is the telemetry source of truth; any value the skill body echoes into the `alpha_input_contract` is label-only and does NOT influence routing.

**Per-session budget gate.** Enforced inside `mcp__alpha-pipeline__alpha_dispatch` itself — the wrapper checks each call's `expected_duration_seconds` against a 2× headroom test on the remaining session budget and refuses dispatch when the estimate would exceed it. The refusal surfaces as a `{error, detail, remediation}` failure on the dispatch return. Enforcement site: `.claude/mcp/alpha-pipeline/src/tools/alpha_dispatch.ts`. The "fanout_target_N >5 risks the gate" guidance below follows from the cumulative-budget math; raise N only when the orchestrator has measured headroom.

```pseudocode
# Step 1 — Plan fan-out
campaign_id = "" by default (per the write-time rule above)
planner_input = {
    "task": <the α-investigation task carried from the planner subtask>,
    "campaign_id": campaign_id,
    "fanout_target_N": <int; typical R1 values: 3–5; >5 risks the
                       per-session budget gate>,
    "constraints": [<constraint strings carried from the planner subtask>]
}
planner_response = mcp__alpha-pipeline__alpha_dispatch(
    subagent_type = "alpha-planner",
    prompt = json.stringify({"alpha_input_contract": planner_input}),
    depth = 1,
    expected_duration_seconds = 60,   # tune after first measurement cohort lands
    node_type = "planner"
)
# planner_response shape (success): {output_text, spawn_id, tokens, elapsed_seconds, model}
# planner_response shape (failure): {error, detail, remediation}

IF planner_response carries `error`:
    surface failure to orchestrator with planner_response.error + planner_response.detail
    ABORT — return failure summary
ELSE:
    planner_output = parse planner_response.output_text as JSON
    # See "Planner output contract" below for the field-level shape.
    IF planner_output.fanout_count == 0:
        surface to orchestrator: planner judged the task non-decomposable;
        include planner_output.decomposition_rationale
        ABORT — no leaves to fan to
    leaf_prompts = planner_output.leaf_prompts
    planner_spawn_id = planner_response.spawn_id  # for parent_spawn_id threading

# Step 2 — Leaf fan-out (sequential)
leaf_outputs = []
missing_leaves = []
FOR each leaf in leaf_prompts:
    leaf_input = {
        "leaf_id": leaf.leaf_id,
        "prompt": leaf.prompt,
        "campaign_id": campaign_id,
        "lens": leaf.lens IF leaf.lens is present ELSE OMIT
    }
    leaf_response = mcp__alpha-pipeline__alpha_dispatch(
        subagent_type = "alpha-leaf",
        prompt = json.stringify({"alpha_input_contract": leaf_input}),
        depth = 2,
        expected_duration_seconds = 90,
        parent_spawn_id = planner_spawn_id,
        node_type = "leaf"
    )
    IF leaf_response carries `error`:
        missing_leaves.append({"leaf_id": leaf.leaf_id, "failure_class": leaf_response.error})
        CONTINUE  # do NOT retry; do NOT abort; the wrapper has written the failure row
    ELSE:
        leaf_output = parse leaf_response.output_text as JSON
        # See "Leaf output contract" below for the field-level shape.
        leaf_outputs.append(leaf_output)

# Partial-failure threshold
IF len(missing_leaves) > floor(len(leaf_prompts) / 2):
    surface to orchestrator: "α-leaf fan-out: more than half failed; aborting before synthesis"
    ABORT — return partial-failure summary (leaf_outputs + missing_leaves)

# Step 3 — Fold
# α-synthesizer input declares ONLY {campaign_id, leaf_outputs, synthesis_target}.
# When missing_leaves is non-empty, prepend the coverage signal onto
# synthesis_target — do NOT add a fourth field.
synthesis_target_base = <the synthesis target string carried from the planner subtask>
IF missing_leaves is non-empty:
    missing_leaves_prefix = (
        "Coverage note: the following leaves failed during fan-out and are NOT "
        "represented in leaf_outputs[]: " + json.stringify(missing_leaves) + ". "
        "Echo these into your output's `missing_leaves` field. "
        "Synthesis target follows: "
    )
    synthesis_target = missing_leaves_prefix + synthesis_target_base
ELSE:
    synthesis_target = synthesis_target_base

synthesizer_input = {
    "campaign_id": campaign_id,
    "leaf_outputs": leaf_outputs,
    "synthesis_target": synthesis_target
}
synth_response = mcp__alpha-pipeline__alpha_dispatch(
    subagent_type = "alpha-synthesizer",
    prompt = json.stringify({"alpha_input_contract": synthesizer_input}),
    depth = 1,
    expected_duration_seconds = 120,
    node_type = "synthesizer"
)
IF synth_response carries `error`:
    surface failure to orchestrator with synth_response.error + synth_response.detail
    ABORT — return failure summary
ELSE:
    synthesis_artifact = parse synth_response.output_text as JSON
    # See "Synthesizer output contract" below for the field-level shape.
    return synthesis_artifact to the orchestrator
    # The orchestrator typically hands the artifact to the validator next.
```

Notes:

- `parent_spawn_id` threads from the planner into each leaf (Step 2 only). Step 1 and Step 3 omit it — the synthesizer is a sibling of the planner, not a child of a leaf.
- If `output_text` does not parse as JSON, treat the step as failed and surface the unparsed `output_text` in the failure detail.
- The `missing_leaves` prose prefix widens the declared `synthesis_target` field, NOT a new field. The synthesizer reads the `Coverage note: ... Synthesis target follows: ...` framing as inline coverage data and echoes the list into its output's `missing_leaves` field.

## Output contracts

Each α-agent emits a single JSON object as its final turn output, captured verbatim into `response.output_text`. Parse it with `JSON.parse` and pull the fields below. These schemas were derived from each agent body's `## Output contract` section at authoring time; if a returned envelope diverges from the shape documented here, treat it as a step-level failure (parse-error path in the table at § Error handling and failure folding) and surface the unparsed `output_text` to the orchestrator rather than guessing at field aliases.

**Planner — `planner_response.output_text`.**

```json
{
  "campaign_id": "<echoed>",
  "leaf_prompts": [
    {"leaf_id": "leaf-1", "prompt": "<self-contained instruction>", "lens": "<optional analysis-lens string; omit when absent>"}
  ],
  "fanout_count": 2,
  "decomposition_rationale": "<one paragraph>"
}
```

The `lens` field on each `leaf_prompts[]` entry is optional — when present, echo it through into the matching `leaf_input.lens` on Step 2; when absent, omit `lens` from `leaf_input`. Ignore other extra fields. Envelope-missing failure: `{error, campaign_id: null, leaf_prompts: [], fanout_count: 0, decomposition_rationale: null}` — caught by the Step 1 `fanout_count == 0` branch.

**Leaf — each `leaf_response.output_text`.**

```json
{
  "leaf_id": "<echoed>",
  "campaign_id": "<echoed>",
  "findings": ["<finding-1>", "<finding-2>"],
  "evidence_pointers": ["<file:line or knowledge-path citation>"],
  "completion_note": "<one sentence>"
}
```

Envelope-missing failure: `{error, leaf_id: null, campaign_id: null, findings: [], evidence_pointers: [], completion_note: null}` — treat as a Step 2 failure and record `{leaf_id, failure_class}` into `missing_leaves`.

**Synthesizer — `synth_response.output_text`.**

```json
{
  "campaign_id": "<echoed>",
  "synthesis": "<the depth-0 artifact body — prose, scaled to synthesis_target>",
  "leaf_coverage": ["leaf-1", "leaf-2"],
  "missing_leaves": ["<leaf_id> — <one-sentence why>"],
  "synthesis_self_check": "<one paragraph>"
}
```

`synthesis` is the load-bearing artifact body the orchestrator hands downstream. `missing_leaves` echoes the coverage gap you prefixed onto `synthesis_target`. Envelope-missing failure: same shape with `synthesis: null` and the literal `alpha_input_contract envelope not found in user-prompt body` in `synthesis_self_check` — surface as a Step 3 failure.

## Error handling and failure folding

| Where it fails | What you do |
|---|---|
| Step 1 (planner) returns `error`, OR `output_text` does not parse as JSON, OR `fanout_count == 0` | Abort. Surface the failure (or the `decomposition_rationale`) to the orchestrator. There is nothing to fan to. |
| Step 2 (leaf k) returns `error`, OR its `output_text` does not parse | Record `{leaf_id, failure_class}` into `missing_leaves`, continue with leaf k+1. The wrapper has already written the failure JSONL row. |
| Step 2 cumulative: more than `floor(N/2)` leaves failed | Abort before Step 3. Return a partial-failure summary (the successful `leaf_outputs[]` plus `missing_leaves[]`). The remaining set is too thin to synthesize. |
| Step 2 cumulative: 1 to `floor(N/2)` leaves failed | Proceed to Step 3. Build the `synthesis_target` prose prefix per the pseudocode so the synthesizer sees the coverage gap inline. |
| Step 3 (synthesizer) returns `error`, OR its `output_text` does not parse | Abort. Surface the failure to the orchestrator. The synthesizer body is responsible for emitting its own structured failure envelope when its inputs are degenerate; if it returned free-form prose instead of a JSON block, treat that as a Step 3 failure and include the unparsed `output_text` in the failure detail. |

The apparatus does NOT retry inside this skill body. The `attempt_number` field on the `alpha-dispatched.jsonl` and `alpha-failures.jsonl` row interfaces (`AlphaDispatchedRow`, `AlphaFailuresRow` in `.claude/mcp/alpha-pipeline/src/schemas.ts`) is hardcoded to `1` at every write site in `.claude/mcp/alpha-pipeline/src/tools/alpha_dispatch.ts` — that output-row hardcode is the future-retry-layer extension point, not an input slot. There is no `attempt_number` key in the `mcp__alpha-pipeline__alpha_dispatch` input schema, and the skill body is one-shot per call.

## Telemetry pointer

Per-α-child token + span records land at `{session_dir}/alpha-tokens.jsonl`, `alpha-spans.jsonl`, `alpha-dispatched.jsonl`, and `alpha-failures.jsonl` — written by the dispatch wrapper itself; the α-pipeline-owned PostToolUse measurement hook emits a cross-check sidecar. The four wrapper-written JSONLs are the load-bearing observables. Do NOT write to them from this skill body.

## Caller-discipline note

Only the orchestrator (`main`) may invoke this skill — `caller-allowlist: [orchestrator]` is hard-enforced by `bin/skill-agent-gate.py` (PreToolUse:Skill hook); `main` and `orchestrator` are synonyms there. A subagent reaching for `/alpha-apparatus` is a routing error upstream — invoke this skill only when a planner subtask carries `Alpha phase: yes`.
