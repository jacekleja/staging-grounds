#!/usr/bin/env python3
# dispatch-child-safe: false
"""
retry-burst-counter.py - PostToolUse hook on dispatch tools.

Instruments same-type dispatch bursts: records, per same-type dispatch burst,
whether the prior dispatch of that type returned a reject/gaps verdict
(read from the post-stop-verify sidecar).

RECORDS ONLY — does not gate, does not sys.exit(2).

Output file: {session_dir}/retry-burst-counter.jsonl (append-only JSONL).

Each line (one JSON object per dispatch observed):
  {
    "ts":                "<ISO-8601 UTC>",
    "subagent_type":     "<type>",
    "tool_use_id":       "<current dispatch id>",
    "prior_tool_use_id": "<most recent prior sidecar id or null>",
    "prior_verdict":     "<verdict from most recent prior sidecar or null>",
    "is_retry_burst":    <true|false>
  }

is_retry_burst is true when prior_verdict is in PROBLEM_VERDICTS
(fail, warn, gaps, request-changes, block, fail-closed).

Fails open: all exceptions swallowed; sys.exit(0) unconditionally.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child

COUNTER_FILENAME = "retry-burst-counter.jsonl"
VERIFY_SIDECAR_PREFIX = "post-stop-verify-"
VERIFY_SIDECAR_SUFFIX = ".verify.json"

# Verdicts indicating the prior dispatch was not accepted (reject or gaps).
PROBLEM_VERDICTS: frozenset[str] = frozenset({
    "fail",
    "warn",
    "gaps",
    "request-changes",
    "block",
    "fail-closed",
})

# Full dispatch tool name set — MCP-prefixed + native forms.
# IMPORTANT: must include the FULL MCP-prefixed form to avoid the dispatch-tool-
# recognition bug that hit S2 and S12a (bare names don't match MCP runtime names).
DISPATCH_TOOL_NAMES: frozenset[str] = frozenset({
    "mcp__context-tools__dispatch_agent",   # MCP-prefixed form used at runtime
    "Agent",                                 # Claude Code native tool
    "Task",                                  # Codex / alternate native tool
    "dispatch_agent",                        # bare legacy form
})


# ---------------------------------------------------------------------------
# Session resolution (same pattern as dispatch-status-rate-gate.py)
# ---------------------------------------------------------------------------

def _resolve_session_dir() -> str | None:
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return None
    session_dir = os.path.join(os.getcwd(), ".agent_context", "sessions", session_id)
    if not os.path.isdir(session_dir):
        return None
    return session_dir


# ---------------------------------------------------------------------------
# Sidecar discovery
# ---------------------------------------------------------------------------

def _load_sidecar(path: str) -> dict | None:
    """Return parsed JSON dict from path, or None on any error."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _find_prior_sidecar(session_dir: str, subagent_type: str,
                         current_tool_use_id: str) -> dict | None:
    """Return the most recent post-stop-verify sidecar matching subagent_type.

    Scans all post-stop-verify-*.verify.json files in session_dir, filters by
    subagent_type, and returns the one with the lexicographically greatest 'ts'
    field.  ISO-8601 UTC timestamps sort correctly as strings.
    Excludes any sidecar whose tool_use_id equals current_tool_use_id so the
    current dispatch's own sidecar is never read as the "prior" burst.
    Returns None when no matching sidecar exists.
    """
    try:
        entries = os.listdir(session_dir)
    except OSError:
        return None

    best: dict | None = None
    best_ts: str = ""

    for name in entries:
        if not (name.startswith(VERIFY_SIDECAR_PREFIX)
                and name.endswith(VERIFY_SIDECAR_SUFFIX)):
            continue
        sidecar = _load_sidecar(os.path.join(session_dir, name))
        if sidecar is None:
            continue
        if sidecar.get("subagent_type") != subagent_type:
            continue
        if sidecar.get("tool_use_id") == current_tool_use_id:
            continue  # skip the current dispatch's own sidecar
        ts = str(sidecar.get("ts", ""))
        if ts > best_ts:
            best_ts = ts
            best = sidecar

    return best


# ---------------------------------------------------------------------------
# JSONL append (atomic write; same tmp+os.replace idiom as dispatch-status-rate-gate.py)
# ---------------------------------------------------------------------------

def _append_record(session_dir: str, record: dict) -> None:
    """Append one JSON record line to retry-burst-counter.jsonl.

    Read-then-replace keeps each line intact.  Low dispatch frequency means
    concurrent-write races are negligible for this instrumentation hook.
    """
    path = os.path.join(session_dir, COUNTER_FILENAME)
    line = json.dumps(record, sort_keys=True) + "\n"
    try:
        try:
            with open(path, encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = ""
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(existing + line)
        os.replace(tmp_path, path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Root-orchestrator-only guard — same depth guards as dispatch-status-rate-gate.py.
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return

    # Suppress in any child context (CAA_CHILD_SIDECAR_DIR set by dispatch-agent.ts
    # for ALL dispatched claude-subprocess children and L2-sidecar children).
    if os.environ.get("CAA_CHILD_SIDECAR_DIR"):
        return

    # Belt-and-suspenders: default-profile dispatched children (CAA_DISPATCH_CHILD=1).
    exit_if_dispatched_child()

    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        return

    session_dir = _resolve_session_dir()
    if session_dir is None:
        return

    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(event, dict):
        return

    # Only act on PostToolUse for dispatch tools.
    if event.get("hook_event_name") != "PostToolUse":
        return
    tool_name = str(event.get("tool_name", ""))
    if tool_name not in DISPATCH_TOOL_NAMES:
        return

    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    subagent_type = str(tool_input.get("subagent_type") or "unknown")
    tool_use_id = str(event.get("tool_use_id") or "")

    # Find the most recent prior sidecar for this subagent_type.
    prior = _find_prior_sidecar(session_dir, subagent_type, tool_use_id)

    prior_verdict: str | None = None
    prior_tool_use_id: str | None = None
    is_retry_burst = False

    if prior is not None:
        _v = prior.get("verdict")
        prior_verdict = str(_v) if isinstance(_v, str) and _v else None
        _tid = prior.get("tool_use_id")
        prior_tool_use_id = str(_tid) if isinstance(_tid, str) and _tid else None
        is_retry_burst = prior_verdict in PROBLEM_VERDICTS

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {
        "ts": ts,
        "subagent_type": subagent_type,
        "tool_use_id": tool_use_id,
        "prior_tool_use_id": prior_tool_use_id,
        "prior_verdict": prior_verdict,
        "is_retry_burst": is_retry_burst,
    }
    _append_record(session_dir, record)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
