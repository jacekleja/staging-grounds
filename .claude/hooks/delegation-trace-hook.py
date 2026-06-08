#!/usr/bin/env python3
# dispatch-child-safe: false
"""PreToolUse hook on Agent tool: log every delegation decision to JSONL.

Fires before each subagent launch. Appends a JSONL entry to
{session_dir}/delegation-trace.jsonl for every Agent tool call, regardless
of subagent_type. This provides a passive audit trail of all delegation events.

Silent (no output, exit 0) always -- PreToolUse tracker, never blocks.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child


def get_session_dir(event):
    """Return the session-scoped directory for state files.

    Uses CLAUDE_SESSION_ID from environment. Falls back to .agent_context/audit/
    if session ID is not available.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    cwd = event.get("cwd", os.getcwd())
    if session_id:
        return os.path.join(cwd, ".agent_context", "sessions", session_id)
    return os.path.join(cwd, ".agent_context", "audit")


def extract_files_mentioned(prompt):
    """Extract file paths from prompt text using regex.

    Matches strings that look like file paths with extensions. Deduplicates.
    Returns empty list if prompt is empty or missing.
    """
    if not prompt:
        return []
    pattern = r'(?:^|[\s\'\"(,])((\\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+\.\w{1,6})(?=[\s\'\")\],:]|$)'
    matches = re.findall(pattern, prompt, re.MULTILINE)
    # re.findall returns tuples when there are groups -- extract first group (full path)
    paths = [m[0] for m in matches if m[0]]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _write_full_prompt(session_dir: str, tool_use_id: str, prompt: str) -> None:
    """Write full delegation prompt body to {session_dir}/delegation-prompts/<id>.md.

    Fail-quiet: swallows IOError, never raises. Skips silently if tool_use_id or
    prompt is empty.
    """
    if not tool_use_id or not prompt:
        return
    prompts_dir = os.path.join(session_dir, "delegation-prompts")
    try:
        os.makedirs(prompts_dir, exist_ok=True)
        out_path = os.path.join(prompts_dir, f"{tool_use_id}.md")
        with open(out_path, "w") as f:
            f.write(prompt)
    except IOError:
        pass


def main():
    exit_if_dispatched_child("delegation-trace-hook")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    # Only act on Agent tool calls (defensive -- matcher should handle this)
    if event.get("tool_name") != "Agent":
        return

    tool_input = event.get("tool_input", {})
    prompt = tool_input.get("prompt", "")

    # Build trace entry
    ts = datetime.now(timezone.utc).isoformat()

    prompt_first_line = ""
    if prompt:
        first_line = prompt.split("\n")[0]
        prompt_first_line = first_line[:200]

    entry = {
        "timestamp": ts,
        "tool_use_id": event.get("tool_use_id", ""),
        "agent_type": tool_input.get("subagent_type"),
        "model": tool_input.get("model"),
        "isolation": tool_input.get("isolation"),
        "description": tool_input.get("description", ""),
        "prompt_length": len(prompt),
        "prompt_first_line": prompt_first_line,
        "files_mentioned": extract_files_mentioned(prompt),
        "background": tool_input.get("run_in_background", False),
    }

    session_dir = get_session_dir(event)
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")

    # Ensure session dir exists
    os.makedirs(session_dir, exist_ok=True)

    # Append-only write -- no read required, no race condition
    try:
        with open(trace_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except IOError:
        pass

    _write_full_prompt(session_dir, entry["tool_use_id"], prompt)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
