#!/usr/bin/env python3
# dispatch-child-safe: false
"""PreToolUse hook on Write/Edit/MultiEdit/smart_write/smart_edit tools.

Hard gate for orchestrator-depth writes to agent-surface paths (agent bodies, hooks,
skills, knowledge/constraints, knowledge/decisions, orchestrator-prompt, CLAUDE.md).
Soft advisory warning for other non-safe paths.

Gate discriminator (i) — fires ONLY at root-orchestrator depth
(CLAUDE_HOOK_ORCHESTRATOR_DEPTH==1 AND CLAUDE_SESSION_DEPTH==0 AND
CLAUDE_SESSION_ID present):
- Write / smart_write on any agent-surface path → always deny (full-authoring blocked).
- MultiEdit on any agent-surface path → deny (only single small scrubs permitted).
- Edit / smart_edit on agent-surface:
    * Allow when changed-span (old_string lines) is ≤3 AND the session
      distinct-file agent-surface-edit count is 0–1 after adding current file.
    * Deny otherwise.
  Distinct-file count is keyed by canonicalized rel_path, incremented once per
  path, never double-counted for repeat edits to the same file — so multiple
  consecutive smart_edit calls to the same agent body all count as 1, not N.
  Deny message routes explicitly to agent-content-author.

Bootstrap / operator-direct carve-out: when CLAUDE_SESSION_ID is absent or
CLAUDE_HOOK_ORCHESTRATOR_DEPTH is not "1", the gate does not apply (pass-through).
Dispatched subagents are excluded by the exit_if_dispatched_child() guard.

Cross-cluster (a): .claude/knowledge/constraints/** and
.claude/knowledge/decisions/** are gated here at orchestrator depth; other
.claude/knowledge/** writes are left to knowledge-write-guard.py's advisory
regime. settings.json write-gate entries precede knowledge-write-guard entries
for all matchers so write-gate fires first on constrained/decisions paths.

Safe paths (orchestrator expected to write directly): .agent_context/,
.claude/settings.json, .claude/settings.local.json, plan-*.md, root dotfiles.

Path-C split-brain clause (D6 extension): uses caa_paths.is_under_main_only().
[verified: track2-split-brain-audit.md §R2, §D6;
           constraints/path-c/path-c-bin-docs-write-guard-gap.md]
"""
import json
import os
import sys

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from caa_paths import is_under_main_only, main_root, worktree_root
from _dispatch_child_guard import exit_if_dispatched_child


# ---------------------------------------------------------------------------
# Agent-surface path classification
# ---------------------------------------------------------------------------

# Prefix set: paths the orchestrator must not author directly at root depth.
# Cross-cluster (a): knowledge/constraints/ and knowledge/decisions/ are gated
# here; other knowledge/** remains under knowledge-write-guard.py.
_AGENT_SURFACE_PREFIXES = [
    ".claude/agents/",
    ".claude/knowledge/constraints/",
    ".claude/knowledge/decisions/",
    ".claude/hooks/",
    ".claude/mcp/",
    ".claude/skills/",
]

# Exact-match agent-surface paths
_AGENT_SURFACE_EXACT = frozenset({
    ".claude/orchestrator-prompt.md",
    "CLAUDE.md",
})


def get_file_path(tool_name, tool_input):
    """Extract file path from tool input for Write/Edit/MultiEdit/smart_* variants."""
    # All supported tools use 'file_path' as the key
    return tool_input.get("file_path", "")


def _derive_worktree_equivalent(main_abs_path: str, wt_root: str) -> str:
    """Substitute main_root prefix with worktree_root prefix.

    Operates on realpath'd inputs. Falls back to lexical substitution on the
    original strings if realpath fails; fail-open since the string is advisory.
    """
    try:
        real_main = os.path.realpath(str(main_root()))
        real_worktree = os.path.realpath(wt_root)
        real_target = os.path.realpath(main_abs_path)
    except (OSError, ValueError):
        real_main = str(main_root())
        real_worktree = wt_root
        real_target = main_abs_path

    if real_target.startswith(real_main + os.sep):
        tail = real_target[len(real_main):]  # keeps leading os.sep
        return real_worktree + tail
    return main_abs_path


# Keep these aliases so existing tests that import the old predicates directly
# (e.g. test_write_gate.py TestPathCSplitBrainClause / TestPathCClaudeNonSymlinkedClause)
# continue to work. They delegate to is_under_main_only via caa_paths.
# The old signature was (target, main_root, worktree_root) with string args;
# we replicate that shape here for backward compatibility.

def is_main_agent_context_top_level(target: str, main_root_str: str, worktree_root_str: str) -> bool:
    """Backward-compat wrapper: was the .agent_context/ split-brain predicate.

    Now delegates to is_under_main_only but scoped to .agent_context/ only,
    matching the old function's narrower semantics so existing tests pass.
    """
    import pathlib
    if not target or not main_root_str or not worktree_root_str:
        return False
    try:
        real_main = os.path.realpath(main_root_str)
        real_worktree = os.path.realpath(worktree_root_str)
        real_target = os.path.realpath(target)
    except (OSError, ValueError):
        return False
    if real_main == real_worktree:
        return False
    if real_target.startswith(real_worktree + os.sep):
        return False
    prefix = real_main + os.sep + ".agent_context" + os.sep
    if not real_target.startswith(prefix):
        return False
    from caa_paths import MANDATORY_SYMLINK_SET
    tail = real_target[len(prefix):]
    first_segment = tail.split(os.sep, 1)[0]
    if first_segment in MANDATORY_SYMLINK_SET["agent_context_subdirs"]:
        return False
    # `worktrees/` is the worktree-root container — see the matching carve-out in
    # caa_paths.is_under_main_only() for the rationale. This wrapper re-implements
    # the membership check inline rather than delegating, so it needs the same
    # carve-out to stay consistent. iss_623311a61a19.
    if first_segment == "worktrees":
        return False
    return True


def is_main_claude_non_symlinked(target: str, main_root_str: str, worktree_root_str: str) -> bool:
    """Backward-compat wrapper: was the .claude/ split-brain predicate.

    Now delegates to is_under_main_only but scoped to .claude/ only,
    matching the old function's narrower semantics so existing tests pass.
    """
    if not target or not main_root_str or not worktree_root_str:
        return False
    try:
        real_main = os.path.realpath(main_root_str)
        real_worktree = os.path.realpath(worktree_root_str)
        real_target = os.path.realpath(target)
    except (OSError, ValueError):
        return False
    if real_main == real_worktree:
        return False
    if real_target.startswith(real_worktree + os.sep):
        return False
    prefix = real_main + os.sep + ".claude" + os.sep
    if not real_target.startswith(prefix):
        return False
    from caa_paths import MANDATORY_SYMLINK_SET
    tail = real_target[len(prefix):]
    first_segment = tail.split(os.sep, 1)[0]
    if first_segment in MANDATORY_SYMLINK_SET["claude_subdirs"]:
        return False
    return True


def is_safe_path(rel_path):
    """Return True if the path is in a truly safe (orchestrator-writable) location.

    NOTE: agent-surface paths (.claude/agents/, .claude/hooks/, etc.) are NOT in
    this set — they are handled by is_agent_surface_path() which fires BEFORE this
    function in main(). The dotfile catch-all here therefore only reaches
    non-agent-surface dotpaths (e.g. .gitignore, .env, .github/).
    """
    # Normalize separators
    rel_path = rel_path.replace("\\", "/")

    # .agent_context/ is always safe for direct writes (session state, plans, etc.)
    if rel_path.startswith(".agent_context/"):
        return True

    # Specific .claude/ files the orchestrator manages directly
    safe_exact = {
        ".claude/settings.json",
        ".claude/settings.local.json",
    }
    if rel_path in safe_exact:
        return True

    # plan-*.md anywhere in the path
    basename = os.path.basename(rel_path)
    import fnmatch
    if fnmatch.fnmatch(basename, "plan-*.md"):
        return True

    # Any path starting with '.' (dotfiles or dotdirs at root level).
    # Catches .gitignore, .env, .github/, etc.
    # Agent-surface paths (also starting with '.claude/') are caught by
    # is_agent_surface_path() earlier in main() and never reach here.
    if rel_path.startswith("."):
        return True

    return False


def is_agent_surface_path(rel_path):
    """Return True if this path is an agent-surface path subject to the hard gate.

    Agent-surface paths require delegation to agent-content-author or implementer;
    the orchestrator must not author them directly at root depth.

    Cross-cluster (a) split: knowledge/constraints/ and knowledge/decisions/ are
    gated here; other knowledge/** remains under knowledge-write-guard.py.
    """
    rel_path = rel_path.replace("\\", "/")
    for prefix in _AGENT_SURFACE_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    return rel_path in _AGENT_SURFACE_EXACT


# ---------------------------------------------------------------------------
# Orchestrator-depth session-dir helper
# ---------------------------------------------------------------------------

def _get_session_dir_if_orchestrator(project_root):
    """Return the session dir path only when running at root-orchestrator depth.

    Returns None for:
    - Non-root orchestrator depth  (CLAUDE_HOOK_ORCHESTRATOR_DEPTH != "1")
    - L2 sidecar children          (CLAUDE_SESSION_DEPTH != "0", REV-2 guard)
    - Operator-direct / bootstrap  (CLAUDE_SESSION_ID absent)
    Dispatched subagents are excluded upstream by exit_if_dispatched_child().
    """
    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return None
    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        return None
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return None
    return os.path.join(project_root, ".agent_context", "sessions", session_id)


# ---------------------------------------------------------------------------
# Session counter for distinct-file tracking
# ---------------------------------------------------------------------------

def _load_edited_paths(session_dir):
    """Load the set of distinct agent-surface paths edited this session.

    Returns (counter_path, set_of_canonical_paths).
    """
    counter_path = os.path.join(session_dir, "write-gate-edited-paths.json")
    try:
        with open(counter_path, "r") as f:
            data = json.load(f)
            return counter_path, set(data.get("paths", []))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return counter_path, set()


def _save_edited_paths(counter_path, paths):
    """Atomically persist the edited-paths set.

    Failure is non-fatal: counter loss makes the gate more permissive, not more
    restrictive — acceptable fail-open for a gate that can be retried.
    """
    try:
        os.makedirs(os.path.dirname(counter_path), exist_ok=True)
        tmp = counter_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"paths": sorted(paths)}, f)
        os.replace(tmp, counter_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------

def _deny(rel_path, reason):
    """Emit a deny decision to stderr and sys.exit(2).

    exit(2) causes Claude Code to block the tool call and show the stderr
    message to the model as the denial reason.
    SystemExit is a BaseException subclass and propagates through main()'s
    `except Exception` wrapper unimpeded.
    """
    msg = (
        f"--- WRITE GATE DENY ---\n"
        f"Direct write to agent-surface path '{rel_path}' blocked "
        f"({reason}). Delegate this work to agent-content-author: "
        f"dispatch Agent(subagent_type='agent-content-author', ...).\n"
        f"--- END WRITE GATE DENY ---"
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


def _apply_agent_surface_gate(tool_name, tool_input, rel_path, session_dir):
    """Apply the hard gate for agent-surface paths at root-orchestrator depth.

    Implements discriminator (i):
    - Write / smart_write  → always deny (full-authoring).
    - MultiEdit            → deny (not in the single-small-scrub allow path).
    - Edit / smart_edit    → allow when changed-span ≤3 lines AND session
                             distinct-file count 0–1; deny otherwise.

    Distinct-file count semantics: keyed by canonicalized rel_path; incremented
    once per DISTINCT path; repeat edits to the same path never double-count.
    Multiple smart_edit calls to the same agent body in one session count as 1.
    """
    # Write / smart_write: full-file authoring → always deny
    if tool_name in ("Write", "mcp__context-tools__smart_write"):
        _deny(
            rel_path,
            "Write/smart_write (full-file authoring) of agent-surface paths is not "
            "allowed at orchestrator depth"
        )
        # sys.exit(2) above; not reached

    # MultiEdit: deny (only single small scrubs are in the allow path)
    if tool_name == "MultiEdit":
        _deny(
            rel_path,
            "MultiEdit of agent-surface paths is not allowed at orchestrator depth; "
            "dispatch agent-content-author for multi-change authoring"
        )
        # not reached

    # Edit / smart_edit: apply discriminator (i)
    if tool_name in ("Edit", "mcp__context-tools__smart_edit"):
        # (i-a) Changed-span must be ≤3 lines.
        # old_string is the span being replaced; its line count is the span size.
        old_string = tool_input.get("old_string", "")
        new_string_edit = tool_input.get("new_string", "")
        # Measure both sides: a small old_string with a large new_string is still a large change.
        changed_lines = max(
            len(old_string.splitlines()) if old_string else 1,
            len(new_string_edit.splitlines()) if new_string_edit else 1,
        )
        if changed_lines > 3:
            _deny(
                rel_path,
                f"edit span is {changed_lines} lines (limit: ≤3 for orchestrator-depth "
                f"agent-surface edits); dispatch agent-content-author for larger changes"
            )
            # not reached

        # (i-b) Session distinct-file count must be 0–1 after adding current file.
        # Canonicalize to avoid double-counting e.g. ./foo vs foo or foo/ vs foo.
        canonical = os.path.normpath(rel_path).replace("\\", "/")
        counter_path, edited_paths = _load_edited_paths(session_dir)

        if canonical not in edited_paths:
            # This would be a new distinct file for this session
            if len(edited_paths) >= 1:
                # Already edited one distinct file → this is the second → deny
                _deny(
                    rel_path,
                    f"second distinct agent-surface file edit in session "
                    f"(already edited: {sorted(edited_paths)!r}); "
                    f"dispatch agent-content-author for multi-file authoring"
                )
                # not reached
            # First distinct file: record it and allow
            edited_paths.add(canonical)
            _save_edited_paths(counter_path, edited_paths)
        # else: same file edited again → already in set → allow without re-counting


def main():
    exit_if_dispatched_child("write-gate")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    # Extended tool set: native Write/Edit/MultiEdit + MCP smart_write/smart_edit
    tool_name = event.get("tool_name", "")
    if tool_name not in (
        "Write", "Edit", "MultiEdit",
        "mcp__context-tools__smart_write",
        "mcp__context-tools__smart_edit",
    ):
        return

    tool_input = event.get("tool_input", {})
    if not tool_input:
        return

    file_path = get_file_path(tool_name, tool_input)
    if not file_path:
        return

    # main_root() walks up from __file__ (.claude/hooks/write-gate.py).
    # Under Path C, __file__ resolves via the settings.json command path which
    # always points at main's copy — intentional.
    # [verified: .claude/hooks/find-project-root.sh:32-37; .claude/settings.json hook cmds]
    project_root = str(main_root())

    # Normalize the path to be relative to the project root
    try:
        if os.path.isabs(file_path):
            rel_path = os.path.relpath(file_path, project_root)
        else:
            rel_path = file_path
        # Normalize path separators
        rel_path = rel_path.replace("\\", "/")
    except ValueError:
        # On Windows, relpath can raise ValueError for cross-drive paths
        return

    # Path-C split-brain clause (D6 extension) — fires BEFORE path classification
    # so main-physical writes to .agent_context/, .claude/, bin/, etc. are all caught.
    # [verified: track2-split-brain-audit.md §R2, §D6;
    #            constraints/path-c/path-c-bin-docs-write-guard-gap.md]
    wt_root_str = os.environ.get("CAA_WORKTREE_ROOT", "")
    if wt_root_str and os.path.isabs(file_path):
        import pathlib
        if is_under_main_only(pathlib.Path(file_path)):
            worktree_equivalent = _derive_worktree_equivalent(file_path, wt_root_str)
            warning = (
                f"--- WRITE GATE WARNING ---\n"
                f"Path C split-brain risk: you are writing to {file_path}. "
                f"Under Path C this path is worktree-local and writing to main "
                f"bypasses invariant 3/5. Write to {worktree_equivalent} instead.\n"
                f"--- END WRITE GATE WARNING ---"
            )
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": warning,
                }
            }))
            return

    # --- Path classification (checked in this strict order) ---

    # 1. Agent-surface paths → hard gate at root-orchestrator depth.
    #    Must be checked FIRST so the dotfile catch-all in is_safe_path() does not
    #    short-circuit agent-surface paths that start with '.claude/'.
    if is_agent_surface_path(rel_path):
        session_dir = _get_session_dir_if_orchestrator(project_root)
        if session_dir is None:
            # Not root-orchestrator depth, or no session (operator-direct /
            # bootstrap carve-out): pass through silently.
            return
        _apply_agent_surface_gate(tool_name, tool_input, rel_path, session_dir)
        return

    # 2. Other .claude/knowledge/** paths (NOT constraints/ or decisions/):
    #    write-gate stays silent; knowledge-write-guard.py owns advisory for these.
    if rel_path.startswith(".claude/knowledge/"):
        return

    # 3. Truly safe paths → exit silently (orchestrator expected to write directly).
    if is_safe_path(rel_path):
        return

    # 4. All other paths → soft advisory warning (backward compat; non-agent-surface).
    warning = (
        f"--- WRITE GATE WARNING ---\n"
        f"You are writing directly to {rel_path}. The orchestrator should typically "
        f"delegate file creation/modification to subagents (implementer, architect, researcher). "
        f"Consider whether this write should be part of a delegated task with proper review.\n"
        f"--- END WRITE GATE WARNING ---"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": warning
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never crash — always exit 0.
        # SystemExit (from _deny → sys.exit(2)) is a BaseException, not Exception,
        # so it propagates through this handler unimpeded.
        pass
