#!/usr/bin/env python3
# dispatch-child-safe: false
"""PostToolUse hook (all tools): inject delegation reminder when orchestrator is doing
substantive work without delegating to subagents.

Fires after every tool call. Classifies the tool as substantive or housekeeping.
After 5+ substantive tool calls with zero Agent delegations, injects a soft warning
reminding the orchestrator to delegate to appropriate subagents.

Silent (no output, exit 0) in all non-warning paths, including when:
- CLAUDE_SESSION_ID is not set (subagent or non-session context)
- The tool call is not substantive
- The substantive count is below threshold
- Cooldown period has not elapsed since last warning
- Agent delegations have already been made (delegation-trace.jsonl has entries)
"""
import json
import os
import sys

from _dispatch_child_guard import exit_if_dispatched_child


# Tools that represent substantive work the orchestrator is doing directly.
# These are counted toward the no-delegation threshold.
SUBSTANTIVE_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Glob",
    "Grep",
    "mcp__context-tools__smart_read",
    "mcp__context-tools__smart_write",
    "mcp__context-tools__smart_bash",
    "mcp__context-tools__smart_grep",
    "mcp__context-tools__smart_glob",
    "mcp__context-tools__git_query",
    "mcp__context-tools__deps",
    "mcp__context-tools__build_run",
    "mcp__context-tools__test_run",
}

# Minimum substantive calls before any warning is considered.
WARNING_THRESHOLD = 5

# Minimum number of additional substantive calls since last warning before re-warning.
# This prevents spamming a warning on every subsequent call.
WARNING_COOLDOWN = 5


def get_session_dir():
    """Return the session-scoped directory for state files.

    Derives project root from script location (.claude/hooks/delegation-monitor.py),
    which is reliable even when event.cwd is unreliable (e.g. after Bash tool changes
    directory). Falls back to None if CLAUDE_SESSION_ID is not set.
    """
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return  # not orchestrator-depth
    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        return  # not root-orchestrator-depth (REV-2: suppress in L2 children)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return None
    # Script is at {project_root}/.claude/hooks/delegation-monitor.py
    # Two dirname() calls walk up: hooks/ -> .claude/ -> project_root/
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, ".agent_context", "sessions", session_id)


def load_state(state_file):
    """Load delegation-monitor.json state. Returns fresh state on any error."""
    fresh = {"substantive_count": 0, "last_warning_at": 0}
    if not os.path.exists(state_file):
        return fresh
    try:
        with open(state_file, "r") as f:
            data = json.load(f)
        # Validate structure -- corrupt or wrong-schema files get reset
        if not isinstance(data.get("substantive_count"), int):
            return fresh
        if not isinstance(data.get("last_warning_at"), int):
            return fresh
        return data
    except (json.JSONDecodeError, IOError, KeyError, TypeError):
        return fresh


def save_state(state_file, state):
    """Write delegation-monitor.json state. Silently ignores write errors."""
    try:
        with open(state_file, "w") as f:
            json.dump(state, f)
    except IOError:
        pass


def count_delegation_entries(session_dir):
    """Count lines in delegation-trace.jsonl. Returns 0 if file absent or empty."""
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")
    if not os.path.exists(trace_file):
        return 0
    try:
        count = 0
        with open(trace_file, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
    except IOError:
        return 0


def main():
    exit_if_dispatched_child("delegation-monitor")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    # Only act on substantive tool calls -- housekeeping tools are silently ignored.
    tool_name = event.get("tool_name", "")
    if tool_name not in SUBSTANTIVE_TOOLS:
        return

    # Session guard -- exit silently if not running under claude-session.
    # Subagent-context exclusion is enforced PRIMARILY by the M2 guard
    # (CLAUDE_HOOK_ORCHESTRATOR_DEPTH != "1") at the top of get_session_dir();
    # the CLAUDE_SESSION_ID-absence Layer-1 check immediately below it is
    # defense-in-depth. CLAUDE_SESSION_ID IS empirically inherited by Task-tool
    # subagent hook subprocesses, so Layer-1 alone is NOT a reliable
    # depth discriminator -- see knowledge/constraints/hook-directive-audience-map.md
    # section "CLAUDE_SESSION_ID inheritance".
    session_dir = get_session_dir()
    if session_dir is None:
        return

    # Ensure session dir exists before writing state.
    try:
        os.makedirs(session_dir, exist_ok=True)
    except OSError:
        return

    state_file = os.path.join(session_dir, "delegation-monitor.json")

    # Load, increment, and save state.
    state = load_state(state_file)
    state["substantive_count"] += 1
    save_state(state_file, state)

    substantive_count = state["substantive_count"]
    last_warning_at = state["last_warning_at"]

    # Check threshold -- don't warn until enough substantive calls have accumulated.
    if substantive_count < WARNING_THRESHOLD:
        return

    # Check cooldown -- don't re-warn until enough additional calls have elapsed.
    if substantive_count - last_warning_at < WARNING_COOLDOWN:
        return

    # Check delegation-trace.jsonl -- if any Agent calls were made, the orchestrator
    # is delegating and no warning is needed.
    delegation_count = count_delegation_entries(session_dir)
    if delegation_count > 0:
        return

    # Update last_warning_at before injecting the warning, so the count is
    # accurate in the message and we don't warn again immediately.
    state["last_warning_at"] = substantive_count
    save_state(state_file, state)

    # Inject soft delegation reminder via additionalContext (never deny).
    warning_text = (
        "--- DELEGATION REMINDER ---\n"
        f"You have made {substantive_count} substantive tool calls in this session "
        "without delegating to any subagent. Per delegation rules, research, code "
        "reading, analysis, design documents, and artifact creation should be delegated "
        "to appropriate subagents (researcher, implementer, architect, etc.). If the "
        "current work is non-trivial, present an orientation checkpoint and plan the "
        "work with proper delegation.\n"
        "--- END DELEGATION REMINDER ---"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": warning_text
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
