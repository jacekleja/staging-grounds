#!/usr/bin/env python3
# dispatch-child-safe: false
"""PreToolUse hook on Agent tool: build-pass gate warning before validator spawns.

Fires before each Agent tool call. If the subagent type is "validator" AND
the delegation prompt's `active_rubrics` lists at least one code-inspection
rubric (`code-vs-spec`, `constraint-compliance`), checks for a
build-pass signal file in the session directory. Warns via additionalContext
if no recent (< 30 minutes) build pass has been recorded.

Validator dispatches with only non-code rubrics (e.g. `ux-surface`,
`gestalt-coherence`, `deferral-ref-resolution`, `cross-artifact-coherence`)
skip the gate — no build state is relevant when the artifact under review is
prose/spec/docs.

The `active_rubrics` field is extracted by the centralized delegation_prompt_parser
(JSON-first, prose fallback). Sibling schema gate hard-blocks any delegation prompt
missing the `active_rubrics` literal token, so when this hook fires the token is
reliably present. If the field's array body is empty or unparseable, the hook
conserves the prior behavior and gates anyway (fail-closed: empty rubrics list →
no code rubric intersection → skip gate, same as the previous regex behavior for
empty `[]`).

The orchestrator writes the signal file via:
    touch {session_dir}/build-pass
    git rev-parse HEAD > {session_dir}/build-pass-sha
after a successful build_run call.

Silent (exit 0) when not a validator spawn, when the validator's rubric set
is exclusively non-code, or when not running under claude-session
(CLAUDE_SESSION_ID not set).
"""
import json
import os
import re
import subprocess
import sys
import time

# Ensure sibling modules resolve regardless of cwd at hook-fire time.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HOOKS_DIR)

from delegation_prompt_parser import CODE_RUBRICS, parse_prompt
from _dispatch_child_guard import exit_if_dispatched_child


BUILD_PASS_MAX_AGE_SECONDS = 30 * 60  # 30 minutes

_SHA_RE = re.compile(r'^[0-9a-f]{40}$')

# _CODE_RUBRICS kept for backward compatibility with external references and the
# regression test in test_delegation_prompt_parser.py that pins both constants
# as equal. The single source of truth is delegation_prompt_parser.CODE_RUBRICS.
_CODE_RUBRICS = CODE_RUBRICS


def _get_current_sha(project_root, worktree_root=None):
    """Return current HEAD SHA string, or None on any error.

    Under Path C (CAA_WORKTREE_ROOT set), uses worktree_root as cwd so the
    SHA matches the worktree's detached HEAD, which is what the orchestrator
    recorded at build-pass time.
    """
    cwd = worktree_root if worktree_root else project_root
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _read_recorded_sha(session_dir):
    """Return SHA string from build-pass-sha companion file, or None if absent/invalid."""
    sha_file = os.path.join(session_dir, 'build-pass-sha')
    if not os.path.exists(sha_file):
        return None
    try:
        content = open(sha_file).read().strip()
    except OSError:
        return None
    if not _SHA_RE.match(content):
        # Malformed or empty — treat as missing
        return None
    return content



def run_gate(event: dict) -> dict:
    """Pure function for the IPC worker: returns _action dict without process-exit side effects.

    result["_action"] is "warn" (advisory) or "pass". build-pass-gate never blocks; it is
    purely advisory per the backlog operator directive.
    "warn" -> result["additionalContext"] is the advisory message string.
    """
    if event.get("tool_name") != "Agent":
        return {"_action": "pass"}

    tool_input = event.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type")
    if subagent_type != "validator":
        return {"_action": "pass"}

    # Skip when no code-related rubric is active.
    prompt = tool_input.get("prompt", "")
    parsed = parse_prompt(prompt)

    if not parsed.active_rubrics:
        import re as _re
        token_present = bool(_re.search(r"\bactive_rubrics\b", prompt))
        if token_present:
            return {"_action": "pass"}
        # Token absent -- fail-closed: gate
    elif not bool(CODE_RUBRICS & set(parsed.active_rubrics)):
        return {"_action": "pass"}

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not session_id:
        return {"_action": "pass"}
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)
    if not os.path.isdir(session_dir):
        return {"_action": "pass"}

    signal_file = os.path.join(session_dir, "build-pass")
    deny_reason = None
    if not os.path.exists(signal_file):
        deny_reason = (
            "Build-pass gate: No recent successful build found. "
            "Run build_run before spawning validator."
        )
    else:
        try:
            mtime = os.path.getmtime(signal_file)
            age_seconds = time.time() - mtime
            if age_seconds > BUILD_PASS_MAX_AGE_SECONDS:
                deny_reason = (
                    "Build-pass gate: Build-pass signal is stale "
                    f"({int(age_seconds // 60)} minutes old, max 30). "
                    "Run build_run again before spawning validator."
                )
        except OSError:
            deny_reason = (
                "Build-pass gate: Could not read build-pass signal file. "
                "Run build_run before spawning validator."
            )

    sha_drift_warning = None
    recorded_sha = _read_recorded_sha(session_dir)
    if recorded_sha is not None:
        worktree_root = os.environ.get("CAA_WORKTREE_ROOT", "") or None
        current_sha = _get_current_sha(project_root, worktree_root=worktree_root)
        if current_sha is not None and current_sha != recorded_sha:
            sha_drift_warning = (
                f"build-pass recorded at SHA {recorded_sha}, "
                f"current tree is at {current_sha} "
                "— tree changed since build verification"
            )

    if deny_reason or sha_drift_warning:
        parts = []
        if deny_reason:
            parts.append(deny_reason)
        if sha_drift_warning:
            parts.append(sha_drift_warning)
        context = "\n".join(parts)
        return {
            "_action": "warn",
            "additionalContext": f"--- BUILD PASS WARNING ---\n{context}\n--- END BUILD PASS WARNING ---",
        }

    return {"_action": "pass"}


def main():
    exit_if_dispatched_child("build-pass-gate")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    result = run_gate(event)

    if result["_action"] == "warn":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": result["additionalContext"],
            }
        }))



if __name__ == "__main__":
    main()
