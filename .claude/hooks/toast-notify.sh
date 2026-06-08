#!/usr/bin/env bash
# Windows toast notification for Claude Code Notification events (WSL only).
# Reads hook JSON on stdin; extracts .cwd and .message; invokes PowerShell.
# Silently skips on non-WSL systems (no powershell.exe / wslpath).
#
# Two suppression paths gate the toast, evaluated cheapest-first:
#   1. {session_dir}/loop-active present → orchestrator is mid-loop; the Stop
#      hook will block the yield via turn-continuity-block.py. Even though
#      Notification is technically distinct from Stop, the user-facing
#      semantics are identical — the toast says "input requested" but the
#      session is not actually about to yield.
#   2. Fresh subagent-active sentinel present → orchestrator is waiting on
#      background fan-out (allow-bg-agent-active branch).
# See the suppression blocks below.
set -u

command -v powershell.exe >/dev/null 2>&1 || exit 0
command -v wslpath >/dev/null 2>&1 || exit 0

payload="$(cat)"
cwd="$(printf '%s' "$payload" | jq -r '.cwd // ""' 2>/dev/null || echo "")"
msg="$(printf '%s' "$payload" | jq -r '.message // "waiting for input"' 2>/dev/null || echo "waiting for input")"

title="Claude Code"
if [ -n "$cwd" ]; then
  title="Claude: $(basename "$cwd")"
fi

script_dir="$(dirname "$(readlink -f "$0")")"

# ---------------------------------------------------------------------------
# Suppress notification when the turn is not actually idle. Two predicates,
# evaluated cheapest-first.
#
# Suppression #1 — loop-active sentinel (cheap single -f test).
# KEEP IN SYNC with turn-continuity-block.py loop-active sentinel read —
# suppress idle signal when the orchestrator is mid-loop (the Stop hook will
# block the yield). turn-continuity-block.py reads
# {session_dir}/loop-active around lines 158-160 to drive its block/allow
# decision; this check mirrors that read so the toast does not fire during
# mid-loop yields.
#
# Suppression #2 — fresh subagent-active sentinel scan (python3 heredoc).
# KEEP IN SYNC with cycle-hook.py:any_fresh_subagent_active() for TTL
# semantics and sentinel-dir layout. Scans
# {project_root}/.agent_context/sessions/<id>/subagent-active/ for files with
# mtime within sentinel_ttl seconds of now (default 180s, overridable via
# .claude/session-cycling.json:sentinel_ttl).
#
# Fail-open: any error path falls through to the toast. Better to over-notify
# than under-notify when the suppression mechanism is itself broken.
# ---------------------------------------------------------------------------
session_id="${CLAUDE_SESSION_ID:-}"
if [ -n "$session_id" ]; then
  # Resolve project root via the same helper other hooks use; fall back to
  # git toplevel if find-project-root.sh is unavailable.
  project_root="$(bash "$script_dir/find-project-root.sh" 2>/dev/null || \
    git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || echo "")"
  if [ -n "$project_root" ]; then
    # Suppression #1: cheap loop-active sentinel check — short-circuits before
    # the more expensive subagent-active directory scan below.
    if [ -f "$project_root/.agent_context/sessions/$session_id/loop-active" ]; then
      exit 0  # orchestrator mid-loop — suppress toast
    fi

    # Suppression #2: fresh subagent-active sentinel scan.
    sentinel_dir="$project_root/.agent_context/sessions/$session_id/subagent-active"
    config_file="$project_root/.claude/session-cycling.json"
    bg_active="$(python3 - "$sentinel_dir" "$config_file" <<'PYEOF' 2>/dev/null || echo "0"
import json, os, sys, time
# KEEP IN SYNC with cycle-hook.py:any_fresh_subagent_active()
sentinel_dir, config_file = sys.argv[1], sys.argv[2]
ttl = 180
try:
    if os.path.exists(config_file):
        with open(config_file) as f:
            ttl = int(json.load(f).get("sentinel_ttl", 180))
except Exception:
    pass
try:
    entries = os.listdir(sentinel_dir)
except OSError:
    print("0"); sys.exit(0)
now = time.time()
for name in entries:
    try:
        if now - os.path.getmtime(os.path.join(sentinel_dir, name)) < ttl:
            print("1"); sys.exit(0)
    except OSError:
        continue
print("0")
PYEOF
)"
    bg_active="${bg_active:-0}"
    if [ "$bg_active" = "1" ]; then
      exit 0  # background work in flight — suppress toast
    fi
  fi
fi

exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File "$(wslpath -w "$script_dir/toast-notify.ps1")" \
  -Title "$title" -Message "$msg"
