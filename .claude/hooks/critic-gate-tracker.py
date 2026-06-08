#!/usr/bin/env python3
# dispatch-child-safe: false
"""PreToolUse + SubagentStop hook: track generative agent calls for the pre-flight-gate.

State substrate: `{session_dir}/critic-gate.jsonl` — append-only JSONL written here
and read by critic-gate-check.py. Other consumers (dashboard/server/collectors/jsonl.js,
the two test_critic_gate_*.py suites, settings.json hook registrations) read the same
filename and schema keys; do not rename in isolation.

PreToolUse: fires before each Agent tool launch. If the subagent is a generative
type (researcher, synthesizer, architect, planner, solution-designer, ux-designer),
appends a JSONL entry to critic-gate.jsonl. If the subagent is the pre-flight-gate,
appends a clear entry (action="critic_clear"). Foreground agents are written with
"background": false; background agents with "background": true (the warning is
deferred and fires at SubagentStop, not at launch time).

SubagentStop: fires when a background agent completes. Reads delegation-trace.jsonl
to determine if the completed agent was a background generative type. If so, appends
a "generative" entry with "background_completion": true so the check hook sees it.

Append-only JSONL — no read-modify-write, no race condition.

Optional subround_M field: added to ux-designer generative entries when the launch
is inside a designer-loop iteration. Used by critic-gate-check.py's per-(task, R)
clear semantic to prevent stale-entry warnings on M consecutive ux-designer launches.

Silent (no output, exit 0) always — tracker, never blocks.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child


GENERATIVE_TYPES = {"researcher", "synthesizer", "architect", "planner", "solution-designer", "ux-designer"}


# KEEP IN SYNC with .claude/hooks/cycle-hook.py (_sentinel_session_dir)
def _sentinel_session_dir(session_id):
    """Canonical sentinel-directory path — MUST match cycle-hook.py derivation.

    Derive project root from __file__ (not event.cwd, which is unreliable —
    may reflect Bash-tool cwd after cd commands). Both critic-gate-tracker.py
    and cycle-hook.py live in .claude/hooks/, so the three-parents-up walk
    yields the same project root from either script.
    """
    # .claude/hooks/critic-gate-tracker.py → project root = 3 parents up
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(cwd, ".agent_context", "sessions", session_id, "subagent-active")


def _sentinel_write(session_id, tool_use_id):
    """Create a sentinel file marking that a subagent is active.

    Called from PreToolUse/Agent path. Failure is silent (hook must not
    break Claude Code). File content is empty — existence + mtime are
    the signal read by cycle-hook.py's any_fresh_subagent_active().
    """
    try:
        sentinel_dir = _sentinel_session_dir(session_id)
        os.makedirs(sentinel_dir, exist_ok=True)
        # Prefer tool_use_id as filename key for debuggability and uniqueness.
        # Fall back to time_ns + pid if absent (agent-latency-tracker.py uses
        # a similar synthesis pattern).
        key = tool_use_id if tool_use_id else f"{time.time_ns()}-{os.getpid()}"
        sentinel_path = os.path.join(sentinel_dir, f"{key}.sentinel")
        open(sentinel_path, "w").close()  # empty file; mtime = now
    except (OSError, IOError):
        pass  # hook must never crash Claude Code


def determine_hook_event(event):
    """Return the correct hookEventName for the output wrapper.

    Copied from cycle-hook.py -- using the wrong event name causes output to
    be silently discarded by Claude Code.
    """
    hook_event = event.get("hook_event_name", "")
    if hook_event:
        return hook_event
    # Fallback: detect from payload structure
    if "agent_id" in event and "agent_transcript_path" in event:
        return "SubagentStop"
    return "PostToolUse"


def count_background_generative_launches(trace_file):
    """Return count of background generative agent launches from delegation-trace.jsonl."""
    count = 0
    try:
        with open(trace_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("background") and entry.get("agent_type") in GENERATIVE_TYPES:
                    count += 1
    except (IOError, OSError):
        pass
    return count


def count_background_completions(gate_file):
    """Return count of background_completion entries already in critic-gate.jsonl."""
    count = 0
    try:
        with open(gate_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("background_completion"):
                    count += 1
    except (IOError, OSError):
        pass
    return count


def _extract_subround_m(event):
    """Extract subround_M from the delegation prompt in the event, if present.
    Returns the integer value or None if absent/unparseable.
    """
    try:
        tool_input = event.get("tool_input", {})
        prompt = tool_input.get("prompt", "") or ""
        # Look for `"subround_M": N` pattern in the delegation prompt text (JSON-style)
        match = re.search(r'"subround_M"\s*:\s*(\d+)', prompt)
        if match:
            return int(match.group(1))
        # Also try YAML-style `subround_M: N`
        match = re.search(r'\bsubround_M\s*:\s*(\d+)', prompt)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def _is_c1_synthesizer_dispatch(event):
    """Return True when this is a §B.6 C1 single-impl-report synthesizer dispatch.

    C1 dispatches write to {session_dir}/digests/ and append to pending-digest.jsonl.
    Their only in-session consumer is the cycling drainer (not a generative agent), so
    writing a generative gate entry would produce a structurally false-positive nag.

    Two signals are checked (either suffices):
      (a) delegation prompt's output_contract.artifact_path or target_artifact_path
          contains '/digests/' (slash-bounded, case-sensitive)
      (b) delegation-prompt text contains the substring 'pending-digest.jsonl'

    If the event is not for a synthesizer, returns False immediately.
    """
    try:
        tool_input = event.get("tool_input", {})
        if tool_input.get("subagent_type") != "synthesizer":
            return False
        prompt = tool_input.get("prompt", "") or ""
        # Signal (b): prompt text names pending-digest.jsonl directly
        if "pending-digest.jsonl" in prompt:
            return True
        # Signal (a): any artifact_path / target_artifact_path value contains /digests/
        if "/digests/" in prompt:
            return True
    except Exception:
        pass
    return False


def _extract_task_and_round(event):
    """Extract task_id and round from the delegation prompt in the event.
    Returns (task_str, round_str) or (None, None) if absent/unparseable.
    round_str is formatted as "R<N>" (e.g., "R1").
    """
    try:
        tool_input = event.get("tool_input", {})
        prompt = tool_input.get("prompt", "") or ""
        # Extract task_id (JSON-style: "task_id": "VALUE")
        task_match = re.search(r'"task_id"\s*:\s*"([^"]+)"', prompt)
        # Also try YAML-style: `task_id: VALUE`
        if not task_match:
            task_match = re.search(r'\btask_id\s*:\s*"([^"]+)"', prompt)
        if not task_match:
            task_match = re.search(r'\btask_id\s*:\s*(\S+)', prompt)
        # Extract round (JSON-style: "round": N)
        round_match = re.search(r'"round"\s*:\s*(\d+)', prompt)
        if not round_match:
            round_match = re.search(r'\bround\s*:\s*(\d+)', prompt)
        if task_match and round_match:
            task_str = task_match.group(1).strip(',').strip()
            round_str = f"R{round_match.group(1)}"
            return task_str, round_str
    except Exception:
        pass
    return None, None


def handle_subagent_stop(event, session_id):
    """Handle SubagentStop: write a generative completion entry if a background
    generative agent just finished and hasn't been accounted for yet.

    Uses delegation-trace.jsonl as the source of truth for agent_type.
    Derives project root from script location (same pattern as cycle-hook.py)
    to avoid unreliable event.cwd values.
    """
    if not session_id:
        return  # no session context — nothing to record
    # Script is at {project_root}/.claude/hooks/critic-gate-tracker.py
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")
    gate_file = os.path.join(session_dir, "critic-gate.jsonl")

    # Count background generative launches vs completions already recorded
    launches = count_background_generative_launches(trace_file)
    completions = count_background_completions(gate_file)

    if launches <= completions:
        # All background generative launches already have completion entries -- no-op
        return

    # There is at least one unaccounted-for background generative completion.
    # Determine the agent_type from the delegation trace (first unmatched background
    # generative entry). We use a simple count-based index: the nth SubagentStop
    # corresponds to the nth background generative launch in trace order.
    agent_type = None
    bg_index = completions  # 0-indexed: first unmatched entry
    bg_seen = 0
    try:
        with open(trace_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("background") and entry.get("agent_type") in GENERATIVE_TYPES:
                    if bg_seen == bg_index:
                        agent_type = entry.get("agent_type")
                        break
                    bg_seen += 1
    except (IOError, OSError):
        pass

    if not agent_type:
        return

    # Ensure session dir exists
    os.makedirs(session_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat()
    entry = {
        "action": "generative",
        "type": agent_type,
        "background_completion": True,
        "ts": ts,
    }

    try:
        with open(gate_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except IOError:
        pass


def main():
    exit_if_dispatched_child("critic-gate-tracker")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    hook_event = determine_hook_event(event)

    # SubagentStop path: handle background generative completions
    if hook_event == "SubagentStop":
        handle_subagent_stop(event, session_id)
        return

    # PreToolUse path: only act on Agent tool calls
    # (defensive -- matcher should handle this, but SubagentStop has no tool_name)
    if event.get("tool_name") != "Agent":
        return

    tool_input = event.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type")
    is_background = bool(tool_input.get("run_in_background", False))

    # Diagnostic: log when subagent_type is missing (helps distinguish
    # "untyped by design" from "field name changed")
    if subagent_type is None:
        print(
            "critic-gate-tracker: Agent tool_input has no subagent_type field",
            file=sys.stderr
        )
        return

    # SI-4: Sentinel marks this subagent window for cycle-hook suppression.
    # Fires for ALL subagent types (not just GENERATIVE_TYPES) so cycle-hook
    # suppresses warnings during implementer/validator/coherence-auditor/etc. windows too.
    # Must be placed BEFORE the GENERATIVE_TYPES filter below.
    _sentinel_write(session_id, event.get("tool_use_id", ""))

    # Only track generative types and pre-flight-gate clears
    if subagent_type not in GENERATIVE_TYPES and subagent_type != "pre-flight-gate":
        return

    # Gate file write requires a valid session_id to avoid writing to a garbage path
    if not session_id:
        return

    cwd = event.get("cwd", "")
    session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    gate_file = os.path.join(session_dir, "critic-gate.jsonl")

    # Ensure session dir exists
    os.makedirs(session_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat()

    if subagent_type in GENERATIVE_TYPES:
        # C1 synthesizer dispatches have no in-session generative consumer; skip the entry
        # so the check hook never nags on those paths. Sentinel write above still fired.
        if _is_c1_synthesizer_dispatch(event):
            return
        entry = {
            "action": "generative",
            "type": subagent_type,
            "background": is_background,
            "description": tool_input.get("description", ""),
            "ts": ts,
        }
        # For ux-designer entries inside a designer-loop: add subround_M if present.
        # This field is optional — absence means a standard (non-loop) ux-designer launch.
        # The per-(task, round) clear semantic in critic-gate-check.py handles both cases.
        if subagent_type == "ux-designer":
            task, round_str = _extract_task_and_round(event)
            if task is not None:
                entry["task"] = task
                entry["round"] = round_str
            subround_m = _extract_subround_m(event)
            if subround_m is not None:
                entry["subround_M"] = subround_m
    elif subagent_type == "pre-flight-gate":
        # action="critic_clear" is the on-disk schema key consumed by critic-gate-check.py
        # and the test_critic_gate_*.py suites; rename requires coordinated edits across them.
        entry = {
            "action": "critic_clear",
            "ts": ts,
        }
        # Add task+round to enable per-(task, round) clear semantic for ux-designer entries.
        task, round_str = _extract_task_and_round(event)
        if task is not None:
            entry["task"] = task
            entry["round"] = round_str

    # Append-only write -- no read required, no race condition
    try:
        with open(gate_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except IOError:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
