#!/usr/bin/env python3
"""Register an L2 child at spawn time into children-registry.json + children-active/ sentinel.

Wire format per W5-registration-design.md (Shape B(i)):
  {parent_session_dir}/children-registry.json[child_id] = {
      child_session_dir, status, spawned_at_ts, task_title
  }
  {parent_session_dir}/children-active/{child_id}  (sentinel file, empty)

Exit codes:
  0 — success
  1 — I/O error (file open/write/rename failure, directory creation failure)
  2 — schema validation failure (malformed child_id format, missing required arg)
  3 — idempotency violation (registry already has a row for this child_id,
      and --mark-failed was NOT specified; or for --mark-failed, no existing row)

Usage:
    bin/parent_messages_register_child.py [-h] [--self-test]
        --child-id CHILD_ID
        [--parent-session-dir PATH]
        --child-session-dir PATH
        --task-title STRING
        [--ts ISO8601]
        [--mark-failed]
"""
import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# child_id must match c-{digits}-{digits}-{4-digit-counter} pattern.
# The plan says c-{parent_session_id}-{nnnn}; session IDs carry an extra segment
# in the form {ts}-{pid}-{suffix} so we accept the general form c-<nonempty>-<4digits>.
_CHILD_ID_RE = re.compile(r"^c-.+-\d{4}$")

_LOCK_FILENAME = ".children-registry.lock"
_REGISTRY_FILENAME = "children-registry.json"
_ACTIVE_DIR = "children-active"

_STATUS_ACTIVE = "active"
_STATUS_REAPED = "reaped"
_STATUS_FAILED = "failed"
_TERMINAL_STATUSES = {_STATUS_REAPED, _STATUS_FAILED}


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _validate_child_id(child_id: str) -> bool:
    """Return True if child_id matches the canonical c-<nonempty>-<4digits> pattern."""
    return bool(_CHILD_ID_RE.match(child_id))


def _load_registry(registry_path: Path) -> dict:
    """Return registry dict (child_id → row). Empty dict on missing file."""
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not read registry at {registry_path}: {exc}", file=sys.stderr)
        return {}


def _atomic_write_registry(registry_path: Path, registry: dict) -> None:
    """Write registry dict atomically via a sibling .tmp file + os.rename.

    Raises OSError on any I/O failure — callers map to exit code 1.
    """
    tmp_path = registry_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    os.rename(str(tmp_path), str(registry_path))


def register_child(
    parent_session_dir: Path,
    child_id: str,
    child_session_dir: Path,
    task_title: str,
    ts: str | None = None,
) -> int:
    """Write registry row + sentinel for child_id.

    Returns exit code: 0 success, 1 io-error, 2 schema-error, 3 idempotency-violation.
    """
    if not _validate_child_id(child_id):
        print(
            f"ERROR: child_id {child_id!r} does not match required pattern "
            "c-<nonempty>-<4digits> (e.g. c-1779347314-1073349-0001).",
            file=sys.stderr,
        )
        return 2

    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()

    registry_path = parent_session_dir / _REGISTRY_FILENAME
    lock_path = parent_session_dir / _LOCK_FILENAME
    active_dir = parent_session_dir / _ACTIVE_DIR

    # Ensure children-active/ directory exists before taking lock.
    try:
        active_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: could not create {active_dir}: {exc}", file=sys.stderr)
        return 1

    # Hold an exclusive lock across the read-modify-write window to defend against
    # multi-orchestrator races (single-orchestrator is the deployed topology today,
    # but flock is cheap and documents the invariant).
    try:
        lock_fd = open(str(lock_path), "a")  # noqa: SIM115 — explicit close below
    except OSError as exc:
        print(f"ERROR: could not open lock file {lock_path}: {exc}", file=sys.stderr)
        return 1

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        registry = _load_registry(registry_path)

        if child_id in registry:
            existing_status = registry[child_id].get("status", "")
            if existing_status == _STATUS_ACTIVE:
                print(
                    f"ERROR: idempotency violation — child_id {child_id!r} already has an "
                    f"active row in {registry_path}. Reap the existing child via "
                    "bin/suborch-reap.py before reusing this child_id.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"ERROR: idempotency violation — child_id {child_id!r} has a terminal "
                    f"row (status={existing_status!r}) in {registry_path}. "
                    "Pick a fresh child_id (increment the nnnn counter) or delete the "
                    "historical row via an explicit operator action to preserve traceability.",
                    file=sys.stderr,
                )
            return 3

        row: dict = {
            "child_session_dir": str(child_session_dir),
            "status": _STATUS_ACTIVE,
            "spawned_at_ts": ts,
            "task_title": task_title,
            # Optional — populated post-hoc by bin/caa/launcher.py:_register_launcher_session_id
            # after the L2 launcher starts and writes monitor.pid.  Stays None for L1 rows
            # (no CAA_CHILD_SIDECAR_DIR).  bin/suborch-reap.py:step3_teardown_monitor reads
            # this field to resolve monitor.pid at {sessions_root}/{launcher_session_id}/.
            "launcher_session_id": None,
        }
        registry[child_id] = row

        try:
            _atomic_write_registry(registry_path, registry)
        except OSError as exc:
            print(f"ERROR: could not write registry at {registry_path}: {exc}", file=sys.stderr)
            return 1

        # Touch sentinel AFTER successful registry write.  If this call crashes mid-way,
        # the registry row exists but no sentinel is present; a future defensive sweep can
        # detect rows with status==active, old spawned_at_ts, and missing sentinel.
        sentinel_path = active_dir / child_id
        try:
            fd = os.open(str(sentinel_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            # Sentinel already present — roll back the registry row we just wrote.
            # This second-layer check catches races that slip through the registry check.
            registry.pop(child_id, None)
            try:
                _atomic_write_registry(registry_path, registry)
            except OSError as undo_exc:
                print(
                    f"ERROR: sentinel {sentinel_path} pre-existed AND registry rollback failed: "
                    f"{undo_exc}. Registry may be inconsistent.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"ERROR: idempotency violation — sentinel {sentinel_path} already exists "
                "(race condition or duplicate invocation). Registry row rolled back.",
                file=sys.stderr,
            )
            return 3
        except OSError as exc:
            # Sentinel touch failed — roll back the registry row.
            registry.pop(child_id, None)
            try:
                _atomic_write_registry(registry_path, registry)
            except OSError:
                pass  # best-effort rollback; the outer error is the actionable one
            print(
                f"ERROR: could not touch sentinel {sentinel_path}: {exc}. Registry row rolled back.",
                file=sys.stderr,
            )
            return 1

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    return 0


def mark_failed(
    parent_session_dir: Path,
    child_id: str,
) -> int:
    """Transition child_id row to status=failed AND remove the children-active sentinel.

    Used by the L1 orchestrator's post-spawn rollback path when Skill(dispatch-l2) returns
    failure after register_child already returned 0.

    Returns exit code: 0 success, 1 io-error, 3 no-existing-row (idempotency-violation).
    """
    registry_path = parent_session_dir / _REGISTRY_FILENAME
    lock_path = parent_session_dir / _LOCK_FILENAME
    sentinel_path = parent_session_dir / _ACTIVE_DIR / child_id

    try:
        lock_fd = open(str(lock_path), "a")  # noqa: SIM115
    except OSError as exc:
        print(f"ERROR: could not open lock file {lock_path}: {exc}", file=sys.stderr)
        return 1

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        registry = _load_registry(registry_path)

        if child_id not in registry:
            print(
                f"ERROR: idempotency violation — child_id {child_id!r} not found in "
                f"{registry_path}. Cannot mark-failed a non-existent row.",
                file=sys.stderr,
            )
            return 3

        registry[child_id]["status"] = _STATUS_FAILED

        try:
            _atomic_write_registry(registry_path, registry)
        except OSError as exc:
            print(
                f"ERROR: could not write registry at {registry_path}: {exc}", file=sys.stderr
            )
            return 1

        # Remove sentinel AFTER registry write.  Missing sentinel is the watchdog's signal
        # that the child is no longer active; writing status=failed first ensures the reaper
        # sees a consistent terminal state if it races the sentinel removal.
        try:
            sentinel_path.unlink()
        except FileNotFoundError:
            pass  # sentinel absent is fine — perhaps it was never created (crash mid-register)
        except OSError as exc:
            print(
                f"ERROR: registry updated to status=failed but sentinel removal failed "
                f"at {sentinel_path}: {exc}. Watchdog may see stale sentinel.",
                file=sys.stderr,
            )
            return 1

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    return 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _run_self_test() -> int:
    """Run in-process tempdir fixtures. Returns 0 on pass, 1 on failure."""
    failures = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        parent = Path(tmpdir)
        child_id = "c-1779347314-1073349-0001"
        child_session_dir = parent / "sessions" / child_id
        child_session_dir.mkdir(parents=True)
        ts_fixed = "2026-05-21T12:00:00.000000+00:00"

        # --- Test 1: register-fresh — new row written, sentinel created ---
        rc = register_child(parent, child_id, child_session_dir, "Test task title", ts=ts_fixed)
        if rc != 0:
            print(f"FAIL: test1 register-fresh returned {rc}, expected 0", file=sys.stderr)
            failures += 1
        else:
            registry_path = parent / "children-registry.json"
            sentinel_path = parent / "children-active" / child_id
            try:
                reg = json.loads(registry_path.read_text(encoding="utf-8"))
                row = reg[child_id]
                expected = {
                    "child_session_dir": str(child_session_dir),
                    "status": "active",
                    "spawned_at_ts": ts_fixed,
                    "task_title": "Test task title",
                    "launcher_session_id": None,
                }
                if row == expected and sentinel_path.exists():
                    print("PASS: test1 — register-fresh wrote correct row and sentinel", file=sys.stderr)
                else:
                    print(
                        f"FAIL: test1 — row={row!r} expected={expected!r} "
                        f"sentinel_exists={sentinel_path.exists()}",
                        file=sys.stderr,
                    )
                    failures += 1
            except Exception as exc:  # pragma: no cover — unexpected parse error
                print(f"FAIL: test1 — unexpected error: {exc}", file=sys.stderr)
                failures += 1

        # --- Test 2: idempotency-violation (active row) — exit 3 ---
        rc = register_child(parent, child_id, child_session_dir, "Duplicate title", ts=ts_fixed)
        if rc == 3:
            print("PASS: test2 — idempotency-violation on active row returned exit 3", file=sys.stderr)
        else:
            print(f"FAIL: test2 — expected exit 3, got {rc}", file=sys.stderr)
            failures += 1

        # --- Test 3: mark-failed rollback — status=failed, sentinel removed ---
        rc = mark_failed(parent, child_id)
        if rc != 0:
            print(f"FAIL: test3 mark-failed returned {rc}, expected 0", file=sys.stderr)
            failures += 1
        else:
            registry_path = parent / "children-registry.json"
            sentinel_path = parent / "children-active" / child_id
            try:
                reg = json.loads(registry_path.read_text(encoding="utf-8"))
                row = reg[child_id]
                if row["status"] == "failed" and not sentinel_path.exists():
                    print("PASS: test3 — mark-failed set status=failed and removed sentinel", file=sys.stderr)
                else:
                    print(
                        f"FAIL: test3 — status={row['status']!r}, sentinel_exists={sentinel_path.exists()}",
                        file=sys.stderr,
                    )
                    failures += 1
            except Exception as exc:  # pragma: no cover
                print(f"FAIL: test3 — unexpected error: {exc}", file=sys.stderr)
                failures += 1

        # --- Test 4: idempotency-violation (failed row) — exit 3 ---
        rc = register_child(parent, child_id, child_session_dir, "Reuse attempt", ts=ts_fixed)
        if rc == 3:
            print("PASS: test4 — idempotency-violation on failed row returned exit 3", file=sys.stderr)
        else:
            print(f"FAIL: test4 — expected exit 3, got {rc}", file=sys.stderr)
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
        prog="parent_messages_register_child.py",
        description=(
            "Register an L2 child at spawn time: writes a row to "
            "{parent_session_dir}/children-registry.json and touches "
            "{parent_session_dir}/children-active/{child_id}. "
            "Mirrors bin/parent_messages_write.py shape per W5-registration-design.md."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run in-process tempdir fixtures and exit (0=pass / 1=fail)",
    )
    parser.add_argument(
        "--child-id",
        metavar="CHILD_ID",
        help="Child ID to register (format: c-{parent_session_id}-{nnnn})",
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
        "--child-session-dir",
        metavar="PATH",
        help="Absolute path to the child's session directory.",
    )
    parser.add_argument(
        "--task-title",
        metavar="STRING",
        help="Short human-readable description of the dispatched task (<120 chars conventional).",
    )
    parser.add_argument(
        "--ts",
        metavar="ISO8601",
        help="Override spawned_at_ts timestamp (default: current UTC). Used for deterministic tests.",
    )
    parser.add_argument(
        "--mark-failed",
        action="store_true",
        help=(
            "Post-spawn rollback: rewrite row status=failed AND remove the "
            "children-active/{child_id} sentinel. Requires --child-id and "
            "--parent-session-dir (or CLAUDE_SESSION_DIR). "
            "Does NOT require --child-session-dir or --task-title."
        ),
    )

    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    # --- Validate required args ---
    if not args.child_id:
        parser.error("--child-id is required (or use --self-test)")

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

    if args.mark_failed:
        return mark_failed(parent_session_dir, args.child_id)

    # --- Registration path: validate additional required args ---
    if not args.child_session_dir:
        parser.error("--child-session-dir is required (or use --mark-failed or --self-test)")
    if not args.task_title:
        parser.error("--task-title is required (or use --mark-failed or --self-test)")

    child_session_dir = Path(args.child_session_dir)

    return register_child(
        parent_session_dir=parent_session_dir,
        child_id=args.child_id,
        child_session_dir=child_session_dir,
        task_title=args.task_title,
        ts=args.ts,
    )


if __name__ == "__main__":
    sys.exit(main())
