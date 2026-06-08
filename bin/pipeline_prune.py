"""pipeline_prune.py — Physical pruning of inactive-pipeline files in a git worktree.

Called by bin/claude-session after worktree creation to delete agent, hook,
rule, and skill files owned by inactive pipelines.  The manifest's flat-basename
path-safety invariant (invariant 9, enforced by S1's _enforce_flat_basename) is
assumed satisfied; this module does naive join.

Exports:
    PruneCounts                     — dataclass of per-dimension prune counts
    prune_inactive_pipeline_files   — idempotent prune function
"""

import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

# pipeline_manifest is in the same directory; let the caller's sys.path setup
# take care of the import if needed.  We only use PipelineManifest type here.
try:
    from pipeline_manifest import (
        DEFAULT_RESERVED_AGENTS,
        DEFAULT_RESERVED_HOOKS,
        PipelineManifest,
    )
except ImportError:
    # Allow unit tests to import without sys.path surgery by inserting the bin
    # directory.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pipeline_manifest import (
        DEFAULT_RESERVED_AGENTS,
        DEFAULT_RESERVED_HOOKS,
        PipelineManifest,
    )


# ---------------------------------------------------------------------------
# Public data class
# ---------------------------------------------------------------------------


@dataclass
class PruneCounts:
    """Per-dimension counts of files physically removed from the worktree."""

    agents: int = 0
    hooks: int = 0
    rules: int = 0
    skills: int = 0
    rubrics: int = 0
    skip_worktree_bits_set: int = 0
    inactive_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return self.agents + self.hooks + self.rules + self.skills + self.rubrics


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def prune_inactive_pipeline_files(
    worktree_root: pathlib.Path,
    inactive_manifests: "dict[str, PipelineManifest]",
    *,
    logger=None,
) -> PruneCounts:
    """Delete every file in worktree_root that belongs to an inactive pipeline.

    Iterates inactive_manifests; for each pipeline removes:
      - .claude/agents/<basename>   for each entry in manifest.agents
      - .claude/hooks/<basename>    for each entry in manifest.hooks (HookEntry.path)
      - .claude/skills/<basename>   for each entry in manifest.skills
      (Note: legacy manifest.rules iteration retained for back-compat with
      historical manifests; the rules corpus directory has been eliminated.)

    After unlinking, sets the git skip-worktree bit on every successfully
    pruned path (one batched update-index --stdin call).  This suppresses the
    resulting D-entries from `git status` and prevents bulk-stage commands
    (`git add -A`) from picking up the deletions.

    FileNotFoundError → warn-and-continue (idempotent; safe to call twice).
    Other OS errors → warn-and-continue (never fatal to the caller).
    update-index failure → logged but non-fatal (unlinks already complete).

    Returns a PruneCounts with per-dimension counts and ordered inactive names.
    """
    if logger is None:
        def logger(msg):
            pass

    counts = PruneCounts()
    inactive_names: list[str] = []
    # Worktree-relative paths of every file successfully unlinked; fed to
    # update-index --skip-worktree after the unlink pass completes.
    pruned_rel_paths: list[str] = []

    for pname, manifest in sorted(inactive_manifests.items()):
        inactive_names.append(pname)

        # Agents — defense-in-depth: re-check reserved set at the point of
        # deletion so a manifest that bypassed the parser guard cannot cause
        # a universal agent to be permanently deleted (iss_1ba1e99d532a).
        for basename in manifest.agents:
            if basename.lower() in {n.lower() for n in DEFAULT_RESERVED_AGENTS}:
                raise ValueError(
                    f"prune: refusing to delete reserved agent {basename!r} "
                    f"(pipeline {pname!r}); this file is in DEFAULT_RESERVED_AGENTS."
                )
            target = worktree_root / '.claude' / 'agents' / basename
            if _unlink_one(target, f'prune: agent file missing: {target}', logger):
                counts.agents += 1
                pruned_rel_paths.append(str(target.relative_to(worktree_root)))

        # Hooks — defense-in-depth: re-check reserved set at the point of
        # deletion (iss_1ba1e99d532a).
        for hook_entry in manifest.hooks:
            if hook_entry.path in DEFAULT_RESERVED_HOOKS:
                raise ValueError(
                    f"prune: refusing to delete reserved hook {hook_entry.path!r} "
                    f"(pipeline {pname!r}); this file is in DEFAULT_RESERVED_HOOKS."
                )
            target = worktree_root / '.claude' / 'hooks' / hook_entry.path
            if _unlink_one(target, f'prune: hook file missing: {target}', logger):
                counts.hooks += 1
                pruned_rel_paths.append(str(target.relative_to(worktree_root)))

        # Rules
        for basename in manifest.rules:
            target = worktree_root / '.claude' / 'rules' / basename
            if _unlink_one(target, f'prune: rule file missing: {target}', logger):
                counts.rules += 1
                pruned_rel_paths.append(str(target.relative_to(worktree_root)))

        # Skills
        for basename in manifest.skills:
            target = worktree_root / '.claude' / 'skills' / basename
            # Collect contained file paths BEFORE rmtree using git ls-files so
            # only index-tracked files are included.  Physical rglob would pick
            # up __pycache__, .DS_Store, etc., which cause update-index to exit
            # non-zero and abort the entire batch.
            if target.is_dir() and not target.is_symlink():
                skill_file_rel_paths = _git_ls_files_for_dir(
                    worktree_root, target, logger
                )
            else:
                skill_file_rel_paths = None
            if _unlink_one(target, f'prune: skill file missing: {target}', logger):
                counts.skills += 1
                if skill_file_rel_paths is not None:
                    pruned_rel_paths.extend(skill_file_rel_paths)
                else:
                    pruned_rel_paths.append(str(target.relative_to(worktree_root)))

        # Rubrics — pipeline-owned, located under .claude/pipelines/<pname>/rubrics/<basename>
        for basename in manifest.rubrics:
            target = worktree_root / '.claude' / 'pipelines' / pname / 'rubrics' / basename
            if _unlink_one(target, f'prune: rubric file missing: {target}', logger):
                counts.rubrics += 1
                pruned_rel_paths.append(str(target.relative_to(worktree_root)))

    counts.inactive_names = tuple(inactive_names)

    if pruned_rel_paths:
        _set_skip_worktree_bits(worktree_root, pruned_rel_paths, logger)
        counts.skip_worktree_bits_set = len(pruned_rel_paths)

    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _git_ls_files_for_dir(
    worktree_root: pathlib.Path,
    skill_dir: pathlib.Path,
    logger,
) -> list[str]:
    """Return worktree-relative paths of index-tracked files under skill_dir.

    Uses `git ls-files` so untracked files (__pycache__, .DS_Store, etc.) are
    excluded from the result — passing untracked paths to update-index causes
    git to exit non-zero and abort the entire batch.

    On subprocess failure or OSError → log a warning and return [] (warn-and-
    continue contract; the skill directory still contributes zero paths to the
    skip-worktree batch but the prune itself is unaffected).
    """
    rel_prefix = str(skill_dir.relative_to(worktree_root))
    # Use :(literal,top) magic to prevent git from treating rel_prefix as a
    # glob/regex pathspec.  Without this a directory named "first-*" would
    # expand and return files from sibling skill directories whose names start
    # with "first-", violating the helper's contract to return only files under
    # skill_dir.
    literal_pathspec = f':(literal,top){rel_prefix}'
    try:
        result = subprocess.run(
            ['git', '-C', str(worktree_root), 'ls-files', literal_pathspec],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger(
            f'prune: git ls-files failed for {rel_prefix!r}: {exc}; '
            f'skip-worktree bits will not be set for this skill directory'
        )
        return []
    paths = [line for line in result.stdout.splitlines() if line]
    return paths


def _set_skip_worktree_bits(
    worktree_root: pathlib.Path,
    rel_paths: list[str],
    logger,
) -> None:
    """Set the git skip-worktree bit on all rel_paths in one batched call.

    Mirrors _apply_skip_worktree_bits in bin/claude-session: single subprocess
    spawn with paths piped to stdin.  A failure is logged but never fatal —
    the unlinks already happened and the caller's PruneCounts is already updated.
    """
    try:
        subprocess.run(
            ['git', '-C', str(worktree_root), 'update-index',
             '--skip-worktree', '--stdin'],
            input='\n'.join(rel_paths) + '\n',
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        logger(
            f'prune: update-index --skip-worktree failed ({e.returncode}); '
            f'phantom-D suppression incomplete — {len(rel_paths)} path(s) unprotected; '
            f'stderr: {e.stderr.strip()}'
        )
    except OSError as exc:
        # Covers FileNotFoundError (git binary missing), PermissionError, etc.
        logger(
            f'prune: update-index --skip-worktree subprocess error: {exc}; '
            f'phantom-D suppression incomplete — {len(rel_paths)} path(s) unprotected'
        )


def _unlink_one(path: pathlib.Path, missing_msg: str, logger) -> bool:
    """Remove path; return True on success, False on already-absent (silent) or OS error (logged)."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except FileNotFoundError:
        # Already absent is a no-op success case — no warning needed.
        # missing_msg param retained for caller compatibility but unused here.
        return False
    except OSError as exc:
        logger(f'prune: OS error unlinking {path}: {exc}')
        return False
