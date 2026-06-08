#!/usr/bin/env python3
# dispatch-child-safe: false
"""
terminal-title.py — Set the Windows Terminal tab title via OSC 0 escape code.

Mechanism: OSC 0 (ESC ] 0 ; <title> BEL) sets both the window title and icon
name, which Windows Terminal displays as the tab label. Hook stdout is NOT
rendered in the terminal for most Claude Code events — the bytes would go to
the debug log, not the TTY. Instead we open /dev/tty directly: the hook
subprocess inherits the session's controlling terminal and can write to it
regardless of where its stdout/stderr are redirected.

Events handled:
  Stop              → "🟢 idle — <label>"   (Claude finished, waiting for input)
                      SUPPRESSED when (a) the orchestrator is mid-loop — see
                      _loop_active() — or (b) background subagents/bash are
                      still in flight — see _bg_work_active(). The Stop hook
                      may fire even when turn-continuity-block.py emits
                      decision:"block" to auto-resume Claude; in that case the
                      turn is NOT actually idle, so the title must not flick.
  UserPromptSubmit  → "working — <label>"   (user just submitted, Claude about to act)
  SessionStart      → "working — <label>"   (fresh/resumed session, Claude will act next)
  anything else     → no-op (defensive: hook may get wired to more events later)

The label is the basename of the session's cwd (fallback: "claude").
"""

import json
import os
import sys
import time

from _dispatch_child_guard import exit_if_dispatched_child


# KEEP IN SYNC with turn-continuity-block.py loop-active sentinel read — suppress
# idle signal when the orchestrator is mid-loop (the Stop hook will block the yield).
# turn-continuity-block.py reads {session_dir}/loop-active at main() ~lines 158-160;
# when present and stop_hook_active=false, it emits decision:"block" and Claude
# auto-resumes — the turn is NOT actually idle. This check mirrors that read so
# the title does not flick to "🟢 idle" during mid-loop yields.
def _loop_active(session_id: str) -> bool:
    """Return True if {session_dir}/loop-active sentinel exists.

    Same project-root resolution as _bg_work_active (__file__ 3-parents-up) for
    consistency within this file; do NOT switch to os.getcwd() — the latter is
    turn-continuity-block.py's internal pattern, not a shared contract.

    Fail-open: any error path returns False so the idle title still fires.
    Better to over-notify than under-notify when suppression is itself broken.
    """
    if not session_id:
        return False
    try:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        sentinel_path = os.path.join(
            project_root, ".agent_context", "sessions", session_id, "loop-active"
        )
        return os.path.exists(sentinel_path)
    except Exception:
        return False


# KEEP IN SYNC with cycle-hook.py:any_fresh_subagent_active() — same TTL semantics
# and same sentinel-dir layout. The Stop-branch suppression below relies on this
# being a faithful mirror; drifting from cycle-hook.py produces inconsistent
# "is the session actually idle?" verdicts across consumers.
def _bg_work_active(session_id: str) -> bool:
    """Return True if any fresh sentinel exists in the session's subagent-active/ dir.

    Mirrors cycle-hook.py:any_fresh_subagent_active() behavior:
      - sentinel dir = {project_root}/.agent_context/sessions/{session_id}/subagent-active/
      - project_root derived from __file__ (3 parents up; same as cycle-hook.py)
      - TTL read from .claude/session-cycling.json (default 180s); same source key
        as cycle-hook.py:_get_sentinel_ttl() so drift cannot occur unilaterally.
      - Missing dir / scan errors → return False (fail-open: better to over-notify
        than under-notify when the suppression mechanism itself is broken).
    """
    if not session_id:
        return False
    try:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        sentinel_dir = os.path.join(
            project_root, ".agent_context", "sessions", session_id, "subagent-active"
        )

        # TTL: read from session-cycling.json, same key as _get_sentinel_ttl()
        ttl_seconds = 180
        config = os.path.join(project_root, ".claude", "session-cycling.json")
        if os.path.exists(config):
            try:
                with open(config) as f:
                    ttl_seconds = int(json.load(f).get("sentinel_ttl", 180))
            except (json.JSONDecodeError, ValueError, IOError):
                pass  # ignore config errors — keep default

        try:
            entries = os.listdir(sentinel_dir)
        except (OSError, FileNotFoundError):
            return False

        now = time.time()
        for name in entries:
            full = os.path.join(sentinel_dir, name)
            try:
                age = now - os.path.getmtime(full)
            except OSError:
                continue
            if age < ttl_seconds:
                return True
        return False
    except Exception:
        # Fail-open: any unexpected error → treat as "no background work" so the
        # idle title still fires. Matches the surrounding hook's pass-on-exception
        # discipline (a broken suppression check must NEVER break the title write).
        return False


def main() -> None:
    exit_if_dispatched_child("terminal-title")
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Malformed or empty stdin — nothing to do.
        return

    event = payload.get("hook_event_name", "")

    if event == "Stop":
        mode = "idle"
    elif event in ("UserPromptSubmit", "SessionStart"):
        mode = "working"
    else:
        # Unrecognised event — exit without touching the title.
        return

    # Suppress idle title when the turn is not actually idle. Only gates the
    # Stop branch — UserPromptSubmit and SessionStart are "working" transitions
    # INTO work, so suppression is never correct there.
    #
    # Two independent suppression predicates, evaluated cheapest-first:
    #   1. loop-active sentinel present → orchestrator is mid-loop; the Stop
    #      hook will block the yield via turn-continuity-block.py.
    #   2. fresh background subagent/bash sentinel present → orchestrator is
    #      waiting on background fan-out (allow-bg-agent-active branch).
    if mode == "idle":
        session_id = os.environ.get("CLAUDE_SESSION_ID", "")
        if _loop_active(session_id):
            return
        if _bg_work_active(session_id):
            return

    cwd = payload.get("cwd") or os.getcwd()
    label = os.path.basename(cwd.rstrip("/")) or "claude"

    if mode == "idle":
        title = f"\U0001f7e2 idle — {label}"   # 🟢 idle — <label>
    else:
        title = f"working — {label}"            # working — <label>

    sequence = f"\x1b]0;{title}\x07"

    try:
        with open("/dev/tty", "w") as tty:
            tty.write(sequence)
            tty.flush()
    except Exception:
        # No controlling terminal (e.g., CI, piped test run) — silently skip.
        # A missing or broken terminal must NEVER stall the Claude Code loop.
        pass


if __name__ == "__main__":
    main()
    # Always exit 0 — hook failures must not interrupt Claude Code processing.
    sys.exit(0)
