#!/usr/bin/env python3
"""Render-divergence check: is working_tree a clean render-output of HEAD?

R5: predicate is Case 1 (W == H byte-for-byte) OR Case 2 (baseline-cache
provenance: size + mtime_ns + sha256 all match recorded values). No
power-set enumeration. No render module imports. No registry argument.

stdlib-only — this module MUST NOT import orchestrator_prompt_render to
prevent an import cycle at render-time (launcher imports both).
"""
import hashlib
import json
import os
import subprocess
import sys


def is_clean_render(working_bytes, working_stat, head_template, baseline):
    """True iff working_tree is a clean render-output of head_template.

    Two-case predicate (R5):
      Case 1: working_bytes == head_template_bytes — admits the fresh-clone
              / worktree-rebuild state where the working tree is HEAD verbatim.
      Case 2: baseline JSON exists AND all three provenance fields match:
                size(W) == baseline.canonical_size,
                mtime_ns(W) == baseline.canonical_mtime_ns,
                sha256(W) == baseline.canonical_sha256.
              The mtime witness rules out byte-coincidence with prior
              renders (Y_1, Y_2) — any author edit bumps mtime.

    Returns False otherwise (missing/stale baseline, mtime mismatch, absent
    working-tree file, or any other non-admitted state).

    Bound: O(1) — three field comparisons + one sha256(working_bytes).
    """
    # Case 1: W == H byte-for-byte (fresh-clone / worktree-rebuild).
    head_bytes = head_template.encode('utf-8') if isinstance(head_template, str) else head_template
    if working_bytes == head_bytes:
        return True

    # Case 2: baseline-cache provenance.
    if baseline is None or working_stat is None:
        return False
    if working_stat.st_size != baseline.get("canonical_size"):
        return False
    if working_stat.st_mtime_ns != baseline.get("canonical_mtime_ns"):
        return False
    # Size and mtime match: compute sha256 as paranoid cross-check.
    w_sha = hashlib.sha256(working_bytes).hexdigest()
    if w_sha != baseline.get("canonical_sha256"):
        return False
    return True


def _cli():
    import argparse
    p = argparse.ArgumentParser(
        description="Render-divergence check for one render-target path."
    )
    p.add_argument('--path', required=True,
                   help='Repo-relative path of the render target.')
    p.add_argument('--worktree', required=True,
                   help='Absolute worktree root for `git -C`.')
    args = p.parse_args()

    canonical_abs = os.path.join(args.worktree, args.path)
    baseline_abs = os.path.join(
        args.worktree, ".agent_context", "render-baselines", args.path + ".json"
    )

    # HEAD-miss probe.
    try:
        head_bytes = subprocess.check_output(
            ['git', '-C', args.worktree, 'show', f'HEAD:{args.path}'],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        sys.exit(2)  # HEAD-miss — substantive by definition; HK-3 stages with -f

    # Working-tree probe.
    # R6 FIX (Finding #3): a missing working-tree file is a SUBSTANTIVE author
    # edit (pure-deletion, F10), not a HEAD-miss. Exit 1, not 2. Mechanically
    # both still trigger `git add -f` at HK-3, but the exit code now carries
    # the correct semantic — exit 2 is reserved for "HEAD has no entry for
    # this path", exit 1 covers every other substantive divergence including
    # the absent-working-tree case.
    try:
        with open(canonical_abs, 'rb') as fh:
            working_bytes = fh.read()
        working_stat = os.stat(canonical_abs)
    except FileNotFoundError:
        sys.exit(1)  # substantive divergence — author pure-deletion

    # Baseline probe.
    baseline = None
    if os.path.exists(baseline_abs):
        try:
            with open(baseline_abs, 'r', encoding='utf-8') as fh:
                baseline = json.load(fh)
        except (json.JSONDecodeError, OSError):
            baseline = None  # Treat corrupt as missing.

    head_template = head_bytes.decode('utf-8')
    sys.exit(0 if is_clean_render(working_bytes, working_stat, head_template, baseline) else 1)


if __name__ == '__main__':
    _cli()
