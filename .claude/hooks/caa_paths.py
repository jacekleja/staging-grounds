"""Shared Path-C resolution library for CAA Python hooks.

Provides four canonical functions for root resolution and path guards.
All other hooks that do os.path.dirname(__file__) * 3 should import
main_root() / worktree_root() from here instead.

Audit trail: track2-split-brain-audit.md §R1, §R2, §R4.
"""
import json
import os
import pathlib
from functools import lru_cache

# ---------------------------------------------------------------------------
# Mandatory-symlink set — single source of truth
# Loaded from the sibling JSON file so TypeScript callers can also import it
# without duplication (R4).
# ---------------------------------------------------------------------------

_JSON_PATH = pathlib.Path(__file__).parent / "mandatory_symlink_set.json"

def _load_symlink_set() -> dict:
    with open(_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)

_symlink_data = _load_symlink_set()

# Frozensets for O(1) membership tests
MANDATORY_SYMLINK_SET: dict = {
    "agent_context_subdirs": frozenset(_symlink_data["agent_context_subdirs"]),
    "claude_subdirs": frozenset(_symlink_data["claude_subdirs"]),
}


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def main_root() -> pathlib.Path:
    """Return the git main-repo root, resolving any worktree-suffix.

    Walks up from this file (.claude/hooks/caa_paths.py) looking for
    .claude/mcp or .claude/agents anchors — the same heuristic as
    detect-project-root.ts priority 1.

    Falls back to __file__-derived 3-levels-up path (the historical
    assumption: script is at {project_root}/.claude/hooks/<name>.py).
    This matches the semantics of write-gate.py's project_root derivation
    and is intentional for hook-side code: under Path C, __file__ resolves
    via the hook-discovery path in settings.json which always points at main.
    [verified: .claude/hooks/find-project-root.sh:32-37 (walk-up loop);  # lint-ignore: RAW_LINE_CITATION  # iss_FIXME: citation-anchor-lint-3-inline-ignores-skills-hooks — upgrade :32-37 to anchor form (walk-up loop)
               .claude/settings.json hook command paths resolve to main]
    """
    p = pathlib.Path(__file__).resolve().parent  # .claude/hooks/
    for _ in range(12):
        if (p / ".claude" / "mcp").is_dir() or (p / ".claude" / "agents").is_dir():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    # Fallback: 3-levels-up from __file__ (historical pattern, reliable when
    # hooks are at {root}/.claude/hooks/*)
    return pathlib.Path(__file__).resolve().parent.parent.parent


def worktree_root() -> pathlib.Path:
    """Return the current worktree root.

    Prefers CAA_WORKTREE_ROOT env var (set by bin/claude-session at spawn).
    Falls through to main_root() when not set or path does not exist —
    which is correct for non-Path-C (single-repo) scenarios.
    """
    raw = os.environ.get("CAA_WORKTREE_ROOT", "")
    if raw:
        p = pathlib.Path(raw)
        if p.is_dir():
            return p.resolve()
    return main_root()


def resolve_worktree_first(rel_path: str) -> pathlib.Path:
    """Worktree-first read fallback for non-symlinked subtrees (R3).

    For a relative path: try worktree_root()/rel_path first; if it
    exists return it, else return main_root()/rel_path.
    For an absolute path: return as-is (already resolved by caller).

    Use this when reading files that live in non-symlinked subtrees
    (e.g. bin/, docs/, .claude/agents/) so worktree-local edits are
    visible before falling through to the main copy.

    Worktree root priority (immune-to-symlink-first, mirrors TS resolveWorktreeFirst):
      1. worktree_root() already checks CAA_WORKTREE_ROOT env var first (set by
         launcher at spawn, immune to symlink resolution). This is the robust pattern
         per path-c/symlink-breaks-runtime-dirname-walk-path-resolution.md.
      2. Falls back to main_root() when CAA_WORKTREE_ROOT is not set or does not exist.

    Lockstep note (iss_cedbfb2f9b45 fix): mirrors the TS-side resolveWorktreeFirst
    fix in path-resolution.ts. Any further semantic change MUST update both sides
    in the same commit per the RC-2 invariant.

    C2.4 ambiguous-resolution guard (Python companion): use
    resolve_worktree_first_strict() when the caller needs fail-stop behavior on
    symlink ambiguity (worktree copy is a symlink with content differing from main).
    """
    p = pathlib.Path(rel_path)
    if p.is_absolute():
        return p
    wt_candidate = worktree_root() / p
    if wt_candidate.exists():
        return wt_candidate
    return main_root() / p


def resolve_worktree_first_strict(rel_path: str):
    """Like resolve_worktree_first, but raises ValueError on ambiguous symlink.

    C2.4 companion (iss_cedbfb2f9b45): when the resolved worktree path is a
    symbolic link whose content sha256 differs from the main candidate, raises
    ValueError with structured info instead of silently returning the symlink path.

    Returns the resolved Path when unambiguous. The ValueError message is a
    JSON-serialisable dict with keys:
      error, path, worktree_candidate, main_candidate,
      worktree_sha256, main_sha256, detail, remediation.

    Use this in callers that inline file content into prompts and need the
    same fail-stop discipline as the TS-side assembleInlines C2.4 guard.
    """
    import hashlib
    import json as _json

    p = pathlib.Path(rel_path)
    if p.is_absolute():
        return p

    wt_candidate = worktree_root() / p
    main_candidate = main_root() / p

    if not wt_candidate.exists():
        return main_candidate

    # Worktree candidate exists — check for symlink ambiguity (C2.4).
    if wt_candidate.is_symlink() and main_candidate.exists() and main_candidate.is_file():
        try:
            wt_bytes = wt_candidate.read_bytes()
            main_bytes = main_candidate.read_bytes()
        except OSError:
            # Best-effort: if we can't read either, fall through to normal resolution.
            return wt_candidate
        wt_sha = hashlib.sha256(wt_bytes).hexdigest()
        main_sha = hashlib.sha256(main_bytes).hexdigest()
        if wt_sha != main_sha:
            info = {
                "error": "inline_files_ambiguous_resolution",
                "path": rel_path,
                "worktree_candidate": str(wt_candidate),
                "main_candidate": str(main_candidate),
                "worktree_sha256": wt_sha,
                "main_sha256": main_sha,
                "detail": (
                    f"Worktree copy at \"{wt_candidate}\" is a symbolic link whose content "
                    f"(sha256={wt_sha[:8]}) differs from main copy at \"{main_candidate}\" "
                    f"(sha256={main_sha[:8]}). Cannot determine which version is authoritative."
                ),
                "remediation": (
                    f"Investigate the symlink at \"{wt_candidate}\": update it to point to the "
                    f"correct target, remove it to let main's copy be used, or pass an absolute path."
                ),
            }
            raise ValueError(_json.dumps(info))

    return wt_candidate


def is_under_main_only(path: pathlib.Path) -> bool:
    """Return True when path is under main physically AND not under worktree
    AND not under any mandatory-symlink target (R2).

    This is the unified split-brain predicate: covers .agent_context/,
    .claude/, bin/, docs/, setup/, and any other top-level non-symlinked
    directory — replacing the two separate is_main_agent_context_top_level()
    and is_main_claude_non_symlinked() predicates.

    CWE-59 symlink defence: realpath is applied to all inputs before prefix
    checks. Non-existent leaf paths are tolerated (os.path.realpath does not
    raise for missing leaves on Python's implementation).

    Identity case (main == worktree): returns False. No Path C, no split-brain.
    Relative paths: returns False (relative paths cannot unambiguously be
    pinned to main vs worktree without resolving).
    """
    if not path:
        return False
    p = pathlib.Path(path)
    if not p.is_absolute():
        return False

    try:
        real_main = os.path.realpath(str(main_root()))
        real_worktree = os.path.realpath(str(worktree_root()))
        real_target = os.path.realpath(str(p))
    except (OSError, ValueError):
        return False

    # Identity case — no split-brain possible
    if real_main == real_worktree:
        return False

    # Worktree-escape guard: target is under the worktree root — always legitimate
    if real_target.startswith(real_worktree + os.sep):
        return False

    # Must be under main to be a split-brain risk
    if not real_target.startswith(real_main + os.sep):
        return False

    # Compute path relative to main root
    rel = real_target[len(real_main) + 1:]  # strip leading sep
    parts = rel.split(os.sep)
    if not parts:
        return False

    top_level = parts[0]

    # .agent_context/ allow-list carve-out
    if top_level == ".agent_context":
        if len(parts) > 1 and parts[1] in MANDATORY_SYMLINK_SET["agent_context_subdirs"]:
            return False
        # `worktrees/` is the worktree-root container — not a symlink target, but
        # always legitimate territory for worktree-first dispatches. Not added to
        # MANDATORY_SYMLINK_SET (which describes subdirs that ARE symlinked to main)
        # because doing so would semantically pollute that set (see
        # `.claude/knowledge/constraints/path-c/mandatory-symlink-set-archive-asymmetry.md`
        # for the precedent — `archive/` already has this asymmetry as a known defect).
        # Fixes iss_623311a61a19.
        if len(parts) > 1 and parts[1] == "worktrees":
            return False
        return True

    # .claude/ allow-list carve-out
    if top_level == ".claude":
        if len(parts) > 1 and parts[1] in MANDATORY_SYMLINK_SET["claude_subdirs"]:
            return False
        return True

    # All other top-level directories under main are split-brain risks when a
    # worktree is active (bin/, docs/, setup/, etc. are uniformly worktree-local).
    return True
