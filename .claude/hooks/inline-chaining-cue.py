#!/usr/bin/env python3
# dispatch-child-safe: false
"""
inline-chaining-cue.py - PostToolUse/Stop hook (matcher: "").

Tracks consecutive orchestrator inline tool calls in a per-session counter file.
When the nested signature crosses the approved thresholds, emits a one-shot
additionalContext cue that points back to the four turn-articulation options.

Fix 12: emits a researcher-dispatch nudge when >5 reads occur with 0 dispatches
        in the current chain.
Fix 16: emits a throttled positive-reflection cue on every 5th dispatch; emits a
        count-aware corrective cue at the existing thresholds.  Both cues are
        covered by BYTE_CAP.

Hard byte-cap: every emitted additionalContext body is capped at BYTE_CAP bytes.

Fails open: every exception is swallowed; sys.exit(0) unconditionally.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from _dispatch_child_guard import exit_if_dispatched_child

COUNTER_FILENAME = "inline-chain-counter.json"
BANNER_OPEN = "--- INLINE-CHAINING CHECK ---"
BANNER_CLOSE = "--- END INLINE-CHAINING CHECK ---"
PROMPT_SECTION_POINTER = ".claude/orchestrator-prompt.md § Four turn-articulation options"
INLINE_THRESHOLD = 8
WRITE_THRESHOLD = 4
BYTE_CAP = 320  # hard cap on additionalContext body bytes (Fix 16)

DISPATCH_TOOL_NAMES = {
    "Agent",
    "Task",
    "dispatch_agent",                   # bare name (legacy / native)
    "mcp__context-tools__dispatch_agent",  # MCP-prefixed name used by the runtime
}
WRITE_TOOL_NAMES = {
    "smart_write",
    "smart_edit",
    "Write",
    "Edit",
    "MultiEdit",
    "mcp__context-tools__smart_write",
    "mcp__context-tools__smart_edit",
}


def _resolve_session_dir() -> str | None:
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return None
    session_dir = os.path.join(os.getcwd(), ".agent_context", "sessions", session_id)
    if not os.path.isdir(session_dir):
        return None
    return session_dir


def _zero_state() -> dict[str, int]:
    return {"inline": 0, "write": 0, "dispatch_total": 0}


def _counter_path(session_dir: str) -> str:
    return os.path.join(session_dir, COUNTER_FILENAME)


def _coerce_counter(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _read_state(session_dir: str) -> dict[str, int]:
    try:
        with open(_counter_path(session_dir), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _zero_state()
        return {
            "inline": _coerce_counter(data.get("inline")),
            "write": _coerce_counter(data.get("write")),
            # dispatch_total is new in S12a; old files without it default to 0
            "dispatch_total": _coerce_counter(data.get("dispatch_total")),
        }
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return _zero_state()


def _write_state(session_dir: str, state: dict[str, int]) -> None:
    path = _counter_path(session_dir)
    tmp_path = path + ".tmp"
    payload = {
        "inline": _coerce_counter(state.get("inline")),
        "write": _coerce_counter(state.get("write")),
        "dispatch_total": _coerce_counter(state.get("dispatch_total")),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, path)
    except OSError:
        pass


def _is_dispatch_tool(tool_name: str) -> bool:
    return tool_name in DISPATCH_TOOL_NAMES or tool_name.startswith("mcp__alpha-pipeline__")


def _is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOL_NAMES


def _apply_byte_cap(body: str) -> str:
    """Enforce BYTE_CAP on an additionalContext body (Fix 16). No-op when within budget.

    Trims to BYTE_CAP-3 bytes and appends "..." so the total stays at or under
    BYTE_CAP regardless of multi-byte characters.
    """
    encoded = body.encode("utf-8")
    if len(encoded) <= BYTE_CAP:
        return body
    return encoded[: BYTE_CAP - 3].decode("utf-8", errors="ignore") + "..."


def _emit_cue(body: str) -> None:
    """Emit an additionalContext cue after applying the hard byte cap."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": _apply_byte_cap(body),
        }
    }
    print(json.dumps(payload))


def _build_corrective_cue(inline_count: int) -> str:
    """Count-aware corrective cue body (Fix 16)."""
    return (
        f"\n{BANNER_OPEN}\n"
        f"{inline_count} inline calls in a row, no dispatch. "
        f"Default is delegate: (b) specialist or (c) sub-orchestrator. "
        f"If this chain isn't one trivial op, dispatch now.\n"
        f"{BANNER_CLOSE}\n"
    )


def _build_read_chain_cue(read_count: int) -> str:
    """Read-chain nudge body (Fix 12)."""
    return (
        f"\n{BANNER_OPEN}\n"
        f"{read_count} reads, 0 dispatches this turn. A long "
        f"read-and-analyze chain is research work - dispatch "
        f"(b) researcher instead of reading on.\n"
        f"{BANNER_CLOSE}\n"
    )


def _build_positive_cue() -> str:
    """Throttled positive-reflection cue body (Fix 16)."""
    return (
        f"\n{BANNER_OPEN}\n"
        f"Dispatched - the default (b)/(c) path, working as intended. "
        f"Delegation is your center of gravity; keep it up.\n"
        f"{BANNER_CLOSE}\n"
    )


def _handle_post_tool_use(session_dir: str, event: dict[str, Any]) -> None:
    tool_name = str(event.get("tool_name", ""))

    if _is_dispatch_tool(tool_name):
        state = _read_state(session_dir)
        state["dispatch_total"] += 1
        # Throttled positive-reflection: emit once every 5 dispatches (Fix 16)
        if state["dispatch_total"] % 5 == 0:
            _emit_cue(_build_positive_cue())
        state["inline"] = 0
        state["write"] = 0
        _write_state(session_dir, state)
        return

    state = _read_state(session_dir)
    state["inline"] += 1
    if _is_write_tool(tool_name):
        state["write"] += 1

    # Fix 12: read-chain nudge when >5 non-write inline calls with 0 dispatches
    read_count = state["inline"] - state["write"]
    if read_count > 5 and state["dispatch_total"] == 0:
        _emit_cue(_build_read_chain_cue(read_count))
        state["inline"] = 0
        state["write"] = 0
        _write_state(session_dir, state)  # dispatch_total stays 0; S12b refines wording
        return

    # Fix 16: count-aware corrective at the existing WRITE_THRESHOLD / INLINE_THRESHOLD
    if state["write"] >= WRITE_THRESHOLD or state["inline"] >= INLINE_THRESHOLD:
        _emit_cue(_build_corrective_cue(state["inline"]))
        state["inline"] = 0
        state["write"] = 0
        _write_state(session_dir, state)  # dispatch_total preserved across corrective fire
        return

    _write_state(session_dir, state)


def _handle_event(session_dir: str, event: dict[str, Any]) -> None:
    hook_event_name = event.get("hook_event_name", "")
    if hook_event_name == "Stop":
        _write_state(session_dir, _zero_state())
        return
    if hook_event_name == "PostToolUse":
        _handle_post_tool_use(session_dir, event)


def main() -> None:
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return

    # Suppress in any child context: CAA_CHILD_SIDECAR_DIR is set by dispatch-agent.ts
    # for ALL dispatched claude-subprocess children (both hook_profile='none' and ='full')
    # and by bin/claude-session for L2-sidecar children.  Absent only in the root
    # orchestrator.  This catches hook_profile='full' dispatched children where
    # CAA_DISPATCH_CHILD is intentionally unset (opted out of axis-4 suppression).
    if os.environ.get("CAA_CHILD_SIDECAR_DIR"):
        return

    # Belt-and-suspenders: also exit for default-profile dispatched children
    # (CAA_DISPATCH_CHILD=1 set by dispatch-agent.ts when hookProfile != 'full').
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

    _handle_event(session_dir, event)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
