#!/usr/bin/env python3
# dispatch-child-safe: false
"""
turn-continuity-block.py — Stop hook that mechanically enforces no-mid-loop-yield.

Reads the Stop-hook JSON payload from stdin. Resolves the session_dir via
os.getcwd() + CLAUDE_SESSION_ID (same pattern as cycle-hook.py § main).
Checks for the `loop-active` sentinel and either blocks or allows the stop.

Decision branches:
  allow-absent        — sentinel absent: legitimate idle, exit 0 silently.
  allow-stop_hook_active — sentinel present but stop_hook_active=true: one-shot
                        retry is exhausted; allow stop to prevent infinite block.
  allow-bg-agent-active  — sentinel present, stop_hook_active=false, only legacy
                        Agent(run_in_background=true) is in flight: allow stop.
  allow-async-dispatch-inflight  — sentinel present, stop_hook_active=false, only
                        a dispatch_agent MCP subprocess is in flight (a
                        {session_dir}/children/<spawn_id>/ directory without an
                        async-complete.json sidecar): allow stop.
  allow-bg-agent-active-and-async-dispatch-inflight — sentinel present,
                        stop_hook_active=false, BOTH the legacy and the async
                        predicates fire: allow stop, distinct tag preserves the
                        concurrent state in audit-telemetry.jsonl.
  block               — sentinel present, stop_hook_active=false, no agent in
                        flight by either predicate: emit JSON decision to
                        stdout instructing orchestrator to continue.

Cycling-scoped short-circuit (Fix B1):
  When `{session_dir}/cycling-in-progress` exists alongside `loop-active`, the
  three Class-X allow tags are short-circuited and the hook proceeds directly
  to `block` (telemetry tag `cycling-short-circuit`).  While the sentinel is
  present, any externally-observable dispatch (Step 1.5 records-curator,
  Step 5 cycling-promoter, or any orphan from earlier turns) is cycling-
  internal noise rather than a signal that the orchestrator may yield turn.
  The short-circuit fires before Class-X is evaluated; it does NOT affect
  allow-absent or allow-stop_hook_active.

  Lifecycle (writer / clearer split):
    Writer: .claude/skills/cycling/SKILL.md § Step 0.5 (cycle-mode, handoff-mode, terminal-mode).
    Clearer (launcher, cycle-mode + handoff-mode): bin/claude-session's
      per-episode preamble unlinks the sentinel alongside loop-active before
      the next episode starts.  Both modes intentionally do NOT clear the
      sentinel inside the skill — keeping it armed through the Step 8
      checkpoint is required to prevent the Step 5 cycling-promoter's still-
      fresh subagent trace (within the 180s TTL) from triggering a Class-X
      release between Step 7.5 / 8.0 and the checkpoint, which would yield
      turn before SIGTERM and silently break the cycle.
    Clearer (skill, terminal-mode): terminal-mode HK-1a Step 4a and HK-1b
      Step 5 clear the sentinel before STOP — terminal-mode is the end of
      the cycling window in the session; the post-loop housekeeping
      subtasks (HK-2 / HK-3 / HK-4) must not inherit it.
    Clearer (skill, abort paths): every error/abort branch in /cycling that
      fires AFTER Step 0.5 (in-flight INFLIGHT, subagent-active abort,
      handoff-mode 1.1–1.5 ERRORs, terminal-mode HALT and failure paths)
      clears the sentinel before emitting its user-facing diagnostic.
      Abort paths cannot rely on the launcher sweep — they return without
      triggering SIGTERM, so the session continues and the sentinel must
      come down at the abort site.
    Clearer (hook, retry-exhaust safety net):
      _decide_allow_stop_hook_active_retry_exhaust clears BOTH sentinels
      (loop-active and cycling-in-progress) when stop_hook_active=true on
      a blocked stop — the one-shot retry is exhausted, the writer
      discipline already failed, and the next turn must start fresh.
      Without this defensive double-clear, a stranded cycling-in-progress
      would defeat future non-cycling Class-X paths in the same session.

Fail-safe: any unhandled exception in the outer scope is caught, a single
stderr line is written, and the hook exits 0.  A hook crash must NEVER
produce a stuck block.  Per the Axis 2 Argument Against item 3.

Telemetry: one record appended to {session_dir}/audit-telemetry.jsonl on
every fire.  Telemetry write is wrapped in try/except; IOError on telemetry
write must NOT block the decision branch.

Two-class gate-side carving (per hook-decomposition-eval.md § Gate observability):

  Class X — externally-observable in-flight state.  The
  any_fresh_subagent_active and any_inflight_async_dispatch predicates
  directly verify a child dispatch is still in flight without trusting any
  writer.  Maps to P1 (sync-mid-dispatch) in the writer-discipline taxonomy.

  Class Y — claimed in-flight state.  The loop-active sentinel is the
  writer's claim; no out-of-band verification path exists.  Maps to
  P2/P3/P4 (inter-dispatch lifecycle, multi-pass bounded loop, single-turn
  prose-only) — three writer-discipline classes collapse to one observable
  state from the gate's perspective.

The four decision branches encode this two-class carving correctly:
allow-absent and allow-stop_hook_active are pre-conditions of either class;
the three Class X allow-tags cover externally-observable in-flight; block
covers Class Y.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child


# ---------------------------------------------------------------------------
# Background-agent predicate — KEEP IN SYNC with cycle-hook.py:any_fresh_subagent_active()
# ---------------------------------------------------------------------------

def _get_sentinel_ttl(default=180):
    """Read sentinel_ttl from .claude/session-cycling.json with a default fallback.

    # KEEP IN SYNC with cycle-hook.py:_get_sentinel_ttl()
    """
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config = os.path.join(cwd, ".claude", "session-cycling.json")
    if os.path.exists(config):
        try:
            with open(config) as f:
                return int(json.load(f).get("sentinel_ttl", default))
        except (json.JSONDecodeError, ValueError, IOError):
            pass  # Ignore config errors — fall back to default
    return default


def any_fresh_subagent_active(session_id, session_dir, ttl_seconds=None):
    """Return True if any sentinel in the session sentinel dir is fresh.

    A sentinel is fresh if its mtime is within `ttl_seconds` of now.
    Stale sentinels are treated as absent.
    A missing directory is not an error; returns False.

    # KEEP IN SYNC with cycle-hook.py:any_fresh_subagent_active()
    # NOTE: cycle-hook.py derives sentinel_dir via _sentinel_session_dir(session_id);
    # this duplicate derives it inline from the caller-supplied session_dir.
    # Both produce the same path under the Path C symlink invariant (bin/claude-session
    # § _symlink_shared_state).  The duplicate is intentional per the project's
    # cross-language KEEP-IN-SYNC tolerance (see constraints/platform/hooks-behavior.md
    # § Additional consumers of any_fresh_subagent_active semantics).
    """
    if ttl_seconds is None:
        ttl_seconds = _get_sentinel_ttl()
    # FIX: use caller-supplied session_dir rather than __file__-derived cwd.
    sentinel_dir = os.path.join(session_dir, "subagent-active")
    try:
        entries = os.listdir(sentinel_dir)
    except (OSError, FileNotFoundError):
        return False
    now = time.time()
    for name in entries:
        full = os.path.join(sentinel_dir, name)
        try:
            age = now - os.path.getmtime(full)
        except OSError:
            continue
        if age < ttl_seconds:
            return True
    return False


# ---------------------------------------------------------------------------
# Async-dispatch TTL — configurable via env for non-standard deployments
# ---------------------------------------------------------------------------

def _parse_env_float(name: str, default: float) -> float:
    """Parse an env var as a positive float, falling back to `default` on error.

    Called at module scope; must never raise — any bad value silently returns
    the default to preserve the fail-safe contract (module docstring lines
    21-23).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        v = float(raw)
    except (ValueError, TypeError):
        return default
    return v if v > 0 else default


# Threshold equal to dispatch-agent-status.ts `unknown`-verdict boundary:
# DEFAULT_SUBPROCESS_TIMEOUT_S (3600) + GRACE_MARGIN_S (60) = 3660.
# Beyond this, the status tool itself returns `unknown`; a hook still
# treating the child as in-flight past 3660s would be the only place in
# the system believing the child is alive.
# Verified: dispatch-agent.ts:39, dispatch-agent-status.ts:30+36, index.ts:565.
_ASYNC_DISPATCH_TTL_S: float = _parse_env_float(
    "TURN_CONTINUITY_ASYNC_DISPATCH_TTL_S", 3660.0
)


def any_inflight_async_dispatch(session_id, session_dir, ttl_seconds=None):
    """Return True if any dispatch_agent MCP subprocess is currently in flight.

    An in-flight async dispatch is a {session_dir}/children/<spawn_id>/
    directory that LACKS an async-complete.json sidecar and whose mtime
    is within `ttl_seconds` of now.  Stale dirs (older than TTL) are
    treated as orphaned (e.g. timed-out by the wrapper SIGKILL ceiling
    without ever writing the sidecar) and excluded.

    A missing children/ directory is not an error; returns False.
    Per-entry I/O errors are swallowed with `continue`; never raises.

    # KEEP IN SYNC with dispatch-completion-watcher.py:_list_children()
    Both functions walk {session_dir}/children/<spawn_id>/ and key on
    the async-complete.json filename. The watcher returns spawn_ids
    WITH the sidecar (the completion set); this function returns True
    when any spawn_id is WITHOUT the sidecar (the in-flight inverse).
    Renaming the sidecar, relocating children/, or altering the
    directory-creation lifecycle in only one place silently breaks both.

    # Sidecar write site: .claude/mcp/context-tools/src/tools/dispatch-agent.ts
    # TTL source-of-truth: dispatch-agent.ts:39 (DEFAULT_SUBPROCESS_TIMEOUT_S 3600)
    #                    + dispatch-agent-status.ts:36 (GRACE_MARGIN_S 60) = 3660

    # Route-asymmetry RESOLVED (2026-05-26): dispatch-agent.ts now creates
    # children/<spawn_id>/ pre-spawn on ALL routes (claude-subprocess, gpt, gemini).
    # This predicate therefore observes in-flight dispatches on every route immediately
    # at dispatch time, not only at exit-callback time. dispatch-agent.ts is the
    # invariant owner. Historical context: .claude/knowledge/decisions/
    # turn-continuity-hardening.md § Anomaly — 2026-05-26.
    """
    if ttl_seconds is None:
        ttl_seconds = _ASYNC_DISPATCH_TTL_S
    children_dir = os.path.join(session_dir, "children")
    try:
        entries = os.listdir(children_dir)
    except (OSError, FileNotFoundError):
        return False
    now = time.time()
    for entry in entries:
        full = os.path.join(children_dir, entry)
        try:
            if not os.path.isdir(full):
                continue
            if os.path.exists(os.path.join(full, "async-complete.json")):
                # Completed — watcher will (or already did) read this sidecar.
                continue
            age = now - os.path.getmtime(full)
        except OSError:
            continue
        if age < ttl_seconds:
            return True
    return False


# ---------------------------------------------------------------------------
# Session-dir resolution
# ---------------------------------------------------------------------------

def _resolve_session_dir(session_id: str) -> str:
    """Derive the session dir from cwd + session_id.

    Mirrors cycle-hook.py § main: cwd is Claude Code's stable working dir
    (set at spawn by bin/claude-session; Bash tool cwd changes are isolated
    to child subprocesses and cannot leak back).
    """
    cwd = os.getcwd()
    return os.path.join(cwd, ".agent_context", "sessions", session_id)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def _write_telemetry(
    session_dir: str,
    session_id: str,
    decision: str,
    stop_hook_active: bool,
    loop_active_present: bool,
    tag: str = "",
) -> None:
    """Append one telemetry record to {session_dir}/audit-telemetry.jsonl.

    Failure is silent — telemetry is non-load-bearing per Subtask 9 failure-
    mode budget.  A full disk or missing dir must NOT affect the decision path.
    """
    try:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "event": "turn-continuity-block.fire",
            "session_id": session_id,
            "decision": decision,
            "stop_hook_active": stop_hook_active,
            "loop_active_present": loop_active_present,
        }
        if tag:
            record["tag"] = tag
        telemetry_path = os.path.join(session_dir, "audit-telemetry.jsonl")
        with open(telemetry_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # telemetry errors are silently swallowed
        pass


# ---------------------------------------------------------------------------
# Decision branches — named per Shape E (no behavior change)
# ---------------------------------------------------------------------------

def _decide_allow_absent(
    session_dir: str,
    session_id: str,
    sentinel_path: str,
    stop_hook_active: bool,
) -> None:
    """Sentinel absent — legitimate idle.  Allow, write telemetry, defensive clear.

    Covers no writer-discipline class — this is the 'nothing claimed' branch.
    Defensive os.unlink documents the post-allow invariant: after this branch
    returns, the sentinel MUST NOT exist.
    """
    _write_telemetry(
        session_dir, session_id, "allow-absent", stop_hook_active, False
    )
    try:
        os.unlink(sentinel_path)
    except FileNotFoundError:
        pass


def _decide_allow_stop_hook_active_retry_exhaust(
    session_dir: str,
    session_id: str,
    sentinel_path: str,
    stop_hook_active: bool,
) -> None:
    """One-shot retry exhausted — allow stop to prevent infinite block.

    This is the cap that prevents an infinite block when the writer
    discipline failed AND the orchestrator did not recover via the
    next-tool-call escape.  Defensively clears BOTH sentinels (loop-active
    and cycling-in-progress) — the one-shot retry is exhausted; the next
    turn must start fresh, and a stranded cycling-in-progress would defeat
    future non-cycling Class-X paths in the same session until the launcher
    sweep fires (next session) or an explicit cycling abort cleanup removes
    it.

    NOTE: defensive clear here is correct because the one-shot retry is
    spent.  Do NOT clear in _decide_allow_externally_observable_dispatch_in_flight
    — the orchestrator is still mid-loop there and a future yield in the
    same logical loop must still find the sentinel present.
    """
    print(
        "turn-continuity-block: stop_hook_active=true; one-shot retry exhausted,"
        " allowing stop to prevent infinite block.",
        file=sys.stderr,
    )
    _write_telemetry(
        session_dir, session_id, "allow-stop_hook_active", stop_hook_active, True
    )
    try:
        os.unlink(sentinel_path)
    except FileNotFoundError:
        pass
    try:
        os.unlink(os.path.join(session_dir, "cycling-in-progress"))
    except FileNotFoundError:
        pass


def _decide_allow_externally_observable_dispatch_in_flight(
    session_id: str,
    session_dir: str,
) -> tuple[str, str, str] | None:
    """Class X branch — return (decision, tag, msg) if externally-observable
    dispatch in flight, else None.

    Three sub-tags reachable:
      - allow-bg-agent-active                       (legacy sentinel fires)
      - allow-async-dispatch-inflight               (async children dir fires)
      - allow-bg-agent-active-and-async-dispatch-inflight  (BOTH fire)

    Both predicates are eagerly evaluated (NOT short-circuited) so the
    combined-tag branch is reachable when both signals fire simultaneously.
    audit-telemetry.jsonl preserves the concurrent state via the
    distinct combined tag.

    Covers P1 in the writer-discipline taxonomy.  Returns the (decision,
    tag, msg) triple rather than performing the print/telemetry write
    inline so the caller (main) remains the single site that emits the
    side effects.
    """
    bg_legacy = any_fresh_subagent_active(session_id, session_dir)
    bg_async = any_inflight_async_dispatch(session_id, session_dir)
    if not (bg_legacy or bg_async):
        return None
    if bg_legacy and bg_async:
        return (
            "allow-bg-agent-active-and-async-dispatch-inflight",
            "bg-agent-active-and-async-dispatch-inflight",
            "BOTH a legacy background subagent AND an async dispatch_agent"
            " subprocess are in flight",
        )
    if bg_legacy:
        return (
            "allow-bg-agent-active",
            "bg-agent-active-allow",
            "a fresh background subagent is in flight",
        )
    return (
        "allow-async-dispatch-inflight",
        "async-dispatch-inflight-allow",
        "an async dispatch_agent subprocess is in flight",
    )


def _block_with_canonical_reason(
    session_dir: str,
    session_id: str,
    sentinel_path: str,
    stop_hook_active: bool,
    tag: str = "",
) -> None:
    """Class Y branch — sentinel present, stop_hook_active=false, no agent
    in flight by either external predicate.  Emit JSON decision: block.

    Covers P2 (inter-dispatch lifecycle), P3 (multi-pass bounded loop), and
    P4 (single-turn prose-only) — three writer-discipline classes collapse
    to this single branch because the hook cannot verify them externally;
    it trusts the loop-active sentinel writer.

    The reason string enumerates the five canonical clear sites
    (a)-(e) per orchestrator-prompt § How work moves through you §
    No mid-loop turn-yield.

    Optional `tag` is forwarded into telemetry only — it does NOT alter the
    JSON reason emitted to the orchestrator.  Callers use it to distinguish
    block fires (e.g. `tag="cycling-short-circuit"` for the Fix B1 path)
    without splintering the reason text the orchestrator reads.
    """
    decision_payload = {
        "decision": "block",
        "reason": (
            f"loop-active sentinel present at {sentinel_path}. "
            "The orchestrator is mid-loop. Continue calling tools until you reach a clear site — "
            "immediately before: (a) final user-facing reply, (b) halt-and-surface, "
            "(c) completeness-risk surface awaiting operator confirm, "
            "(d) hard-to-reverse confirms; or (e) after `/cycling terminal` emits SESSION-COMPLETION-SENTINEL. "
            "Clear the sentinel BEFORE yielding turn to the user."
        ),
    }
    print(json.dumps(decision_payload), flush=True)
    _write_telemetry(
        session_dir, session_id, "block", stop_hook_active, True, tag=tag
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    exit_if_dispatched_child("turn-continuity-block")
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception as exc:
        print(f"turn-continuity-block: stdin parse error: {exc}", file=sys.stderr)
        return

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    stop_hook_active = bool(payload.get("stop_hook_active", False))

    try:
        if not session_id:
            # Cannot resolve session_dir; fail-open (allow stop).
            return
        session_dir = _resolve_session_dir(session_id)
        sentinel_path = os.path.join(session_dir, "loop-active")
        loop_active_present = os.path.exists(sentinel_path)
    except Exception as exc:
        print(
            f"turn-continuity-block: sentinel path resolution error: {exc}",
            file=sys.stderr,
        )
        return

    if not loop_active_present:
        _decide_allow_absent(session_dir, session_id, sentinel_path, stop_hook_active)
        return

    if stop_hook_active:
        _decide_allow_stop_hook_active_retry_exhaust(
            session_dir, session_id, sentinel_path, stop_hook_active
        )
        return

    # Cycling-scoped short-circuit (Fix B1) — when `/cycling` is mid-execution,
    # any externally-observable dispatch in flight is cycling-internal noise
    # (Step 1.5 records-curator, Step 5 cycling-promoter, or an orphan from a
    # prior turn).  Skip Class-X and block immediately.  This protects the
    # loop-active contract against unrelated in-flight dispatches without
    # changing Class-X behavior outside `/cycling` (other long-running loops
    # legitimately rely on the Class-X allow tags).
    cycling_sentinel = os.path.join(session_dir, "cycling-in-progress")
    if os.path.exists(cycling_sentinel):
        print(
            "turn-continuity-block: loop-active present AND cycling-in-progress"
            " set; Class-X allow paths short-circuited (cycling-internal"
            " dispatch); blocking.",
            file=sys.stderr,
        )
        _block_with_canonical_reason(
            session_dir,
            session_id,
            sentinel_path,
            stop_hook_active,
            tag="cycling-short-circuit",
        )
        return

    # Class X — externally-observable dispatch in flight?
    result = _decide_allow_externally_observable_dispatch_in_flight(
        session_id, session_dir
    )
    if result is not None:
        decision, tag, msg = result
        print(
            f"turn-continuity-block: loop-active present but {msg};"
            f" allowing stop ({tag}).",
            file=sys.stderr,
        )
        _write_telemetry(
            session_dir,
            session_id,
            decision,
            stop_hook_active,
            True,
            tag=tag,
        )
        return

    # Class Y — block.
    _block_with_canonical_reason(session_dir, session_id, sentinel_path, stop_hook_active)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Outer fail-safe: any unhandled exception must NOT block.
        # A non-zero exit here would not block (no "decision: block" was emitted),
        # but confusing stderr is itself a problem — catch and print one line.
        print(f"turn-continuity-block: unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)
