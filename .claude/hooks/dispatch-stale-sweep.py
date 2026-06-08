#!/usr/bin/env python3
# dispatch-child-safe: false
"""
Dispatch stale-sweep hook — registered on both Stop and UserPromptSubmit.

Belt to the dispatch-completion-watcher.py suspenders: this hook surfaces
any async-complete.json sidecars that are present on disk but absent from
the watcher's persisted seen-set.

Root cause addressed: if the watcher's poll window (MAX_WAIT_S=590s) expires
while a dispatch is still in flight AND all self-restart slots are consumed
(or the process was SIGTERM'd), the sidecar lands silently. Both hook contexts
provide a guaranteed surfacing opportunity:
  - UserPromptSubmit: next human input triggers the hook (operator regains
    attention anyway), surfacing via stdout additionalContext injection.
  - Stop: turn-end fires immediately; surfacing via stderr + exit 2 triggers
    asyncRewake wake-back, matching the shape of dispatch-completion-watcher.py.

Output contract (context-dependent):
  - Stop hook:             stderr + exit 2  (asyncRewake wake-back)
  - UserPromptSubmit hook: stdout + exit 0  (additionalContext injection)
  - Both: update the seen-set so repeat invocations are silent.
  - If none found:         exit 0 silently.

Hook-context detection: the harness delivers a JSON payload on stdin for every
invocation.  Stop payloads carry hook_event_name="Stop" (and stop_hook_active).
UserPromptSubmit payloads carry hook_event_name="UserPromptSubmit".
See turn-continuity-block.py:408 and cycle-hook.py:1568 for the same pattern.
"""
import datetime
import json
import math
import os
import shutil
import sys

# --- Session resolution (mirrors dispatch-completion-watcher.py) -----------

_SEEN_STATE_FILENAME = "dispatch-completion-watcher-seen.json"

# IP2/U4 SHARED staleness grace constant — mirrors schemas.ts:GRACE_MARGIN_S (= 60).
# Python cannot import the TS constant; this value MUST be kept in sync with
# .claude/mcp/context-tools/src/schemas.ts export const GRACE_MARGIN_S = 60.
# Three consumers share this formula: dispatch-agent-status.ts,
# dispatch-completion-watcher.py, and this sweep hook.
_GRACE_MARGIN_S: float = 60.0

# IP2/U4 SHARED live-phase subprocess timeout default — mirrors dispatch-agent-status.ts:DEFAULT_SUBPROCESS_TIMEOUT_S (= 3600).
# Python cannot import the TS constant; this value MUST be kept in sync with
# .claude/mcp/context-tools/src/tools/dispatch-agent-status.ts const DEFAULT_SUBPROCESS_TIMEOUT_S = 3600.
# Three consumers share this formula: dispatch-agent-status.ts,
# dispatch-completion-watcher.py, and this sweep hook.
_DEFAULT_SUBPROCESS_TIMEOUT_S: float = 3600.0

_DEFAULT_QUEUE_WAIT_TIMEOUT_SECONDS: float = 120.0

_DISPATCH_TERMINAL_ERROR_CODES = frozenset({
    "codex_lock_timeout",
    "codex_lock_host_fs_error",
    "subprocess_spawn_failed",
    "gpt_auth_mode_conflict",
    "gpt_governor_invalid_config",
    "gpt_governor_failed",
    "gpt_governor_timeout",
    "codex_auth_snapshot_invalid",
    "codex_auth_refresh_timeout",
    "codex_auth_refresh_uncertain",
    "auth_snapshot_expired",
})

_TERMINAL_DS_PHASES = frozenset({
    "lock_timeout",
    "lock_failed",
    "spawn_failed",
    "governor_timeout",
    "governor_failed",
    "auth_refresh_uncertain",
    "auth_snapshot_invalid",
    "auth_mode_conflict",
})


def _read_json_object(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_dispatch_state(child_dir: str) -> dict | None:
    """Parse dispatch-state.json for a child directory. Returns None on missing/malformed."""
    path = os.path.join(child_dir, "dispatch-state.json")
    return _read_json_object(path)


def _finite_number(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return default


def _parse_iso_epoch_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        return None


def _child_profile_start_seconds(child_dir: str) -> float | None:
    path = os.path.join(child_dir, "child-profile.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        metadata = data.get("compose_metadata") if isinstance(data, dict) else None
        composed_at_ms = metadata.get("composed_at_ms") if isinstance(metadata, dict) else None
        if isinstance(composed_at_ms, bool):
            return None
        if isinstance(composed_at_ms, (int, float)) and math.isfinite(float(composed_at_ms)):
            return float(composed_at_ms) / 1000.0
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return None


def _dispatch_state_mtime_seconds(child_dir: str) -> float | None:
    try:
        return os.path.getmtime(os.path.join(child_dir, "dispatch-state.json"))
    except OSError:
        return None


def _corrupt_dispatch_state_is_stale(child_dir: str, now: float) -> bool:
    started_at_s = _child_profile_start_seconds(child_dir)
    if started_at_s is None:
        started_at_s = _dispatch_state_mtime_seconds(child_dir)
    if started_at_s is None:
        return True
    return now - started_at_s > _DEFAULT_SUBPROCESS_TIMEOUT_S + _GRACE_MARGIN_S


def _dispatch_state_error_code(ds: dict) -> str | None:
    error_code = ds.get("error_code")
    return error_code if isinstance(error_code, str) and error_code in _DISPATCH_TERMINAL_ERROR_CODES else None


def _is_dispatch_state_terminal(ds: dict) -> bool:
    phase = ds.get("phase")
    return _dispatch_state_error_code(ds) is not None or phase in _TERMINAL_DS_PHASES


def _is_dispatch_state_stale(child_dir: str, ds: dict, now: float) -> bool:
    """Return True when dispatch-state indicates the child is stuck past its bound.

    Mirrors dispatch-agent-status.ts rung-2 staleness.  Corrupt required
    timestamps use a finite child-profile/mtime fallback, so malformed records
    cannot remain non-stale forever.
    """
    phase = ds.get("phase")
    if _is_dispatch_state_terminal(ds):
        return False
    if phase in ("queued", "spawning"):
        queued_at_s = _parse_iso_epoch_seconds(ds.get("queued_at"))
        if queued_at_s is None:
            return _corrupt_dispatch_state_is_stale(child_dir, now)
        queue_wait_sec = _finite_number(ds.get("queue_wait_timeout_seconds"), 0.0)
        if ds.get("queue_kind") == "governor":
            deadline_s = _parse_iso_epoch_seconds(ds.get("queue_deadline_at"))
            if deadline_s is None:
                deadline_s = queued_at_s + max(queue_wait_sec, _DEFAULT_QUEUE_WAIT_TIMEOUT_SECONDS)
            return now > deadline_s + _GRACE_MARGIN_S
        elapsed = now - queued_at_s
        return elapsed > queue_wait_sec + _GRACE_MARGIN_S
    if phase == "live":
        spawned_at_s = _parse_iso_epoch_seconds(ds.get("spawned_at"))
        if spawned_at_s is None:
            return _corrupt_dispatch_state_is_stale(child_dir, now)
        subproc_timeout_sec = _finite_number(
            ds.get("subprocess_timeout_seconds"),
            _DEFAULT_SUBPROCESS_TIMEOUT_S,
        )
        elapsed = now - spawned_at_s
        return elapsed > subproc_timeout_sec + _GRACE_MARGIN_S
    return False


def _resolve_session_dir() -> str | None:
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        return None
    return os.path.join(os.getcwd(), ".agent_context", "sessions", session_id)


# --- Seen-set read/write (same contract as watcher; shared file) -----------

def _read_seen_set(session_dir: str) -> set[str]:
    """Return the persisted set of already-reported spawn_ids.

    Treats absent file or any parse failure as empty set — degrades gracefully.
    """
    path = os.path.join(session_dir, _SEEN_STATE_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return set()
        ids = data.get("seen_spawn_ids", [])
        if not isinstance(ids, list):
            return set()
        return {str(x) for x in ids}
    except (OSError, ValueError, TypeError, AttributeError):
        return set()


def _write_seen_set(session_dir: str, seen: set[str]) -> None:
    """Atomically persist seen to {session_dir}/{_SEEN_STATE_FILENAME}."""
    path = os.path.join(session_dir, _SEEN_STATE_FILENAME)
    tmp_path = path + ".tmp"
    payload = {
        "seen_spawn_ids": sorted(seen),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except OSError:
        pass


# --- Child enumeration -----------------------------------------------------


def _path_inside(parent: str, child: str) -> bool:
    try:
        parent_abs = os.path.abspath(parent)
        child_abs = os.path.abspath(child)
        return (
            child_abs != parent_abs
            and os.path.commonpath([parent_abs, child_abs]) == parent_abs
        )
    except ValueError:
        return False


def _codex_home_candidates(child_dir: str, _ds: dict) -> list[str]:
    expected_home = os.path.join(child_dir, "codex-home")
    # dispatch-state is child-written; stale cleanup must not let it redirect deletion.
    if not _path_inside(child_dir, expected_home):
        return []
    return [expected_home]


def _rmtree_onerror(func, path, _exc_info) -> None:
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def _write_cleanup_failure(child_dir: str, detail: str) -> None:
    path = os.path.join(child_dir, "codex-home-stale-cleanup-failed.json")
    tmp_path = path + ".tmp"
    payload = {
        "cleanup_failed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "detail": detail,
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except OSError:
        pass


def _delete_stale_codex_home(child_dir: str, ds: dict) -> None:
    """Fail closed for stale gpt child homes by removing the whole local CODEX_HOME."""
    failures: list[str] = []
    for codex_home in _codex_home_candidates(child_dir, ds):
        if not os.path.lexists(codex_home):
            continue
        try:
            if os.path.islink(codex_home) or os.path.isfile(codex_home):
                os.unlink(codex_home)
            else:
                shutil.rmtree(codex_home, onerror=_rmtree_onerror)
        except OSError as exc:
            failures.append(f"{codex_home}: {exc}")
            continue
        if os.path.lexists(codex_home):
            failures.append(f"{codex_home}: path still exists after cleanup")
    if failures:
        _write_cleanup_failure(child_dir, "; ".join(failures))


def _list_completed_children(session_dir: str) -> set[str]:
    """Return spawn_ids whose dispatch has reached a terminal or stale state.

    Primary: async-complete.json present (unchanged authoritative witness).

    Extended per design IP5 / R11/F3:
    (a) dispatch-state.json carries a terminal error_code or terminal phase —
        terminal via dispatch-state (async-complete write may have been swallowed
        in the partial-failure residual — R9/R10 backstop).
    (b) dispatch-state.json is STALE per the SHARED staleness rule — a stuck
        queued/spawning/live child surfaced rather than silently never-completed.

    A dispatch-state.json-ONLY child that is NON-terminal and NON-stale is still
    in-flight and is NOT included.
    """
    children_dir = os.path.join(session_dir, "children")
    try:
        entries = os.listdir(children_dir)
    except FileNotFoundError:
        return set()

    result: set[str] = set()
    import time as _time
    now = _time.time()
    for entry in entries:
        child_dir = os.path.join(children_dir, entry)
        # Primary: authoritative async-complete.json witness.
        if os.path.isfile(os.path.join(child_dir, "async-complete.json")):
            result.add(entry)
            continue
        # (a)+(b): dispatch-state.json backstop for partial-failure residual and stale children.
        dispatch_state_path = os.path.join(child_dir, "dispatch-state.json")
        ds = _read_dispatch_state(child_dir)
        if ds is None:
            if os.path.isfile(dispatch_state_path) and _corrupt_dispatch_state_is_stale(child_dir, now):
                result.add(entry)
            continue
        if _is_dispatch_state_terminal(ds):
            # (a) Terminal phase written by producer — async-complete may be absent.
            result.add(entry)
        elif _is_dispatch_state_stale(child_dir, ds, now):
            result.add(entry)
        # else: in-flight, non-terminal, non-stale → NOT surfaced.
    return result


def _read_terminal_record(session_dir: str, spawn_id: str) -> dict | None:
    """Fallback record synthesizer for dispatch-state.json-only terminal entries.

    Called when async-complete.json is absent.  Reads dispatch-state.json and
    synthesizes a summary-compatible dict so _format_summary_line can surface it.
    Returns None if dispatch-state.json is also unreadable.

    Synthesized fields: exit_code=None, signal=None, route (from ds), elapsed_seconds=0,
    detail note indicating phase or stale.
    """
    child_dir = os.path.join(session_dir, "children", spawn_id)
    ds = _read_dispatch_state(child_dir)
    if ds is None:
        import time as _time
        if (
            os.path.isfile(os.path.join(child_dir, "dispatch-state.json"))
            and _corrupt_dispatch_state_is_stale(child_dir, _time.time())
        ):
            return {
                "exit_code": None,
                "signal": "stale-corrupt-dispatch-state",
                "route": "unknown",
                "elapsed_seconds": 0.0,
                "error_code": None,
                "retryable": None,
            }
        return None
    phase = ds.get("phase", "unknown")
    route = ds.get("route", "unknown")
    error_code = _dispatch_state_error_code(ds)
    retryable = ds.get("retryable") if isinstance(ds.get("retryable"), bool) else None
    # Build a note for the detail field consumed by _format_summary_line.
    import time as _time
    now = _time.time()
    is_stale = _is_dispatch_state_stale(child_dir, ds, now)
    if is_stale:
        note = f"stale-{phase}"
    else:
        note = f"dispatch-state-terminal:{phase}"
    if error_code is not None:
        note = f"{note}:{error_code}"
    record = {
        "exit_code": None,
        "signal": note,   # _format_summary_line renders signal as "signal=<name>" when non-null
        "route": route,
        "elapsed_seconds": 0.0,
        "error_code": error_code,
        "retryable": retryable,
    }
    if is_stale:
        # Synthesize the wake record before fail-closed credential cleanup.
        _delete_stale_codex_home(child_dir, ds)
    return record


def _read_sidecar(session_dir: str, spawn_id: str) -> dict | None:
    """Parse the sidecar JSON for spawn_id. Returns None on any parse error."""
    path = os.path.join(session_dir, "children", spawn_id, "async-complete.json")
    return _read_json_object(path)


def _format_summary_line(spawn_id: str, data: dict) -> str:
    """Format one completion summary line for the stale-sweep notice."""
    exit_code = data.get("exit_code")
    signal_name = data.get("signal")
    route = data.get("route", "unknown")
    elapsed = data.get("elapsed_seconds", 0.0)
    error_code = data.get("error_code")
    retryable = data.get("retryable")
    suffix = ""
    if isinstance(error_code, str):
        suffix += f" error_code={error_code}"
    if isinstance(retryable, bool):
        suffix += f" retryable={str(retryable).lower()}"

    if signal_name:
        return (
            f"spawn_id={spawn_id} exit_code=null signal={signal_name}"
            f" route={route} elapsed_seconds={elapsed:.1f}{suffix}"
        )
    return (
        f"spawn_id={spawn_id} exit_code={exit_code}"
        f" route={route} elapsed_seconds={elapsed:.1f}{suffix}"
    )


# --- Hook context detection -------------------------------------------------


def _detect_stop_hook_context() -> bool:
    """Return True when invoked as a Stop hook, False otherwise.

    The harness delivers a JSON payload on stdin for every hook invocation.
    Stop payloads carry hook_event_name="Stop" (present in both Claude Code and
    gemini adapters; confirmed via _codex_hook_adapter.py:291, cycle-hook.py:1568)
    or stop_hook_active=true (used by turn-continuity-block.py:408).
    UserPromptSubmit payloads carry hook_event_name="UserPromptSubmit".

    Returns False on any parse failure so bare-CLI, test-harness, and
    non-harness invocations preserve the historical UserPromptSubmit behavior.
    The isatty() guard prevents blocking in interactive terminal contexts.
    """
    if sys.stdin.isatty():
        # Interactive terminal — no harness JSON payload present.
        return False
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return False
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return False
        if payload.get("hook_event_name") == "Stop":
            return True
        # Secondary check: stop_hook_active is present on Stop payloads.
        if payload.get("stop_hook_active"):
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------


def _write_wake_sentinel(session_dir: str) -> None:
    """Write the rate-gate exemption sentinel before emitting the wake message.

    dispatch-status-rate-gate.py reads and atomically removes this file to exempt
    the first dispatch_agent_status call following a stale-sweep wake.  The file's
    mtime determines freshness; the rate-gate enforces a 30 s TTL.
    Failure is non-fatal: the rate-gate degrades to normal counter logic.
    """
    path = os.path.join(session_dir, "dispatch-stale-sweep-wake-pending")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    except OSError:
        pass


def main() -> None:
    session_dir = _resolve_session_dir()
    if session_dir is None:
        sys.exit(0)
    if not os.path.isdir(session_dir):
        sys.exit(0)

    completed = _list_completed_children(session_dir)
    seen = _read_seen_set(session_dir)

    unseen = completed - seen
    if not unseen:
        sys.exit(0)

    # Read sidecars for all unseen ids; fall back to dispatch-state.json synthesis
    # when async-complete.json is absent (partial-failure residual / stale child).
    readable: list[tuple[str, dict]] = []
    for spawn_id in sorted(unseen):
        data = _read_sidecar(session_dir, spawn_id)
        if data is None:
            # (c) Fallback: synthesize a summary-compatible record from dispatch-state.json.
            data = _read_terminal_record(session_dir, spawn_id)
        if data is not None:
            readable.append((spawn_id, data))

    if not readable:
        sys.exit(0)

    # Detect hook context: determines the delivery channel for the summary.
    is_stop_hook = _detect_stop_hook_context()

    # Build the summary (same content for both delivery channels).
    lines: list[str] = [
        f"dispatch-stale-sweep: {len(readable)} previously-unnoticed dispatch"
        f" completion(s) found on disk (watcher window expired before sidecar landed):"
    ]
    ids: list[str] = []
    for spawn_id, data in readable:
        lines.append(_format_summary_line(spawn_id, data))
        ids.append(spawn_id)

    ids_str = ",".join(ids)
    lines.append(
        f"Call mcp__context-tools__dispatch_agent_status with"
        f" spawn_ids=[{ids_str}] to read full results."
    )

    # Persist seen-set BEFORE writing output so a concurrent watcher starting
    # immediately after this exit reads the updated file (mirrors the ordering
    # invariant in dispatch-completion-watcher.py _emit_and_exit).
    new_seen = seen | {spawn_id for spawn_id, _ in readable}
    _write_seen_set(session_dir, new_seen)
    # Signal dispatch-status-rate-gate.py to exempt the next status poll (Fix 10
    # sentinel side-channel).  Written after seen-set update so both files land
    # before output is emitted, matching the ordering invariant comment above.
    _write_wake_sentinel(session_dir)

    summary = "\n".join(lines) + "\n"

    if is_stop_hook:
        # Stop hook: stderr + exit 2 triggers asyncRewake wake-back.
        # Message shape mirrors dispatch-completion-watcher.py _emit_and_exit.
        sys.stderr.write(summary)
        sys.stderr.flush()
        sys.exit(2)

    # UserPromptSubmit: stdout becomes additionalContext for the model turn.
    sys.stdout.write(summary)
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
