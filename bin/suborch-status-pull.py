#!/usr/bin/env python3
"""L1-side events.jsonl pull-reader CLI for L2 child terminal events.

One-shot CLI invoked by the L1 orchestrator while monitoring an in-flight L2 child;
each stdout line is one Monitor wake (a matched caa.suborch.event/v1 JSON line).
See .claude/skills/dispatch-l2/SKILL.md § Step 6 (Wait on the terminal sidecar) for
the canonical invocation context within the L2 dispatch flow.

Mirrors bin/suborch-watchdog.py's file-tailing pattern and bin/parent_messages_write.py's
argparse + exit-code + self-test conventions.

Exit codes:
  0 — clean exit (EOF on --no-follow; SIGTERM/SIGINT on --follow; all self-test fixtures pass)
  1 — bad invocation (missing required arg, unreadable path, mutually-exclusive flags)
  2 — schema validation failure (unknown kind in --filter, non-integer --since-seq)
  3 — I/O error (events.jsonl exists but is unreadable)
  4 — Concern-D violation (seq regression; orchestrator must surface for operator triage)
  5 — --self-test failure

Usage:
    bin/suborch-status-pull.py
        --child-session-dir PATH        REQUIRED unless --events-path or --self-test
        [--events-path PATH]            explicit override; mutually exclusive with --child-session-dir
        [--filter KIND1,KIND2,...]      default: completed,failed,attention-required
        [--since-seq N]                 default: -1 (emit from BOF); resume-skip
        [--follow | --no-follow]        default: --follow
        [--poll-interval-ms N]          default: 200; --follow tail poll cadence
        [--self-test]
        [-h]
"""
import argparse
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Coupling: mirrors bin/l2_terminal_event.py § _VALID_FAILURE_CLASSES.
# If that module adds a fifth failure class, update this frozenset in the same commit.
_VALID_FAILURE_CLASSES = frozenset({
    "uncatchable_exception",
    "wait-timeout",
    "parent-aborted",
    "criteria-unmet",
})

# Quiet kinds — ALWAYS filtered from stdout regardless of --filter value.
# Source: .claude/hooks/l2-heartbeat-emitter.py (canonical quiet-kind taxonomy).
# Heartbeats fire every ~30s; passing them to Monitor would dominate context budget.
_ALWAYS_QUIET = frozenset({"heartbeat", "waiting-on-parent"})

# Closed kind taxonomy per v5/v6 §7.2 (11 kinds).
# [verified: tests/suborch/test_tier1_concern_d_status_pull.py § _KIND_TAXONOMY]
_KIND_TAXONOMY = frozenset({
    "started",
    "progress",
    "attention-required",
    "cycle-requested",
    "completed",
    "failed",
    "heartbeat-stale",
    "mega-monitor-stale",
    "restarted",
    "heartbeat",
    "waiting-on-parent",
})

_DEFAULT_FILTER = frozenset({"completed", "failed", "attention-required"})
_DEFAULT_POLL_INTERVAL_MS = 200


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="suborch-status-pull.py",
        description=(
            "L1-side events.jsonl pull-reader CLI. Tails {child_session_dir}/events.jsonl, "
            "enforces Concern-D monotonic seq, filters quiet kinds, and emits one matched "
            "JSON line per stdout line for the Monitor primitive."
        ),
    )
    parser.add_argument(
        "--child-session-dir",
        metavar="PATH",
        help="Absolute path to the child session directory (events.jsonl lives here).",
    )
    parser.add_argument(
        "--events-path",
        metavar="PATH",
        help=(
            "Explicit override for the events.jsonl path. "
            "Mutually exclusive with --child-session-dir. Used for tests."
        ),
    )
    parser.add_argument(
        "--filter",
        metavar="KIND1,KIND2,...",
        default=None,
        help=(
            "Comma-separated event kinds to emit. "
            f"Default: {','.join(sorted(_DEFAULT_FILTER))}. "
            "Supports repeated --filter flags; comma-separated values within each flag are split. "
            "heartbeat and waiting-on-parent are always suppressed regardless of this value."
        ),
        action="append",
    )
    parser.add_argument(
        "--since-seq",
        metavar="N",
        type=int,
        default=-1,
        help="Skip events with seq <= N. Default: -1 (emit from BOF). Resume-skip cursor.",
    )
    follow_group = parser.add_mutually_exclusive_group()
    follow_group.add_argument(
        "--follow",
        dest="follow",
        action="store_true",
        default=True,
        help="Tail the file (default). Blocks on EOF and polls for new lines.",
    )
    follow_group.add_argument(
        "--no-follow",
        dest="follow",
        action="store_false",
        help="Read to EOF and exit (one-shot; for tests and operator inspection).",
    )
    parser.add_argument(
        "--poll-interval-ms",
        metavar="N",
        type=int,
        default=_DEFAULT_POLL_INTERVAL_MS,
        help=f"Tail poll cadence in ms (default: {_DEFAULT_POLL_INTERVAL_MS}). --follow only.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run in-process tempdir fixtures and exit (0=pass / 1=fail).",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return args

    # Mutual exclusion: --child-session-dir and --events-path
    if args.child_session_dir and args.events_path:
        parser.error("--child-session-dir and --events-path are mutually exclusive.")
    if not args.child_session_dir and not args.events_path:
        parser.error(
            "--child-session-dir or --events-path is required (or use --self-test)."
        )

    # Parse --filter into a frozenset
    if args.filter is None:
        args.filter_set = _DEFAULT_FILTER
    else:
        raw_kinds: list[str] = []
        for token in args.filter:
            raw_kinds.extend(k.strip() for k in token.split(",") if k.strip())
        for kind in raw_kinds:
            if kind not in _KIND_TAXONOMY:
                parser.error(
                    f"--filter contains unknown kind {kind!r}. "
                    f"Closed taxonomy (v5/v6 §7.2): {sorted(_KIND_TAXONOMY)}."
                )
        args.filter_set = frozenset(raw_kinds)

    # Effective filter strips always-quiet kinds
    args.effective_filter = args.filter_set - _ALWAYS_QUIET
    if not args.effective_filter:
        print(
            "WARNING: effective filter is empty after removing always-quiet kinds — "
            "no events will be emitted",
            file=sys.stderr,
        )

    return args


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_events_path(args: argparse.Namespace) -> Path:
    """Return the resolved events.jsonl path from CLI args."""
    if args.events_path:
        return Path(args.events_path)
    return Path(args.child_session_dir) / "events.jsonl"


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def _process_lines(
    lines: list[str],
    last_seq_seen: int,
    since_seq: int,
    effective_filter: frozenset,
    out_lines: list[str],
    stderr_warnings: list[str],
) -> tuple[int, int | None]:
    """Process a batch of raw text lines.

    Returns (new_last_seq_seen, concern_d_exit_code_or_None).
    Appends emittable JSON strings to out_lines and warnings to stderr_warnings.
    concern_d_exit_code is 4 on violation, None on clean pass.
    """
    for line_no, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            continue

        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            # Partial writes are normal under append; skip silently.
            # [verified: bin/suborch-watchdog.py § _last_heartbeat_ts § skip-malformed lines]
            continue

        # Concern-D: track seq across ALL parsed lines, including quiet kinds.
        # Heartbeats ARE part of the monotonic sequence per v5/v6 §7.1.
        parsed_seq = event.get("seq")
        if parsed_seq is None or not isinstance(parsed_seq, int):
            stderr_warnings.append(
                f"WARNING: line missing seq field — Concern-D check skipped (event_id={event.get('event_id')})"
            )
        else:
            if parsed_seq <= last_seq_seen:
                # Strict-increase violation: exit 4.
                stderr_warnings.append(
                    f"CONCERN-D-VIOLATION: seq={parsed_seq} <= last_seq_seen={last_seq_seen} "
                    f"(event_id={event.get('event_id')})"
                )
                return last_seq_seen, 4
            last_seq_seen = parsed_seq

        # --since-seq resume skip: skip events with seq <= since_seq.
        if parsed_seq is not None and isinstance(parsed_seq, int) and parsed_seq <= since_seq:
            continue

        # Filter: always-quiet kinds suppressed first; then filter-set gate.
        kind = event.get("kind", "")
        if kind in _ALWAYS_QUIET:
            continue
        if kind not in effective_filter:
            continue

        # failure_class validation on failed events.
        if kind == "failed":
            fc = event.get("failure_class")
            event_id = event.get("event_id", "<unknown>")
            if fc is None:
                stderr_warnings.append(
                    f"WARNING: failed event {event_id} missing failure_class field"
                )
            elif fc not in _VALID_FAILURE_CLASSES:
                # Unknown class — pass through verbatim with warning.
                # The CLI does NOT rewrite; a new class the writer added would be silently dropped otherwise.
                stderr_warnings.append(
                    f"WARNING: failed event {event_id} has unknown failure_class={fc!r}"
                )
            # Event is emitted in both missing and unknown-class cases.

        out_lines.append(json.dumps(event, ensure_ascii=False))

    return last_seq_seen, None


def _emit_and_flush(lines: list[str]) -> None:
    """Write lines to stdout, one per line, line-buffered.

    buffering=1 (line-buffered) on the stdout stream ensures each emission
    wakes the Monitor primitive immediately — mirrors bin/l2_terminal_event.py § buffering=1.
    """
    for line in lines:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _flush_warnings(warnings: list[str]) -> None:
    for w in warnings:
        print(w, file=sys.stderr)


# ---------------------------------------------------------------------------
# One-shot (--no-follow) and follow (--follow) runners
# ---------------------------------------------------------------------------


def _run_no_follow(events_path: Path, args: argparse.Namespace) -> int:
    """Read events.jsonl to EOF; process and emit matched events; exit."""
    if not events_path.exists():
        # Child hasn't started writing yet — expected absence, exit 0 fast-path.
        return 0

    try:
        text = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: could not read {events_path}: {exc}", file=sys.stderr)
        return 3

    lines = text.splitlines()
    out_lines: list[str] = []
    stderr_warnings: list[str] = []

    last_seq_seen, concern_d_code = _process_lines(
        lines,
        last_seq_seen=-1,
        since_seq=args.since_seq,
        effective_filter=args.effective_filter,
        out_lines=out_lines,
        stderr_warnings=stderr_warnings,
    )

    _flush_warnings(stderr_warnings)
    _emit_and_flush(out_lines)

    if concern_d_code is not None:
        return concern_d_code
    return 0


def _run_follow(events_path: Path, args: argparse.Namespace) -> int:
    """Tail events.jsonl; emit matched events line-by-line; block on EOF with poll."""
    poll_interval_s = args.poll_interval_ms / 1000.0
    last_seq_seen = -1
    cursor = 0  # byte offset into file

    # SIGTERM handler — exit 0 cleanly per W4 design Unknown 4.
    # The Monitor primitive may use SIGTERM on session-end; exit 0 is correct disposition.
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    while True:
        # Open and read from cursor to current EOF on each tick.
        try:
            if not events_path.exists():
                # Child hasn't started writing yet — wait and retry.
                time.sleep(poll_interval_s)
                continue

            with open(events_path, encoding="utf-8", errors="replace") as fh:
                fh.seek(cursor)
                new_text = fh.read()
                new_cursor = fh.tell()
        except OSError as exc:
            print(f"ERROR: could not read {events_path}: {exc}", file=sys.stderr)
            return 3

        if new_text:
            lines = new_text.splitlines()
            out_lines: list[str] = []
            stderr_warnings: list[str] = []

            last_seq_seen, concern_d_code = _process_lines(
                lines,
                last_seq_seen=last_seq_seen,
                since_seq=args.since_seq,
                effective_filter=args.effective_filter,
                out_lines=out_lines,
                stderr_warnings=stderr_warnings,
            )

            _flush_warnings(stderr_warnings)
            _emit_and_flush(out_lines)

            if concern_d_code is not None:
                return concern_d_code

            cursor = new_cursor

        time.sleep(poll_interval_s)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _seed_event(kind: str, seq: int, **extra) -> str:
    """Produce one valid caa.suborch.event/v1 JSONL line for fixtures."""
    obj = {
        "schema": "caa.suborch.event/v1",
        "event_id": f"01HTEST{seq:06d}",
        "kind": kind,
        "child_id": "c-test",
        "parent_session_id": "test-parent",
        "ts": "2026-05-21T00:00:00+00:00",
        "seq": seq,
        "dispatch_task": "test-task",
        "depth": 1,
    }
    obj.update(extra)
    return json.dumps(obj)


def _run_self_test() -> int:
    """In-process fixture tests. Returns 0 on all-pass, 1 on any failure."""
    import subprocess

    failures = 0
    script = Path(__file__).resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        child_dir = tmp / "child"
        child_dir.mkdir()
        events_path = child_dir / "events.jsonl"

        # -----------------------------------------------------------------------
        # Test 1: Concern-D violation — regressed seq triggers exit 4 + stderr CONCERN-D-VIOLATION
        # -----------------------------------------------------------------------
        events_path.write_text(
            _seed_event("started", seq=5) + "\n" +
            _seed_event("progress", seq=3) + "\n",  # regression: 3 < 5
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script), "--events-path", str(events_path), "--no-follow"],
            capture_output=True, text=True,
        )
        if result.returncode != 4:
            print(f"FAIL T1: expected exit 4, got {result.returncode}", file=sys.stderr)
            print(f"  stderr: {result.stderr!r}", file=sys.stderr)
            failures += 1
        elif "CONCERN-D-VIOLATION" not in result.stderr:
            print(f"FAIL T1: exit 4 but 'CONCERN-D-VIOLATION' not in stderr: {result.stderr!r}", file=sys.stderr)
            failures += 1
        else:
            print("PASS T1: Concern-D violation → exit 4 + CONCERN-D-VIOLATION stderr", file=sys.stderr)

        # -----------------------------------------------------------------------
        # Test 2: Heartbeat filtering — heartbeat advances last_seq_seen but is not emitted
        # -----------------------------------------------------------------------
        events_path.write_text(
            _seed_event("heartbeat", seq=0) + "\n" +
            _seed_event("heartbeat", seq=1) + "\n" +
            _seed_event("completed", seq=2) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script), "--events-path", str(events_path), "--no-follow",
             "--filter", "completed,failed,attention-required"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAIL T2: expected exit 0, got {result.returncode}; stderr={result.stderr!r}", file=sys.stderr)
            failures += 1
        else:
            stdout_lines = [ln for ln in result.stdout.strip().splitlines() if ln]
            if len(stdout_lines) != 1:
                print(f"FAIL T2: expected 1 emitted line, got {len(stdout_lines)}: {result.stdout!r}", file=sys.stderr)
                failures += 1
            else:
                emitted = json.loads(stdout_lines[0])
                if emitted.get("kind") != "completed":
                    print(f"FAIL T2: emitted event kind={emitted.get('kind')!r}, expected 'completed'", file=sys.stderr)
                    failures += 1
                else:
                    print("PASS T2: heartbeat filtered, completed emitted", file=sys.stderr)

        # -----------------------------------------------------------------------
        # Test 3: terminal-event parse + failure_class extraction passthrough
        # -----------------------------------------------------------------------
        events_path.write_text(
            _seed_event("failed", seq=0, failure_class="criteria-unmet") + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script), "--events-path", str(events_path), "--no-follow"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAIL T3: expected exit 0, got {result.returncode}; stderr={result.stderr!r}", file=sys.stderr)
            failures += 1
        else:
            stdout_lines = [ln for ln in result.stdout.strip().splitlines() if ln]
            if not stdout_lines:
                print("FAIL T3: no output emitted for failed event", file=sys.stderr)
                failures += 1
            else:
                emitted = json.loads(stdout_lines[0])
                if emitted.get("failure_class") != "criteria-unmet":
                    print(f"FAIL T3: failure_class={emitted.get('failure_class')!r}, expected 'criteria-unmet'", file=sys.stderr)
                    failures += 1
                else:
                    print("PASS T3: failed event + failure_class passthrough", file=sys.stderr)

        # -----------------------------------------------------------------------
        # Test 4: empty-file safe handling — exit 0 in --no-follow
        # -----------------------------------------------------------------------
        events_path.write_text("", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script), "--events-path", str(events_path), "--no-follow"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAIL T4: empty file → expected exit 0, got {result.returncode}; stderr={result.stderr!r}", file=sys.stderr)
            failures += 1
        elif result.stdout.strip():
            print(f"FAIL T4: empty file → expected no stdout, got {result.stdout!r}", file=sys.stderr)
            failures += 1
        else:
            print("PASS T4: empty-file safe handling → exit 0, no output", file=sys.stderr)

        # -----------------------------------------------------------------------
        # Test 5: malformed-line safe handling — skip silently, emit valid lines
        # -----------------------------------------------------------------------
        events_path.write_text(
            "this is not json\n" +
            _seed_event("completed", seq=0) + "\n" +
            "{broken\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script), "--events-path", str(events_path), "--no-follow"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAIL T5: expected exit 0, got {result.returncode}; stderr={result.stderr!r}", file=sys.stderr)
            failures += 1
        else:
            stdout_lines = [ln for ln in result.stdout.strip().splitlines() if ln]
            if len(stdout_lines) != 1:
                print(f"FAIL T5: expected 1 emitted line (skipping 2 malformed), got {len(stdout_lines)}: {result.stdout!r}", file=sys.stderr)
                failures += 1
            else:
                print("PASS T5: malformed lines skipped silently, valid line emitted", file=sys.stderr)

    if failures:
        print(f"\nSELF-TEST FAILED: {failures} assertion(s) failed.", file=sys.stderr)
        return 5
    print("\nSELF-TEST PASSED: all assertions passed.", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.self_test:
        return _run_self_test()

    events_path = _resolve_events_path(args)

    if args.follow:
        return _run_follow(events_path, args)
    else:
        return _run_no_follow(events_path, args)


if __name__ == "__main__":
    sys.exit(main())
