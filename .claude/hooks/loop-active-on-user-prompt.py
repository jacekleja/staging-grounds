#!/usr/bin/env python3
# dispatch-child-safe: false
"""
loop-active-on-user-prompt.py — UserPromptSubmit hook that arms the loop-active sentinel.

Fires on every UserPromptSubmit. Writes a zero-byte {session_dir}/loop-active
sentinel so that the next Stop event sees the orchestrator as mid-loop and
turn-continuity-block.py blocks a premature yield.

Path-resolution contract: session_dir is derived as
{os.getcwd()}/.agent_context/sessions/{CLAUDE_SESSION_ID} — IDENTICAL to
turn-continuity-block.py:_resolve_session_dir. The two hooks MUST resolve to
the same physical directory or the writer and the reader will disagree.

Fail-safe: any unhandled exception is caught in __main__, a single stderr
line is emitted, and the hook exits 0. UserPromptSubmit hooks cannot block;
crashing here would only pollute stderr.
"""

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child


# ---------------------------------------------------------------------------
# Session-dir resolution — KEEP IN SYNC with turn-continuity-block.py:_resolve_session_dir
# ---------------------------------------------------------------------------

def _resolve_session_dir(session_id: str) -> str:
    """Derive the session dir from cwd + session_id.

    Mirrors turn-continuity-block.py:_resolve_session_dir. cwd is Claude Code's
    stable working dir (set at spawn by bin/claude-session). The writer (this
    hook) and the reader (turn-continuity-block.py) MUST use the same form.
    """
    cwd = os.getcwd()
    return os.path.join(cwd, ".agent_context", "sessions", session_id)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def _write_telemetry(session_dir: str, session_id: str, outcome: str) -> None:
    """Append one telemetry record to {session_dir}/audit-telemetry.jsonl.

    Failure is silent — telemetry is non-load-bearing.
    """
    try:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "event": "loop-active-on-user-prompt.fire",
            "session_id": session_id,
            "outcome": outcome,
        }
        telemetry_path = os.path.join(session_dir, "audit-telemetry.jsonl")
        with open(telemetry_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    exit_if_dispatched_child("loop-active-on-user-prompt")
    # Drain stdin so the parent does not see SIGPIPE; payload contents are unused.
    try:
        json.load(sys.stdin)
    except Exception:
        # Malformed or empty stdin — proceed anyway; the sentinel write does
        # not depend on payload fields.
        pass

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        # No session_id: cannot resolve session_dir. Silent exit.
        # No telemetry record either — we have no path to write it to.
        return

    session_dir = _resolve_session_dir(session_id)

    try:
        os.makedirs(session_dir, exist_ok=True)
        sentinel_path = os.path.join(session_dir, "loop-active")
        # touch — idempotent; second fire on an existing sentinel is a no-op.
        pathlib.Path(sentinel_path).touch()
        _write_telemetry(session_dir, session_id, "wrote")
    except Exception as exc:
        print(
            f"loop-active-on-user-prompt: sentinel write error: {exc}",
            file=sys.stderr,
        )
        # Best-effort telemetry — session_dir may or may not exist.
        _write_telemetry(session_dir, session_id, "skipped-error")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Outer fail-safe: any unhandled error must NOT crash the hook.
        print(
            f"loop-active-on-user-prompt: unexpected error: {exc}",
            file=sys.stderr,
        )
    sys.exit(0)
