#!/usr/bin/env python3
# dispatch-child-safe: true
"""PostToolUse hook on mcp__context-tools__build_run: additively writes the build-pass sentinels on success.

Companion to `build-pass-gate.py` (the reactive warning). This hook is the
proactive writer — it relieves the orchestrator of the manual sequence:
    touch {session_dir}/build-pass
    git rev-parse HEAD > {session_dir}/build-pass-sha

Behavior on every PostToolUse:mcp__context-tools__build_run event:
  - SUCCESS (BuildResult.success == True AND exit_code == 0): write
    `{session_dir}/build-pass` (zero-byte) and `{session_dir}/build-pass-sha`
    (current HEAD SHA). Non-empty parse_errors are treated as warnings and do
    NOT block sentinel writes when success==True and exit_code==0 (e.g.,
    "Unknown build tool, exit code: 0" from the fallback parser is informational
    only — the underlying command still exited cleanly).
  - FAILURE (success == False, or response unparseable): NO-OP. The hook is
    additive-only; `build-run.ts:241-247 (clearBuildPassSentinels)` is the
    authoritative in-process clearer on failure. Avoiding duplicate clear-work
    prevents the iss_45e1865c94c9 regression where a parser-shape mismatch
    caused the hook to clobber sentinels build-run.ts had just legitimately
    written. A stale-pass sentinel surviving a failing rebuild (which the
    in-process clearer prevents) would let the validator gate falsely
    greenlight a broken tree.

SHA source: `git rev-parse HEAD`, run from `CAA_WORKTREE_ROOT` when set (Path C
worktree convention; matches build-pass-gate.py's drift-check cwd).

Silent (exit 0) when not running under claude-session (CLAUDE_SESSION_ID unset)
or when {session_dir} does not exist. Never blocks Claude Code.

State shape comes from BuildResult in
.claude/mcp/context-tools/src/tools/build-run.ts — top-level `success: bool`.
The MCP response wraps it as content[0].text = json.dumps(BuildResult).
"""
import json
import os
import subprocess
import sys


def _extract_build_result(tool_response):
    """Return the parsed BuildResult dict, or None if the response is not parseable.

    Claude Code's PostToolUse event delivers MCP tool returns as a bare content
    list at the top level (live shape, confirmed via debug instrumentation):
        tool_response = [{"type": "text", "text": "<json>"}]
    where <json> is json.dumps(BuildResult).

    The original assumed shape (dict wrapper) is also handled for robustness:
        tool_response = {"content": [{"type": "text", "text": "<json>"}], "is_error": bool}

    Returns None on any malformation — the caller treats unparseable as a no-op.
    """
    if isinstance(tool_response, list):
        # Live shape: content list delivered directly as tool_response
        content_list = tool_response
    elif isinstance(tool_response, dict):
        # Fallback: older assumed dict-wrapper shape
        if tool_response.get("is_error") is True:
            return None
        content = tool_response.get("content")
        if not isinstance(content, list):
            return None
        content_list = content
    else:
        return None

    for item in content_list:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "")
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError, TypeError):
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _current_head_sha():
    """Return the current HEAD SHA string, or None on any failure.

    Runs from CAA_WORKTREE_ROOT when set so the SHA matches the worktree's
    detached HEAD — same convention as build-pass-gate.py's drift check.
    """
    cwd = os.environ.get("CAA_WORKTREE_ROOT") or None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _write_pass_sentinels(session_dir):
    """Write build-pass (zero-byte) and build-pass-sha (HEAD SHA).

    Best-effort: any write failure is logged to stderr and swallowed. The hook
    must not block the agent regardless.
    """
    sha = _current_head_sha()
    if sha is None:
        print(
            "build-pass-sha-writer: git rev-parse HEAD failed; sentinels NOT written",
            file=sys.stderr,
        )
        return

    pass_file = os.path.join(session_dir, "build-pass")
    sha_file = os.path.join(session_dir, "build-pass-sha")

    try:
        open(pass_file, "w").close()
        with open(sha_file, "w") as f:
            f.write(sha + "\n")
    except OSError as e:
        print(
            f"build-pass-sha-writer: failed to write sentinels ({e})",
            file=sys.stderr,
        )


def _clear_pass_sentinels(session_dir):
    """Remove build-pass and build-pass-sha if present. Idempotent."""
    for name in ("build-pass", "build-pass-sha"):
        path = os.path.join(session_dir, name)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(
                f"build-pass-sha-writer: failed to remove {name} ({e})",
                file=sys.stderr,
            )


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    # Defensive: matcher should already restrict to build_run, but verify.
    if event.get("tool_name") != "mcp__context-tools__build_run":
        return

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return

    # Project root: this file lives at <root>/.claude/hooks/build-pass-sha-writer.py
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)
    if not os.path.isdir(session_dir):
        return

    result = _extract_build_result(event.get("tool_response"))
    success = isinstance(result, dict) and result.get("success") is True

    # parse_errors (e.g. "Unknown build tool, exit code: 0" from the fallback
    # parser) are treated as informational warnings, not failures.  They do NOT
    # block sentinel writes when success==True.  Only success==False or an
    # unparseable response clears the sentinels.
    if success:
        _write_pass_sentinels(session_dir)
    else:
        # Parser returns None when tool_response shape is unrecognized, or when
        # success==False. Previously this branch called _clear_pass_sentinels(),
        # which clobbered sentinels that build-run.ts's in-process writer had
        # just written correctly (iss_45e1865c94c9). Now a no-op: build-run.ts
        # is the authoritative writer; the hook is additive-only.
        pass


if __name__ == "__main__":
    main()
