#!/usr/bin/env bash
# Find the CAA project root by walking up from cwd looking for .claude/ anchors.
#
# This mirrors the tiebreaking logic in detect-project-root.ts (Priority 1):
#   1. Collect ALL directories containing .claude/mcp/ or .claude/agents/
#   2. Tiebreak: prefer the one with .claude/.architecture-manifest.json
#   3. If no manifest, prefer the LOWEST-level (deepest, closest to cwd)
#   4. Fallback: git rev-parse --show-toplevel
#
# Tiebreaker rationale (rule 3 inversion, 2026-05-27, commit e2bc372e F3):
# When cwd is inside a sandbox-within-a-CAA-project, the SANDBOX is the
# correct project root, not the enclosing host project. The manifest
# discriminator (rule 2) already handles the "true outer project" case, so
# the no-manifest fallback only fires when neither candidate is explicitly
# tagged. Preferring lowest-level prevents hooks fired from a sandbox cwd
# from resolving to the host project and leaking telemetry there. Mirrors
# detect-project-root.ts:181-197 (findCaaRoot lowest-level-wins block).
#
# Usage (in settings.json hook commands):
#   python3 "$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/.claude/hooks/NAME.py"
#
# Or standalone:
#   .claude/hooks/find-project-root.sh
#   # prints the project root path to stdout
#
# Nested-git-repo diagnostic:
#   When the resolved CAA root differs from the git root, a diagnostic note is
#   emitted to stderr (mirrors detect-project-root.ts emitNestedRepoWarning()).
#   NOTE: The settings.json usage above pipes stderr to /dev/null (2>/dev/null),
#   so the diagnostic is only visible when this script is invoked directly (e.g.
#   for debugging or if the 2>/dev/null redirect is removed).

set -euo pipefail

_find_caa_root() {
  local candidates=()
  local p
  p="$(pwd)"

  # Walk up collecting candidates
  while [ "$p" != "/" ]; do
    if [ -d "$p/.claude/mcp" ] || [ -d "$p/.claude/agents" ]; then
      candidates+=("$p")
    fi
    p="$(dirname "$p")"
  done

  local count=${#candidates[@]}

  if [ "$count" -eq 0 ]; then
    # Fallback to git root
    if git rev-parse --show-toplevel 2>/dev/null; then
      return
    fi
    # Last resort: cwd
    pwd
    return
  fi

  local caa_root=""

  if [ "$count" -eq 1 ]; then
    caa_root="${candidates[0]}"
  else
    # Tiebreak: prefer candidate with .architecture-manifest.json
    local found_manifest=""
    for c in "${candidates[@]}"; do
      if [ -f "$c/.claude/.architecture-manifest.json" ]; then
        found_manifest="$c"
        break
      fi
    done

    if [ -n "$found_manifest" ]; then
      caa_root="$found_manifest"
    else
      # No manifest — prefer lowest-level (first in array, since we walk bottom-up).
      # Mirrors detect-project-root.ts:181-197 (F3, commit e2bc372e). See header
      # comment block for rationale.
      caa_root="${candidates[0]}"
      # Mirrors detect-project-root.ts emission so the tiebreaker is visible when
      # it fires. Add .claude/.architecture-manifest.json to the intended root if
      # this resolved wrong.
      echo "[find-project-root] INFO: tiebreaker fired (no .architecture-manifest.json found). Choosing lowest-level candidate: $caa_root (candidates: ${candidates[*]})." >&2
    fi
  fi

  echo "$caa_root"

  # Nested-git-repo diagnostic: if git root differs from CAA root, emit to stderr.
  # This mirrors detect-project-root.ts emitNestedRepoWarning().
  if command -v git >/dev/null 2>&1; then
    local git_root
    git_root="$(git rev-parse --show-toplevel 2>/dev/null)" || true
    if [ -n "$git_root" ] && [ "$git_root" != "$caa_root" ]; then
      echo "[find-project-root] INFO: Nested git repo detected — git root is $git_root, CAA project root is $caa_root. Using CAA project root." >&2
    fi
  fi
}

_find_caa_root
