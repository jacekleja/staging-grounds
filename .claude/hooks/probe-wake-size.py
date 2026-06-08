#!/usr/bin/env python3
# dispatch-child-safe: false
"""
probe-wake-size.py — asyncRewake wake-message size-ceiling probe.

Registered on Stop event with asyncRewake: true in an isolated probe-scoped
settings file. Emits exactly PROBE_WAKE_SIZE_BYTES to stderr on first
invocation, writes a sentinel, then exits 2 to trigger the asyncRewake wake.
Subsequent invocations (sentinel present) exit 0 to prevent the self-wake loop
documented in .claude/knowledge/decisions/b2-async-dispatch-mechanism-digest.md
§ asyncRewake creates self-perpetuating wake loop without exit-0 guard.

Marker layout (see plan-asyncrewake-ceiling-canary.md § Probe sizes and outcome taxonomy):
  bytes 0..63: PROBE_WAKE_SIZE_START_<rand>_SIZE_<N>_OFFSET_0 (padded to 64)
  bytes N/2..N/2+63: PROBE_WAKE_SIZE_MID_<rand>_OFFSET_<N/2> (padded to 64)
  bytes N-16..N: PROBE_END_<rand> (padded to 16)
  filler: ASCII 'X' bytes
"""

import json
import os
import random
import string
import sys
import tempfile
from datetime import datetime, timezone


SENTINEL_FILENAME = "probe-wake-size-done.json"
# Minimum N to fit all three markers without overlap:
#   start: bytes 0..63 (64 bytes)
#   mid:   bytes N/2..N/2+63 (64 bytes)
#   end:   bytes N-16..N (16 bytes)
# For no overlap: mid_start (N/2) >= 64 AND end_start (N-16) >= mid_end (N/2+64)
# Second condition: N-16 >= N/2+64 => N/2 >= 80 => N >= 160
# Use 160 as the strict minimum; spec recommends N >= 256.
MIN_N_FULL_LAYOUT = 160


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rand_token_8() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def build_payload(n: int, rand_token: str) -> bytes:
    """
    Build exactly N bytes containing the three marker strings at spec-mandated offsets.

    For N < MIN_N_FULL_LAYOUT the markers are collapsed: start marker is written
    at offset 0 (truncated to fit), end marker at N-16 (or 0 if N<16), and the
    mid marker is omitted. This degenerate case is documented in the impl report.
    """
    payload = bytearray(b"X" * n)

    if n >= MIN_N_FULL_LAYOUT:
        # Full three-marker layout
        start_text = f"PROBE_WAKE_SIZE_START_{rand_token}_SIZE_{n}_OFFSET_0"
        mid_offset = n // 2
        mid_text = f"PROBE_WAKE_SIZE_MID_{rand_token}_OFFSET_{mid_offset}"
        end_offset = n - 16
        end_text = f"PROBE_END_{rand_token}"

        # Write start marker: right-pad with 'X' to exactly 64 bytes
        start_bytes = start_text.encode("ascii")[:64].ljust(64, b"X")
        payload[0:64] = start_bytes

        # Write mid marker: right-pad with 'X' to exactly 64 bytes
        mid_bytes = mid_text.encode("ascii")[:64].ljust(64, b"X")
        payload[mid_offset:mid_offset + 64] = mid_bytes

        # Write end marker: right-pad with 'X' to exactly 16 bytes
        end_bytes = end_text.encode("ascii")[:16].ljust(16, b"X")
        payload[end_offset:end_offset + 16] = end_bytes

    else:
        # Degenerate case: N too small to place all three non-overlapping markers.
        # Write a truncated start marker from offset 0, end marker from N-16 (or 0).
        # Mid marker is omitted entirely.
        start_text = f"PROBE_WAKE_SIZE_START_{rand_token}_SIZE_{n}_OFFSET_0"
        start_bytes = start_text.encode("ascii")[:min(64, n)].ljust(min(64, n), b"X")
        payload[0:len(start_bytes)] = start_bytes

        if n >= 16:
            end_text = f"PROBE_END_{rand_token}"
            end_bytes = end_text.encode("ascii")[:16].ljust(16, b"X")
            payload[n - 16:n] = end_bytes

    assert len(payload) == n, f"payload length {len(payload)} != {n}"
    return bytes(payload)


def main() -> None:
    # Step 1: resolve PROBE_SESSION_DIR
    session_dir = os.environ.get("PROBE_SESSION_DIR", "/tmp/probe-wake-size-default")
    os.makedirs(session_dir, exist_ok=True)

    # Step 2: read PROBE_WAKE_SIZE_BYTES
    size_env = os.environ.get("PROBE_WAKE_SIZE_BYTES")
    if size_env is None:
        print("probe-wake-size.py: PROBE_WAKE_SIZE_BYTES not set", file=sys.stderr)
        sys.exit(1)
    try:
        n = int(size_env)
        if n <= 0:
            raise ValueError("must be positive")
    except ValueError:
        print(
            f"probe-wake-size.py: PROBE_WAKE_SIZE_BYTES={size_env!r} is not a positive integer",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 3: sentinel check — exit 0 silently on subsequent invocations
    sentinel_path = os.path.join(session_dir, SENTINEL_FILENAME)
    if os.path.exists(sentinel_path):
        sys.exit(0)

    # Step 4: compose payload
    rand_token = rand_token_8()
    payload = build_payload(n, rand_token)
    assert len(payload) == n

    # Step 5: write sentinel atomically (tmp+rename)
    mid_offset = n // 2
    sentinel_data = {
        "rand_token": rand_token,
        "size_bytes": n,
        "exit_ts": iso_now(),
        "marker_offsets": {
            "start": 0,
            "mid": mid_offset,
            "end_marker_start": n - 16,
        },
    }
    tmp_path = sentinel_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(sentinel_data, f, indent=2)
        os.replace(tmp_path, sentinel_path)  # atomic on POSIX
    except OSError as exc:
        # Sentinel write failure: non-fatal but we log it; the payload will
        # still emit and the hook will exit 2. On a re-fire the sentinel will
        # be absent and the payload re-emits (harmless duplicate).
        print(f"probe-wake-size.py: sentinel write failed: {exc}", file=sys.stderr)

    # Step 6: emit exactly N bytes to stderr in chunks, verify count before flush
    chunk_size = 65536
    total_written = 0
    offset = 0
    while offset < n:
        chunk = payload[offset:offset + chunk_size]
        sys.stderr.buffer.write(chunk)
        total_written += len(chunk)
        offset += len(chunk)

    assert total_written == n, f"emitted {total_written} bytes, expected {n}"
    sys.stderr.buffer.flush()

    # Step 7: exit 2 — asyncRewake wake signal
    sys.exit(2)


if __name__ == "__main__":
    main()
