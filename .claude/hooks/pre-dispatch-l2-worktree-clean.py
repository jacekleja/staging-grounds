#!/usr/bin/env python3
"""pre-dispatch-l2-worktree-clean.py — PreToolUse:Skill hook (matcher: Skill).

Warns the L1-root orchestrator when it invokes `Skill(skill: "dispatch-l2", ...)`
while the parent worktree carries uncommitted edits. The L2 launcher
(`bin/claude-session` → `_create_worktree` with `parent_worktree_root`) branches
the child worktree from the parent's HEAD via
`git worktree add --detach <parent_head_sha>` — `git worktree add` reads commit
objects only, so any dirty path in L1's working tree is invisible to the L2
child. If those edits were load-bearing for the L2 task, the child silently
runs against stale parent state; the failure surfaces downstream as "the fix I
just made isn't there in L2."

This hook is the soft-warning floor for the WIP-invisibility discipline. The
primary design-time anchor lives in `.claude/orchestrator-prompt.md § L2 dispatch
— commit parent state first`; the active 6-step checklist the skill invocation
itself runs through lives in `.claude/skills/dispatch-l2/SKILL.md § Active Checklist (L1 caller)`.
This hook fires when both have failed — the orchestrator composed and issued
the Skill call without first cleaning up worktree state.

Mechanism: soft additionalContext warning. Mirrors
`pre-dispatch-loop-active-check.py`'s framing — this is reactive nudging, never
a hard block. The orchestrator decides whether the dirty paths are load-bearing
for the L2 task (the dispatch may have been intentional with documented WIP).
Matches `build-pass-gate.py`'s discipline-nudge contract, not a gate.

Scope of "dirty" — full `git status --porcelain` output, no path filter. The
auto-mirror argument (`bin/`, `.claude/hooks/`, `.claude/pipelines/` copied to
main's working tree on every edit) does NOT justify exempting those paths:
auto-mirror only updates main's working tree, but L2 reads commits from the
object DB via `git worktree add --detach <parent_sha>`, so dirty mirrored paths
are equally invisible. The invisibility argument is symmetric for ALL
non-symlinked tracked files. The hook lists the first 5 dirty paths and the
consequence; the orchestrator's judgement decides relevance.

Fail-open: any unexpected error (git crash, subprocess timeout, JSON decode
failure, missing session_id) exits 0 silently. A hook crash must NEVER block
dispatch; this is a discipline-nudge, not a gate. Subprocess errors on the
git invocation specifically return silently WITHOUT emitting a warning — a
warning containing an error message would be operator-confusing noise.

Parallel constraint reference:
`.claude/knowledge/constraints/path-c/worktree-local-edits-lost-across-cycle.md
§ Implications` — this hook is the symmetric L2-dispatch sibling of the
cycle-boundary edit-loss it documents.
"""
import json
import os
import subprocess
import sys


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    # Defensive: matcher is "Skill" but the hook contract does not guarantee
    # the matcher fired exactly on the named tool / skill name. Re-check both.
    if event.get("tool_name") != "Skill":
        return
    tool_input = event.get("tool_input", {}) or {}
    if tool_input.get("skill") != "dispatch-l2":
        return

    # Derive project root from script location (.claude/hooks/<this>.py).
    # event.cwd is unreliable here — Bash tool cwd changes can leak through.
    # The orchestrator's worktree IS the parent worktree at L2-dispatch time;
    # this script lives at <worktree>/.claude/hooks/<this>.py, so three
    # dirname climbs land on the worktree root.
    worktree_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Graceful fallback: if the worktree root isn't a git directory we are not
    # running under the expected layout; nothing to check against.
    if not os.path.isdir(os.path.join(worktree_root, ".git")) and not os.path.isfile(os.path.join(worktree_root, ".git")):
        return

    # Run git status --porcelain with a short timeout — a hung git must not
    # delay dispatch. Any subprocess failure (non-zero exit, timeout, missing
    # git binary) falls through to silent return; a warning containing the
    # error would be operator-confusing noise.
    try:
        result = subprocess.run(
            ["git", "-C", worktree_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return
    if result.returncode != 0:
        return

    porcelain = result.stdout.strip()
    if not porcelain:
        # Clean worktree — discipline honored, nothing to nudge.
        return

    dirty_lines = porcelain.splitlines()
    total = len(dirty_lines)
    sample = dirty_lines[:5]
    sample_block = "\n".join(f"  {line}" for line in sample)
    overflow_note = f"\n  ... and {total - 5} more" if total > 5 else ""

    warning = (
        f"Dispatching dispatch-l2 but the parent worktree at {worktree_root} "
        f"has {total} uncommitted path(s):\n"
        f"{sample_block}{overflow_note}\n"
        "L2 will branch from this worktree's HEAD via "
        "`git worktree add --detach <sha>`; uncommitted edits in the parent "
        "are not visible to L2. If any dirty path above is load-bearing for "
        "the L2 task, commit and push it BEFORE dispatching. If the dispatch "
        "is intentional and the dirty state is not relevant to the L2 task, "
        "ignore this warning. "
        "Design-time anchor: .claude/orchestrator-prompt.md § L2 dispatch — "
        "commit parent state first. "
        "Active 6-step checklist: .claude/skills/dispatch-l2/SKILL.md § Active Checklist (L1 caller)."
    )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"--- L2-DISPATCH WORKTREE-STATE WARNING ---\n{warning}\n"
                "--- END L2-DISPATCH WORKTREE-STATE WARNING ---"
            ),
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"pre-dispatch-l2-worktree-clean: unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)
