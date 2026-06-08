#!/usr/bin/env python3
"""Append a parent-to-child message line to parent-messages.jsonl.

Wire format per dispatch-l2 SKILL.md §K.2:
  {from: "parent", ts: <ISO-8601 UTC>, body: <str>, abort_now?: <bool>}

When --abort-now is set, also touches {child_session_dir}/abort-now sentinel
AFTER the JSONL append. child_session_dir is resolved from
{parent_session_dir}/children-registry.json[child_id]["child_session_dir"].

Exit codes:
  0 — success
  1 — I/O error (body unreadable, file open/write failure, etc.)
  2 — monotonicity violation (new ts <= last line's ts in target file)
  3 — unresolvable child_session_dir (only on --abort-now; no JSONL written)

Usage:
    bin/parent_messages_write.py [-h] [--self-test]
        [--parent-session-dir PATH] --child-id CHILD_ID
        (--body STRING | --body-file PATH) [--abort-now] [--ts ISO8601]
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _load_registry(registry_path: Path) -> dict:
    """Return registry dict (child_id → row). Empty dict on missing file."""
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not read registry at {registry_path}: {exc}", file=sys.stderr)
        return {}


def _last_ts(jsonl_path: Path) -> str | None:
    """Return the ts string from the last non-empty line of jsonl_path, or None.

    Malformed last-line is logged but does NOT block the append — this file is
    only ever written by this writer, so malformed content should not occur in
    practice; blocking all future writes on a parse error is worse than logging.
    """
    if not jsonl_path.exists():
        return None
    try:
        text = jsonl_path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    last_line = lines[-1]
    try:
        row = json.loads(last_line)
        return row.get("ts") or None
    except (json.JSONDecodeError, AttributeError) as exc:
        print(
            f"WARNING: could not parse last line of {jsonl_path} for monotonicity check: {exc}",
            file=sys.stderr,
        )
        return None


def write_parent_message(
    parent_session_dir: Path,
    child_id: str,
    body: str,
    abort_now: bool = False,
    ts: str | None = None,
) -> int:
    """Append a parent-message line; on abort_now, touch the abort-now sentinel.

    Returns exit code: 0 success, 1 io-error, 2 monotonicity-violation,
    3 unresolvable-child_session_dir (only when abort_now=True).
    """
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()

    # --- Abort-now: resolve child_session_dir BEFORE the append (fail-shut) ---
    child_session_dir: Path | None = None
    if abort_now:
        registry_path = parent_session_dir / "children-registry.json"
        registry = _load_registry(registry_path)
        row = registry.get(child_id)
        if not row:
            print(
                f"ERROR: child_id {child_id!r} not found in {registry_path}; "
                "cannot resolve child_session_dir for abort-now sentinel touch.",
                file=sys.stderr,
            )
            return 3
        csd_str = row.get("child_session_dir", "")
        if not csd_str:
            print(
                f"ERROR: registry row for {child_id!r} has empty child_session_dir; "
                "cannot touch abort-now sentinel.",
                file=sys.stderr,
            )
            return 3
        child_session_dir = Path(csd_str)
        # Auto-create the child_session_dir IPC namespace if it does not yet exist.
        # Rationale: the manifest's `child_session_dir` is an IPC namespace path
        # that both abort-now-write (this script) and the L2 abort-now gate
        # (.claude/hooks/l2-abort-now-gate.py reads `manifest.child_session_dir`)
        # agree to use for the abort-now sentinel + events.jsonl emission. The L2
        # process itself runs at a DIFFERENT conventional session-dir
        # (.agent_context/sessions/<l2-session-id>/) — see D14 / D16 findings,
        # Ep-9 of L2 sub-orchestrator validation campaign. The namespace path
        # does not yet exist when abort-now fires before the gate's first
        # events.jsonl write, so auto-create is required for the abort-now
        # mechanism to function. Fail-shut behavior is preserved for creation
        # errors (permissions, disk full).
        try:
            child_session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(
                f"ERROR: registry row for {child_id!r} has child_session_dir={csd_str!r}; "
                f"could not create the IPC namespace directory for abort-now sentinel: {exc}",
                file=sys.stderr,
            )
            return 3

    # --- Monotonicity check ---
    msg_dir = parent_session_dir / "parent-messages" / child_id
    jsonl_path = msg_dir / "parent-messages.jsonl"
    prev_ts = _last_ts(jsonl_path)
    if prev_ts is not None and ts <= prev_ts:
        print(
            f"ERROR: monotonicity violation — new ts {ts!r} <= last ts {prev_ts!r} "
            f"in {jsonl_path}",
            file=sys.stderr,
        )
        return 2

    # --- Append ---
    record: dict = {"from": "parent", "ts": ts, "body": body}
    if abort_now:
        record["abort_now"] = True

    line = json.dumps(record, ensure_ascii=False) + "\n"
    # Atomic append: O_APPEND holds VFS inode lock on local Linux fs for writes <= PIPE_BUF.
    # A single parent-message line is well under PIPE_BUF (4096 bytes); no fcntl required.
    assert len(line.encode("utf-8")) < 4096, (
        "parent_messages_write: message line exceeds PIPE_BUF — atomicity not guaranteed"
    )

    try:
        msg_dir.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8", buffering=1) as fh:
            fh.write(line)
    except OSError as exc:
        print(f"ERROR: could not write to {jsonl_path}: {exc}", file=sys.stderr)
        return 1

    # --- Sentinel touch (AFTER successful JSONL append) ---
    if abort_now and child_session_dir is not None:
        try:
            (child_session_dir / "abort-now").touch()
        except OSError as exc:
            # Sentinel touch failed — abort signal is incomplete.  The JSONL line
            # was written but the PreToolUse hook will not fire.  Warn and return 1
            # so the caller knows the abort was not fully applied.
            print(
                f"ERROR: JSONL append succeeded but abort-now sentinel touch failed "
                f"at {child_session_dir / 'abort-now'}: {exc}",
                file=sys.stderr,
            )
            return 1

    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _run_self_test() -> int:
    """Run in-process tempdir fixtures. Returns 0 on pass, 1 on failure."""
    failures = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        parent = Path(tmpdir)
        fake_child_session = parent / "fake-child-sessions" / "c-test-0001"
        fake_child_session.mkdir(parents=True)

        # Write children-registry.json (dict keyed by child_id)
        registry = {
            "c-test-0001": {
                "child_id": "c-test-0001",
                "status": "active",
                "child_session_dir": str(fake_child_session),
            }
        }
        (parent / "children-registry.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )

        jsonl_path = parent / "parent-messages" / "c-test-0001" / "parent-messages.jsonl"
        ts1 = "2026-01-01T00:00:00+00:00"
        ts2 = "2026-01-01T00:00:01+00:00"

        # --- Test 1: non-abort write appends a valid JSONL line ---
        rc = write_parent_message(parent, "c-test-0001", "hello world", abort_now=False, ts=ts1)
        if rc != 0:
            print(f"FAIL: test1 non-abort write returned {rc}, expected 0", file=sys.stderr)
            failures += 1
        else:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[0])
            if row.get("from") == "parent" and row.get("body") == "hello world" and row.get("ts") == ts1 and "abort_now" not in row:
                print("PASS: test1 — non-abort write appended correct JSONL line", file=sys.stderr)
            else:
                print(f"FAIL: test1 — unexpected row content: {row}", file=sys.stderr)
                failures += 1

        # --- Test 2: abort+sentinel ---
        rc = write_parent_message(parent, "c-test-0001", "stop now", abort_now=True, ts=ts2)
        if rc != 0:
            print(f"FAIL: test2 abort+sentinel write returned {rc}, expected 0", file=sys.stderr)
            failures += 1
        else:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
            row2 = json.loads(lines[1])
            sentinel = fake_child_session / "abort-now"
            if row2.get("abort_now") is True and sentinel.exists():
                print("PASS: test2 — abort write appended JSONL line with abort_now=true AND sentinel exists", file=sys.stderr)
            else:
                print(f"FAIL: test2 — row2={row2}, sentinel exists={sentinel.exists()}", file=sys.stderr)
                failures += 1

        # --- Test 3: monotonicity-exit-2 (ts2 already on disk, ts2 again → violation) ---
        rc = write_parent_message(parent, "c-test-0001", "retry same ts", abort_now=False, ts=ts2)
        if rc == 2:
            print("PASS: test3 — monotonicity violation returned exit 2", file=sys.stderr)
        else:
            print(f"FAIL: test3 — expected exit 2, got {rc}", file=sys.stderr)
            failures += 1

        # Confirm no third line was appended
        lines_after = jsonl_path.read_text(encoding="utf-8").splitlines()
        if len(lines_after) == 2:
            print("PASS: test3 — no extra line appended on monotonicity violation", file=sys.stderr)
        else:
            print(f"FAIL: test3 — expected 2 lines after monotonicity violation, got {len(lines_after)}", file=sys.stderr)
            failures += 1

        # --- Test 4: unresolvable-row exit-3 ---
        rc = write_parent_message(parent, "c-nonexistent", "abort missing", abort_now=True, ts="2026-01-02T00:00:00+00:00")
        if rc == 3:
            print("PASS: test4 — unresolvable row returned exit 3", file=sys.stderr)
        else:
            print(f"FAIL: test4 — expected exit 3, got {rc}", file=sys.stderr)
            failures += 1

        # Confirm no JSONL line was written for c-nonexistent
        nonexistent_jsonl = parent / "parent-messages" / "c-nonexistent" / "parent-messages.jsonl"
        if not nonexistent_jsonl.exists():
            print("PASS: test4 — no JSONL line written for unresolvable child", file=sys.stderr)
        else:
            print("FAIL: test4 — JSONL file was created despite exit 3", file=sys.stderr)
            failures += 1

        # --- Test 5: empty-file vacuous monotonicity pass ---
        ts5 = "2026-01-01T12:00:00+00:00"
        rc = write_parent_message(parent, "c-fresh", "first message ever", abort_now=False, ts=ts5)
        if rc == 0:
            fresh_jsonl = parent / "parent-messages" / "c-fresh" / "parent-messages.jsonl"
            if fresh_jsonl.exists():
                row5 = json.loads(fresh_jsonl.read_text(encoding="utf-8").strip())
                if row5.get("body") == "first message ever":
                    print("PASS: test5 — empty-file vacuous monotonicity pass", file=sys.stderr)
                else:
                    print(f"FAIL: test5 — unexpected row: {row5}", file=sys.stderr)
                    failures += 1
            else:
                print("FAIL: test5 — JSONL file not created", file=sys.stderr)
                failures += 1
        else:
            print(f"FAIL: test5 — expected exit 0, got {rc}", file=sys.stderr)
            failures += 1

    if failures == 0:
        print("ALL SELF-TEST CASES PASSED", file=sys.stderr)
        return 0
    else:
        print(f"SELF-TEST FAILED: {failures} case(s) failed", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parent_messages_write.py",
        description=(
            "Append a parent-to-child message line to parent-messages.jsonl. "
            "Wire format per dispatch-l2 §K.2: {from, ts, body, abort_now?}."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run in-process tempdir fixtures and exit (0=pass / 1=fail)",
    )
    parser.add_argument(
        "--parent-session-dir",
        metavar="PATH",
        help=(
            "Absolute path to the parent session directory. "
            "Falls back to CLAUDE_SESSION_DIR env var if not given."
        ),
    )
    parser.add_argument(
        "--child-id",
        metavar="CHILD_ID",
        help="Recipient child ID (format: c-{parent_session_id}-{nnnn})",
    )
    body_group = parser.add_mutually_exclusive_group()
    body_group.add_argument(
        "--body",
        metavar="STRING",
        help="Message body as a string",
    )
    body_group.add_argument(
        "--body-file",
        metavar="PATH",
        help="Read message body from file; use - for stdin",
    )
    parser.add_argument(
        "--abort-now",
        action="store_true",
        help=(
            "Set abort_now: true on the JSONL line AND touch "
            "{child_session_dir}/abort-now sentinel"
        ),
    )
    parser.add_argument(
        "--ts",
        metavar="ISO8601",
        help="Override timestamp (default: current UTC). Used for deterministic tests.",
    )

    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    # --- Validate required args ---
    if not args.child_id:
        parser.error("--child-id is required (or use --self-test)")
    if args.body is None and args.body_file is None:
        parser.error("one of --body or --body-file is required (or use --self-test)")

    # --- Resolve parent_session_dir ---
    psd_str = args.parent_session_dir or os.environ.get("CLAUDE_SESSION_DIR", "")
    if not psd_str:
        print(
            "ERROR: parent session directory not provided. "
            "Use --parent-session-dir PATH or set CLAUDE_SESSION_DIR.",
            file=sys.stderr,
        )
        return 1
    parent_session_dir = Path(psd_str)
    if not parent_session_dir.is_dir():
        print(
            f"ERROR: parent session directory does not exist: {parent_session_dir}",
            file=sys.stderr,
        )
        return 1

    # --- Read body ---
    if args.body is not None:
        body = args.body
    else:
        try:
            if args.body_file == "-":
                body = sys.stdin.read()
            else:
                body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: could not read body file: {exc}", file=sys.stderr)
            return 1

    return write_parent_message(
        parent_session_dir=parent_session_dir,
        child_id=args.child_id,
        body=body,
        abort_now=args.abort_now,
        ts=args.ts,
    )


if __name__ == "__main__":
    sys.exit(main())
