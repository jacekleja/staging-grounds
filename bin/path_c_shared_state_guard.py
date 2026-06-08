#!/usr/bin/env python3
"""Path-C shared-state guard for Python launchers, hooks, and scripts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Literal

Usage = Literal["read", "mutate"]

FALLBACK_CLAUDE_SUBDIRS = ["knowledge", "knowledge-log", "mcp"]
FALLBACK_AGENT_CONTEXT_SUBDIRS = ["sessions", "logs", "audit", "campaigns", "archive"]
ISSUE_FILE_RELS = [".agent_context/issues.jsonl", ".agent_context/issues.jsonl.lock"]
SETTINGS_LOCAL_REL = ".claude/settings.local.json"


class PathCSharedStateError(RuntimeError):
    def __init__(self, code: str, root: Path, root_rel: str, usage: Usage):
        self.code = code
        self.root = root
        self.root_rel = root_rel
        self.usage = usage
        super().__init__(
            f"{code}: Path-C shared-state invariant failed for {root_rel} at {root} "
            f"during {usage}; shared roots must remain symlinks to main before filesystem mutation."
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "root": str(self.root),
            "root_rel": self.root_rel,
            "usage": self.usage,
            "message": str(self),
        }


def _logical_abs(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _has_prefix(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_logical_path_c(main_root: str | Path, worktree_root: str | Path) -> bool:
    main = _logical_abs(main_root)
    worktree = _logical_abs(worktree_root)
    if main == worktree:
        return False
    worktrees_base = main / ".agent_context" / "worktrees"
    return worktree == worktrees_base or _has_prefix(worktree, worktrees_base)


def _normalize_rel(rel_path: str) -> str:
    return rel_path.replace("\\", "/").removeprefix("./")


def shared_root_rels(main_root: str | Path) -> list[str]:
    main = _logical_abs(main_root)
    claude_subdirs = list(FALLBACK_CLAUDE_SUBDIRS)
    agent_context_subdirs = list(FALLBACK_AGENT_CONTEXT_SUBDIRS)
    config = main / ".claude" / "hooks" / "mandatory_symlink_set.json"
    try:
        parsed = json.loads(config.read_text(encoding="utf-8"))
        if isinstance(parsed.get("claude_subdirs"), list) and all(
            isinstance(v, str) for v in parsed["claude_subdirs"]
        ):
            claude_subdirs = list(parsed["claude_subdirs"])
        if isinstance(parsed.get("agent_context_subdirs"), list) and all(
            isinstance(v, str) for v in parsed["agent_context_subdirs"]
        ):
            agent_context_subdirs = list(parsed["agent_context_subdirs"])
    except Exception:
        pass

    rels = [
        *(f".claude/{subdir}" for subdir in claude_subdirs),
        *(f".agent_context/{subdir}" for subdir in agent_context_subdirs),
        *ISSUE_FILE_RELS,
        SETTINGS_LOCAL_REL,
    ]

    bootstrap = main / ".claude" / "bootstrap-config.json"
    try:
        parsed = json.loads(bootstrap.read_text(encoding="utf-8"))
        extras = parsed.get("worktree_symlinks")
        if isinstance(extras, list):
            for raw in extras:
                if not isinstance(raw, str):
                    continue
                rel = raw.strip()
                parts = rel.split("/")
                if (
                    not rel
                    or rel.startswith("/")
                    or "\\" in rel
                    or any(part in {"", ".", ".."} for part in parts)
                ):
                    continue
                rels.append(rel)
    except Exception:
        pass

    return list(dict.fromkeys(_normalize_rel(rel) for rel in rels))


def guard_root(
    root_rel: str,
    *,
    main_root: str | Path,
    worktree_root: str | Path,
    usage: Usage,
) -> None:
    if not is_logical_path_c(main_root, worktree_root):
        return
    normalized = _normalize_rel(root_rel)
    root = _logical_abs(worktree_root) / normalized
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        if usage == "mutate":
            raise PathCSharedStateError("path-c-shared-state-root-missing", root, normalized, usage)
        return

    if not os.path.islink(root):
        raise PathCSharedStateError("path-c-shared-state-local-root-exists", root, normalized, usage)

    try:
        real_root = Path(os.path.realpath(root))
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            if usage == "mutate":
                raise PathCSharedStateError("path-c-shared-state-target-missing", root, normalized, usage)
            return
        raise
    if not real_root.exists():
        if usage == "mutate":
            raise PathCSharedStateError("path-c-shared-state-target-missing", root, normalized, usage)
        return

    real_worktree = Path(os.path.realpath(worktree_root))
    if _has_prefix(real_root, real_worktree):
        raise PathCSharedStateError("path-c-shared-state-symlink-broken", root, normalized, usage)


def guard_roots(
    root_rels: Iterable[str],
    *,
    main_root: str | Path,
    worktree_root: str | Path,
    usage: Usage,
) -> None:
    for root_rel in dict.fromkeys(_normalize_rel(root_rel) for root_rel in root_rels):
        guard_root(root_rel, main_root=main_root, worktree_root=worktree_root, usage=usage)


def matching_roots_for_path(
    target_path: str | Path,
    *,
    main_root: str | Path,
    worktree_root: str | Path,
) -> list[str]:
    target = _logical_abs(target_path)
    worktree = _logical_abs(worktree_root)
    matches: list[tuple[int, str]] = []
    for rel in shared_root_rels(main_root):
        root = worktree / rel
        if target == root or _has_prefix(target, root):
            matches.append((len(str(root)), rel))
    return [rel for _, rel in sorted(matches, reverse=True)]


def guard_path(
    target_path: str | Path,
    *,
    main_root: str | Path,
    worktree_root: str | Path,
    usage: Usage,
) -> None:
    guard_roots(
        matching_roots_for_path(target_path, main_root=main_root, worktree_root=worktree_root),
        main_root=main_root,
        worktree_root=worktree_root,
        usage=usage,
    )


def ensure_parent_for_path(
    target_path: str | Path,
    *,
    main_root: str | Path,
    worktree_root: str | Path,
) -> None:
    target = _logical_abs(target_path)
    if not is_logical_path_c(main_root, worktree_root):
        target.parent.mkdir(parents=True, exist_ok=True)
        return

    matches = matching_roots_for_path(target, main_root=main_root, worktree_root=worktree_root)
    if not matches:
        target.parent.mkdir(parents=True, exist_ok=True)
        return

    root_rel = matches[0]
    guard_root(root_rel, main_root=main_root, worktree_root=worktree_root, usage="mutate")
    root = _logical_abs(worktree_root) / root_rel
    parent = target.parent
    if target == root or parent == root:
        guard_root(root_rel, main_root=main_root, worktree_root=worktree_root, usage="mutate")
        return

    rel_parts = parent.relative_to(root).parts
    cursor = root
    for part in rel_parts:
        guard_root(root_rel, main_root=main_root, worktree_root=worktree_root, usage="mutate")
        cursor = cursor / part
        cursor.mkdir(exist_ok=True)
    guard_root(root_rel, main_root=main_root, worktree_root=worktree_root, usage="mutate")
