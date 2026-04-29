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
import sys
from dataclasses import dataclass, field

# pipeline_manifest is in the same directory; let the caller's sys.path setup
# take care of the import if needed.  We only use PipelineManifest type here.
try:
    from pipeline_manifest import PipelineManifest
except ImportError:
    # Allow unit tests to import without sys.path surgery by inserting the bin
    # directory.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pipeline_manifest import PipelineManifest


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
      - .claude/rules/<basename>    for each entry in manifest.rules
      - .claude/skills/<basename>   for each entry in manifest.skills

    FileNotFoundError → warn-and-continue (idempotent; safe to call twice).
    Other OS errors → warn-and-continue (never fatal to the caller).

    Returns a PruneCounts with per-dimension counts and ordered inactive names.
    """
    if logger is None:
        def logger(msg):
            pass

    counts = PruneCounts()
    inactive_names: list[str] = []

    for pname, manifest in sorted(inactive_manifests.items()):
        inactive_names.append(pname)

        # Agents
        for basename in manifest.agents:
            target = worktree_root / '.claude' / 'agents' / basename
            if _unlink_one(target, f'prune: agent file missing: {target}', logger):
                counts.agents += 1

        # Hooks (HookEntry.path is the basename)
        for hook_entry in manifest.hooks:
            target = worktree_root / '.claude' / 'hooks' / hook_entry.path
            if _unlink_one(target, f'prune: hook file missing: {target}', logger):
                counts.hooks += 1

        # Rules
        for basename in manifest.rules:
            target = worktree_root / '.claude' / 'rules' / basename
            if _unlink_one(target, f'prune: rule file missing: {target}', logger):
                counts.rules += 1

        # Skills
        for basename in manifest.skills:
            target = worktree_root / '.claude' / 'skills' / basename
            if _unlink_one(target, f'prune: skill file missing: {target}', logger):
                counts.skills += 1

        # Rubrics — pipeline-owned, located under .claude/pipelines/<pname>/rubrics/<basename>
        for basename in manifest.rubrics:
            target = worktree_root / '.claude' / 'pipelines' / pname / 'rubrics' / basename
            if _unlink_one(target, f'prune: rubric file missing: {target}', logger):
                counts.rubrics += 1

    counts.inactive_names = tuple(inactive_names)
    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
