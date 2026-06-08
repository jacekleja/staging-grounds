#!/usr/bin/env python3
# dispatch-child-safe: false
"""
orchestrator-pulse.py — PostToolUse hook (matcher: "").

Touches {session_dir}/orchestrator-last-tool-call when the TRIPLE self-gate
holds, signalling that the orchestrator made a tool call.  The sentinel's mtime
is the "last-active" timestamp consumed by statusline-bloom.sh for the idle
(💤) indicator.

Triple self-gate (ALL three must hold):
  1. CLAUDE_HOOK_ORCHESTRATOR_DEPTH == "1"   — orchestrator depth only
  2. not dispatched-child                    — not a claude-subprocess child
  3. CLAUDE_SESSION_DEPTH == "0"             — not an L2-sidecar child

Two side-effects on the orchestrator-depth branch (order matters):
  (a) os.utime(sentinel, None)  — update mtime (creates file on first fire)
  (b) append one ISO-ts line to orchestrator-pulse.log  — diagnostic surface

PITFALL: always resolve session_dir from $CLAUDE_SESSION_ID + os.getcwd(),
NEVER from $CLAUDE_PROJECT_DIR (not injected into agent bash subshells).
Mirrors dispatch-watcher-seed.py:32-37.

INSTALL-SET NOTE: included in caa-setup fresh installs + upgrades via the
hooks-dir walk in setup/src/init.ts buildCopyMap() (lines 382-394), which
picks up every *.py file that isn't test_*.py.

Fails open: every exception is swallowed; sys.exit(0) unconditionally.
"""
import datetime
import os
import sys

from _dispatch_child_guard import exit_if_dispatched_child


def main() -> None:
    # Gate 1: orchestrator depth
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return

    # Gate 2: dispatched-child exclusion (sys.exit(0) inside if triggered)
    exit_if_dispatched_child()

    # Gate 3: root session depth (excludes L2-sidecar children)
    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        return

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return

    session_dir = os.path.join(os.getcwd(), ".agent_context", "sessions", session_id)
    if not os.path.isdir(session_dir):
        return

    sentinel = os.path.join(session_dir, "orchestrator-last-tool-call")
    # Side-effect (a): touch sentinel (create + update mtime)
    with open(sentinel, "a"):
        os.utime(sentinel, None)

    # Side-effect (b): diagnostic log — lets operator distinguish hook-not-firing
    # from hook-firing-but-failing when the idle segment never renders (Pitfall 5).
    log_path = os.path.join(session_dir, "orchestrator-pulse.log")
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{ts}\tfired\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-open: PostToolUse hook must never block a tool call.
        pass
    sys.exit(0)
