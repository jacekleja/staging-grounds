#!/usr/bin/env python3
"""pre-dispatch-loop-active-check.py — PreToolUse:Agent hook.

Warns the orchestrator when a subagent is dispatched while the
`{session_dir}/loop-active` sentinel is absent. The sentinel is a zero-byte
file written by the UserPromptSubmit hook
(.claude/hooks/loop-active-on-user-prompt.py) with defense-in-depth writes
from loop-entry skills (`/cycling`, `/capture-intent reread`), and cleared
by orchestrator imperative directives at the five canonical sites
enumerated in .claude/orchestrator-prompt.md § No mid-loop turn-yield. Its
presence is what makes `turn-continuity-block.py` (Stop hook) prevent
mid-loop turn-yield.

The failure this hook catches: orchestrator enters a lifecycle loop and
dispatches a subagent (planner, validator, implementer, coherence-auditor,
…) WITHOUT first writing the sentinel. The no-mid-loop-yield discipline
then silently degrades — when the loop eventually wants to Stop, the Stop
hook sees no sentinel and cleanly allows the yield. The discipline was
bypassed and nothing caught it. Audit reference:
`audit-hooks-batch-1-gates.md § Cohort-level gaps`.

Mechanism: soft additionalContext warning. Subagent dispatch is the
strongest available signal of mid-loop intent (the lifecycle is built on
delegations), but it is not a perfect signal — orchestrator may dispatch a
researcher for a one-off exploration outside any formal loop. Over-warn is
the chosen failure mode (mirrors `build-pass-gate.py`'s KNOWN OVER-GATE).
The warning is reactive nudging, never a hard block.

Background-spawn handling: this hook treats `run_in_background=true`
dispatches identically. The sentinel discipline is about loop state, not
dispatch mode — a background dispatch while mid-loop is still mid-loop
work, and the orchestrator still owes the sentinel before it can yield.
The dispatch payload's mode does not change the invariant.

Fail-open: any unexpected error exits 0 silently. A hook crash must NEVER
block dispatch; this is a discipline-nudge, not a gate.
"""
import json
import os
import sys


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    # Defensive: matcher is "Agent" but the hook contract does not guarantee
    # the matcher fired exactly on the named tool. Re-check.
    if event.get("tool_name") != "Agent":
        return

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        # Cannot resolve session_dir without a session_id; degrade silently.
        # This matches build-pass-gate.py — without a session_id the path
        # collapses to the sessions root and would cause false-positive warns.
        return

    # Derive project root from script location (.claude/hooks/<this>.py).
    # event.cwd is unreliable here — Bash tool cwd changes can leak through.
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)

    # Graceful fallback: if the session dir doesn't exist we are not running
    # under claude-session in the expected layout; nothing to check against.
    if not os.path.isdir(session_dir):
        return

    sentinel_path = os.path.join(session_dir, "loop-active")
    if os.path.exists(sentinel_path):
        # Discipline honored — nothing to nudge.
        return

    # Sentinel absent and a subagent is being dispatched. Warn.
    tool_input = event.get("tool_input", {}) or {}
    subagent_type = tool_input.get("subagent_type") or "(unknown)"

    warning = (
        f"Dispatching subagent '{subagent_type}' but {sentinel_path} is absent. "
        "If this dispatch is part of a lifecycle loop "
        "(design → pre-flight → plan → implement → validate → coherence-auditor → fix), "
        "write the sentinel BEFORE dispatching so turn-continuity-block.py can "
        "prevent a mid-loop yield: `touch \"$SESSION_DIR/loop-active\"`. "
        "Clear it on halt, surface-to-user, or loop termination. "
        "If this dispatch is a one-off outside any formal loop, ignore this warning. "
        "Sentinel discipline reference: "
        ".claude/knowledge/reference/sentinels.md § loop-active (file name)."
    )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"--- LOOP-ACTIVE SENTINEL WARNING ---\n{warning}\n"
                "--- END LOOP-ACTIVE SENTINEL WARNING ---"
            ),
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Outer fail-safe: never block a dispatch on a hook crash.
        print(f"pre-dispatch-loop-active-check: unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)
