#!/usr/bin/env python3
"""PreToolUse hook: warn when Write/Edit/MultiEdit/smart_write/smart_edit/smart_bash targets .claude/knowledge/**/*.md.

Guards the knowledge tool as the preferred (and audited) write path. Emits a
soft additionalContext warning pointing to knowledge(action='update') and
orchestrator-prompt.md §I + knowledge-resolution-policy.md. Always appends a belt-and-suspenders change-log
entry with actor="hook:belt-and-suspenders" so the knowledge-hygiene pipeline
sees out-of-band writes even if this hook is the only audit trail.

For mcp__context-tools__smart_bash: scans the shell command for common write-operation
shapes (>, >>, tee, sed -i, cp, mv, dd of=) and warns if any resolved target falls
under .claude/knowledge/**/*.md. Warn-only (exit 0) — does not catch obfuscated or
variable-built paths; a missed warning is a soft miss, not a correctness failure.

Excluded paths (no warning, no change-log entry):
  - .claude/knowledge/archive/**   (archival moves may use direct tools)
  - .claude/knowledge-log/.change-log.jsonl  (the log itself; lives in the sibling knowledge-log/ dir)
  - .claude/knowledge/.study-state      (written by bin/claude-study outside hook context)
  - .claude/knowledge/session-log/**    (knowledge-hygiene pipeline writes smart_write here; grandfathered)

Bootstrap bypass: if .agent_context/bootstrap-in-progress exists, suppress ALL
checks (warning, change-log, hard-refuse extensions) for bootstrap-extractor writes.

Warn-vs-deny: exits 0 (allow) for ordinary knowledge file writes. S2 audit shows
bootstrap-extractor.md uses native Write tool on .claude/knowledge/ux/ — deny
would break that writer. User memory feedback_warnings_over_denials.md mandates
soft warnings for the general case.

PARITY MIRROR — three sites to update in lockstep on any allow-list change:
  1. knowledge.ts:3906 (CL-7, actionConnectionAdd § Step 7: allowedCallers)
  2. knowledge.ts:4151 (CL-10, actionConnectionUpdate § CL-10 (mirrored from add): allowedCallers)
  3. knowledge-write-guard.py (CITES_ALLOW_LIST)

EXTENSION 1 — Allow-list mirror (defense-in-depth, A6 R2 § 2 + A7 Subtask 5):
When the target is .claude/knowledge/connections/edges.json AND the proposed content
contains a cites-type entry ("type": "cites"), the caller agent_id is checked
against the authorized allow-list {records-curator, cycling-promoter, main, test}.
Out-of-set callers are hard-refused (sys.exit(2)). When agent_id cannot be extracted
(e.g., native Edit/Write by the operator), falls back to warn-only (exit 0) per
A8 R2 § Legitimate-bypass: operator-direct edits must not be gated.

EXTENSION 2 — Co-citation pattern check (A8 R2 § Tier-misuse detection + A7 Subtask 5):
When the target is under constraints/, decisions/, or is edges.json, AND the proposed
content declares confidence_tier=mechanical in a <!-- record-meta --> block, the write
must carry at least one [verified: ...] citation matching Form 1 (function-anchor:
path (functionName)) OR Form 2 (section-anchor: path § Heading). Missing → hard-refuse
(sys.exit(2)) AND auto-emit a tier-misuse-detection finding to the pending-findings
JSONL. When agent_id cannot be extracted, falls back to warn-only (exit 0).

EXTENSION 3 — Apply-curator-verdicts profile gate (Wave-3 A1):
When the knowledge MCP tool receives action=apply-curator-verdicts, require
agent_id=curator-verdict-bridge, apply_profile=curator-verdict-cascade-v1, contained
profile paths, pinned record refs, and caller-visible verdicts only. When the bridge
identity invokes primitive knowledge actions, allow only the applier's closed side-effect
shapes and hard-refuse any general-purpose bridge write.

# TODO: pending-hook-findings.jsonl sink has no current consumer; see issue iss_610e2b1577dc
# The .agent_context/pending-hook-findings.jsonl file is written by _emit_tier_misuse_finding()
# but no downstream pipeline reads it yet. Filed as an issue; wiring the consumer is deferred.
"""
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone
from _caa_source_predicate import is_caa_source  # shared CAA-source predicate helper

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from caa_paths import main_root as _caa_main_root

_BIN_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "bin"
if str(_BIN_DIR) not in _sys.path:
    _sys.path.insert(0, str(_BIN_DIR))
from path_c_shared_state_guard import (  # noqa: E402
    PathCSharedStateError,
    ensure_parent_for_path,
    guard_path,
    guard_root,
)


# Tools matched by this hook (set for O(1) lookup)
GUARDED_TOOLS = {"Write", "Edit", "MultiEdit", "mcp__context-tools__smart_write", "mcp__context-tools__smart_edit"}

# smart_bash takes a separate code path (tool_input carries command, not file_path)
SMART_BASH_TOOL = "mcp__context-tools__smart_bash"

# knowledge() profile gate takes a separate code path (tool_input carries action, not file_path).
KNOWLEDGE_TOOL = "mcp__context-tools__knowledge"

# Excluded path prefixes/exact paths (relative to project root, forward-slash normalized)
EXCLUDED_PREFIXES = (
    ".claude/knowledge/archive/",
    ".claude/knowledge/session-log/",
)
# Note: .claude/knowledge-log/ is a sibling dir (not a child of .claude/knowledge/),
# so the primary startswith(".claude/knowledge/") guard in _is_guarded_knowledge_path
# already excludes it. No entry needed here for knowledge-log paths.
EXCLUDED_EXACT = {
    ".claude/knowledge/.study-state",
}

# Change-log location (mirrors knowledge.ts CHANGE_LOG_PATH)
CHANGE_LOG_PATH = ".claude/knowledge-log/.change-log.jsonl"

# Extension 1: edges.json allow-list (defense-in-depth mirror of server-side CL-7 + CL-10).
# PARITY MIRROR — three sites to update in lockstep on any allow-list change:
#   1. knowledge.ts:3906 (CL-7, actionConnectionAdd § Step 7: allowedCallers)
#   2. knowledge.ts:4151 (CL-10, actionConnectionUpdate § CL-10 (mirrored from add): allowedCallers)
#   3. knowledge-write-guard.py (CITES_ALLOW_LIST)
EDGES_PATH = ".claude/knowledge/connections/edges.json"
CITES_ALLOW_LIST = frozenset({"records-curator", "cycling-promoter", "main", "test"})

# Extension 3: A1 deterministic applier profile gate.
APPLIER_AGENT_ID = "curator-verdict-bridge"
APPLIER_ACTION = "apply-curator-verdicts"
APPLIER_PROFILE = "curator-verdict-cascade-v1"
APPLIER_EDGE_TYPES = frozenset({"obsoletes", "scoped-coexist"})
APPLIER_REPORT_PREFIX = ".claude/knowledge-log/mutations/reports/"
APPLIER_FINDINGS_PREFIX = ".agent_context/sessions/"
APPLIER_SENTINEL_NODE = "record:curator-obsolescence"
APPLIER_SOURCE_ID_RE = re.compile(r"^curator-[0-9a-f]{24}$")
EDGE_ID_RE = re.compile(r"^[0-9a-f]{64}$")

# Extension 2: strict surfaces for co-citation / mechanical-tier check.
STRICT_PREFIXES = (
    ".claude/knowledge/constraints/",
    ".claude/knowledge/decisions/",
)

# Pending-findings JSONL path for tier-misuse-detection auto-emit.
# Written here; consumed by knowledge-hygiene pipeline the next cycle.
PENDING_FINDINGS_PATH = ".agent_context/pending-hook-findings.jsonl"


def _project_root() -> str:
    # Delegates to caa_paths.main_root() which applies the same walk-up heuristic
    # (anchor on .claude/mcp or .claude/agents) with a fallback to the historical
    # 3-levels-up pattern. This replaces the bare os.path.dirname chain.
    return str(_caa_main_root())


def _worktree_root() -> str:
    return os.environ.get("CAA_WORKTREE_ROOT") or os.getcwd()


def _hard_refuse_path_c(exc: PathCSharedStateError) -> None:
    print(
        "PATH-C-SHARED-STATE-GUARD: "
        f"{exc.code}: {exc}. Restore the shared-state symlink before writing.",
        file=sys.stderr,
    )
    sys.exit(2)


def _guard_path_c_target(project_root: str, target: str, usage: str = "mutate") -> None:
    abs_target = target if os.path.isabs(target) else os.path.join(project_root, target)
    try:
        guard_path(
            abs_target,
            main_root=project_root,
            worktree_root=_worktree_root(),
            usage=usage,
        )
        if _normalize_path(abs_target, project_root).startswith(".claude/knowledge/"):
            guard_root(
                ".claude/knowledge-log",
                main_root=project_root,
                worktree_root=_worktree_root(),
                usage="mutate",
            )
    except PathCSharedStateError as exc:
        _hard_refuse_path_c(exc)


def _normalize_path(file_path: str, project_root: str) -> str:
    """Return forward-slash relative path from project root."""
    if os.path.isabs(file_path):
        try:
            rel = os.path.relpath(file_path, project_root)
        except ValueError:
            return file_path
    else:
        rel = file_path
    return rel.replace("\\", "/")


def _is_excluded(rel_path: str) -> bool:
    """Return True if this path is in an excluded location."""
    if rel_path in EXCLUDED_EXACT:
        return True
    for prefix in EXCLUDED_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    return False


def _is_guarded_knowledge_path(rel_path: str) -> bool:
    """Return True if path matches .claude/knowledge/**/*.md and is not excluded."""
    if not rel_path.startswith(".claude/knowledge/"):
        return False
    if not rel_path.endswith(".md"):
        return False
    if _is_excluded(rel_path):
        return False
    return True


def _append_change_log(
    project_root: str,
    rel_path: str,
    session_id: str | None,
    tool_name: str,
    tool_input: dict,
) -> None:
    """Append a belt-and-suspenders change-log entry for this write event.

    Writes a ChangeLogEntry-compatible JSONL line (schema_v=1). Never raises —
    change-log failures must not block the primary tool call.
    """
    log_abs = os.path.join(project_root, CHANGE_LOG_PATH)
    try:
        ensure_parent_for_path(log_abs, main_root=project_root, worktree_root=_worktree_root())
    except (OSError, PathCSharedStateError):
        return  # Can't create dir — silently skip

    episode_raw = os.environ.get("EPISODE", "")
    try:
        episode = int(episode_raw) if episode_raw.strip() else None
    except (ValueError, AttributeError):
        episode = None

    # Map guarded tool name → operation value, using os.path.exists(abs_path) to
    # discriminate create vs update for Write/smart_write.
    abs_path = os.path.join(project_root, rel_path)
    if tool_name in ("Write", "mcp__context-tools__smart_write"):
        operation = "create" if not os.path.exists(abs_path) else "update-full-replace"
    elif tool_name in ("Edit", "MultiEdit", "mcp__context-tools__smart_edit"):
        operation = "update-replace-section"
    else:
        operation = "log-external-write"  # unknown-tool fallback

    # Capture bytes_written from content (Write/smart_write) or new_string (Edit/MultiEdit).
    if "content" in tool_input:
        bytes_written = len(tool_input["content"].encode("utf-8"))
    elif "new_string" in tool_input:
        bytes_written = len(tool_input["new_string"].encode("utf-8"))
    else:
        bytes_written = 0  # MultiEdit with no string fields available — rare

    entry = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session_id,
        "episode": episode,
        "file": rel_path,
        "section": None,
        "operation": operation,
        "status": "success",
        "source_finding_ids": [],
        "actor": "hook:belt-and-suspenders",
        "bytes_written": bytes_written,
        "schema_v": 1,
    }
    try:
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with open(log_abs, "a", encoding="utf-8") as f:
            f.write(line)
    except (OSError, TypeError):
        return  # Never crash the hook


# ---------------------------------------------------------------------------
# smart_bash write-target extraction (F23 gate)
# ---------------------------------------------------------------------------

def _extract_write_targets(command: str) -> list:
    """Return a list of candidate write-target path strings from a shell command.

    Conservative allow-listed parser: only matches literal write shapes. Read-only
    commands (cat, grep, ls, etc.) produce zero results because they carry no
    write-shape match. Obfuscated paths (variable-built, bash -c subshells) are
    intentionally NOT caught — missed warnings are soft misses for this warn-only gate.
    """
    import shlex
    targets = []

    # --- output redirection: > and >> ---
    # Find all occurrences of > or >> followed by a path token.
    import re
    # Match >> before > to avoid partial match confusion.
    # Negative lookbehind: don't match 2>&1 or heredoc <<
    for m in re.finditer(r'(?<![<2])>>?\s*([^\s|&;><]+)', command):
        token = m.group(1).strip()
        if token:
            targets.append(token)

    # --- tee [flags] <path> ... ---
    # Match `tee` or `tee -a` followed by path args (stop at pipe/semicolon/ampersand).
    for m in re.finditer(r'\btee(?:\s+-a)?\s+([^\s|&;><][^\s|&;><]*(?:\s+[^\s|&;><-][^\s|&;><]*)*)', command):
        # tee can accept multiple path args; split them
        for part in m.group(1).split():
            if not part.startswith('-'):
                targets.append(part)

    # --- sed -i[.suffix] ... <file> ---
    # sed -i or sed --in-place, optional backup suffix, then pattern, then file operand.
    for m in re.finditer(r'\bsed\s+(?:--in-place|-i(?:\.\S+)?)\s+\S+\s+([^\s|&;><]+)', command):
        targets.append(m.group(1).strip())

    # --- cp/mv <src> <dest>  (capture LAST positional = destination) ---
    # Only match when there are at least two non-flag tokens after cp/mv.
    for m in re.finditer(r'\b(?:cp|mv)\s+((?:[^\s|&;><][^\s|&;><]*\s+)+)([^\s|&;><]+)', command):
        # m.group(2) is the last token = destination
        targets.append(m.group(2).strip())

    # --- dd of=<path> ---
    for m in re.finditer(r'\bdd\b[^|&;]*\bof=([^\s|&;><]+)', command):
        targets.append(m.group(1).strip())

    return targets


def _handle_smart_bash(event: dict, tool_input: dict, project_root: str, session_id) -> None:
    """Warn if a smart_bash command targets .claude/knowledge/**/*.md via a write-shape."""
    command = tool_input.get("command", "")
    if not command:
        return

    candidates = _extract_write_targets(command)
    guarded = []
    for target in candidates:
        rel = _normalize_path(target, project_root)
        if _is_guarded_knowledge_path(rel):
            _guard_path_c_target(project_root, target, "mutate")
            guarded.append(rel)

    if not guarded:
        return

    targets_str = ", ".join(guarded)
    warning = (
        f"--- KNOWLEDGE WRITE GUARD (smart_bash) ---\n"
        f"Shell command targets knowledge file(s) via a write-operation shape: {targets_str}\n"
        f"Preferred write path: knowledge(action='update') (mode=append|replace|replace_section) — \n"
        f"this routes through the knowledge tool, appends a verified change-log entry, and \n"
        f"enforces edit-discipline rules.\n"
        f"Reference: .claude/orchestrator-prompt.md §I + .claude/knowledge/decisions/knowledge-resolution-policy.md\n"
        f"If you must use a shell write here, also call \n"
        f"knowledge(action='log-external-write') to record the out-of-band write.\n"
        f"--- END KNOWLEDGE WRITE GUARD ---"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": warning,
        }
    }))

    # Belt-and-suspenders change-log entry per guarded target
    for rel in guarded:
        _append_change_log(project_root, rel, session_id, SMART_BASH_TOOL, tool_input)


# ---------------------------------------------------------------------------
# Extension 1: Allow-list mirror helpers
# ---------------------------------------------------------------------------

def _is_edges_path(rel_path: str) -> bool:
    """Return True if the target is exactly the edges.json connection store."""
    return rel_path == EDGES_PATH


def _has_cites_in_parsed(obj: object) -> bool:
    """Recursively check whether any dict in obj has type == 'cites'."""
    if isinstance(obj, dict):
        if obj.get("type") == "cites":
            return True
        return any(_has_cites_in_parsed(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_cites_in_parsed(item) for item in obj)
    return False


def _content_has_cites_edge(content: str) -> bool:
    """Check whether proposed content adds an edge with type='cites'.

    Tries strict JSON parse first; falls back to substring on parse failure
    (e.g., Edit fragments that are not standalone JSON documents).
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # Fallback for Edit fragments — substring match. Acknowledged limitation:
        # may false-positive on a `notes` field literally containing `"type": "cites"`.
        return '"type": "cites"' in content or '"type":"cites"' in content
    # Strict path: traverse parsed structure for any edge with type='cites'.
    return _has_cites_in_parsed(parsed)


def _get_proposed_content(tool_input: dict) -> str:
    """Extract the proposed content text from tool_input for any guarded tool."""
    content = tool_input.get("content", "")
    if content:
        return content
    # Edit/MultiEdit/smart_edit: use new_string
    new_string = tool_input.get("new_string", "")
    if new_string:
        return new_string
    # MultiEdit: edits array — join all new_strings
    edits = tool_input.get("edits", [])
    if isinstance(edits, list):
        parts = [e.get("new_string", "") for e in edits if isinstance(e, dict)]
        return "\n".join(p for p in parts if p)
    return ""


# ---------------------------------------------------------------------------
# Extension 3: apply-curator-verdicts profile gate helpers
# ---------------------------------------------------------------------------

def _deny_knowledge_profile(reason: str) -> None:
    """Hard-refuse an unauthorized knowledge() profile or bridge-primitive call."""
    sys.stderr.write(f"knowledge-write-guard [DENY]: apply-curator-verdicts profile rejected: {reason}.\n")
    sys.exit(2)


def _contained_rel_path(candidate: object, project_root: str) -> str | None:
    """Return a project-root relative path when candidate stays inside the project."""
    if not isinstance(candidate, str) or candidate.strip() == "":
        return None
    abs_candidate = candidate if os.path.isabs(candidate) else os.path.join(project_root, candidate)
    try:
        rel = os.path.relpath(os.path.abspath(abs_candidate), os.path.abspath(project_root))
    except ValueError:
        return None
    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        return None
    return rel.replace("\\", "/")


def _slugify_heading(heading: str) -> str:
    slug = heading.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return re.sub(r"-+", "-", slug)


def _knowledge_record_path(candidate: object, project_root: str) -> str | None:
    rel = _contained_rel_path(candidate, project_root)
    if rel is None:
        return None
    if not rel.startswith(".claude/knowledge/") or not rel.endswith(".md"):
        return None
    if _is_excluded(rel):
        return None
    return rel


def _is_pinned_record_ref_id(value: object, project_root: str) -> bool:
    if not isinstance(value, str) or "#" not in value:
        return False
    path_part, slug = value.rsplit("#", 1)
    rel = _knowledge_record_path(path_part, project_root)
    return rel is not None and slug != "" and value == f"{rel}#{slug}"


def _validate_verdict_ref(ref: object, project_root: str, label: str) -> str | None:
    if not isinstance(ref, dict):
        return f"{label}.ref must be an object"
    ref_path = _knowledge_record_path(ref.get("path"), project_root)
    if ref_path is None:
        return f"{label}.ref.path must be a contained .claude/knowledge/*.md path"
    heading = ref.get("heading")
    if not isinstance(heading, str) or heading.strip() == "":
        return f"{label}.ref.heading must be non-empty"
    if ref.get("level") != 2:
        return f"{label}.ref.level must be 2"
    expected = f"{ref_path}#{_slugify_heading(heading)}"
    if ref.get("id") != expected:
        return f"{label}.ref.id must equal pinned record id {expected}"
    return None


def _validate_optional_record_id(value: object, project_root: str, label: str, allow_sentinel: bool = False) -> str | None:
    if value is None:
        return None
    if allow_sentinel and value == APPLIER_SENTINEL_NODE:
        return None
    if not _is_pinned_record_ref_id(value, project_root):
        return f"{label} must be a pinned .claude/knowledge record id"
    return None


def _validate_profile_paths(tool_input: dict, project_root: str) -> str | None:
    report_path = tool_input.get("apply_report_path")
    if report_path is not None:
        rel = _contained_rel_path(report_path, project_root)
        if rel is None or not rel.startswith(APPLIER_REPORT_PREFIX) or not rel.endswith(".apply-report.json"):
            return "apply_report_path must stay under .claude/knowledge-log/mutations/reports/*.apply-report.json"

    findings_dir = tool_input.get("findings_dir")
    if findings_dir is not None:
        rel = _contained_rel_path(findings_dir, project_root)
        if rel is None or not rel.startswith(APPLIER_FINDINGS_PREFIX) or not rel.endswith("/findings"):
            return "findings_dir must stay under .agent_context/sessions/<session>/findings"
        parts = rel.split("/")
        if len(parts) != 4 or not re.match(r"^[A-Za-z0-9._-]+$", parts[2]):
            return "findings_dir must name exactly one safe session findings directory"
    return None


def _validate_apply_profile_call(tool_input: dict, project_root: str) -> None:
    if tool_input.get("agent_id") != APPLIER_AGENT_ID:
        _deny_knowledge_profile(f"action={APPLIER_ACTION!r} requires agent_id={APPLIER_AGENT_ID!r}")
    if tool_input.get("apply_profile") != APPLIER_PROFILE:
        _deny_knowledge_profile(f"action={APPLIER_ACTION!r} requires apply_profile={APPLIER_PROFILE!r}")
    if "primitives" in tool_input:
        _deny_knowledge_profile("callers may supply verdicts only; cascade primitives are internal to the applier")

    verdicts = tool_input.get("verdicts")
    if not isinstance(verdicts, list) or len(verdicts) == 0:
        _deny_knowledge_profile("verdicts must be a non-empty array")

    path_error = _validate_profile_paths(tool_input, project_root)
    if path_error is not None:
        _deny_knowledge_profile(path_error)

    for idx, verdict in enumerate(verdicts):
        if not isinstance(verdict, dict):
            _deny_knowledge_profile(f"verdicts[{idx}] must be an object")
        ref_error = _validate_verdict_ref(verdict.get("ref"), project_root, f"verdicts[{idx}]")
        if ref_error is not None:
            _deny_knowledge_profile(ref_error)
        for key in ("winner_ref", "loser_ref", "peer_ref"):
            err = _validate_optional_record_id(verdict.get(key), project_root, f"verdicts[{idx}].{key}")
            if err is not None:
                _deny_knowledge_profile(err)
        obsoletes_emit = verdict.get("obsoletes_emit")
        if obsoletes_emit is not None:
            if not isinstance(obsoletes_emit, dict):
                _deny_knowledge_profile(f"verdicts[{idx}].obsoletes_emit must be an object")
            err = _validate_optional_record_id(
                obsoletes_emit.get("from"),
                project_root,
                f"verdicts[{idx}].obsoletes_emit.from",
                allow_sentinel=True,
            )
            if err is not None:
                _deny_knowledge_profile(err)


def _validate_bridge_update(tool_input: dict, project_root: str) -> None:
    if tool_input.get("mode") != "replace_section":
        _deny_knowledge_profile("bridge update is restricted to mode='replace_section' record-meta writes")
    rel = _knowledge_record_path(tool_input.get("path"), project_root)
    if rel is None:
        _deny_knowledge_profile("bridge update path must be a contained .claude/knowledge/*.md path")
    section = tool_input.get("section")
    if not isinstance(section, str) or section.strip() == "":
        _deny_knowledge_profile("bridge update must name a non-empty section")
    expected_id = f"{rel}#{_slugify_heading(section)}"
    if tool_input.get("source_finding_ids") != [expected_id]:
        _deny_knowledge_profile(f"bridge update source_finding_ids must be exactly [{expected_id!r}]")
    if "<!-- record-meta" not in str(tool_input.get("content", "")):
        _deny_knowledge_profile("bridge update content must carry a record-meta block")


def _validate_bridge_connection_add(tool_input: dict, project_root: str) -> None:
    edge_type = tool_input.get("type")
    if edge_type not in APPLIER_EDGE_TYPES:
        _deny_knowledge_profile("bridge connection-add is restricted to obsoletes/scoped-coexist edges")
    if not isinstance(tool_input.get("citation"), str) or tool_input.get("citation", "").strip() == "":
        _deny_knowledge_profile("bridge connection-add requires a citation")
    from_err = _validate_optional_record_id(
        tool_input.get("from"),
        project_root,
        "connection-add.from",
        allow_sentinel=edge_type == "obsoletes",
    )
    if from_err is not None:
        _deny_knowledge_profile(from_err)
    to_err = _validate_optional_record_id(tool_input.get("to"), project_root, "connection-add.to")
    if to_err is not None:
        _deny_knowledge_profile(to_err)


def _validate_bridge_connection_delete(tool_input: dict) -> None:
    edge_id = tool_input.get("id")
    if not isinstance(edge_id, str) or EDGE_ID_RE.match(edge_id) is None:
        _deny_knowledge_profile("bridge connection-delete requires a server-computed 64-hex edge id")


def _validate_bridge_log_external_write(tool_input: dict, project_root: str) -> None:
    if tool_input.get("operation") != APPLIER_ACTION:
        _deny_knowledge_profile("bridge log-external-write operation must be apply-curator-verdicts")
    if tool_input.get("source") != APPLIER_AGENT_ID:
        _deny_knowledge_profile("bridge log-external-write source must be curator-verdict-bridge")
    rel = _contained_rel_path(tool_input.get("file"), project_root)
    if rel is None:
        _deny_knowledge_profile("bridge log-external-write file must stay inside the project")

    source_ids = tool_input.get("source_finding_ids") or []
    if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
        _deny_knowledge_profile("bridge log-external-write source_finding_ids must be a string array")

    if rel == EDGES_PATH:
        if source_ids:
            _deny_knowledge_profile("cites re-key audit rows must not claim source_finding_ids")
        return

    if rel.startswith(APPLIER_REPORT_PREFIX) and rel.endswith(".apply-report.json"):
        if len(source_ids) != 1 or APPLIER_SOURCE_ID_RE.match(source_ids[0]) is None:
            _deny_knowledge_profile("apply-report audit rows must pin exactly one curator-<24hex> source id")
        return

    _deny_knowledge_profile("bridge log-external-write is restricted to edges.json or mutation apply reports")


def _validate_bridge_primitive_call(tool_input: dict, project_root: str) -> None:
    action = tool_input.get("action")
    if action == "update":
        _validate_bridge_update(tool_input, project_root)
    elif action == "connection-add":
        _validate_bridge_connection_add(tool_input, project_root)
    elif action == "connection-delete":
        _validate_bridge_connection_delete(tool_input)
    elif action == "log-external-write":
        _validate_bridge_log_external_write(tool_input, project_root)
    else:
        _deny_knowledge_profile(f"bridge agent_id may not call knowledge(action={action!r})")


def _handle_knowledge_tool(event: dict, tool_input: dict, project_root: str) -> None:
    action = tool_input.get("action")
    agent_id = tool_input.get("agent_id", "") or event.get("agent_id", "") or ""

    if action == APPLIER_ACTION:
        _validate_apply_profile_call(tool_input, project_root)
        return

    if "apply_profile" in tool_input or "verdicts" in tool_input:
        _deny_knowledge_profile("apply_profile/verdicts are accepted only with action='apply-curator-verdicts'")

    if agent_id == APPLIER_AGENT_ID:
        _validate_bridge_primitive_call(tool_input, project_root)


# ---------------------------------------------------------------------------
# Extension 2: Co-citation pattern check helpers
# ---------------------------------------------------------------------------

def _is_strict_surface(rel_path: str) -> bool:
    """Return True if rel_path is a strict surface: constraints/, decisions/, or edges.json."""
    if rel_path == EDGES_PATH:
        return True
    for prefix in STRICT_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    return False


# DUPLICATED: see knowledge.ts § parseRecordMeta (record-meta-parser.ts) — Python/TypeScript
# split per A8 R2 § Integration Points. Minimal subset: only extracts confidence_tier.
_RECORD_META_BLOCK_RE = __import__("re").compile(r"<!--\s*record-meta\s*([\s\S]*?)\s*-->")
_FIELD_RE = __import__("re").compile(r"^([a-z_]+)\s*:\s*(.+)$")


def _extract_confidence_tier(content: str) -> str | None:
    """Return the confidence_tier value from a <!-- record-meta --> block, or None if absent.

    # DUPLICATED: see .claude/mcp/context-tools/src/lib/record-meta-parser.ts (parseRecordMeta)
    # Python/TypeScript split per A8 R2 § Integration Points — sharing impractical across runtimes.
    """
    match = _RECORD_META_BLOCK_RE.search(content)
    if match is None:
        return None
    block = match.group(1)
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fm = _FIELD_RE.match(line)
        if fm and fm.group(1) == "confidence_tier":
            return fm.group(2).strip()
    return None


def _has_qualifying_citation(content: str) -> bool:
    """Return True if content contains at least one Form 1 or Form 2 verified citation.

    Per A8 R2 § Tier-misuse detection co-citation pattern (line 99):
    - Form 1: [verified: path (functionName)]  — function-anchor, identifier-only paren
    - Form 2: [verified: path § Heading]       — section-anchor with § separator

    The semantics: "if you claim this write is mechanical, the claim must be anchored
    to a concrete code/markdown surface, not a free-form interpretive claim."
    Forms 3/4/5 (grep-fragment, line-approximation, web-source) are NOT accepted —
    they are less-anchored and do not satisfy the concreteness requirement.
    """
    import re as _re
    # Match all [verified: ...] citations in the content
    pattern = _re.compile(r"\[verified:\s*(.+?)\]")
    for m in pattern.finditer(content):
        inner = m.group(1).strip()
        # Form 2: path § Heading — section-anchor
        if " § " in inner:
            return True
        # Form 1: path (functionName) — identifier-only paren content
        form_paren = _re.match(r"^(.+?)\s*\(([^)]+)\)$", inner)
        if form_paren:
            paren_content = form_paren.group(2).strip()
            # Form 1 requires identifier-only: [A-Za-z_]\w* with no spaces/special chars.
            # Form 3 (grep-fragment) has spaces or non-identifier chars — rejected here.
            if _re.match(r"^[A-Za-z_]\w*$", paren_content):
                return True
    return False


def _emit_tier_misuse_finding(
    project_root: str, target_file: str, session_id: str | None, agent_id: str = ""
) -> None:
    """Append a tier-misuse-detection finding to the pending-findings JSONL.

    Written to PENDING_FINDINGS_PATH; uses append semantics so multiple violations
    in a session accumulate. Never raises — finding emission failures must not block
    the primary refuse path.

    # TODO: pending-hook-findings.jsonl sink has no current consumer; see issue iss_610e2b1577dc
    # Filed to track consumer-wiring gap in the knowledge-hygiene pipeline.
    """
    findings_abs = os.environ.get("PENDING_FINDINGS_PATH") or os.path.join(project_root, PENDING_FINDINGS_PATH)
    findings_dir = os.path.dirname(findings_abs)
    try:
        ensure_parent_for_path(findings_abs, main_root=project_root, worktree_root=_worktree_root())
    except OSError:
        return  # Can't create dir — skip
    except PathCSharedStateError:
        return  # Broken Path-C session state — primary refusal already carries the signal.

    from datetime import datetime, timezone as _tz
    entry = {
        "ts": datetime.now(_tz.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session_id,
        "actor": "hook:tier-misuse-detection",
        "topic": "tier-misuse-detection",
        "tags": ["gotcha", "gaming"],
        "durability": "campaign",
        "content": (
            f"knowledge-write-guard: agent '{agent_id}' attempted a direct write to strict "
            f"surface '{target_file}' declaring confidence_tier=mechanical in <!-- record-meta --> "
            "but the write contains no qualifying Form 1 (function-anchor) or Form 2 (section-anchor) "
            "[verified: ...] citation. Write hard-refused. "
            "Fix hint: add a [verified: path (functionName)] or [verified: path § Heading] "
            "citation before retrying. "
            "Reference: A8 R2 § Tier-misuse detection (line 99)."
        ),
        "evidence": "[verified: .agent_context/sessions/.../A8-curator-failure-bypass-design-R2.md § Tier-misuse detection (line 106)]",
    }
    try:
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with open(findings_abs, "a", encoding="utf-8") as f:
            f.write(line)
    except (OSError, TypeError):
        return  # Never crash the hook


def main() -> None:
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = event.get("tool_name", "")

    # smart_bash dispatch — BEFORE the GUARDED_TOOLS membership return so it is reachable.
    # smart_bash tool_input carries `command`, not `file_path`, so it cannot use the
    # file_path flow below. This block is wholly self-contained and returns before any
    # file_path/edges/strict-surface logic runs.
    if tool_name == SMART_BASH_TOOL:
        tool_input = event.get("tool_input") or {}
        project_root = _project_root()
        # Bootstrap bypass — same sentinel + same project_root semantics as the
        # file_path path below (lines ~398-400 in the original source); carried here
        # because that check sits AFTER the file_path guard smart_bash cannot pass.
        bootstrap_sentinel = os.path.join(project_root, ".agent_context", "bootstrap-in-progress")
        if os.path.exists(bootstrap_sentinel):
            return
        session_id = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("SESSION_ID") or None
        _handle_smart_bash(event, tool_input, project_root, session_id)
        return

    if tool_name == KNOWLEDGE_TOOL:
        tool_input = event.get("tool_input") or {}
        project_root = _project_root()
        bootstrap_sentinel = os.path.join(project_root, ".agent_context", "bootstrap-in-progress")
        if os.path.exists(bootstrap_sentinel):
            return
        _handle_knowledge_tool(event, tool_input, project_root)
        return

    if tool_name not in GUARDED_TOOLS:
        return

    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return

    project_root = _project_root()
    rel_path = _normalize_path(file_path, project_root)

    session_id = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("SESSION_ID") or None

    # Bootstrap bypass: suppress ALL checks when bootstrap is in progress.
    # The bootstrap-extractor runs before the knowledge infrastructure is fully set up;
    # refusing its writes would break the initial project setup.
    bootstrap_sentinel = os.path.join(project_root, ".agent_context", "bootstrap-in-progress")
    if os.path.exists(bootstrap_sentinel):
        return

    _guard_path_c_target(project_root, file_path, "mutate")

    # -----------------------------------------------------------------------
    # Extension 1: Allow-list mirror for direct cites-edge writes to edges.json.
    # Defense-in-depth: catches direct file writes that bypass the MCP tool's
    # server-side check at knowledge.ts:3906 (CL-7) + knowledge.ts:4151 (CL-10).
    # -----------------------------------------------------------------------
    if _is_edges_path(rel_path):
        proposed = _get_proposed_content(tool_input)
        if _content_has_cites_edge(proposed):
            agent_id = tool_input.get("agent_id", "") or event.get("agent_id", "") or ""
            if agent_id == "":
                # agent_id absent → native Edit/Write by operator; degrade to warn-only.
                # Per A8 R2 § Legitimate-bypass: operator-direct edits must not be gated.
                sys.stderr.write(
                    f"knowledge-write-guard [WARN]: direct write to {rel_path} with "
                    f"type='cites' but agent_id could not be determined (native tool). "
                    f"If this is an agent write, pass agent_id in tool_input. "
                    f"Parity mirror: knowledge.ts:3906 (CL-7, actionConnectionAdd § Step 7: allowedCallers) "
                    f"+ knowledge.ts:4151 (CL-10, actionConnectionUpdate § CL-10 (mirrored from add): allowedCallers) "
                    f"+ knowledge-write-guard.py (CITES_ALLOW_LIST).\n"
                )
                # Allow — operator-direct bypass (A8 R2 § Legitimate-bypass)
            elif agent_id not in CITES_ALLOW_LIST:
                sys.stderr.write(
                    f"knowledge-write-guard [DENY]: direct write to {rel_path} with "
                    f"type='cites' rejected for agent_id={agent_id!r}. "
                    f"Allow-list: {{records-curator, cycling-promoter, main, test}}. "
                    f"Parity mirror: knowledge.ts:3906 (CL-7, actionConnectionAdd § Step 7: allowedCallers) "
                    f"+ knowledge.ts:4151 (CL-10, actionConnectionUpdate § CL-10 (mirrored from add): allowedCallers) "
                    f"+ knowledge-write-guard.py (CITES_ALLOW_LIST). "
                    f"Use knowledge(action='connection-add') or knowledge(action='connection-update') "
                    f"from an authorized agent.\n"
                )
                sys.exit(2)
        # edges.json is not a .md file — skip warning path below
        return

    # -----------------------------------------------------------------------
    # Extension 2: Co-citation pattern check for mechanical-tier on strict surfaces.
    # Per A8 R2 § Tier-misuse detection: confidence_tier=mechanical on a strict
    # surface (constraints/, decisions/, edges.json) requires a qualifying Form 1
    # (function-anchor) or Form 2 (section-anchor) citation in the write content.
    # -----------------------------------------------------------------------
    if _is_strict_surface(rel_path):
        proposed = _get_proposed_content(tool_input)
        tier = _extract_confidence_tier(proposed)
        if tier == "mechanical" and not _has_qualifying_citation(proposed):
            agent_id = tool_input.get("agent_id", "") or event.get("agent_id", "") or ""
            if agent_id == "":
                # agent_id absent → native Edit/Write by operator; degrade to warn-only.
                # Per A8 R2 § Legitimate-bypass: operator-direct edits must not be gated.
                sys.stderr.write(
                    f"knowledge-write-guard [WARN]: write to strict surface '{rel_path}' declares "
                    f"confidence_tier=mechanical but contains no qualifying Form 1/2 citation "
                    f"and agent_id could not be determined (native tool). "
                    f"If this is an agent write, pass agent_id in tool_input.\n"
                )
                # Allow — operator-direct bypass (A8 R2 § Legitimate-bypass)
            else:
                # Emit finding before refusing (finding emit is best-effort; never crashes)
                _emit_tier_misuse_finding(project_root, rel_path, session_id, agent_id)
                sys.stderr.write(
                    f"knowledge-write-guard [DENY]: write to strict surface '{rel_path}' declares "
                    f"confidence_tier=mechanical in <!-- record-meta --> but contains no qualifying "
                    f"Form 1 (function-anchor) or Form 2 (section-anchor) [verified: ...] citation. "
                    f"Add a [verified: path (functionName)] or [verified: path § Heading] citation and retry. "
                    f"Reference: A8 R2 § Tier-misuse detection (line 99).\n"
                )
                sys.exit(2)

    # -----------------------------------------------------------------------
    # Original guard: warn on .md knowledge files and log belt-and-suspenders entry.
    # -----------------------------------------------------------------------
    if not _is_guarded_knowledge_path(rel_path):
        return

    # Emit warning via additionalContext (hooks-behavior.md confirmed mechanism)
    warning = (
        f"--- KNOWLEDGE WRITE GUARD ---\n"
        f"Direct write to knowledge file detected: {rel_path}\n"
        f"Preferred write path: knowledge(action='update') (mode=append|replace|replace_section) — "
        f"this routes through the knowledge tool, appends a verified change-log entry, and "
        f"enforces edit-discipline rules.\n"
        f"Reference: .claude/orchestrator-prompt.md §I + .claude/knowledge/decisions/knowledge-resolution-policy.md\n"
        f"If you must use a direct write tool here, ensure you also call "
        f"knowledge(action='log-external-write') to record the out-of-band write.\n"
        f"--- END KNOWLEDGE WRITE GUARD ---"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": warning,
        }
    }))

    # Belt-and-suspenders change-log entry — always written regardless of warn/deny
    _append_change_log(project_root, rel_path, session_id, tool_name, tool_input)

    # CAA-source carve gate (INSTALL-V2 subtask 7).
    # Per .claude/knowledge/decisions/install-carve-mechanism.md § "CAA-source predicate (canonical)".
    # The `audience:` discipline applies only in CAA source. The block below is the
    # forward-compatible insertion point for any audience-related warning logic added
    # by future plans (decision #21 in plan-INSTALL-V2.md). Today the body is empty
    # because no audience-warning emission exists yet. The gateway warning above is
    # NOT carve-gated — it cites orchestrator-prompt.md §I + knowledge-resolution-policy.md,
    # both of which ship to target projects.
    in_caa_source = is_caa_source(pathlib.Path(project_root))
    if in_caa_source:
        # FUTURE: audience-related warning emission goes here. See decision #21
        # in .agent_context/plan-INSTALL-V2.md and rule file § "CAA-source predicate (canonical)".
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never crash — always exit 0; log to stderr so internal errors are discoverable
        sys.stderr.write(f"knowledge-write-guard: internal error, skipping: {e}\n")
        pass
