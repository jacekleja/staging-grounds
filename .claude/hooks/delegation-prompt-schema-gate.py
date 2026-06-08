#!/usr/bin/env python3
# dispatch-child-safe: false
"""Schema-gate hook for Agent delegation prompts.

Mechanism: Axis C (exit-2 + stderr) per Session 2 recommendation.
Platform: Claude Code >= v2.1.90 required for exit-2 to block PreToolUse/Agent.

Block conditions (for agents in SCHEMA_GATED_AGENTS):
  - universal field missing
  - conditional field missing
  - prohibited field present (JSON-mode prompts only)

Fail-open conditions (degrade to advisory, exit 0):
  - YAML matrix unparseable (returns None from _load_matrix)
  - Claude Code version below v2.1.90 (version probe writes sentinel and emits advisory)
  - Bypass token present (<!-- SCHEMA-BYPASS: <reason> --> within first 20 lines)

See: .claude/knowledge/reference/delegation-prompt-schema.md §2 for the YAML matrix
     schema-gate-beta-recommendation.md (Session 2 β research) for the mechanism rationale
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml

# Ensure sibling modules resolve regardless of cwd at hook-fire time.
# Same pattern as write-gate.py and knowledge-write-guard.py.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HOOKS_DIR)

from delegation_prompt_parser import parse_prompt, validate_schema, SchemaError
from _dispatch_child_guard import exit_if_dispatched_child


# The five canonical schema consumers. Hard-coded — do NOT derive from YAML.
# If a new canonical consumer is added, update this set AND the YAML block AND
# run bin/test_schema_gate_matrix_sync.py.
SCHEMA_GATED_AGENTS = {"planner", "validator", "pre-flight-gate", "coherence-auditor", "design-planner", "agent-content-author", "surface-gate"}

SCHEMA_DOC_REL = ".claude/knowledge/reference/delegation-prompt-schema.md"
SCHEMA_REF = f"{SCHEMA_DOC_REL} §2"

_BYPASS_TOKEN_RE = re.compile(
    r'^[ \t]*<!-- SCHEMA-BYPASS: ([^>]*) -->\s*$'
)

# Set to True by the version probe when Claude Code is below v2.1.90 or undetectable.
# Causes the failure path to degrade to advisory (_warn) instead of hard-block (_block).
_DEGRADED = False


def _find_schema_doc(cwd: str) -> str:
    """Return absolute path to the schema doc."""
    return os.path.join(cwd, SCHEMA_DOC_REL)


def _load_matrix(schema_path: str):
    """Load the YAML matrix block from the schema doc.

    Returns a dict with universal_required on success. Any agents block is ignored.
    Returns None on any failure (parse error, missing markers, missing file).
    Emits a single stderr diagnostic on failure.
    """
    try:
        with open(schema_path) as f:
            content = f.read()
    except OSError as e:
        print(f"[schema-gate] degraded: cannot read schema doc ({e}) — gate is advisory-only this fire",
              file=sys.stderr)
        return None

    begin_marker = "<!-- schema-gate-matrix:begin -->"
    end_marker = "<!-- schema-gate-matrix:end -->"

    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)

    if begin_idx == -1 or end_idx == -1 or end_idx <= begin_idx:
        print("[schema-gate] degraded: YAML matrix markers not found — gate is advisory-only this fire",
              file=sys.stderr)
        return None

    yaml_text = content[begin_idx + len(begin_marker):end_idx]

    try:
        matrix = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        print(f"[schema-gate] degraded: YAML parse error ({e}) — gate is advisory-only this fire",
              file=sys.stderr)
        return None

    if not isinstance(matrix, dict):
        print("[schema-gate] degraded: YAML matrix is not a dict — gate is advisory-only this fire",
              file=sys.stderr)
        return None

    if "universal_required" not in matrix:
        print("[schema-gate] degraded: YAML matrix missing universal_required — gate is advisory-only this fire",
              file=sys.stderr)
        return None

    return matrix


def _detect_bypass(prompt: str):
    """Check for bypass token in first 20 lines of prompt.

    Returns (bypass_active: bool, reason: str).
    - 0 matches → (False, "")
    - 1 match   → (True, reason_string)
    - 2+ matches → (False, "") and emits stderr warning
    """
    lines = prompt.split("\n")[:20]
    matches = []
    for line in lines:
        m = _BYPASS_TOKEN_RE.match(line)
        if m:
            matches.append(m.group(1).strip())

    if len(matches) == 0:
        return False, ""
    if len(matches) == 1:
        return True, matches[0]
    # Multiple occurrences: ignore all
    print("[schema-gate] multiple bypass tokens detected; ignoring all", file=sys.stderr)
    return False, ""


def _write_bypass_record(event: dict, tool_input: dict, reason: str, cwd: str) -> None:
    """Write a bypass audit record to {session_dir}/delegation-trace.jsonl.

    Fail-quiet on any error: write to stderr but do not crash.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    else:
        session_dir = os.path.join(cwd, ".agent_context", "audit")

    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "schema-gate-bypass",
        "session_id": session_id,
        "agent_type": tool_input.get("subagent_type", ""),
        "schema_bypass_reason": reason,
        "tool_call_id": event.get("tool_use_id", None),
    }

    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(trace_file, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"[schema-gate] bypass record write failed: {e}", file=sys.stderr)


def _write_block_record(subagent_type: str, tool_use_id: str, session_dir: str, details: str) -> None:
    """Write a block audit record to {session_dir}/delegation-trace.jsonl.

    Fail-quiet on any error: write to stderr but do not crash.
    """
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "subagent_type": subagent_type,
        "tool_use_id": tool_use_id,
        "violation_detail": details,
        "session_dir": session_dir,
    }

    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(trace_file, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"[schema-gate] block record write failed: {e}", file=sys.stderr)


def _warn(message: str) -> dict:
    """Build an additionalContext warning response."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }


def _block(error_text: str) -> None:
    """Write error_text to stderr and exit with code 2 (hard block)."""
    print(error_text, file=sys.stderr)
    sys.exit(2)


def _run_version_probe(session_dir: str) -> None:
    """Check Claude Code version; set _DEGRADED if below v2.1.90.

    Idempotent per session: writes {session_dir}/.schema-gate-version-warning
    sentinel on first probe so subsequent fires skip the subprocess call.
    """
    global _DEGRADED
    sentinel = os.path.join(session_dir, ".schema-gate-version-warning")

    if os.path.exists(sentinel):
        # Read sentinel to restore degraded state from previous probe
        try:
            content = open(sentinel).read().strip()
            if content == "degraded":
                _DEGRADED = True
        except OSError:
            pass
        return

    # Probe not yet run this session
    min_version = (2, 1, 90)
    degraded = False
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5
        )
        stdout = result.stdout.strip()
        m = re.search(r'(\d+)\.(\d+)\.(\d+)', stdout)
        if m:
            parsed_ver = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if parsed_ver < min_version:
                degraded = True
                print(
                    f"[schema-gate] advisory: Claude Code {'.'.join(str(x) for x in parsed_ver)} < v2.1.90; "
                    "exit-2 blocking may not be enforced — gate degraded to advisory mode.",
                    file=sys.stderr
                )
        else:
            # Unparseable version output — treat as below minimum
            degraded = True
            print(
                f"[schema-gate] advisory: could not parse claude --version output ({stdout!r}); "
                "gate degraded to advisory mode.",
                file=sys.stderr
            )
    except Exception as e:
        # Subprocess error (not found, timeout, etc.) — treat as below minimum
        degraded = True
        print(
            f"[schema-gate] advisory: claude --version probe failed ({e}); "
            "gate degraded to advisory mode.",
            file=sys.stderr
        )

    # Write sentinel (best-effort)
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(sentinel, "w") as f:
            f.write("degraded" if degraded else "ok")
    except OSError:
        pass

    if degraded:
        _DEGRADED = True


def _get_mirrored_prefixes() -> tuple:
    """Return the SYNC_PREFIXES tuple from deployment-sync.py.

    Lazy-imports at call time so deployment-sync.py module-level code does
    not execute at gate import time.  Falls back to a hard-coded copy of the
    current known value when the import fails; the fallback is logged to
    stderr so operators can notice a drift.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_deployment_sync_prefixes",
            os.path.join(_HOOKS_DIR, "deployment-sync.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.SYNC_PREFIXES
    except Exception as e:
        # Fail-open: use the known-good value so the gate doesn't block on an
        # import error.  The hardcoded value must be kept in sync with
        # deployment-sync.py SYNC_PREFIXES.  Emit a stderr warning so this
        # drift is visible.
        print(
            f"[schema-gate] path-discipline: could not import SYNC_PREFIXES from "
            f"deployment-sync.py ({e}); using hardcoded fallback ('bin/', '.claude/families/').",
            file=sys.stderr,
        )
        return ("bin/", ".claude/families/")


def _worktree_session_dir() -> str | None:
    """Return the resolved CAA_SESSION_DIR if it is inside a worktree, else None.

    A session is in worktree mode when CAA_SESSION_DIR is set AND its resolved
    path contains '/.agent_context/worktrees/' as a path component.
    """
    raw = os.environ.get("CAA_SESSION_DIR", "")
    if not raw:
        return None
    resolved = os.path.realpath(raw)
    if "/.agent_context/worktrees/" in resolved:
        return resolved
    return None


def _main_root_from_worktree_session_dir(worktree_session_dir: str) -> str | None:
    """Derive the main repo root from a worktree-mode CAA_SESSION_DIR.

    CAA_SESSION_DIR resolves to:
      <main_root>/.agent_context/worktrees/<session_id>/.agent_context/sessions/<session_id>
    so we strip the worktree suffix to recover <main_root>.

    Returns None when the path doesn't match the expected structure.
    """
    marker = "/.agent_context/worktrees/"
    idx = worktree_session_dir.find(marker)
    if idx == -1:
        return None
    return worktree_session_dir[:idx]


def _path_discipline_violations(
    parsed,
    worktree_session_dir: str,
    main_root: str,
    mirrored_prefixes: tuple,
) -> list[tuple[str, str, str]]:
    """Return a list of (field_name, offending_path, suggested_path) triples.

    Scans dispatch fields for absolute paths that:
    1. Resolve under main_root (not the worktree root), AND
    2. Start with one of the mirrored_prefixes (relative to main_root).

    Such paths indicate the dispatch was composed against the main repo when
    the session is running in a worktree — the auto-mirror will clobber any
    worktree edits that land in a mirrored prefix with the (stale) main copy.
    """
    # Derive worktree root: strip /.agent_context/sessions/<id> suffix from
    # the worktree session dir.  The worktree root is:
    #   <main_root>/.agent_context/worktrees/<session_id>
    marker = "/.agent_context/worktrees/"
    idx = worktree_session_dir.find(marker)
    if idx == -1:
        return []
    after_marker = worktree_session_dir[idx + len(marker):]
    # after_marker is "<session_id>/.agent_context/sessions/<session_id>" —
    # we want just the first component.
    worktree_id = after_marker.split("/")[0]
    worktree_root = main_root + marker.rstrip("/") + "/" + worktree_id

    real_main = os.path.realpath(main_root)
    main_prefix = real_main + os.sep

    violations: list[tuple[str, str, str]] = []

    def _check(field_name: str, path_val) -> None:
        if not path_val or not isinstance(path_val, str):
            return
        if not path_val.startswith("/"):
            return  # relative paths are not a main-root path
        real_path = os.path.realpath(path_val)
        if not real_path.startswith(main_prefix):
            return  # not under main root — ok
        # Compute relative path from main root
        rel = real_path[len(real_main) + 1:].replace(os.sep, "/")
        for prefix in mirrored_prefixes:
            if rel.startswith(prefix):
                # Offending: main-rooted path under a mirrored prefix
                suggested = worktree_root.rstrip("/") + "/" + rel
                violations.append((field_name, path_val, suggested))
                return  # report once per field value

    # inputs[] — list of path strings
    for i, item in enumerate(parsed.inputs or []):
        _check(f"inputs[{i}]", item)

    # output_contract sub-fields
    oc = parsed.output_contract or {}
    _check("output_contract.artifact_path", oc.get("artifact_path"))
    _check("output_contract.sidecar_path", oc.get("sidecar_path"))

    # Scalar path fields
    _check("base_path", parsed.base_path)
    _check("target_artifact_path", parsed.target_artifact_path)
    _check("generator_artifact_path", parsed.generator_artifact_path)

    return violations




def run_gate(event: dict) -> dict:
    """Pure function for the IPC worker: returns a result dict without process-exit side effects.

    result["_action"] is one of "pass", "warn", or "block".
    "warn"  -> result["additionalContext"] is the advisory message string.
    "block" -> result["error_text"] is the block reason (replaces _block() sys.exit(2)).

    The CLI main() wrapper calls this and then calls _block() / prints _warn() as before,
    preserving the PreToolUse:Agent hook contract for the orchestrator's native hook path.
    """
    if event.get("tool_name") != "Agent":
        return {"_action": "pass"}

    tool_input = event.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type", "")
    prompt = tool_input.get("prompt", "")

    # Scope filter: silent pass for all non-canonical agent types
    if subagent_type not in SCHEMA_GATED_AGENTS:
        return {"_action": "pass"}

    cwd = event.get("cwd", os.getcwd())

    # Resolve session_dir for trace records and version-probe sentinel
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    else:
        session_dir = os.path.join(cwd, ".agent_context", "audit")

    # Route-C fix: version probe belongs on the native CLI path (main()), not the IPC worker
    # path.  The probe's purpose is to detect whether the platform honors sys.exit(2) for
    # PreToolUse blocking — irrelevant here because the TypeScript layer maps _action:'block'
    # to a hard block without relying on exit codes.  Leaving the probe here caused a 5s
    # blocking spawn inside the IPC worker and session-wide _DEGRADED poisoning (Surface C).
    # The probe call is now in main() only; see the comment there for the full rationale.

    schema_path = _find_schema_doc(cwd)
    matrix = _load_matrix(schema_path)
    degraded = matrix is None

    # parse_prompt() handles JSON-mode (incl. fenced blocks) + prose fallback.
    parsed = parse_prompt(prompt)

    # Bypass token detection (runs regardless of matrix load status)
    bypass_active, bypass_reason = _detect_bypass(prompt)

    if bypass_active:
        _write_bypass_record(event, tool_input, bypass_reason, cwd)
        return {
            "_action": "warn",
            "additionalContext": (
                f"delegation-prompt schema gate — {subagent_type}: "
                f"BYPASS active (reason: {bypass_reason!r}). "
                f"Schema checks skipped. See {SCHEMA_REF}."
            ),
        }

    # Path-discipline check (worktree-mode guard): runs before degraded-schema
    # early-return so a mis-routed dispatch is caught even when the schema doc
    # is unavailable. Independent of the schema matrix -- inspects path fields only.
    _wt_session_dir = _worktree_session_dir()
    if _wt_session_dir:
        _main_root = _main_root_from_worktree_session_dir(_wt_session_dir)
        if _main_root:
            _prefixes = _get_mirrored_prefixes()
            _violations = _path_discipline_violations(
                parsed, _wt_session_dir, _main_root, _prefixes
            )
            if _violations:
                field_name, bad_path, suggested = _violations[0]
                violation_detail = (
                    f"path-discipline violation in field '{field_name}': "
                    f"path '{bad_path}' is rooted at the main repo root "
                    f"'{_main_root}' under a mirrored prefix, but the "
                    f"session is running in a worktree. "
                    f"Expected worktree-rooted path: '{suggested}'. "
                    f"Rationale: the auto-mirror PostToolUse hook "
                    f"(.claude/hooks/deployment-sync.py) copies worktree "
                    f"edits under mirrored prefixes to main immediately — "
                    f"a main-rooted dispatch causes implementers to write "
                    f"to main, and the next worktree write then clobbers "
                    f"those changes via the one-way mirror. "
                    f"Fix: replace all main-rooted paths in this dispatch "
                    f"with their worktree-rooted equivalents."
                )
                error_text = (
                    f"delegation-prompt schema gate — {subagent_type}: "
                    f"{violation_detail} "
                    f"See .claude/knowledge/constraints/path-c/"
                    f"auto-mirror-probe-instrument-replication.md for the "
                    f"full auto-mirror clobber risk description."
                )
                _write_block_record(
                    subagent_type,
                    event.get("tool_use_id", "") or "",
                    session_dir,
                    violation_detail,
                )
                return {"_action": "block", "error_text": error_text}

    if degraded:
        return {
            "_action": "warn",
            "additionalContext": (
                f"delegation-prompt schema gate — {subagent_type}: "
                f"gate DEGRADED (schema doc parse failure). Cannot enforce schema. "
                f"See {SCHEMA_REF}."
            ),
        }

    schema_errors = validate_schema(parsed, subagent_type, matrix=matrix)

    # Translate SchemaError list into the existing H3 section format for
    # backward-compatible error messages (tests assert on the section labels).
    missing_universal = [
        e.field for e in schema_errors
        if e.reason == "missing-required"
        and e.field in (matrix.get("universal_required") or [])
    ]
    cond_missing = [
        e.field for e in schema_errors
        if e.reason == "missing-required"
        and e.field not in (matrix.get("universal_required") or [])
    ]
    cond_prohibited = [
        e.field for e in schema_errors
        if e.reason == "prohibited-present"
    ]
    shape_errors = [
        e.detail
        for e in schema_errors
        if e.reason in (
            "wrong-type", "enum-violation",
            "relative-path-not-allowed", "cross-field-inconsistent",
        )
    ]

    if missing_universal or cond_missing or cond_prohibited or shape_errors:
        sections = []
        if missing_universal:
            sections.append(f"universal missing: {', '.join(missing_universal)}")
        if cond_missing:
            sections.append(f"conditional missing: {', '.join(cond_missing)}")
        if cond_prohibited:
            sections.append(f"prohibited present: {', '.join(cond_prohibited)}")
        if shape_errors:
            sections.append(f"value shape invalid: {';'.join(shape_errors)}")
        details = "; ".join(sections)
        universal_hint = ""
        if missing_universal:
            example_fields = ", ".join(f"`{f}:`" for f in missing_universal[:3])
            universal_hint = (
                " (Detection accepts three structured forms (all case-insensitive)"
                " via `delegation_prompt_parser.py (_prose_field_present)`:"
                f" inline-colon like {example_fields};"
                " markdown heading like `## Inputs` or `### Success criteria`"
                " (heading text = field name with underscores as spaces);"
                " or bold-label like `**inputs:**` or `**Success criteria:**`"
                " (colon optional, label text MUST be the only content between the"
                " `**` markers). Rejected: dirty bold-labels with extra content"
                " between `**` markers (e.g. `**Inputs (read first):**`); bare-prose"
                " mentions in narrative text.)"
            )
        error_text = (
            f"delegation-prompt schema gate — {subagent_type}: "
            f"{details}.{universal_hint} "
            f"See {SCHEMA_REF} for the schema. "
            f"To bypass for emergency dispatch, prepend "
            f"'<!-- SCHEMA-BYPASS: <reason> -->' as a top-of-prompt line "
            f"(start-of-line, within first 20 lines, exactly once)."
        )
        tool_use_id = event.get("tool_use_id", "") or ""
        if _DEGRADED:
            # _degrade_reason field lets the TypeScript discriminator classify this as Route-C
            # without string-matching additionalContext (Step-0b structured degrade signal).
            return {"_action": "warn", "additionalContext": error_text, "_degrade_reason": "version-probe"}
        else:
            _write_block_record(subagent_type, tool_use_id, session_dir, details)
            return {"_action": "block", "error_text": error_text}

    return {"_action": "pass"}


def main():
    exit_if_dispatched_child("delegation-prompt-schema-gate")
    # try: recovering from malformed stdin (hook receives non-JSON from platform)
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    # Route-C fix: version probe runs here (native CLI path) instead of inside run_gate.
    # Rationale: the probe detects whether the platform honors sys.exit(2) for PreToolUse
    # blocking — an exit-code concern that only matters on this CLI path.  On the IPC worker
    # path, blocking is enforced by the TypeScript caller mapping _action:'block', not exit-2.
    # Moving the probe here removes the 5s blocking spawn from the IPC hot path and prevents
    # session-wide _DEGRADED poisoning of the IPC worker (Surface C, iss_c466ea31e9bb).
    # Resolve session_dir the same way run_gate does (mirrors run_gate:423-428).
    cwd = event.get("cwd", os.getcwd())
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    else:
        session_dir = os.path.join(cwd, ".agent_context", "audit")
    _run_version_probe(session_dir)

    result = run_gate(event)

    if result["_action"] == "block":
        # _block() writes to stderr and calls sys.exit(2) -- preserves Axis-C blocking.
        _block(result["error_text"])
    elif result["_action"] == "warn":
        print(json.dumps(_warn(result["additionalContext"])))



if __name__ == "__main__":
    # try: recovering from any unhandled exception (hook must not crash Claude Code)
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
