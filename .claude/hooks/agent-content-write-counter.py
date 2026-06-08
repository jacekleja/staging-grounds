#!/usr/bin/env python3
# dispatch-child-safe: false
"""PostToolUse hook: agent-content-write counter + gestalt-eval dispatch directive.

Implements UD-3 (`.claude/knowledge/decisions/agent-content-quality-campaign.md § UD-3`)
and UD-6 Path A (in-session reconciliation-capable dispatch). Fires on
Write/Edit/MultiEdit/mcp__context-tools__smart_write to agent-facing-content
surfaces (`.claude/agents/**`, `.claude/skills/**`, `.claude/knowledge/**`,
`CLAUDE.md`, `.claude/orchestrator-prompt.md`).

On each match:
  1. Reads the suppression sentinels from `{session_dir}/` (see Sentinel suppression below).
  2. Increments a per-session cross-file counter at `.agent_context/audit/agent-content-write-count`
     (per-file edit counts stored; F=8 / E=25 thresholds fire on the count of DISTINCT
     files edited in the session, not on per-file edit counts; atomic rename per C4
     concurrency contract).
  3. If `audit-counter-campaign-suspend` is present AND CAA_CAMPAIGN_ID env-var is
     non-empty, writes a per-file campaign-stamp sidecar at
     `.agent_context/audit/<file-path-slug>.campaign-stamp.json` (three-way conjunction
     per `.claude/knowledge/reference/sentinels.md § audit-counter-campaign-suspend` —
     origin-attribution for sweep-driven rewrites; reset semantics applied by the
     proactive-sweep skill on sentinel-clear).
  4. On dual-threshold cross (F=8 sampling, E=25 full eval) emits a dispatch directive
     via `hookSpecificOutput.additionalContext` UNLESS suppressed.

Banner literal (load-bearing; matched by orchestrator-prompt self-routing protocol):
  - Banner open:  `--- GESTALT AUDIT REQUIRED ---`
  - Banner close: `--- END GESTALT AUDIT REQUIRED ---`

Directive is reconciliation-capable per OQ-S-4 Path A / UD-6: names
`agent-content-author` as the evaluator; does NOT prescribe edits.

Sentinel suppression (read from `{session_dir}/` derived from CLAUDE_SESSION_ID):
  - `audit-proactive-active` (C5) — suppresses directive emission entirely; counter
    still increments. Set by the proactive corpus-sweep skill as its first tool call.
  - `audit-counter-campaign-suspend` (C5b) — suppresses directive emission AND triggers
    campaign-stamp sidecar write when CAA_CAMPAIGN_ID is also set.

Exit code 0 always. Never emits `permissionDecision`. Fail-open on all errors.
"""
import datetime
import json
import os
import sys

from _dispatch_child_guard import exit_if_dispatched_child

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Campaign-glob: agent-facing content surfaces per agent-facing-content-discipline.md
CAMPAIGN_PATH_PREFIXES = (
    ".claude/agents/",
    ".claude/skills/",
    ".claude/knowledge/",
)
CAMPAIGN_EXACT_FILES = {"CLAUDE.md", ".claude/orchestrator-prompt.md"}
CAMPAIGN_SUFFIX = ".md"  # campaign-glob restricts to markdown surfaces

THRESHOLD_F = 8   # gestalt sampling cadence (UD-3)
THRESHOLD_E = 25  # full gestalt eval cadence (UD-3)

COUNTER_REL = ".agent_context/audit/agent-content-write-count"

# Suppression sentinels — read from {session_dir}/ at PostToolUse time.
# Registry: .claude/knowledge/reference/sentinels.md §§ audit-proactive-active,
# audit-counter-campaign-suspend.
SENTINEL_PROACTIVE = "audit-proactive-active"
SENTINEL_CAMPAIGN_SUSPEND = "audit-counter-campaign-suspend"

# Per-file campaign-stamp sidecar directory (sibling of COUNTER_REL).
CAMPAIGN_STAMP_DIR_REL = ".agent_context/audit"

# Positive allowlist — avoids Signal-A misroute classification in
# bin/test_no_pretooluse_agent_prompt_mutation_lint.py (Signal A is the
# negative-inequality guard form; positive filter is Signal-A-free).
ALLOWED_TOOLS = {"Write", "Edit", "MultiEdit", "mcp__context-tools__smart_write"}

# Surface token mapping for directive target_surface field (heuristic)
_SURFACE_MAP = {
    ".claude/agents/": "S1",
    ".claude/skills/": "S3",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _surface_token(rel_path):
    """Derive S-token from path prefix (heuristic; 'unknown' if no match)."""
    if rel_path == "CLAUDE.md":
        return "S5"
    for prefix, token in _SURFACE_MAP.items():
        if rel_path.startswith(prefix):
            return token
    return "unknown"


def _session_dir(project_root):
    """Return absolute path to `{session_dir}` or None when CLAUDE_SESSION_ID is unset.

    Without a session_id we cannot locate the suppression sentinels — return None and
    let the caller treat it as 'no suppression in scope' (fail-open: directive may fire).
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    if not session_id:
        return None
    return os.path.join(project_root, ".agent_context", "sessions", session_id)


def _check_sentinels(session_dir):
    """Return (suppress_directive, campaign_suspend_active) booleans.

    suppress_directive: True if either C5 (audit-proactive-active) or C5b
      (audit-counter-campaign-suspend) is present in {session_dir}/.
    campaign_suspend_active: True if C5b is present (used to gate sidecar write).

    Fail-open on missing session_dir: both False.
    """
    if not session_dir or not os.path.isdir(session_dir):
        return (False, False)
    c5_present = os.path.exists(os.path.join(session_dir, SENTINEL_PROACTIVE))
    c5b_present = os.path.exists(os.path.join(session_dir, SENTINEL_CAMPAIGN_SUSPEND))
    return (c5_present or c5b_present, c5b_present)


def _file_path_slug(rel_path):
    """Convert a repo-relative path to the campaign-stamp sidecar slug.

    Slug form: forward-slash → double-underscore (reversible without collision for
    paths that do not themselves contain '__'). Stable across writes for the same file.
    """
    return rel_path.replace("/", "__")


def _write_campaign_stamp(project_root, rel_path, new_count):
    """Write per-file campaign-stamp sidecar (best-effort; fail-open on OSError).

    Sidecar path: `.agent_context/audit/<slug>.campaign-stamp.json`. Consumed by the
    proactive-sweep skill's sentinel-clear step to compute reset semantics (per-file
    counter contributions for files with fresh stamps are purged from the aggregate).
    """
    campaign_id = os.environ.get("CAA_CAMPAIGN_ID", "").strip()
    if not campaign_id:
        return  # third leg of three-way conjunction absent — skip silently
    sidecar = os.path.join(
        project_root,
        CAMPAIGN_STAMP_DIR_REL,
        f"{_file_path_slug(rel_path)}.campaign-stamp.json",
    )
    payload = {
        "campaign_id": campaign_id,
        "rel_path": rel_path,
        "count_at_stamp": new_count,
        "stamped_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    tmp = sidecar + ".tmp"
    try:
        os.makedirs(os.path.dirname(sidecar), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, sidecar)
    except OSError:
        return  # fail-open


def is_campaign_path(rel_path):
    """Return True if rel_path (repo-relative) matches the campaign-glob."""
    # Exact-file match
    if rel_path in CAMPAIGN_EXACT_FILES:
        return True
    # Prefix + suffix match (handles ** depth without fnmatch ** gap)
    if rel_path.endswith(CAMPAIGN_SUFFIX):
        for prefix in CAMPAIGN_PATH_PREFIXES:
            if rel_path.startswith(prefix):
                return True
    return False


def increment_counter(project_root, rel_path):
    """Atomic read-modify-write of the per-session cross-file counter dict.

    Returns (per_file_count, session_count, is_new_file), or None on fatal error
    (fail-open).  per_file_count is the total edits to rel_path this session
    (used for campaign-stamp sidecar); session_count is the number of distinct
    files seen across the session (used for F/E threshold checks); is_new_file
    is True only on the first edit to rel_path in this session.
    Best-effort race: read-modify-write window is non-atomic; concurrent
    worktree writers may lose increments. Phase 1 accepts this per the
    Tempering bullet 2 in the subtask plan.
    """
    counter_file = os.path.join(project_root, COUNTER_REL)
    tmp_file = counter_file + ".tmp"

    # Read current state
    try:
        with open(counter_file, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {"_meta": {"schema_version": 1, "last_updated": None}}
    except (OSError, ValueError):
        d = {"_meta": {"schema_version": 1, "last_updated": None}}

    # Ensure _meta key exists
    if "_meta" not in d or not isinstance(d.get("_meta"), dict):
        d["_meta"] = {"schema_version": 1, "last_updated": None}

    # Increment (guard: _meta namespace is not a counted path)
    if rel_path.startswith("_"):
        return None
    is_new_file = rel_path not in d
    d[rel_path] = d.get(rel_path, 0) + 1
    per_file_count = d[rel_path]
    # Session-wide distinct file count (excludes _meta bookkeeping key)
    session_count = sum(1 for k in d if k != "_meta")

    # Refresh _meta timestamp
    d["_meta"]["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"

    # Atomic write
    try:
        os.makedirs(os.path.dirname(counter_file), exist_ok=True)
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp_file, counter_file)  # atomic — POSIX rename(2) guarantee
    except OSError:
        # Mid-write failure: tmp orphaned, canonical counter unchanged (fail-open)
        return None

    return (per_file_count, session_count, is_new_file)


def emit_directive(rel_path, session_count, fire_sampling, fire_full):
    """Emit hookSpecificOutput.additionalContext dispatch directive to stdout.

    Single JSON payload regardless of how many thresholds tripped.
    Directive is reconciliation-capable (OQ-S-4 Path A / UD-6): dispatches
    agent-content-author to evaluate; does NOT prescribe edits.
    """
    # Build threshold description
    if fire_sampling and fire_full:
        thresholds = f"F={THRESHOLD_F} (sampling) AND E={THRESHOLD_E} (full eval)"
    elif fire_full:
        thresholds = f"E={THRESHOLD_E} (full eval)"
    else:
        thresholds = f"F={THRESHOLD_F} (sampling)"

    surface_token = _surface_token(rel_path)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    session_dir_hint = (
        f".agent_context/sessions/{session_id}" if session_id
        else ".agent_context/sessions/<session-id>"
    )

    directive_text = (
        "\n\n--- GESTALT AUDIT REQUIRED ---\n"
        "ORCHESTRATOR-ONLY DIRECTIVE — if you are a subagent, ignore this entirely; "
        "agent-content-author dispatch is caller-allowlisted to the orchestrator\n"
        f"Counter crossed threshold(s): {thresholds} at file {rel_path} (session distinct files: {session_count}).\n"
        "\n"
        "Dispatch the agent-content-author Opus agent in evaluation-mode to produce a\n"
        f"gestalt-eval sidecar at {session_dir_hint}/gestalt-eval-<TASK>.md before\n"
        "this turn ends. Reconcile if the evaluator flags changes-in-doubt; do NOT treat\n"
        "its output as edit prescriptions (per UD-6/OQ-S-4 Path A reconciliation-vs-prescription).\n"
        "\n"
        "Recommended delegation shape:\n"
        "  - subagent_type: agent-content-author\n"
        "  - mode: evaluation\n"
        "  - dispatch_mode: single   (or multi-body if multiple files crossed in this episode)\n"
        f"  - target_surface: {surface_token}\n"
        f"  - inputs: [{rel_path}]\n"
        "--- END GESTALT AUDIT REQUIRED ---\n"
    )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": directive_text,
        }
    }))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    exit_if_dispatched_child("agent-content-write-counter")
    # Step 1: parse stdin JSON (fail-open on parse error)
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return  # not orchestrator-depth — suppress directive emission (CYCLE-SUBAGENT-LEAK class)

    # Step 2: tool-name guard — positive allowlist (Signal-A-free idiom)
    tool_name = event.get("tool_name", "")
    if tool_name not in ALLOWED_TOOLS:
        return

    # Step 3: derive project root (three dirname calls on this file's absolute path)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Step 4: extract file_path from tool_input
    tool_input = event.get("tool_input") or {}
    written_path = tool_input.get("file_path", "")
    if not written_path:
        return  # MultiEdit edge case or non-file tool

    # Step 5: normalize to repo-relative path
    written_path = os.path.abspath(written_path)
    prefix = project_root + "/"
    if not written_path.startswith(prefix):
        return  # out-of-repo write — bail silent
    rel_path = written_path[len(prefix):]

    # Step 6: campaign-glob gate
    if not is_campaign_path(rel_path):
        return

    # Step 7: ensure audit dir exists
    os.makedirs(os.path.join(project_root, ".agent_context", "audit"), exist_ok=True)

    # Step 8: atomic counter increment (always runs — signal stays intact even under
    # suppression, per audit-counter-campaign-suspend enforcement contract)
    result = increment_counter(project_root, rel_path)
    if result is None:
        return  # increment failed (fail-open: no directive emitted)
    per_file_count, session_count, is_new_file = result

    # Step 9: sentinel suppression check + campaign-stamp sidecar
    session_dir = _session_dir(project_root)
    suppress_directive, campaign_suspend_active = _check_sentinels(session_dir)
    if campaign_suspend_active:
        _write_campaign_stamp(project_root, rel_path, per_file_count)
    if suppress_directive:
        return  # sweep-in-progress or campaign-rewrite — counter signal preserved, directive muted

    # Step 10: per-session cross-file threshold check and emit
    # Thresholds measure distinct files across the session; fire only when
    # a new distinct file is added so re-edits do not re-trigger.
    fire_sampling = is_new_file and (session_count % THRESHOLD_F == 0)
    fire_full = is_new_file and (session_count % THRESHOLD_E == 0)

    if fire_sampling or fire_full:
        emit_directive(rel_path, session_count, fire_sampling, fire_full)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-open: swallow all unhandled exceptions; never block the tool call
        pass
    sys.exit(0)
