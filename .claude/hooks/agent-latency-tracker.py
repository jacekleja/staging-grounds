#!/usr/bin/env python3
# dispatch-child-safe: false
"""Hook for tracking wall-clock duration of every Agent (subagent) invocation.

Registered as both PreToolUse/Agent and PostToolUse/Agent.

PreToolUse: Records start time and metadata into a pending state dict.
PostToolUse: Computes elapsed time, writes a JSONL latency event, clears pending.

State file: {session_dir}/agent-latency-pending.json  (dict keyed by tool_use_id)
Output file: {session_dir}/agent-latency-events.jsonl  (append-only JSONL)

Correlation key: tool_use_id (primary). Fallback: oldest unmatched entry of same
subagent_type (handles the case where tool_use_id is absent from PreToolUse payload).

Background-spawn handling: when the Agent tool input has `run_in_background: true`,
PostToolUse/Agent fires at SPAWN-RETURN time (when the platform returns the agentId),
NOT at agent-completion time. PostToolUse still short-circuits for background spawns
to avoid emitting a meaningless spawn-dispatch latency. PreToolUse DOES record a
pending entry (with background=True flag) so that handle_subagent_stop can compute
real end-to-end latency when the background agent finishes.

SubagentStop handling: registered in settings.json SubagentStop array. Uses
count-based index correlation against delegation-trace.jsonl to identify the
completing background agent, matches its pending entry, and emits an agent_complete
event with background=True. Counter file: {session_dir}/latency-bg-stop-count
(separate from cycle-hook.py's sentinel-bg-stop-count to avoid mutual interference).

Silent on all errors -- exit 0 always, never crashes Claude Code.

Silent on all errors -- exit 0 always, never crashes Claude Code.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child


PENDING_FILE_NAME = "agent-latency-pending.json"
EVENTS_FILE_NAME = "agent-latency-events.jsonl"
# Separate from cycle-hook.py's sentinel-bg-stop-count to avoid mutual interference.
BG_STOP_COUNTER_FILE = "latency-bg-stop-count"


def get_project_root():
    """Return the project root used for latency state files."""
    return os.environ.get(
        "CAA_LATENCY_PROJECT_ROOT"
    ) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_session_dir(event):
    """Return the session-scoped directory for state files.

    Uses CLAUDE_SESSION_ID from environment. Falls back to .agent_context/audit/
    if session ID is not available. event is intentionally not trusted for path
    resolution; tests may override the root with CAA_LATENCY_PROJECT_ROOT.
    """
    project_root = get_project_root()
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        return os.path.join(project_root, ".agent_context", "sessions", session_id)
    return os.path.join(project_root, ".agent_context", "audit")


def load_pending(pending_path):
    """Load pending state dict from JSON file. Returns empty dict on any error."""
    try:
        with open(pending_path, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def save_pending(pending_path, pending):
    """Atomically write pending state dict to JSON file."""
    try:
        tmp = pending_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pending, f)
        os.replace(tmp, pending_path)
    except (IOError, OSError):
        pass


def parse_usage_block(tool_response):
    """Parse a <usage>...</usage> block from an agent tool_response string.

    Extracts total_tokens and tool_uses as integers.

    Returns a dict like {"total_tokens": 59519, "tool_uses": 30} on success.
    Returns {} (empty dict) on any failure: missing block, malformed content,
    non-string input, or partially missing fields.
    """
    try:
        if not isinstance(tool_response, str):
            return {}
        match = re.search(r"<usage>(.*?)</usage>", tool_response, re.DOTALL)
        if not match:
            return {}
        block = match.group(1)
        result = {}
        tokens_match = re.search(r"total_tokens:\s*(\d+)", block)
        if tokens_match:
            result["total_tokens"] = int(tokens_match.group(1))
        tool_uses_match = re.search(r"tool_uses:\s*(\d+)", block)
        if tool_uses_match:
            result["tool_uses"] = int(tool_uses_match.group(1))
        # Only return result if we found at least one field
        return result
    except Exception:
        return {}


def emit_agent_completion_event(
    session_dir, *, tool_use_id, subagent_type, description,
    start_time, end_time, background=True,
):
    """Write one background Agent completion row after correlation is resolved."""
    duration_ms = max(0, int((end_time - start_time) * 1000))
    # Guard: a corrupted or epoch-valued start_time (e.g. start_time=1.0) produces
    # an absurdly large duration (~56 years from epoch). Reset to 0 ms.
    if duration_ms >= 10_000_000_000:
        duration_ms = 0

    event_record = {
        "tool": "Agent",
        "event": "agent_complete",
        "subagent_type": subagent_type or "unknown",
        "description": description,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "tool_use_id": tool_use_id,
        "background": background,
    }

    events_path = os.path.join(session_dir, EVENTS_FILE_NAME)
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(events_path, "a") as f:
            f.write(json.dumps(event_record) + "\n")
    except (IOError, OSError):
        pass


def handle_pre_tool_use(event, session_dir):
    """Record start time for an in-flight Agent invocation.

    For background spawns (run_in_background=true), records a pending entry with
    background=True so handle_subagent_stop can compute end-to-end latency.
    PostToolUse still short-circuits for background spawns (spawn-return latency
    is meaningless) — handle_subagent_stop owns the completion path.
    """
    tool_input = event.get("tool_input", {})
    run_in_background = bool(tool_input.get("run_in_background"))
    subagent_type = tool_input.get("subagent_type", "unknown")
    description = str(tool_input.get("description", ""))[:100]

    # Primary key: tool_use_id. Fallback: synthetic key from type + timestamp.
    tool_use_id = event.get("tool_use_id", "")
    start_time = time.time()
    if tool_use_id:
        key = tool_use_id
    else:
        # Synthetic key: subagent_type + start timestamp (nanosecond precision avoids collision)
        key = f"{subagent_type}_{start_time}"

    os.makedirs(session_dir, exist_ok=True)
    pending_path = os.path.join(session_dir, PENDING_FILE_NAME)
    pending = load_pending(pending_path)

    entry = {
        "start_time": start_time,
        "subagent_type": subagent_type,
        "description": description,
        "tool_use_id": tool_use_id,  # may be "" for synthetic keys
    }
    if run_in_background:
        entry["background"] = True

    pending[key] = entry

    save_pending(pending_path, pending)


def handle_post_tool_use(event, session_dir):
    """Compute elapsed time and write a latency event record.

    Short-circuits for background spawns: see module docstring. Skipping at
    PostToolUse (in addition to PreToolUse) prevents the subagent_type fallback
    match from misattributing a background spawn-return to an unrelated
    foreground entry pending in the dict.
    """
    tool_input = event.get("tool_input", {})
    if tool_input.get("run_in_background"):
        return

    end_time = time.time()

    subagent_type = tool_input.get("subagent_type", "unknown")
    description = str(tool_input.get("description", ""))[:100]
    tool_use_id = event.get("tool_use_id", "")

    pending_path = os.path.join(session_dir, PENDING_FILE_NAME)
    pending = load_pending(pending_path)

    matched_key = None
    matched_entry = None

    # Primary match: exact tool_use_id
    if tool_use_id and tool_use_id in pending:
        matched_key = tool_use_id
        matched_entry = pending[tool_use_id]
    else:
        # Fallback: oldest unmatched entry of the same subagent_type.
        # "Oldest" = smallest start_time value.
        candidates = [
            (k, v) for k, v in pending.items()
            if v.get("subagent_type") == subagent_type
        ]
        if candidates:
            matched_key, matched_entry = min(candidates, key=lambda kv: kv[1].get("start_time", 0))

    if matched_entry is None:
        # No matching pending entry -- cannot compute latency. Still exit 0.
        return

    start_time = matched_entry.get("start_time", end_time)
    duration_ms = int((end_time - start_time) * 1000)

    # Use stored description if PostToolUse lacks one
    recorded_description = description or matched_entry.get("description", "")
    recorded_tool_use_id = tool_use_id or matched_entry.get("tool_use_id", "")

    event_record = {
        "tool": "Agent",
        "event": "agent_complete",
        "subagent_type": subagent_type,
        "description": recorded_description,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "tool_use_id": recorded_tool_use_id,
    }

    # Parse usage block from tool_response and add token fields if found
    usage = parse_usage_block(event.get("tool_response", ""))
    if "total_tokens" in usage:
        event_record["total_tokens"] = usage["total_tokens"]
    if "tool_uses" in usage:
        event_record["tool_uses"] = usage["tool_uses"]

    # Append to events JSONL file
    events_path = os.path.join(session_dir, EVENTS_FILE_NAME)
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(events_path, "a") as f:
            f.write(json.dumps(event_record) + "\n")
    except (IOError, OSError):
        pass

    # Remove matched entry from pending state
    del pending[matched_key]
    save_pending(pending_path, pending)


def handle_subagent_stop(event, session_dir):
    """Emit an agent_complete event for a background agent that just finished.

    Uses count-based index correlation against delegation-trace.jsonl to find
    the nth background trace entry (0-indexed by the counter file), then looks
    up the matching pending entry written at PreToolUse for start_time.

    Guard: the counter advance and event emit are gated on finding a genuine
    unmatched background pending entry.  SubagentStop fires for ALL native
    agents (foreground + background).  A foreground stop whose pending entry
    was already consumed by PostToolUse presents no pending entry here and
    must not corrupt the counter or emit a spurious completion.

    Counter file (latency-bg-stop-count) is separate from cycle-hook.py's
    sentinel-bg-stop-count to avoid mutual interference.

    Falls through silently on any correlation failure; never errors.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return

    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")
    counter_file = os.path.join(session_dir, BG_STOP_COUNTER_FILE)

    # Read how many SubagentStop completions for background agents this hook has
    # already processed.
    stop_count = 0
    try:
        with open(counter_file) as f:
            stop_count = int(f.read().strip())
    except (FileNotFoundError, ValueError, IOError):
        pass  # First fire or corrupt — start from 0

    # Walk delegation-trace.jsonl to find the nth background entry (0-indexed).
    tool_use_id = None
    subagent_type = None
    description = None
    bg_index = 0
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
                if not entry.get("background"):
                    continue
                if bg_index == stop_count:
                    tool_use_id = entry.get("tool_use_id", "") or ""
                    subagent_type = entry.get("agent_type", "unknown")
                    description = str(entry.get("description", ""))[:100]
                    break
                bg_index += 1
    except (FileNotFoundError, IOError):
        return  # No trace file — nothing to correlate

    if tool_use_id is None:
        # No correlated trace entry at this stop_count index — stop_count has
        # outrun the number of background launches. Do NOT advance counter.
        return

    end_time = time.time()

    # Look up pending entry written at PreToolUse for start_time.
    pending_path = os.path.join(session_dir, PENDING_FILE_NAME)
    pending = load_pending(pending_path)

    matched_key = None
    matched_entry = None
    if tool_use_id and tool_use_id in pending:
        matched_key = tool_use_id
        matched_entry = pending[tool_use_id]
    else:
        # Fallback: oldest background pending entry of same subagent_type.
        candidates = [
            (k, v) for k, v in pending.items()
            if v.get("background") and v.get("subagent_type") == subagent_type
        ]
        if candidates:
            matched_key, matched_entry = min(candidates, key=lambda kv: kv[1].get("start_time", 0))

    # Guard: do not emit and do not advance the counter if no genuine unmatched
    # background pending entry exists.  A foreground SubagentStop whose pending
    # entry was already consumed by PostToolUse leaves nothing here, so this
    # return prevents both a spurious completion event and counter corruption.
    if matched_entry is None:
        return

    start_time = matched_entry.get("start_time", end_time)
    # Use the trace description if the pending entry's description is empty.
    recorded_description = matched_entry.get("description") or description or ""
    recorded_tool_use_id = tool_use_id or matched_entry.get("tool_use_id", "")

    # Remove the matched pending entry so later stops cannot re-claim it.
    del pending[matched_key]
    save_pending(pending_path, pending)

    # Advance counter only after confirming a pending entry was consumed;
    # stops that find no pending entry must not corrupt the correlation index.
    try:
        with open(counter_file, "w") as f:
            f.write(str(stop_count + 1))
    except IOError:
        pass  # Non-fatal — continue to emit event

    emit_agent_completion_event(
        session_dir,
        tool_use_id=recorded_tool_use_id,
        subagent_type=subagent_type,
        description=recorded_description,
        start_time=start_time,
        end_time=end_time,
        background=True,
    )


def main():
    exit_if_dispatched_child("agent-latency-tracker")
    try:
        raw = sys.stdin.read()
    except (IOError, OSError):
        sys.exit(0)

    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    try:
        hook_event = event.get("hook_event_name", "")
        session_dir = get_session_dir(event)

        if hook_event == "SubagentStop":
            handle_subagent_stop(event, session_dir)
            sys.exit(0)

        # Only act on Agent tool calls (defensive -- matcher should handle this)
        if event.get("tool_name") != "Agent":
            sys.exit(0)

        if hook_event == "PreToolUse":
            handle_pre_tool_use(event, session_dir)
        elif hook_event == "PostToolUse":
            handle_post_tool_use(event, session_dir)
        else:
            # Fallback: detect from payload structure
            # PostToolUse has tool_response; PreToolUse does not
            if "tool_response" in event:
                handle_post_tool_use(event, session_dir)
            else:
                handle_pre_tool_use(event, session_dir)

    except Exception:
        # Never crash Claude Code
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
