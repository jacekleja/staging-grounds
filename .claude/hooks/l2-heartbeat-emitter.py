#!/usr/bin/env python3
# OPT-IN: dormant until L2-sidecar phase activates; requires CAA_CHILD_SIDECAR_DIR env var and explicit settings.json registration
# dispatch-child-safe: false
"""PreToolUse hook: L2 heartbeat emitter.

Appends a `heartbeat` event to the child session's events.jsonl on every
PreToolUse fire, so the parent watchdog does NOT falsely emit `heartbeat-stale`
against an active-but-slow L2 session.

ACTIVATION: complete no-op unless CAA_CHILD_SIDECAR_DIR is set and non-empty.
Root sessions have this env var unset — this hook is inert for them.

PATH RESOLUTION: reads the child-profile.json manifest from CAA_CHILD_SIDECAR_DIR
and extracts ipc.events_path (a verified-absolute path per schema v4 §2). The
events.jsonl path is NEVER computed via env-var traversal — that was the v5 BU-4
bug that v6 corrects against.

OUTPUT DISCIPLINE: no stdout (no-op from Claude Code's perspective). The
heartbeat lands in events.jsonl via direct file append, not via hook stdout.
Never emits hookSpecificOutput of any kind.

REGISTRATION: dormant until registered in .claude/settings.json under PreToolUse
with a wildcard matcher (registration is a separate Phase step, not A4 scope).
This hook file deliberately avoids all Signal-A and Signal-B patterns defined by
the PreToolUse:Agent prompt-mutation lint rule.
"""
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone

from _dispatch_child_guard import is_l2_child

# Crockford base32 alphabet (32 chars, omits I, L, O, U)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _make_ulid() -> str:
    """Generate a ULID (Universally Unique Lexicographically Sortable Identifier).

    Output: 26 chars, Crockford base32 alphabet (0-9 A-Z minus I L O U).
    - First 10 chars: 48-bit millisecond timestamp (big-endian, clamped to 48 bits).
    - Last 16 chars: 80-bit cryptographic randomness from secrets.token_bytes(10).

    Lexicographic ordering of two ULIDs generated in different milliseconds
    preserves time ordering. Within the same millisecond, ordering is random.
    No PyPI dependency — uses only stdlib secrets and time modules.
    """
    # 48-bit millisecond timestamp
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # clamp to 48 bits

    # Encode timestamp into 10 Crockford base32 chars (5 bits each, big-endian)
    ts_chars = []
    val = ts_ms
    for _ in range(10):
        ts_chars.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    ts_part = "".join(reversed(ts_chars))

    # 80-bit randomness: 10 bytes → 16 Crockford base32 chars (80 / 5 = 16)
    rand_bytes = secrets.token_bytes(10)
    rand_int = int.from_bytes(rand_bytes, "big")
    rand_chars = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand_int & 0x1F])
        rand_int >>= 5
    rand_part = "".join(reversed(rand_chars))

    return ts_part + rand_part


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

        events_path = manifest.get("ipc", {}).get("events_path", "")
        if not events_path:
            # Manifest present but ipc.events_path absent — fail-open, no heartbeat
            return

        child_id = manifest.get("child_id", "")
        parent_session_id = manifest.get("parent_session_id", "")
        task_title = manifest.get("task", {}).get("title", "")
        depth = manifest.get("depth", 1)

        record = {
            "schema": "caa.suborch.event/v1",
            "event_id": _make_ulid(),
            "kind": "heartbeat",
            "child_id": child_id,
            "parent_session_id": parent_session_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            # seq=0 for heartbeat events: heartbeats are out-of-band progress signals,
            # not part of the main event sequence. Phase E lint will revisit if
            # globally-monotonic seq is required across all event kinds.
            "seq": 0,
            "dispatch_task": task_title,
            "depth": depth,
        }

        # O_APPEND holds the VFS inode lock on local Linux fs — no extra fcntl needed
        with open(events_path, "a", encoding="utf-8", buffering=1) as ef:
            ef.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        # Hook must never crash the host Claude Code process
        pass

    # No stdout — the heartbeat is recorded via direct file append, not hook stdout


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
