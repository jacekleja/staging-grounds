#!/usr/bin/env python3
# PIPELINE_RUNTIME_GATE_V1
"""PostToolUse telemetry sidecar for the alpha-pipeline MCP server.

Matcher: `mcp__alpha-pipeline__.*` (PostToolUse). Fires once per tool call
under that namespace. Appends one JSONL row per fire to
`{session_dir}/alpha-measurement-tracker-events.jsonl`.

This hook is the cross-check signal for the alpha-pipeline apparatus. The
MCP wrapper itself owns authoritative token/span/dispatch records; this
hook produces a redundant observation so the downstream consumer can
detect "wrapper recorded but hook missed" and "hook recorded but wrapper
missed" cases.

When the alpha pipeline is inactive in a worktree, `bin/pipeline_prune.py`
unlinks this file from `<worktree>/.claude/hooks/`. The harness invokes
the registered command, the file is absent, the hook no-ops (silent-skip
per session-cycling architecture invariant 6). No in-hook active-pipeline
check is performed.

Session-id fallback chain (per iss_348e76238869 — CLAUDE_SESSION_ID
inheritance into subagent-fired hook subprocesses is empirically
unreliable; defensive three-step resolution):
  (1) CLAUDE_SESSION_ID env var (happy path, silent on success)
  (2) Derive from cwd containing .agent_context/sessions/<id>/
      (one stderr diag line: "[alpha-measurement-tracker] derived ...")
  (3) Graceful no-op (one stderr diag line, return cleanly, no record)

Exits 0 on every path. PostToolUse hooks must never block the tool call.
"""

import json
import os
import sys
from datetime import datetime, timezone


EVENTS_FILE_NAME = "alpha-measurement-tracker-events.jsonl"
TOOL_NAME_PREFIX = "mcp__alpha-pipeline__"
SESSIONS_PATH_MARKER = "/.agent_context/sessions/"


# Session-id fallback chain (per iss_348e76238869 — CLAUDE_SESSION_ID
# inheritance into subagent-fired hook subprocesses is empirically
# unreliable; defensive three-step resolution):
#   (1) CLAUDE_SESSION_ID env var (happy path, silent on success)
#   (2) Derive from cwd containing .agent_context/sessions/<id>/
#       (one stderr diag line: "[alpha-measurement-tracker] derived ...")
#   (3) Graceful no-op (one stderr diag line, return cleanly, no record)
def resolve_session_id():
    """Three-step session-id resolution (see iss_348e76238869).

    Returns (session_id, source) where source is "env" or "worktree-derive",
    or (None, None) when both attempts fail.
    """
    env_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if env_id:
        return env_id, "env"

    cwd = os.getcwd()
    if SESSIONS_PATH_MARKER in cwd:
        tail = cwd.split(SESSIONS_PATH_MARKER, 1)[1]
        derived = tail.split("/", 1)[0]
        if derived:
            sys.stderr.write(
                "[alpha-measurement-tracker] derived session_id={} "
                "from worktree path\n".format(derived)
            )
            return derived, "worktree-derive"

    return None, None


def build_row(event, session_id, session_id_source):
    """Build the JSONL row from the PostToolUse event payload."""
    tool_response = event.get("tool_response")
    if isinstance(tool_response, dict):
        is_error = bool(tool_response.get("is_error"))
        success_flag = not is_error
    else:
        # Wrapper returned non-dict response (or absent): treat presence as
        # success, absence as failure. The MCP wrapper's own JSONLs remain
        # authoritative for success/failure semantics.
        success_flag = tool_response is not None and tool_response != ""

    tool_input = event.get("tool_input") or {}
    # Per alpha_dispatch.ts (input zod schema): the tool's input field is
    # `subagent_type` (the agent body basename). The wrapper maps it to
    # `agent_basename` only in its OUTPUT JSONL rows; the hook sees the INPUT
    # shape, so read `subagent_type` here. Output field name in this hook's
    # row remains `alpha_child_agent` (cross-check schema, sketch D-5).
    alpha_child_agent = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None

    duration = None
    if isinstance(tool_response, dict):
        for key in ("duration_ms", "duration"):
            value = tool_response.get(key)
            if isinstance(value, (int, float)):
                duration = int(value)
                break

    hook_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    return {
        "tool_name": event.get("tool_name", ""),
        "hook_ts": hook_ts,
        "session_id": session_id,
        "session_id_source": session_id_source,
        "success_flag": success_flag,
        "tool_use_id": event.get("tool_use_id", ""),
        "observed_tool_call_duration_ms": duration,
        "alpha_child_agent": alpha_child_agent,
        "hook_event": "PostToolUse",
    }


def main():
    try:
        raw = sys.stdin.read()
    except (IOError, OSError):
        sys.exit(0)

    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write(
            "[alpha-measurement-tracker] malformed stdin JSON; skipping\n"
        )
        sys.exit(0)

    if not isinstance(event, dict):
        sys.stderr.write(
            "[alpha-measurement-tracker] stdin JSON not an object; skipping\n"
        )
        sys.exit(0)

    # Defensive matcher check (belt-and-suspenders against matcher mis-parse
    # or wildcard registration).
    tool_name = event.get("tool_name", "")
    if not tool_name.startswith(TOOL_NAME_PREFIX):
        sys.exit(0)

    session_id, session_id_source = resolve_session_id()
    if not session_id:
        sys.stderr.write(
            "[alpha-measurement-tracker] no session_id resolvable "
            "(env missing, cwd not under .agent_context/sessions/); skipping\n"
        )
        sys.exit(0)

    cwd = os.getcwd()
    session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    events_path = os.path.join(session_dir, EVENTS_FILE_NAME)

    try:
        row = build_row(event, session_id, session_id_source)
    except Exception:
        # Never crash Claude Code on row-construction error.
        sys.exit(0)

    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(events_path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except (IOError, OSError):
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
