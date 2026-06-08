#!/usr/bin/env python3
"""PostToolUse hook: MCP schema-error retry warning.

Fires on every PostToolUse event. Emits a soft warning via additionalContext
when ALL of the following hold:
  1. tool_name starts with "mcp__"
  2. tool_response indicates a schema/validation error (is_schema_error())
  3. {session_dir}/cycle-pending sentinel exists (agent is near cycle threshold)
  4. Per-episode warning count < WARNING_CAP (3)

Output contract: JSON via hookSpecificOutput.additionalContext (matches
build-pass-gate.py pattern). Exit code 0 always. Never blocks.

Counter file {session_dir}/mcp-retry-warn-count is incremented through the cap
(fires 1-3 emit; fires 4+ write counter and return silently).
"""
import json
import os
import sys

WARNING_CAP = 3

# Spec-origin note: the spec's canonical example mentioned "InputValidationError"
# as a prefix. That string is NOT produced by this repo's MCP server
# (.claude/mcp/context-tools). Actual errors come from the MCP SDK wrapping
# Zod failures as "Invalid arguments for tool X: [...]". See finalized pattern
# list: .agent_context/M2-MCP-RETRY-WARN-R1-S0.5-patterns.md


def _extract_text(tool_response):
    """Extract the text content string from a tool_response dict.

    Handles list-of-dicts shape [{"type": "text", "text": "..."}] and
    bare-string shape. Returns "" if field is missing or unrecognized.
    """
    if not tool_response:
        return ""
    content = tool_response.get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    if isinstance(content, str):
        return content
    return ""


def is_schema_error(tool_response):
    """Return True if tool_response indicates a schema/validation error.

    Detection: P-0 (is_error field) OR any of P-1..P-6 matching text content.
    P-3 requires both "Expected" AND "received" in the same content string.
    Finalized pattern list: .agent_context/M2-MCP-RETRY-WARN-R1-S0.5-patterns.md
    """
    if not isinstance(tool_response, dict):
        return False

    # P-0: primary gate — catches all Zod + MCP protocol-level errors
    # (includes timeout/cancel; accepted per finalized pattern list §P-0 decision)
    if tool_response.get("is_error") is True:
        return True

    text = _extract_text(tool_response).lower()
    if not text:
        return False

    # P-1: MCP SDK universal schema-error prefix
    if "invalid arguments for tool" in text:
        return True

    # P-2: Zod missing-required-field message ("message":"Required" in JSON)
    if '"required"' in text:
        return True

    # P-3: Zod wrong-type error (both parts required in same content)
    if "expected" in text and "received" in text:
        return True

    # P-4: Zod enum validation error
    if "invalid enum value" in text:
        return True

    # P-5: Zod strict-mode unrecognized parameter
    if "unrecognized key" in text:
        return True

    # P-6: Application-level missing param (is_error=false path)
    if "missing required parameter" in text:
        return True

    return False


def main():
    # Step 1: parse stdin JSON
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    # Step 2: tool-name guard
    tool_name = event.get("tool_name", "")
    if not tool_name.startswith("mcp__"):
        return

    # Step 3: read session id
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    # Step 4: derive project root from __file__ (three dirname calls)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)

    # Step 5: session-dir existence check
    if not os.path.isdir(session_dir):
        return

    # Step 6: cycle-pending gate (fail-open on OSError).
    # Also fires when cycle-pending-curator-contested-blocked is present — the
    # session is in a blocked state in both cases, so MCP retry warnings remain
    # relevant. The contested-blocked sentinel is treated as a superset of
    # cycle-pending for this gate's purposes.
    # Citation: .claude/knowledge/reference/sentinels.md § cycle-pending-curator-contested-blocked
    try:
        cycle_pending = os.path.join(session_dir, "cycle-pending")
        contested_blocked = os.path.join(
            session_dir, "cycle-pending-curator-contested-blocked"
        )
        if not os.path.exists(cycle_pending) and not os.path.exists(contested_blocked):
            return
    except OSError:
        return

    # Step 7: schema-error detection
    tool_response = event.get("tool_response", {})
    if not is_schema_error(tool_response):
        return

    # Step 8: counter cap — increment through the cap, write before emitting
    count_file = os.path.join(session_dir, "mcp-retry-warn-count")
    try:
        with open(count_file, "r") as f:
            n = int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        n = 0

    n_new = n + 1
    # Write atomically via temp file + rename to avoid partial reads on concurrent fires
    tmp_file = count_file + ".tmp"
    with open(tmp_file, "w") as f:
        f.write(str(n_new))
    os.replace(tmp_file, count_file)

    if n_new > WARNING_CAP:
        return

    # Step 9: emit warning JSON
    warning_text = (
        f"--- MCP RETRY WARNING ---\n"
        f"MCP tool `{tool_name}` returned a schema/validation error. "
        f"You are near the cycle threshold, but schema errors are typically "
        f"1-3 line parameter fixes — consider retrying with corrected parameters "
        f"before checkpointing. If you have already retried and the error persists, "
        f"proceed with the cycle.\n"
        f"--- END MCP RETRY WARNING ---"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": warning_text,
        }
    }))


if __name__ == "__main__":
    # Resolve session_dir for logging before calling main(), best-effort
    _session_dir_for_log = None
    try:
        _sid = os.environ.get("CLAUDE_SESSION_ID", "")
        if _sid:
            _pr = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _session_dir_for_log = os.path.join(_pr, ".agent_context", "sessions", _sid)
    except Exception:
        pass

    try:
        main()
    except Exception:
        if _session_dir_for_log and os.path.isdir(_session_dir_for_log):
            try:
                import traceback
                log_path = os.path.join(_session_dir_for_log, "mcp-retry-warn.log")
                with open(log_path, "a") as lf:
                    lf.write(traceback.format_exc())
            except Exception:
                pass
    sys.exit(0)
