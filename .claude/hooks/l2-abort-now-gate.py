#!/usr/bin/env python3
# dispatch-child-safe: false
"""PreToolUse hook: L2 child-side abort-now enforcing gate.

Checks the `{child_session_dir}/abort-now` sentinel on every PreToolUse fire.
When the sentinel is present, appends an `aborted` event to events.jsonl and
hard-exits (sys.exit(2)) so Claude Code's PreToolUse block primitive stops
further tool calls in the child session.

ACTIVATION: complete no-op unless CAA_CHILD_SIDECAR_DIR is set and non-empty.
Root sessions have this env var unset — this hook is inert for them.

PATH RESOLUTION: reads child-profile.json from CAA_CHILD_SIDECAR_DIR and
extracts child_session_dir (sentinel location) and ipc.events_path (event
append target). Path resolution via manifest fields only — no env-var traversal.

EXIT CODES:
  0 — no abort sentinel present (or not an L2 child session); tool call proceeds.
  2 — abort sentinel detected; tool call hard-blocked. stderr contains "abort-now"
      for diagnosability. Claude Code delivers stderr as error feedback to the host
      process on exit 2 at PreToolUse. [verified: .claude/knowledge/constraints/platform/hooks-blocking-primitives.md]

IDEMPOTENCY: if the last event in events.jsonl is already kind="aborted", the
append is skipped and only the hard-exit fires. An fcntl.flock(LOCK_EX) on
events.jsonl covers the read-last + maybe-append + close window.
"""
import fcntl
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone

from _dispatch_child_guard import is_l2_child

# Crockford base32 alphabet (32 chars, omits I, L, O, U) — same as heartbeat emitter
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _make_ulid() -> str:
    """Generate a ULID (same implementation as l2-heartbeat-emitter.py)."""
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    ts_chars = []
    val = ts_ms
    for _ in range(10):
        ts_chars.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    ts_part = "".join(reversed(ts_chars))

    rand_bytes = secrets.token_bytes(10)
    rand_int = int.from_bytes(rand_bytes, "big")
    rand_chars = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand_int & 0x1F])
        rand_int >>= 5
    rand_part = "".join(reversed(rand_chars))

    return ts_part + rand_part


def _read_last_seq(events_path: str) -> int:
    """Return the highest seq seen in events.jsonl, or -1 if the file is empty/missing."""
    try:
        with open(events_path, encoding="utf-8") as fh:
            last_seq = -1
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    seq = row.get("seq")
                    if isinstance(seq, int) and seq > last_seq:
                        last_seq = seq
                except (json.JSONDecodeError, ValueError):
                    pass
        return last_seq
    except (OSError, IOError):
        return -1


def _last_event_is_aborted(events_path: str) -> bool:
    """Return True if the last non-empty event line in events.jsonl is kind='aborted'."""
    try:
        with open(events_path, encoding="utf-8") as fh:
            last_line = ""
            for line in fh:
                if line.strip():
                    last_line = line.strip()
        if not last_line:
            return False
        row = json.loads(last_line)
        return row.get("kind") == "aborted"
    except (OSError, IOError, json.JSONDecodeError, ValueError):
        return False


def main() -> None:
    # Activation guard: fire only in L2-sidecar context; suppress in dispatched-child
    # (is_l2_child() requires CAA_CHILD_SIDECAR_DIR set AND CAA_DISPATCH_CHILD unset)
    if not is_l2_child():
        return
    sidecar_dir = os.environ.get("CAA_CHILD_SIDECAR_DIR", "")

    # Read stdin once (required by the hook harness — we discard the payload)
    try:
        sys.stdin.read()
    except Exception:
        return

    try:
        manifest_path = os.path.join(sidecar_dir, "child-profile.json")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)

        child_session_dir = manifest.get("child_session_dir", "")
        if not child_session_dir:
            # Manifest present but child_session_dir absent — fail-open, no gate
            return

        events_path = manifest.get("ipc", {}).get("events_path", "")
        if not events_path:
            # Manifest present but ipc.events_path absent — fail-open, no gate
            return

    except Exception:
        # Hook must never crash the host Claude Code process
        return

    # Check the abort-now sentinel
    sentinel_path = os.path.join(child_session_dir, "abort-now")
    if not os.path.exists(sentinel_path):
        return

    # Sentinel present — abort the child session
    child_id = manifest.get("child_id", "")
    parent_session_id = manifest.get("parent_session_id", "")
    ts_now = datetime.now(timezone.utc).isoformat()

    try:
        with open(events_path, "a+", encoding="utf-8") as ef:
            # Exclusive lock: covers read-last + maybe-append + close atomically
            fcntl.flock(ef.fileno(), fcntl.LOCK_EX)
            try:
                ef.seek(0)
                content = ef.read()
                # Idempotency: skip append if last event is already 'aborted'
                last_aborted = False
                last_line = ""
                for line in content.splitlines():
                    if line.strip():
                        last_line = line.strip()
                if last_line:
                    try:
                        row = json.loads(last_line)
                        last_aborted = row.get("kind") == "aborted"
                    except (json.JSONDecodeError, ValueError):
                        pass

                if not last_aborted:
                    next_seq = -1
                    for line in content.splitlines():
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                            seq = row.get("seq")
                            if isinstance(seq, int) and seq > next_seq:
                                next_seq = seq
                        except (json.JSONDecodeError, ValueError):
                            pass
                    next_seq += 1

                    record = {
                        "schema": "caa.suborch.event/v1",
                        "event_id": _make_ulid(),
                        "kind": "aborted",
                        "child_id": child_id,
                        "parent_session_id": parent_session_id,
                        "ts": ts_now,
                        "seq": next_seq,
                        "abort_source": "parent-sentinel",
                        "reason": "parent-abort",
                    }
                    ef.seek(0, 2)  # seek to end before append
                    ef.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(ef.fileno(), fcntl.LOCK_UN)

    except Exception:
        # Even on events.jsonl write failure, we must still hard-exit
        pass

    # After the 'aborted' event lands and BEFORE sys.exit(2), the L2 process
    # needs two cleanup writes for clean termination — otherwise it hangs
    # ~10min on Stop-hook polling caps (turn-continuity-block + dispatch-
    # completion-watcher). Both writes are idempotent and best-effort: failures
    # must not block the exit-2 (the gate's core contract). See
    # sessions/1779998598-3026319-d1a1cc464d69/diagnosis-l2-suborch-hang-after-
    # terminal.md (DR-2 root cause).
    #
    # PATH NOTE: The cleanup writes target the L2's REAL session-dir (where
    # bin/claude-session._file_watcher watches and where turn-continuity-block
    # reads `loop-active`). That dir is exposed to the hook via the
    # CAA_CHILD_SESSION_DIR env-var (mirrors bin/l2_terminal_event.py:178).
    # The manifest's `child_session_dir` is the IPC sidecar path (where the
    # abort-now sentinel + events.jsonl live) — a DIFFERENT directory; writing
    # cleanup sentinels there is a no-op because nothing watches that path for
    # those sentinels. See Ep-10/Ep-11 DR-2 investigation in the same diagnosis
    # file (Fix 2 no-op root cause).
    l2_session_dir = os.environ.get("CAA_CHILD_SESSION_DIR", "")
    # Fix-2.1 (Ep-12): if hook subprocess didn't inherit CAA_CHILD_SESSION_DIR
    # from the parent claude process, fall back to reading /proc/{ppid}/environ.
    # The hook's parent is the L2 claude process, whose env DOES carry the var.
    if not l2_session_dir:
        try:
            with open(f"/proc/{os.getppid()}/environ", "rb") as _envf:
                _env_data = _envf.read()
            for _entry in _env_data.split(b"\0"):
                if _entry.startswith(b"CAA_CHILD_SESSION_DIR="):
                    l2_session_dir = _entry[len(b"CAA_CHILD_SESSION_DIR="):].decode("utf-8", errors="replace")
                    break
        except (OSError, IOError):
            pass

    if l2_session_dir:
        # (a) Clear loop-active so turn-continuity-block.py allows Stop.
        try:
            loop_active_path = os.path.join(l2_session_dir, "loop-active")
            if os.path.exists(loop_active_path):
                os.unlink(loop_active_path)
        except OSError:
            pass

        # (b) Write l2-exit-requested so bin/claude-session._file_watcher
        #     SIGTERMs the claude process (the canonical L2 exit path; mirrors
        #     bin/l2_terminal_event.py:182 write contract — empty file, "w" mode).
        try:
            exit_requested_path = os.path.join(l2_session_dir, "l2-exit-requested")
            open(exit_requested_path, "w").close()
        except OSError:
            pass

    sys.stderr.write(
        f"[l2-abort-now-gate] abort-now sentinel detected for child {child_id!r}; "
        "blocking tool call and terminating child session.\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
