#!/usr/bin/env python3
# dispatch-child-safe: false
"""PostToolUse hook: pre-emission L3 self-audit sentinel advisory (Layer 2).

Fires on Write/Edit/MultiEdit/mcp__context-tools__smart_write to generator
artifacts under {session_dir}/. Soft-warns (additionalContext) when the L3
self-audit sentinel line is absent from the just-written file.

Exit code 0 always. Never emits permissionDecision. Fail-open on all errors.

Counter file {session_dir}/pre-emission-race-warn-count.json is incremented
through RACE_WARN_CAP (fires 1-3 emit; fires 4+ write counter and return silently)
to bound spurious partial-read-race advisories.
"""
import fnmatch
import json
import os
import re
import sys

from _dispatch_child_guard import exit_if_dispatched_child

# ---------------------------------------------------------------------------
# Module constants — locked by R3-approved research. Do NOT modify.
# ---------------------------------------------------------------------------

SENTINEL_RE = re.compile(
    r"Pre-emission self-audit:\s+\d+\s+citations? verified,\s+\d+\s+sections? present,"
    r"\s+(?:and\s+)?\d+\s+contradictions? checked[.,]?",
    re.IGNORECASE,
)

RACE_WARN_CAP = 3  # mirrors mcp-retry-warn.py WARNING_CAP

# Generator-path globs (relative to session_dir basename or full path).
# One per writing-agent type; *-sketch*.md covers critique-sketch variants
# and research-*.md covers research-subtopic-*.md spillover (verified via fnmatch).
GENERATOR_GLOBS = [
    "research-*.md",             # researcher (.claude/agents/researcher.md § Output)
    "research-*-*.md",           # researcher subtopic spillover (same agent)
    "plan-*.md",                 # planner (.claude/agents/planner.md § Input Contract)
    "*-design.md",               # architect (.claude/agents/architect.md § Output Format)
    "*-sketch*.md",              # solution-designer (sketch + critique-sketch)
]

# Paths that match a glob above but are NOT generator artifacts.
EXCLUDE_GLOBS = [
    "episode-summaries/*.md",  # cycling-skill UX artifacts
    "marathon-summary.md",     # cycling-skill UX artifact
    "digests/*.md",            # synthesizer C1 digests — sentinel deliberately omitted
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def should_fire_advisory(written_path, session_dir):
    """Return True if the hook should inspect this write for the L3 sentinel."""
    if not written_path.startswith(session_dir):
        return False  # not a session artifact
    rel = os.path.relpath(written_path, session_dir)
    # Skip excluded paths first
    for excl in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(rel, excl):
            return False
    # Must match at least one generator glob by basename
    basename = os.path.basename(written_path)
    return any(fnmatch.fnmatch(basename, g) for g in GENERATOR_GLOBS)


def sentinel_present(artifact_path):
    """Return True if the L3 self-audit sentinel is present in the artifact.

    Returns True on OSError (fail-open: unreadable → assume sentinel present,
    no false advisory). Also returns True on re.error (hook bug, not artifact defect).
    """
    try:
        with open(artifact_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        try:
            return bool(SENTINEL_RE.search(content))
        except re.error:
            return True  # regex failure is a hook bug; default to no advisory
    except OSError:
        return True  # file locked/permission issue — fail-open, no false alarm


def round_number_from_path(path):
    """Extract round number from a path containing -R<N>- (e.g. plan-FOO-R3-sketch.md).

    Returns None for paths without a -R<N>- segment (e.g. plan-TASK.md, *-design.md);
    the caller defaults round_n to '?' in that case.
    """
    m = re.search(r"-R(\d+)-", os.path.basename(path))
    return m.group(1) if m else None


def increment_race_warn_counter(session_dir):
    """Increment and return the per-session partial-read advisory count.

    Uses tmp-file + os.replace for atomic update — mirrors mcp-retry-warn.py
    counter pattern (lines 142-146). On OSError mid-write, the tmp file is
    orphaned but the canonical counter is unchanged (no half-written state
    visible to a concurrent reader).
    """
    counter_file = os.path.join(session_dir, "pre-emission-race-warn-count.json")
    try:
        with open(counter_file, "r") as f:
            n = json.load(f).get("count", 0)
    except (OSError, ValueError):
        n = 0
    n += 1
    tmp_file = counter_file + ".tmp"
    try:
        with open(tmp_file, "w") as f:
            json.dump({"count": n}, f)
        os.replace(tmp_file, counter_file)  # atomic — POSIX rename(2) guarantee
    except OSError:
        pass
    return n


def emit_advisory(written_path, round_n):
    """Write the additionalContext advisory JSON to stdout."""
    basename = os.path.basename(written_path)
    advisory_text = (
        f"--- PRE-EMISSION QUALITY WARNING (L2) ---\n"
        f"Artifact written without L3 self-audit sentinel: {basename}\n"
        f"The expected sentinel line was NOT found in the just-written artifact:\n"
        f'  "Pre-emission self-audit: N citations verified, M sections present, K contradictions checked."'
        f"  (or minor natural-language variations)\n"
        f"This line signals that the agent ran citation-existence sweep, required-section check, and\n"
        f"contradiction scan (Steps 1-3 of .claude/knowledge/meta/pre-emission-self-audit.md).\n"
        f"\n"
        f"ADVISORY (soft-warn mode): your artifact is on disk and you may continue. But before returning,\n"
        f"consider:\n"
        f"  1. Run smart_read(mode='outline') on {basename} to confirm required sections are present.\n"
        f"  2. Scan for [verified:] citations and confirm they resolve.\n"
        f"  3. Append the sentinel line to the artifact.\n"
        f"\n"
        f"If you have already completed the self-audit and just forgot to append the sentinel, append it now.\n"
        f"Reference: .claude/knowledge/meta/pre-emission-self-audit.md § Common Pre-Emission Steps (All Generators)\n"
        f"--- END PRE-EMISSION QUALITY WARNING (L2) ---"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": advisory_text,
        }
    }))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    exit_if_dispatched_child("pre-emission-quality-warn")
    # Step 1: parse stdin JSON
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    # Step 2: tool-name guard (defensive: hook is registered on 4 matchers,
    # but multi-matcher delivery in some Claude Code versions may send others)
    tool_name = event.get("tool_name", "")
    if tool_name not in {"Write", "Edit", "MultiEdit", "mcp__context-tools__smart_write"}:
        return

    # Step 3: read session id
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    # Step 4: derive project root (three dirname calls, mirrors mcp-retry-warn.py:114)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)

    # Step 5: session-dir existence check
    if not os.path.isdir(session_dir):
        return

    # Step 6: file_path extraction
    tool_input = event.get("tool_input", {})
    written_path = tool_input.get("file_path", "")
    if not written_path:
        return

    # Step 7: path-prefix + glob gate
    if not should_fire_advisory(written_path, session_dir):
        return

    # Step 8: sentinel scan with fail-open
    if sentinel_present(written_path):
        return

    # Step 9: race-counter increment + cap check (increment BEFORE cap test,
    # mirrors mcp-retry-warn.py:141-149).
    # Guard: session_id required to avoid writing counter to sessions root dir.
    if not session_id:
        return
    n = increment_race_warn_counter(session_dir)
    if n > RACE_WARN_CAP:
        return

    # Step 10: emit advisory
    round_n = round_number_from_path(written_path) or "?"
    emit_advisory(written_path, round_n)


if __name__ == "__main__":
    # Resolve session_dir for logging before calling main(), best-effort
    _session_dir_for_log = None
    try:
        _sid = os.environ.get("CLAUDE_SESSION_ID", "")
        if _sid:
            _pr = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _session_dir_for_log = os.path.join(_pr, ".agent_context", "sessions", _sid)
    except Exception:
        pass

    try:
        main()
    except Exception:
        if _session_dir_for_log and os.path.isdir(_session_dir_for_log):
            try:
                import traceback
                log_path = os.path.join(_session_dir_for_log, "pre-emission-quality-warn.log")
                with open(log_path, "a") as lf:
                    lf.write(traceback.format_exc())
            except Exception:
                pass
    sys.exit(0)
