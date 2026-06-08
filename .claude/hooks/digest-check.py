#!/usr/bin/env python3
# dispatch-child-safe: false
"""PostToolUse hook for session checkpoint: warn when impl reports are archived without C1 digests.

Matcher scope (settings.json): "mcp__context-tools__session" — fires after EVERY
session() invocation regardless of action (orient, checkpoint, handoff, halt, etc.),
because Claude Code matchers dispatch on tool_name only, not on tool_input.action.
Action narrowing is therefore done in the body (early-return on action != "checkpoint").

When the action IS checkpoint, the hook scans archived_context_files for entries with
reason='subtask-complete' matching the impl report filename pattern, and compares them
against original_report_path values in subtask_digests. Any unmatched impl report
becomes a line in an additionalContext warning.

Soft warning only — never blocks the checkpoint. Consistent with the project
preference for warnings over denials.

Silent (exit 0, no output) when ANY of:
- tool_name != "mcp__context-tools__session"        (matcher fallback safety)
- tool_input.action != "checkpoint"                  (action narrowing)
- CLAUDE_HOOK_ORCHESTRATOR_DEPTH != "1"              (M2-primary, orchestrator-only)
- CLAUDE_SESSION_DEPTH != "0"                        (REV-2, root session only)
- No archived_context_files with reason='subtask-complete' matching IMPL_PATTERN
- All matching archived files have corresponding subtask_digests entries
"""
import json
import os
import re
import sys

from _dispatch_child_guard import exit_if_dispatched_child


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Pattern to match impl report filenames
# Matches: *-impl-*.md, *-impl_*.md, *-impl.md (various naming conventions)
IMPL_PATTERN = re.compile(r'.*-impl[-_].*\.md$|.*-impl\.md$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    exit_if_dispatched_child("digest-check")
    raw = ""
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except (json.JSONDecodeError, EOFError, ValueError) as e:
        print(f"digest-check: JSON parse failed on event: {raw[:200]!r}", file=sys.stderr)
        sys.exit(0)

    # --- Body-level guards ---
    # tool_name check is defensive (matcher already filters); action check is load-bearing
    # because Claude Code matchers cannot narrow on tool_input.action.
    if event.get("tool_name") != "mcp__context-tools__session":
        sys.exit(0)
    if event.get("tool_input", {}).get("action") != "checkpoint":
        sys.exit(0)
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        sys.exit(0)  # not orchestrator-depth (M2-primary, see hook-directive-audience-map.md)
    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        sys.exit(0)  # not root-orchestrator-depth (REV-2)

    # --- Collect archived impl reports and existing digests ---
    tool_input = event.get("tool_input") or {}

    archived_raw = tool_input.get("archived_context_files") or []
    digests_raw = tool_input.get("subtask_digests") or []

    # Build set of original_report_path values that have been digested
    digested_paths = {
        d.get("original_report_path", "")
        for d in digests_raw
        if isinstance(d, dict)
    }

    # Find impl reports archived with reason='subtask-complete' that lack a digest
    missing_digests = []
    for entry in archived_raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("reason") != "subtask-complete":
            continue
        path = entry.get("path", "")
        basename = os.path.basename(path)
        if not IMPL_PATTERN.match(basename):
            continue
        if path not in digested_paths:
            missing_digests.append(path)

    if not missing_digests:
        sys.exit(0)

    # --- Emit additionalContext warning ---
    lines = [f"[digest-check] {len(missing_digests)} impl report(s) were archived without a C1 digest:"]
    for p in missing_digests:
        lines.append(f"  - {p}")
    lines.append("")
    lines.append(
        "Before this checkpoint is finalized, invoke the synthesizer for each undigested impl report "
        "and re-checkpoint with subtask_digests entries populated. "
        "If synthesizer fails, archive with reason='c1-fail' and proceed."
    )

    warning_text = "\n".join(lines)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": warning_text,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # Top-level belt-and-braces: warning-layer hook should not crash the session.
        # Swallow unexpected exceptions silently — this is a tracker, not a blocker.
        sys.exit(0)
