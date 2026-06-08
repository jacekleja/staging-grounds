#!/usr/bin/env python3
# dispatch-child-safe: false
"""PostToolUse/SubagentStop hook: check pre-flight-gate state.

State substrate: `{session_dir}/critic-gate.jsonl` — append-only JSONL written by
critic-gate-tracker.py and read here. The filename and `critic_clear` action key
are also consumed by dashboard/server/collectors/jsonl.js, the test_critic_gate_*.py
suites, and settings.json hook registrations; do not rename in isolation.

Fires after each subagent completes (PostToolUse:Agent) or when a background
agent stops (SubagentStop). If generative agents completed without a subsequent
pre-flight-gate review, injects a warning to invoke the pre-flight-gate before
consuming outputs.

Silent (no output, exit 0) when no unreviewed generative agents exist.
"""
import json
import os
import sys

from _dispatch_child_guard import exit_if_dispatched_child


def determine_hook_event(event):
    """Return the correct hookEventName for the output wrapper.

    This is critical -- using the wrong event name causes output to be
    silently discarded by Claude Code.
    """
    hook_event = event.get("hook_event_name", "")
    if hook_event:
        return hook_event
    # Fallback: detect from payload structure
    if "agent_id" in event and "agent_transcript_path" in event:
        return "SubagentStop"
    return "PostToolUse"


def check_critic_gate(session_dir, is_subagent_stop=False):
    """Check if generative agents completed without pre-flight-gate review.

    Reads critic-gate.jsonl (append-only JSONL written by PreToolUse hook).
    Tracks state: generative entries after the last `critic_clear` action = unreviewed.
    Returns warning text or None.

    At PostToolUse time (is_subagent_stop=False): skip background=True entries
    because PostToolUse fires at launch time for background agents -- those
    agents haven't completed yet, so warning would be a false positive.

    At SubagentStop time (is_subagent_stop=True): include ALL entries including
    background ones -- SubagentStop fires when the agent actually completes,
    so background completions should trigger the warning.

    Per-(task, round) clear semantic for ux-designer:
    Inside a designer-loop, M consecutive ux-designer launches are made (one per
    CONTINUE verdict from ux-aesthetic-critic). Each launch appends a generative
    entry. A pre-flight-gate clear clears ALL pending ux-designer entries for the
    (task, round) tuple to prevent stale-entry warnings accumulating across sub-rounds.
    This semantic is specific to ux-designer; other GENERATIVE_TYPES use per-launch clear.

    Scenario trace (M=2 loop):
      1. ux-designer M=0 launch → entry {type:"generative", agent_type:"ux-designer", task:"T", round:"R1"}
      2. pre-flight-gate clear → entry {type:"clear", task:"T", round:"R1"} → clears entry 1
      3. critic returns CONTINUE; ux-designer M=1 launch → entry {task:"T", round:"R1", subround_M:1}
      4. pre-flight-gate clear → entry {type:"clear", task:"T", round:"R1"} → clears entry 3
      Result: 0 unreviewed ux-designer entries for (T, R1) after step 4. No stale warning.
    """
    gate_file = os.path.join(session_dir, "critic-gate.jsonl")

    if not os.path.exists(gate_file):
        return None

    # Parse JSONL -- collect unreviewed generative agents since last critic_clear.
    # For ux-designer entries: use per-(task, round) clear semantic.
    # For all other GENERATIVE_TYPES: use per-launch (global) clear semantic.
    unreviewed_non_ux = []  # non-ux-designer unreviewed entries
    # Maps (task, round) -> list of pending ux-designer entries for that tuple.
    # Entries without task/round use the sentinel key (None, None).
    unreviewed_ux = {}
    try:
        with open(gate_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                action = entry.get("action")
                if action == "critic_clear":
                    # Per-launch clear for non-ux-designer: global reset.
                    unreviewed_non_ux = []
                    # Per-(task, round) clear for ux-designer:
                    # The tracker writes task+round on clears whenever the dispatching
                    # delegation prompt carries them — present on designer-loop clears,
                    # absent on plain pre-flight-gate clears with no task context.
                    clear_task = entry.get("task")
                    clear_round = entry.get("round")
                    if clear_task is not None and clear_round is not None:
                        key = (clear_task, clear_round)
                        unreviewed_ux.pop(key, None)
                    else:
                        # Clear without task/round: scope is global — drop all pending
                        # ux-designer entries.
                        unreviewed_ux = {}
                elif action == "generative":
                    # At PostToolUse (launch time): skip background entries --
                    # the agent hasn't completed yet, skip to avoid false positive.
                    # At SubagentStop (completion time): include all entries
                    # including background ones -- this is the real completion.
                    is_background = entry.get("background", False)
                    if is_background and not is_subagent_stop:
                        continue
                    agent_type = entry.get("type", "unknown")
                    if agent_type == "ux-designer":
                        # Per-(task, round) tracking for ux-designer entries.
                        task = entry.get("task")
                        round_val = entry.get("round")
                        key = (task, round_val)
                        if key not in unreviewed_ux:
                            unreviewed_ux[key] = []
                        unreviewed_ux[key].append(agent_type)
                    else:
                        unreviewed_non_ux.append(agent_type)
    except IOError:
        return None

    # Collect all unreviewed entries for warning output
    unreviewed = list(unreviewed_non_ux)
    for entries in unreviewed_ux.values():
        unreviewed.extend(entries)

    if not unreviewed:
        return None

    unique_types = list(dict.fromkeys(unreviewed))
    types_str = ", ".join(unique_types)

    return (
        "\n\n--- PRE-FLIGHT-GATE PENDING ---\n"
        f"Generative agent(s) completed without pre-flight-gate review: {types_str}\n"
        "Per orchestrator-prompt.md Pre-Flight Gates, invoke the pre-flight-gate agent\n"
        "before consuming these outputs in downstream agents.\n"
        "--- END PRE-FLIGHT-GATE ---\n"
    )


def main():
    exit_if_dispatched_child("critic-gate-check")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    # Determine event type -- SubagentStop has agent_id + agent_transcript_path
    # but no tool_name; PostToolUse:Agent has tool_name = "Agent".
    hook_event_name = determine_hook_event(event)
    is_subagent_stop = hook_event_name == "SubagentStop"

    # Allow PostToolUse:Agent events and SubagentStop events through.
    # For PostToolUse, enforce that it's specifically the Agent tool
    # (defensive -- matcher should handle this, but guard in case).
    # SubagentStop events have no tool_name field, so skip this check for them.
    if not is_subagent_stop and event.get("tool_name") != "Agent":
        return

    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return  # not orchestrator-depth (M2-primary, see hook-directive-audience-map.md)
    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        return  # not root-orchestrator-depth (REV-2)

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    # Find the project root; derive session-scoped path.
    # SubagentStop events may not carry cwd reliably, so use script-relative
    # path derivation for them (same pattern as critic-gate-tracker.py:93 and
    # cycle-hook.py:307). PostToolUse events always have cwd in the payload.
    if is_subagent_stop:
        if not session_id:
            return  # no session context — nothing to check
        # Script is at {project_root}/.claude/hooks/critic-gate-check.py
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)
    else:
        cwd = event.get("cwd", "")
        session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)

    critic_warning = check_critic_gate(session_dir, is_subagent_stop=is_subagent_stop)

    if critic_warning:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": hook_event_name,
                "additionalContext": critic_warning
            }
        }))


if __name__ == "__main__":
    main()
