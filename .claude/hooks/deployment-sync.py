#!/usr/bin/env python3
# dispatch-child-safe: false
"""PostToolUse hook: auto-mirror worktree edits in bin/* and .claude/families/* to main repo.

Fires after Write / Edit / MultiEdit / mcp__context-tools__smart_write / mcp__context-tools__smart_edit tool calls.
When the target file is under the worktree AND under one of the SYNC_PREFIXES
directories, copies the file to the corresponding main-repo path.

No-op cases (exits immediately without action):
  - Tool not in SYNC_TOOLS
  - No file_path in tool input
  - Target is not under the worktree (e.g. direct main-repo edit — main IS the destination)
  - Target is not under any SYNC_PREFIXES directory
  - Target is already a mandatory-symlink path (symlinked → already shared, no copy needed)
  - main == worktree (single-repo scenario; no split-brain)

Concurrency: acquires an fcntl.LOCK_EX on a per-target lock file in /tmp before
performing the copy so two sessions racing on the same target file serialize without
partial-file corruption.

Mirror direction: worktree → main ONLY. Never main → worktree.
"""
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import sys

from _dispatch_child_guard import exit_if_dispatched_child

# These tools carry a file_path field in their tool_input.
SYNC_TOOLS = {"Write", "Edit", "MultiEdit", "mcp__context-tools__smart_write", "mcp__context-tools__smart_edit"}

# Path prefixes (relative to repo root, forward-slash) that are eligible for mirroring.
# Internal path filtering; the matcher in settings.json is broad (Write|Edit|smart_write).
SYNC_PREFIXES = (
    "bin/",
    ".claude/families/",
    "docs/",
    ".claude/hooks/",
    ".claude/pipelines/",
)


def _lock_path_for(target: pathlib.Path) -> pathlib.Path:
    """Return a stable /tmp lock path keyed by the sha1 of the target's string rep.

    Using sha1 of the absolute target path gives a stable, collision-resistant
    key while keeping the lock filename short enough for any fs.
    """
    digest = hashlib.sha1(str(target).encode()).hexdigest()[:16]
    return pathlib.Path(f"/tmp/caa-deployment-sync-{digest}.lock")


def _normalize_rel(path: pathlib.Path, root: pathlib.Path) -> str | None:
    """Return forward-slash relative path from root, or None if not under root."""
    try:
        rel = path.relative_to(root)
        return str(rel).replace(os.sep, "/")
    except ValueError:
        return None


def _is_sync_eligible(rel: str) -> bool:
    """Return True when the relative path falls under a SYNC_PREFIXES directory."""
    for prefix in SYNC_PREFIXES:
        if rel.startswith(prefix):
            return True
    return False


def _mirror(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Copy src to dst under an exclusive per-destination flock.

    Acquires the lock first so two concurrent sessions syncing the same file
    don't produce a partial/corrupted copy. Uses a stable /tmp lock file
    (not the destination itself) to avoid inode-swap issues.
    """
    lock_path = _lock_path_for(dst)
    lk_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    lk_fh = os.fdopen(lk_fd, "r+")
    try:
        # Block until we hold an exclusive lock; prevents partial-file reads
        # by a concurrent second sync call for the same destination.
        fcntl.flock(lk_fh, fcntl.LOCK_EX)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        # Defense: re-assert exec bit if source has it. shutil.copy2 preserves
        # metadata, but `core.fileMode=false` in this repo can mean the SOURCE
        # itself lost its exec bit on a fresh `git worktree add` checkout (D14:
        # commit 07bafdee "bin/caa-session: set executable mode" recurred Ep-9).
        # If src is exec-set we re-apply on dst; if src is not (worktree-side
        # bit stripped), dst inherits the strip and the session-start probe
        # restores both copies. See docs/archive/claude-study-invariants.md § exec-bit-rule.
        try:
            src_mode = src.stat().st_mode
            if src_mode & 0o111:
                # Source is executable for at least one user class — propagate.
                os.chmod(str(dst), dst.stat().st_mode | (src_mode & 0o111))
        except OSError:
            pass  # best-effort; copy2 already replicated whatever it could
        sys.stderr.write(
            f"deployment-sync: mirrored {src} → {dst}\n"
        )
    finally:
        fcntl.flock(lk_fh, fcntl.LOCK_UN)
        lk_fh.close()


def main() -> None:
    exit_if_dispatched_child("deployment-sync")
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = event.get("tool_name", "")
    if tool_name not in SYNC_TOOLS:
        return

    tool_input = event.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not raw_path:
        return

    # Import caa_paths here (at call time, not import time) so test isolation
    # can patch it without complex module-level machinery.
    from caa_paths import main_root, worktree_root

    main_r = main_root()
    worktree_r = worktree_root()

    # Single-repo / non-Path-C scenario: no-op
    real_main = pathlib.Path(os.path.realpath(str(main_r)))
    real_wt = pathlib.Path(os.path.realpath(str(worktree_r)))
    if real_main == real_wt:
        return

    target = pathlib.Path(raw_path)
    if not target.is_absolute():
        # Resolve relative paths against the session's cwd (the worktree root
        # for Path C sessions). MCP tools like smart_edit report file_path
        # as project-relative in their tool_input, so this branch is the
        # common case — not the exception.
        cwd = event.get("cwd") or str(worktree_r)
        target = pathlib.Path(cwd) / target

    real_target = pathlib.Path(os.path.realpath(str(target)))

    # No-op: target not under worktree (e.g., direct main-repo edit)
    wt_prefix = str(real_wt) + os.sep
    if not str(real_target).startswith(wt_prefix):
        return

    # Compute path relative to worktree
    rel = _normalize_rel(real_target, real_wt)
    if rel is None:
        return

    # No-op: not under a synced prefix
    if not _is_sync_eligible(rel):
        return

    # Resolve destination in main
    dst = main_r / rel

    # No-op: source and destination are the same physical file (shouldn't happen
    # when main != worktree, but guard defensively)
    if real_target == pathlib.Path(os.path.realpath(str(dst))):
        return

    # Source must exist (hook fires post-tool so it should; guard anyway)
    if not real_target.is_file():
        return

    _mirror(real_target, dst)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never crash the hook pipeline; log to stderr for discoverability
        sys.stderr.write(f"deployment-sync: internal error, skipping: {e}\n")
