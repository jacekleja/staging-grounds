#!/usr/bin/env python3
# dispatch-child-safe: false
"""
Dispatch-completion watcher — asyncRewake Stop hook.

Runs as a background process (asyncRewake: true in Stop hook registration).
Snapshots pre-existing async-complete.json sidecars at startup, polls for
new ones, and exits 2 with a coalesced stderr summary when any appear.
Exits 0 on timeout — the perpetual-self-wake guard that prevents an always-
exit-2 hook from locking the orchestrator into an infinite wake loop.

Mirrors session_dir resolution from cycle-hook.py and turn-continuity-block.py.

--- Seen-state persistence ---
Each invocation of this watcher is a fresh process (the harness spawns a new
one on every Stop event).  Without persistence, the watcher's in-process
baseline is empty, so it re-discovers already-consumed async-complete.json
sidecars and re-emits the wake on every subsequent Stop event.

To prevent that noise, we persist the set of already-reported spawn_ids to:

    {session_dir}/dispatch-completion-watcher-seen.json

Schema:
    {
        "seen_spawn_ids": ["1", "2", "8", ...],
        "updated_at": "<iso8601>"
    }

Lifecycle: the file is per-session (keyed by session_dir, which encodes the
CLAUDE_SESSION_ID).  A new session writes to a different path.  No explicit
cleanup is required; the file is left in place on session end and will be
ignored by future sessions that use a different session_dir.

The atomic write-temp-then-rename pattern prevents partial-write corruption if
the process is signalled mid-write.  Two concurrent watcher processes can
still race (both read the set, both see spawn_id X as new, both emit), but
this fires at most twice per spawn_id — vs. the previous behaviour of firing
on EVERY Stop event.  Eliminating the rare double-fire is out of scope.
"""
import datetime
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from _dispatch_child_guard import exit_if_dispatched_child, is_l2_child

# Add bin/ to sys.path so _dispatch_completion_lib can be imported.
# __file__ is .claude/hooks/dispatch-completion-watcher.py, so we need
# 3 .parent hops: hooks -> .claude -> repo-root, then / 'bin'.
# (2 hops would give .claude/bin, which is wrong.)
# This is the single genuinely-new wiring for the auto-reap fix (sketch A3/U1).
import pathlib as _pl
_BIN_DIR = str((_pl.Path(__file__).resolve().parent.parent.parent / 'bin'))
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)
import _dispatch_completion_lib as _dcl  # noqa: E402 -- must come after sys.path insert

# --- Overridable constants (env-override for testing) -----------------------

POLL_INTERVAL_S = float(
    os.environ.get("DISPATCH_COMPLETION_WATCHER_POLL_INTERVAL_S", "2")
)
MAX_WAIT_S = float(
    os.environ.get("DISPATCH_COMPLETION_WATCHER_MAX_WAIT_S", "1800")
)
MAX_INLINE_OUTPUT_BYTES_PER_SPAWN = int(
    os.environ.get("DISPATCH_COMPLETION_WATCHER_INLINE_BYTES_PER_SPAWN", "65536")
)
MAX_INLINE_OUTPUT_BYTES_TOTAL = int(
    os.environ.get("DISPATCH_COMPLETION_WATCHER_INLINE_BYTES_TOTAL", "262144")
)
# Maximum number of self-restarts via os.execv when in-flight dispatches remain.
# Each restart grants a fresh MAX_WAIT_S window and resets the harness 600s timer.
# 6 restarts × 590s ≈ 1 hour total — covers all observed planner/researcher wall-times.
_MAX_RESTARTS_DEFAULT = 6
_RESTART_COUNT_ENV = "CAA_DISPATCH_COMPLETION_WATCHER_RESTART_COUNT"
_MAX_RESTARTS_ENV = "CAA_DISPATCH_COMPLETION_WATCHER_MAX_RESTARTS"

# Per-invocation structured logging — defaults ON; set to "0" to disable.
# See sessions/.../diagnosis-async-wake-misfire.md § Move 1 for the
# verification rationale (distinguishing H1 execve-timeout-preservation from
# H3 sidecar-landed-during-startup-race).
_DEBUG_LOG = os.environ.get("DISPATCH_COMPLETION_WATCHER_DEBUG_LOG", "1") == "1"
_LOG_FILENAME = "dispatch-completion-watcher.log"

# Process start time captured at module load — referenced by _log_event so
# elapsed_s reflects time since THIS process's start.  execve replaces the
# process image, so after a self-restart the Python module re-loads and these
# values are correct per-generation.
_PROCESS_START_MONOTONIC = time.monotonic()

# IP2/U4 SHARED staleness grace constant — mirrors schemas.ts:GRACE_MARGIN_S (= 60).
# Python cannot import the TS constant; this value MUST be kept in sync with
# .claude/mcp/context-tools/src/schemas.ts export const GRACE_MARGIN_S = 60.
# Three consumers share this formula: dispatch-agent-status.ts, this watcher,
# and dispatch-stale-sweep.py.
GRACE_MARGIN_S: float = 60.0

# IP2/U4 SHARED live-phase subprocess timeout default — mirrors dispatch-agent-status.ts:DEFAULT_SUBPROCESS_TIMEOUT_S (= 3600).
# Python cannot import the TS constant; this value MUST be kept in sync with
# .claude/mcp/context-tools/src/tools/dispatch-agent-status.ts const DEFAULT_SUBPROCESS_TIMEOUT_S = 3600.
# Three consumers share this formula: dispatch-agent-status.ts, this watcher,
# and dispatch-stale-sweep.py.
DEFAULT_SUBPROCESS_TIMEOUT_S: float = 3600.0

# Shared queued governor fallback default — mirrors schemas.ts
# DEFAULT_QUEUE_WAIT_TIMEOUT_SECONDS (= 120).  Governor queue records normally
# carry queue_deadline_at; this is only the corrupt/missing-deadline floor.
DEFAULT_QUEUE_WAIT_TIMEOUT_SECONDS: float = 120.0

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

# --------------------------------------------------------------------------


def _read_json_object(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_dispatch_state(child_dir: str) -> dict | None:
    """Parse dispatch-state.json for a child directory.  Returns None on missing/malformed."""
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
    return now - started_at_s > DEFAULT_SUBPROCESS_TIMEOUT_S + GRACE_MARGIN_S


def _dispatch_state_error_code(ds: dict) -> str | None:
    error_code = ds.get("error_code")
    return error_code if isinstance(error_code, str) and error_code in _DISPATCH_TERMINAL_ERROR_CODES else None


def _is_dispatch_state_terminal(ds: dict) -> bool:
    phase = ds.get("phase")
    return _dispatch_state_error_code(ds) is not None or phase in _TERMINAL_DS_PHASES


def _is_dispatch_state_stale(child_dir: str, ds: dict, now: float) -> bool:
    """Return True when the dispatch-state indicates the child is stuck past its bound.

    Mirrors dispatch-agent-status.ts rung-2 staleness — three-consumer SHARED contract
    (R11/F3).  now is epoch-seconds (time.time()).

    governor queued: stale when now > queue_deadline_at + GRACE_MARGIN_S;
                     malformed/missing deadline falls back to
                     queued_at + max(queue_wait_timeout_seconds, DEFAULT_QUEUE_WAIT_TIMEOUT_SECONDS)
    auth queued:     stale when now - queued_at > queue_wait_timeout_seconds + GRACE_MARGIN_S
    live:            stale when now - spawned_at > subprocess_timeout_seconds + GRACE_MARGIN_S

    Corrupt required timestamps fall through to a finite child-profile/mtime
    fallback so a malformed dispatch-state cannot remain in-flight forever.
    """
    phase = ds.get("phase")
    if _is_dispatch_state_terminal(ds):
        return False
    if phase in ("queued", "spawning"):
        queued_at_s = _parse_iso_epoch_seconds(ds.get("queued_at"))
        if queued_at_s is None:
            return _corrupt_dispatch_state_is_stale(child_dir, now)
        queue_wait_sec = _finite_number(ds.get("queue_wait_timeout_seconds"), 0.0)
        queue_kind = ds.get("queue_kind")
        if queue_kind == "governor":
            deadline_s = _parse_iso_epoch_seconds(ds.get("queue_deadline_at"))
            if deadline_s is None:
                deadline_s = queued_at_s + max(queue_wait_sec, DEFAULT_QUEUE_WAIT_TIMEOUT_SECONDS)
            return now > deadline_s + GRACE_MARGIN_S
        elapsed = now - queued_at_s
        return elapsed > queue_wait_sec + GRACE_MARGIN_S
    elif phase == "live":
        spawned_at_s = _parse_iso_epoch_seconds(ds.get("spawned_at"))
        if spawned_at_s is None:
            return _corrupt_dispatch_state_is_stale(child_dir, now)
        subproc_timeout_sec = _finite_number(
            ds.get("subprocess_timeout_seconds"),
            DEFAULT_SUBPROCESS_TIMEOUT_S,
        )
        elapsed = now - spawned_at_s
        return elapsed > subproc_timeout_sec + GRACE_MARGIN_S
    return False


def _log_event(session_dir: str | None, event: str, **fields: object) -> None:
    """Append one JSON line per event to {session_dir}/dispatch-completion-watcher.log.

    Atomic per-record on POSIX: opens with O_APPEND, single os.write() call,
    record is well under PIPE_BUF (4096 bytes) so concurrent watcher writes
    interleave cleanly at record boundaries.

    No-op when _DEBUG_LOG is false, or when session_dir is None (bare-CLI
    context where we have nowhere to write).  All exceptions are swallowed
    silently — instrumentation must NEVER break the watcher's primary job.

    Standard fields on every record:
        ts          — ISO-8601 UTC timestamp with timezone
        pid         — os.getpid()
        ppid        — os.getppid()
        session_id  — CLAUDE_SESSION_ID env var (empty string if unset)
        generation  — execve generation counter (0 = first start, N = after N restarts)
        elapsed_s   — seconds since this process's start (resets on execve)
        event       — caller-supplied event-kind string
        **fields    — caller-supplied event-specific fields
    """
    if not _DEBUG_LOG or session_dir is None:
        return
    try:
        generation = int(os.environ.get(_RESTART_COUNT_ENV, "0"))
    except ValueError:
        generation = -1
    record: dict[str, object] = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
        "generation": generation,
        "elapsed_s": round(time.monotonic() - _PROCESS_START_MONOTONIC, 3),
        "event": event,
    }
    record.update(fields)
    try:
        line = json.dumps(record, default=str) + "\n"
        fd = os.open(
            os.path.join(session_dir, _LOG_FILENAME),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        # Best-effort: log writes must never break the watcher.
        pass


def _resolve_session_dir() -> str | None:
    """Return {cwd}/.agent_context/sessions/{CLAUDE_SESSION_ID} or None.

    Mirrors turn-continuity-block.py § _resolve_session_dir and cycle-hook.py
    § main.  Returns None when CLAUDE_SESSION_ID is absent (bare-CLI /
    non-session-managed context — cycle-hook-skips-bare-claude-print pattern).
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        return None
    return os.path.join(os.getcwd(), ".agent_context", "sessions", session_id)


_SEEN_STATE_FILENAME = "dispatch-completion-watcher-seen.json"


def _read_seen_set(session_dir: str) -> set[str]:
    """Return the persisted set of already-reported spawn_ids.

    Treats absent file or any parse failure as an empty set — the watcher
    degrades to pre-persistence behaviour (may re-emit) rather than crashing.
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
    """Atomically persist seen to {session_dir}/{_SEEN_STATE_FILENAME}.

    Uses write-temp-then-rename so a concurrent SIGTERM cannot produce a
    partially-written file.  Silently swallows OS errors (best-effort write;
    the next invocation will re-read whatever is on disk).
    """
    path = os.path.join(session_dir, _SEEN_STATE_FILENAME)
    tmp_path = path + ".tmp"
    payload = {
        "seen_spawn_ids": sorted(seen),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)  # atomic on POSIX
    except OSError:
        # Best-effort: if the write fails the next watcher will re-discover
        # the same sidecars (harmless re-emit, not a crash).
        pass


def _list_children(session_dir: str) -> set[str]:
    """Return the set of spawn_id dirs that have async-complete.json present.

    Wraps os.listdir in try/except FileNotFoundError so the caller is insulated
    from the pre-first-dispatch window when children/ does not yet exist.
    """
    children_dir = os.path.join(session_dir, "children")
    try:
        entries = os.listdir(children_dir)
    except FileNotFoundError:
        return set()

    result: set[str] = set()
    for entry in entries:
        sidecar = os.path.join(children_dir, entry, "async-complete.json")
        if os.path.isfile(sidecar):
            result.add(entry)
    return result


def _list_inflight_children(session_dir: str) -> set[str]:
    """Return spawn_ids for dispatches that are in-flight (not yet complete/terminal/stale).

    Widened from the previous predicate (has_profile and not has_sidecar) to also
    count dispatch-state.json-present children, per design IP4 / R11/F3.

    Exclusions (child is NOT counted as in-flight):
      1. async-complete.json present → authoritative terminal witness.
      2. dispatch-state.json carries a terminal error_code or terminal phase →
         terminal via dispatch-state (async-complete write may have been swallowed).
      3. dispatch-state.json is STALE per the SHARED staleness rule (R11/F3) →
         stuck queued/spawning/live past its timeout bound.

    Only _list_children/_emit_and_exit key on async-complete.json; this function
    governs the self-restart predicate only.
    """
    children_dir = os.path.join(session_dir, "children")
    try:
        entries = os.listdir(children_dir)
    except FileNotFoundError:
        return set()

    result: set[str] = set()
    now = time.time()
    for entry in entries:
        child_dir = os.path.join(children_dir, entry)
        # Authoritative terminal witness present → NOT in-flight.
        if os.path.isfile(os.path.join(child_dir, "async-complete.json")):
            continue
        dispatch_state_path = os.path.join(child_dir, "dispatch-state.json")
        ds = _read_dispatch_state(child_dir)
        if ds is not None:
            phase = ds.get("phase")
            # Terminal via dispatch-state (async-complete may be absent due to
            # partial-failure residual — R9/R10 backstop).
            if _is_dispatch_state_terminal(ds):
                continue
            # SHARED staleness rule (R11/F3): stuck past its timeout bound → NOT in-flight.
            if _is_dispatch_state_stale(child_dir, ds, now):
                continue
        elif os.path.isfile(dispatch_state_path):
            if not _corrupt_dispatch_state_is_stale(child_dir, now):
                result.add(entry)
            continue
        # Count as in-flight when child-profile.json is present OR dispatch-state.json
        # was readable (covers gemini/gpt routes that may not write child-profile.json).
        if os.path.isfile(os.path.join(child_dir, "child-profile.json")) or ds is not None:
            result.add(entry)
    return result


def _read_sidecar(session_dir: str, spawn_id: str) -> dict | None:
    """Parse the sidecar JSON for spawn_id.  Returns None on any parse error.

    Malformed-sidecar handling: skip on error (per sketch § Open decisions 3).
    The producer writes via atomic tmp+rename, so partial reads are not the
    expected failure mode, but defensive parse-failure handling is still correct.
    """
    path = os.path.join(session_dir, "children", spawn_id, "async-complete.json")
    return _read_json_object(path)


def _format_summary_line(
    spawn_id: str, data: dict, remaining_total_budget: int
) -> tuple[str, int]:
    """Format one per-spawn-id line and return (line, bytes_consumed).

    Signal-killed children (signal != null AND output_text == null) render as
    'exit_code=null signal=<NAME>' with bytes_consumed=0 (budget unchanged).
    Normal exits append an output_text here-doc block if within budget.
    """
    exit_code = data.get("exit_code")
    signal_name = data.get("signal")
    route = data.get("route", "unknown")
    elapsed = data.get("elapsed_seconds", 0.0)
    output_text = data.get("output_text")

    post_spawn_warnings = data.get("post_spawn_warnings") or []
    # Suffix appended after the primary line when warnings are present.
    warnings_suffix = "".join(
        f"\n  post_spawn_warning: {w}" for w in post_spawn_warnings
    )

    if signal_name:
        # Signal-killed: exit_code is null; render both fields; do not advance budget.
        return (
            f"spawn_id={spawn_id} exit_code=null signal={signal_name}"
            f" route={route} elapsed_seconds={elapsed:.1f}"
            + warnings_suffix,
            0,
        )

    base_line = (
        f"spawn_id={spawn_id} exit_code={exit_code}"
        f" route={route} elapsed_seconds={elapsed:.1f}"
    )

    if not output_text:
        # None or empty string — no output to inline; budget unchanged.
        return base_line + warnings_suffix, 0

    output_bytes = output_text.encode("utf-8")
    allowed = min(MAX_INLINE_OUTPUT_BYTES_PER_SPAWN, max(0, remaining_total_budget))

    if allowed == 0:
        # Aggregate budget exhausted — bare fallback marker, no here-doc.
        return base_line + f" call_dispatch_agent_status_for={spawn_id}" + warnings_suffix, 0

    if len(output_bytes) <= allowed:
        line = (
            base_line
            + f"\noutput_text=<<<EOF\n{output_text}\nEOF"
            + warnings_suffix
        )
        return line, len(output_bytes)

    # Truncate to allowed bytes, decode back to str (drop any partial multi-byte char).
    truncated = output_bytes[:allowed].decode("utf-8", errors="ignore")
    line = (
        base_line
        + f"\noutput_text=<<<EOF\n{truncated}"
        + f"\n... [truncated; call dispatch_agent_status(spawn_ids=[{spawn_id}]) for full output]"
        + "\nEOF"
        + warnings_suffix
    )
    return line, allowed


def _find_project_root() -> str:
    """Walk up from this script's directory until bin/claude-session is found."""
    start = Path(__file__).resolve().parent
    for parent in [start, *start.parents]:
        if (parent / "bin" / "claude-session").exists():
            return str(parent)
    raise FileNotFoundError(
        f"Could not locate project root (no bin/claude-session found) "
        f"searching upward from {start}"
    )


def _maybe_auto_reap(session_dir: str, spawn_id: str) -> None:
    """Delegate to shared maybe_auto_reap helper; preserve external behavior.

    Called inside _emit_and_exit BEFORE the watcher's stderr emit so that by the
    time L1 reads the wake message the registry row is already reaped and the
    children-active sentinel is gone. Swallows all errors so a reap failure never
    blocks the wake signal.

    auto_reap_invoked / auto_reap_failed are emitted via the log_event callback so
    they continue to land in dispatch-completion-watcher.log unchanged.
    """
    _dcl.maybe_auto_reap(
        session_dir, spawn_id,
        log_event=lambda event, **f: _log_event(session_dir, event, **f),
    )


def _emit_and_exit(
    session_dir: str, new_spawn_ids: set[str], seen: set[str]
) -> None:
    """Build the coalesced stderr summary and exit 2.

    Snapshot the unseen-set once at the start of this branch (OEQ-D edge case:
    a sidecar landing after this snapshot is missed by THIS run but correctly
    classified as new by the NEXT run's fresh baseline).

    seen is the full accumulated set (persisted + new); it is written to the
    seen-state file before exit so the next watcher process skips these ids.
    """
    # Deterministic ordering by spawn_id ascending (per sketch § Open decisions 5).
    sorted_ids = sorted(new_spawn_ids)

    lines: list[str] = [
        f"dispatch-completion-watcher: {len(sorted_ids)} dispatch(es) completed."
    ]

    remaining_total_budget = MAX_INLINE_OUTPUT_BYTES_TOTAL

    readable_ids: list[str] = []
    for spawn_id in sorted_ids:
        data = _read_sidecar(session_dir, spawn_id)
        if data is None:
            # Sidecar became unreadable between detection and now — skip (best-effort).
            continue
        (line, bytes_consumed) = _format_summary_line(spawn_id, data, remaining_total_budget)
        remaining_total_budget -= bytes_consumed
        lines.append(line)
        readable_ids.append(spawn_id)
        if data.get("route") == "l2-dispatch":
            _maybe_auto_reap(session_dir, spawn_id)

    if not readable_ids:
        # All sidecars unreadable — exit 0 to avoid a spurious wake.
        _log_event(
            session_dir,
            "emit_all_unreadable",
            requested_spawn_ids=sorted_ids,
        )
        sys.exit(0)

    ids_str = ",".join(readable_ids)
    lines.append(
        f"Call mcp__context-tools__dispatch_agent_status with"
        f" spawn_ids=[{ids_str}] to read full results."
    )

    # Persist the updated seen-set BEFORE writing stderr so that a concurrent
    # watcher which starts immediately after this exit can read the file.
    _write_seen_set(session_dir, seen)

    summary = "\n".join(lines) + "\n"
    _log_event(
        session_dir,
        "emit",
        spawn_ids=readable_ids,
        summary_length_bytes=len(summary.encode("utf-8")),
        seen_set_size_after=len(seen),
    )
    sys.stderr.write(summary)
    sys.stderr.flush()
    sys.exit(2)


def main() -> None:
    # Dispatched-child guard: this watcher monitors children spawned BY a
    # dispatcher. A dispatched child has no children of its own, so the poll
    # loop would block for MAX_WAIT_S (~590s) accomplishing nothing.
    # Keyed on CAA_DISPATCH_CHILD (set ONLY by dispatch-agent.ts) to avoid
    # false-positive early-exit for L2-sidecar children, which share
    # CAA_CHILD_SIDECAR_DIR but should NOT suppress this watcher.
    # See sessions/1779681158-51095-e94eb5ba185b/subprocess-hang-diagnosis.md
    # and iss_7f182efcf5b8.
    exit_if_dispatched_child()

    session_dir = _resolve_session_dir()

    # Guard: no session_dir → exit 0 silently (bare-CLI / non-orchestrator context).
    if session_dir is None:
        sys.exit(0)

    # Guard: session dir itself doesn't exist → exit 0 silently.
    if not os.path.isdir(session_dir):
        sys.exit(0)

    # L2-leaf short-circuit: leaf L2 child orchestrators never dispatch further
    # children, so the 590s polling loop would block Stop for ~10min watching
    # for sidecars that will never appear. Exit 0 early when this is an L2
    # context AND no past or in-flight child dispatches exist. A non-leaf L2
    # (one that does dispatch further) lights up children/ and falls through to
    # the regular poll loop. See sessions/1779998598-3026319-d1a1cc464d69/
    # diagnosis-l2-suborch-hang-after-terminal.md (DR-1 root cause).
    if is_l2_child():
        if not _list_children(session_dir) and not _list_inflight_children(session_dir):
            sys.exit(0)

    # SIGTERM handler: set flag so the poll loop exits cleanly without exit-2.
    # A SIGTERM-induced exit-2 would deliver a spurious wake to a model that
    # is about to terminate or has been cycled — specifically what we avoid.
    # The log_event call inside the handler is the diagnostic load-bearing line
    # for H1 (sees the harness-timeout SIGTERM directly rather than inferring
    # from absence).  Per Python signal semantics the handler runs on the main
    # thread between bytecode instructions; the log_event call is safe here.
    _shutdown_requested = False

    def _handle_sigterm(signum: int, frame: object) -> None:
        nonlocal _shutdown_requested
        _log_event(
            session_dir,
            "sigterm",
            signum=signum,
        )
        _shutdown_requested = True

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Load the persisted seen-set from the previous watcher invocation so that
    # already-reported spawn_ids are excluded even across process restarts.
    # Absent or corrupted file → empty set (degrades to pre-persistence behaviour).
    persisted_seen: set[str] = _read_seen_set(session_dir)

    # Snapshot baseline: spawn_ids whose sidecar exists at process start are
    # "pre-existing" and excluded from "new" on every subsequent poll tick.
    # Union with persisted_seen so that sidecars seen by a previous watcher
    # process are also excluded (this is the W4 fix).
    baseline: set[str] = _list_children(session_dir) | persisted_seen

    # emitted_this_run tracks sidecars we already emitted in this process
    # lifetime (OEQ-D coalescing: a sidecar cannot be double-delivered within
    # one watcher run even if the directory listing races across two ticks).
    emitted_this_run: set[str] = set()

    restart_count = int(os.environ.get(_RESTART_COUNT_ENV, "0"))
    max_restarts = int(os.environ.get(_MAX_RESTARTS_ENV, str(_MAX_RESTARTS_DEFAULT)))

    deadline = time.monotonic() + MAX_WAIT_S

    _log_event(
        session_dir,
        "start",
        baseline_size=len(baseline),
        persisted_seen_size=len(persisted_seen),
        max_wait_s=MAX_WAIT_S,
        poll_interval_s=POLL_INTERVAL_S,
        max_restarts=max_restarts,
        debug_log_enabled=_DEBUG_LOG,
    )

    while time.monotonic() < deadline:
        if _shutdown_requested:
            _log_event(
                session_dir,
                "exit_sigterm",
                emitted_this_run=sorted(emitted_this_run),
            )
            sys.exit(0)

        current = _list_children(session_dir)
        new_this_tick = current - baseline - emitted_this_run

        if new_this_tick:
            _log_event(
                session_dir,
                "new_sidecars_detected",
                spawn_ids=sorted(new_this_tick),
                count=len(new_this_tick),
            )
            # Record as emitted before exiting so the set is consistent even
            # though we exit immediately after (defensive against future refactor).
            emitted_this_run.update(new_this_tick)
            # Pass the full accumulated seen set (persisted + new) so
            # _emit_and_exit can write it back to disk before exit.
            _emit_and_exit(session_dir, new_this_tick, persisted_seen | new_this_tick)
            # _emit_and_exit always calls sys.exit — unreachable.

        time.sleep(POLL_INTERVAL_S)

    # Timeout reached. Check for in-flight dispatches before exiting.
    inflight = _list_inflight_children(session_dir)
    if inflight and restart_count < max_restarts:
        # Self-restart via execv to get a fresh harness timer (the harness
        # timeout: 600 caps this process's wall-time; execv resets it).
        # The restart counter is passed via env so the restarted process
        # knows how many restarts have already occurred.
        # The H1-falsification claim asserts execve grants a fresh 590s; the
        # log line here records elapsed_s at the moment of restart so the next
        # generation's "start" log can be compared against this one's "elapsed_s"
        # to verify whether the harness timer actually reset.
        _log_event(
            session_dir,
            "timeout_execve",
            elapsed_at_restart_s=round(time.monotonic() - _PROCESS_START_MONOTONIC, 3),
            max_wait_s=MAX_WAIT_S,
            inflight=sorted(inflight),
            inflight_count=len(inflight),
            next_generation=restart_count + 1,
            max_restarts=max_restarts,
        )
        new_env = os.environ.copy()
        new_env[_RESTART_COUNT_ENV] = str(restart_count + 1)
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)], new_env)
        # os.execve replaces the process — unreachable if execve succeeds.

    if inflight and restart_count >= max_restarts:
        # Cap reached — log to stderr but still exit 0 (not a wake-emit exit).
        # The stale-sweep hook (dispatch-stale-sweep.py) is the residual safety net.
        _log_event(
            session_dir,
            "exit_max_restarts",
            inflight=sorted(inflight),
            inflight_count=len(inflight),
            max_restarts=max_restarts,
        )
        sys.stderr.write(
            f"dispatch-completion-watcher: max restarts ({max_restarts}) reached;"
            f" {len(inflight)} in-flight dispatch(es) remain:"
            f" {sorted(inflight)}. Exiting. stale-sweep hook will surface on next"
            " UserPromptSubmit.\n"
        )
        sys.stderr.flush()
    else:
        # Timeout reached, no in-flight dispatches remain — normal completion.
        _log_event(
            session_dir,
            "exit_normal",
            inflight_count=0,
        )

    # Perpetual-self-wake guard: exit 0 (no wake emit) so the orchestrator
    # does not lock into an infinite wake loop.
    sys.exit(0)


if __name__ == "__main__":
    main()
