#!/usr/bin/env python3
"""Append an L2 child-session terminal event to events.jsonl.

Called from cycling skill terminal-mode Step 1.95 (HK-1b path only).
Writes a single caa.suborch.event/v1 record to the events.jsonl path
resolved via the manifest's ipc.events_path field (verified-absolute path).

PATH RESOLUTION: reads child-profile.json from CAA_CHILD_SIDECAR_DIR and
extracts ipc.events_path (a verified-absolute path per schema v4 §2).
Never computes the path via env-var traversal — that was the v5 BU-4 bug.
Backup mechanism: if manifest read fails, falls back to
CAA_CHILD_SESSION_DIR/events.jsonl (set by claude-session Phase B subtask B5).

Exit codes:
  0 — event appended successfully
  1 — bad invocation (missing required arg, unrecognised --kind)
  2 — manifest read failed AND CAA_CHILD_SESSION_DIR backup unavailable
  3 — EVENT_KIND_UNREGISTERED: manifest present but ipc.events_path absent/empty

  The async-complete.json sidecar write is best-effort and NOT reflected in the exit code;
  events.jsonl is the authoritative terminal record. A write failure is logged to stderr.

Environment variables consumed:
  CAA_CHILD_SIDECAR_DIR  — primary: sidecar dir containing child-profile.json
  CAA_CHILD_SESSION_DIR  — backup: child session dir (used when manifest unreadable)

Usage:
  l2_terminal_event.py [--kind completed|failed] [--failure-class <class>]
                       [--terminal-emitter <marker>]

  --kind              Event kind to emit. Default: "completed".
                      "failed" requires --failure-class.
  --failure-class     failure_class value (required when --kind=failed).
                      Closed enum per orchestrator-prompt.md §K.5:
                        uncatchable_exception | wait-timeout | parent-aborted | criteria-unmet
  --terminal-emitter  Launcher-fallback marker (e.g. "launcher-fallback"). When set
                      and --kind=completed, routes into the sidecar's stderr_tail field
                      only. Does NOT touch the events.jsonl failure_class field (gated
                      on kind==failed) and does NOT expand _VALID_FAILURE_CLASSES.

If CAA_CHILD_SIDECAR_DIR is unset or empty this script exits 0 immediately
(activation gate: root sessions are a no-op).
"""
import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ensure bin/ on import path
from _async_complete_sidecar import write_async_complete_sidecar

# Crockford base32 alphabet (32 chars, omits I, L, O, U)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_VALID_FAILURE_CLASSES = frozenset({
    "uncatchable_exception",
    "wait-timeout",
    "parent-aborted",
    "criteria-unmet",
})


def _make_ulid() -> str:
    """Return a 26-char ULID (Crockford base32, first 10 chars = 48-bit ms timestamp)."""
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    ts_chars: list[str] = []
    val = ts_ms
    for _ in range(10):
        ts_chars.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    rand_chars: list[str] = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand_int & 0x1F])
        rand_int >>= 5
    return "".join(reversed(ts_chars)) + "".join(reversed(rand_chars))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append L2 terminal event to events.jsonl",
        add_help=True,
    )
    parser.add_argument(
        "--kind",
        default="completed",
        choices=["completed", "failed"],
        help='Event kind. Default: "completed".',
    )
    parser.add_argument(
        "--failure-class",
        dest="failure_class",
        default=None,
        help="Required when --kind=failed. Closed enum per orchestrator-prompt.md §K.5.",
    )
    parser.add_argument(
        "--terminal-emitter",
        dest="terminal_emitter",
        default=None,
        help=(
            "Marker for launcher-fallback emissions. When set and --kind=completed, "
            "routes into the sidecar's stderr_tail field only (not the events.jsonl "
            "failure_class field, which remains gated on kind==failed). "
            "Do NOT add values here to _VALID_FAILURE_CLASSES — that enum is sealed."
        ),
    )
    args = parser.parse_args(argv)
    if args.kind == "failed" and not args.failure_class:
        parser.error("--failure-class is required when --kind=failed")
    if args.failure_class and args.failure_class not in _VALID_FAILURE_CLASSES:
        parser.error(
            f"--failure-class must be one of: {sorted(_VALID_FAILURE_CLASSES)}"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    sidecar_dir = os.environ.get("CAA_CHILD_SIDECAR_DIR", "")
    if not sidecar_dir:
        # Activation gate: root sessions are a complete no-op.
        return 0

    manifest_path = os.path.join(sidecar_dir, "child-profile.json")
    manifest: dict = {}
    events_path: str = ""

    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # Manifest unreadable — fall back to CAA_CHILD_SESSION_DIR per design §13 Phase B 4b.
        backup_dir = os.environ.get("CAA_CHILD_SESSION_DIR", "")
        if not backup_dir:
            print(
                f"ERROR: Step 1.95 manifest read failed AND CAA_CHILD_SESSION_DIR unset: {exc}",
                file=sys.stderr,
            )
            return 2
        events_path = os.path.join(backup_dir, "events.jsonl")
        # degraded mode: manifest fields will be empty strings
    else:
        events_path = manifest.get("ipc", {}).get("events_path", "")
        if not events_path:
            # ipc.events_path absent or empty — EVENT_KIND_UNREGISTERED per §13 Phase B 4b.
            print(
                "ERROR: Step 1.95 EVENT_KIND_UNREGISTERED — manifest's ipc.events_path "
                "absent or empty. failure_class: criteria-unmet. Aborting terminal-mode.",
                file=sys.stderr,
            )
            return 3

    record: dict = {
        "schema": "caa.suborch.event/v1",
        "event_id": _make_ulid(),
        "kind": args.kind,
        "child_id": manifest.get("child_id", ""),
        "parent_session_id": manifest.get("parent_session_id", ""),
        "ts": datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        "seq": 0,
        "dispatch_task": manifest.get("task", {}).get("title", ""),
        "depth": manifest.get("depth", 1),
    }
    if args.kind == "failed" and args.failure_class:
        record["failure_class"] = args.failure_class

    # Atomic append: O_APPEND holds VFS inode lock on local Linux fs for writes <= PIPE_BUF.
    # A single ~300-400 byte JSON line is well under PIPE_BUF (4096 bytes); no fcntl required.
    # Mirrors .claude/hooks/l2-heartbeat-emitter.py lines 112-114 atomic-append pattern.
    line = json.dumps(record, ensure_ascii=False) + "\n"
    assert len(line.encode("utf-8")) < 4096, (
        "l2_terminal_event: event line exceeds PIPE_BUF — atomicity not guaranteed"
    )
    with open(events_path, "a", encoding="utf-8", buffering=1) as ef:
        ef.write(line)

    try:
        write_async_complete_sidecar(
            sidecar_dir,
            spawn_id=manifest.get("child_id", ""),
            kind=args.kind,
            # When --terminal-emitter is set and kind=completed, route the marker into
            # failure_class so it lands in the sidecar's stderr_tail field (the helper
            # maps failure_class → stderr_tail). This does NOT touch the events.jsonl
            # failure_class field (gated on kind==failed at line 152-153 above) and
            # does NOT expand _VALID_FAILURE_CLASSES — that enum stays sealed.
            failure_class=(args.terminal_emitter
                           if (args.kind == "completed" and args.terminal_emitter)
                           else (args.failure_class or "")),
            completed_at=record["ts"],  # reuse the ts already in the events.jsonl record
        )
    except OSError as exc:
        print(f"WARNING: could not write async-complete.json: {exc}", file=sys.stderr)

    # Signal the L1 launcher's _file_watcher to SIGTERM the claude subprocess.
    # Must be written AFTER async-complete.json so the wake-channel hook fires first.
    child_session_dir = os.environ.get("CAA_CHILD_SESSION_DIR", "")
    if child_session_dir:
        sentinel_path = os.path.join(child_session_dir, "l2-exit-requested")
        try:
            open(sentinel_path, "w").close()
        except OSError as exc:
            print(f"WARNING: could not write l2-exit-requested sentinel: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
