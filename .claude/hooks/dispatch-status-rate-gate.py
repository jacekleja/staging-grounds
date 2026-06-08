#!/usr/bin/env python3
# dispatch-child-safe: false
"""
dispatch-status-rate-gate.py - PreToolUse hook on mcp__context-tools__dispatch_agent_status.

Prevents dispatch_agent_status spin-polling: tracks consecutive calls with no
intervening dispatch or wake, and denies at THRESHOLD via sys.exit(2).

Counter resets when:
  - A dispatch-stale-sweep-wake-pending sentinel is consumed (stale-sweep woke
    the model; the next status poll is legitimately exempt).
  - The count of children/<spawn_id>/ subdirectories in session_dir changes
    (dispatch progress or cleanup occurred between status polls).

Sentinel consume is always-on-read: the sentinel is removed on every read so that
a single stale-sweep wake exempts exactly one status poll.  The exemption is not
time-windowed; a model turn can legitimately spend longer than any fixed TTL
before issuing its first post-wake dispatch_agent_status call.

Fails open: all exceptions swallowed; sys.exit(0) unconditionally at the end.
"""
from __future__ import annotations

import json
import os
import sys

from _dispatch_child_guard import exit_if_dispatched_child

COUNTER_FILENAME = "dispatch-status-rate-gate.json"
SENTINEL_FILENAME = "dispatch-stale-sweep-wake-pending"

THRESHOLD = 3           # consecutive polls before sys.exit(2) deny


# ---------------------------------------------------------------------------
# Session resolution
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
# Counter state
# ---------------------------------------------------------------------------

def _zero_state() -> dict[str, int]:
    return {"consecutive_polls": 0, "last_seen_dispatch_total": 0}


def _read_state(session_dir: str) -> dict[str, int]:
    path = os.path.join(session_dir, COUNTER_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _zero_state()
        return {
            "consecutive_polls": max(0, int(data.get("consecutive_polls", 0))),
            "last_seen_dispatch_total": max(0, int(data.get("last_seen_dispatch_total", 0))),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _zero_state()


def _write_state(session_dir: str, state: dict[str, int]) -> None:
    path = os.path.join(session_dir, COUNTER_FILENAME)
    tmp_path = path + ".tmp"
    payload = {
        "consecutive_polls": max(0, state.get("consecutive_polls", 0)),
        "last_seen_dispatch_total": max(0, state.get("last_seen_dispatch_total", 0)),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Sentinel side-channel
# ---------------------------------------------------------------------------

def _check_and_consume_sentinel(session_dir: str) -> bool:
    """Return True if a sentinel exists and was successfully removed.

    The sentinel marks an unconsumed dispatch-stale-sweep wake.  Age alone cannot
    invalidate it because legitimate work between wake and first status poll can
    exceed any fixed TTL.  Exemption is granted only when removal succeeds, so an
    undeletable sentinel cannot grant repeated bypasses.
    """
    path = os.path.join(session_dir, SENTINEL_FILENAME)
    try:
        os.path.getmtime(path)
    except OSError:
        return False  # sentinel absent or stat error

    try:
        os.remove(path)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Dispatch-total read (children-dir count; decoupled from inline-chaining-cue)
# ---------------------------------------------------------------------------

def _read_dispatch_total(session_dir: str) -> int | None:
    """Return the number of children/<spawn_id>/ subdirectories in session_dir.

    Each agent dispatch creates one subdirectory.  Decoupled from
    inline-chaining-cue.py — immune to
    field-name and MCP-prefix mismatches that caused the Bug-A runtime misfire
    (inline-chain-counter.json lacked 'dispatch_total'; even when present,
    'mcp__context-tools__dispatch_agent' did not match DISPATCH_TOOL_NAMES).
    Returns 0 when the children dir is absent (no dispatches yet).  Returns None
    on read errors so the caller can fail open instead of denying a poll when the
    progress signal is unavailable.
    """
    children_dir = os.path.join(session_dir, "children")
    try:
        entries = os.listdir(children_dir)
    except (FileNotFoundError, NotADirectoryError):
        return 0
    except OSError:
        return None
    return sum(
        1 for entry in entries
        if os.path.isdir(os.path.join(children_dir, entry))
    )


# ---------------------------------------------------------------------------
# Deny
# ---------------------------------------------------------------------------

def _deny(count: int) -> None:
    """Print deny message to stderr and sys.exit(2).

    exit(2) causes Claude Code to block the tool call and show the stderr message
    to the model as the denial reason.  SystemExit is a BaseException subclass and
    propagates through the outer `except Exception` wrapper unimpeded.
    """
    msg = (
        f"--- DISPATCH STATUS RATE GATE ---\n"
        f"dispatch_agent_status called {count} consecutive time(s) with no intervening "
        f"dispatch or wake.  Stop polling — yield to the wake channel.\n"
        f"Await dispatch-completion-watcher / dispatch-stale-sweep to re-wake the model "
        f"when results are ready.  Do not call dispatch_agent_status again until woken.\n"
        f"--- END DISPATCH STATUS RATE GATE ---"
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Root-orchestrator-only gate — same depth guards as inline-chaining-cue.py.
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return

    # Suppress in any child context: CAA_CHILD_SIDECAR_DIR is set by dispatch-agent.ts
    # for ALL dispatched claude-subprocess children and by bin/claude-session for L2
    # sidecar children.  Absent only in the root orchestrator.
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

    # Defense-in-depth: settings.json matcher already filters dispatch_agent_status.
    tool_name = str(event.get("tool_name", ""))
    if tool_name != "mcp__context-tools__dispatch_agent_status":
        return

    # 1. Read persisted state.
    state = _read_state(session_dir)

    # 2. Sentinel check — always-on-read consume regardless of threshold state.
    if _check_and_consume_sentinel(session_dir):
        # First post-wake poll is exempt; preserve the dispatch progress marker.
        state["consecutive_polls"] = 0
        _write_state(session_dir, state)
        return

    # 3. Reset consecutive counter when an intervening dispatch is detected.
    #    Counts children/<spawn_id>/ subdirs (decoupled from inline-chaining-cue.py).
    current_dispatch_total = _read_dispatch_total(session_dir)
    if current_dispatch_total is None:
        return
    if current_dispatch_total != state["last_seen_dispatch_total"]:
        state["consecutive_polls"] = 0
        state["last_seen_dispatch_total"] = current_dispatch_total

    # 4. Increment consecutive poll counter.
    state["consecutive_polls"] += 1

    # 5. Deny at/above threshold — write state first so the high count persists
    #    across the denial (ensures subsequent polls are denied until sentinel/dispatch).
    if state["consecutive_polls"] >= THRESHOLD:
        _write_state(session_dir, state)
        _deny(state["consecutive_polls"])
        # sys.exit(2) above; not reached.

    # 6. Allow — persist updated state.
    _write_state(session_dir, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
