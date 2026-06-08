#!/usr/bin/env python3
"""
probe-asyncrewake.py — Single-shot asyncRewake probe hook.

Registered on the Stop event with asyncRewake: true in the probe-scoped settings file.
This script is intentionally simple; it records spawn + exit timestamps via marker
files so the timeline can be reconstructed after the test session ends.

Behavior:
  1. Write spawn marker immediately.
  2. Sleep 30s.
  3. Write exit marker.
  4. Print the stderr marker to stderr.
  5. Exit 2 — signals asyncRewake wake mechanism.

Marker files land in the session_dir passed via PROBE_SESSION_DIR env var (set in
the probe settings file command string). Falls back to /tmp if unset.
"""

import json
import os
import random
import string
import sys
import time
from datetime import datetime, timezone


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main():
    marker_dir = os.environ.get("PROBE_SESSION_DIR", "/tmp")
    os.makedirs(marker_dir, exist_ok=True)

    rand_token = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    stderr_marker = f"PROBE_ASYNCREWAKE_WAKE_{rand_token}"

    spawn_path = os.path.join(marker_dir, "probe-asyncrewake-spawn.json")
    exit_path = os.path.join(marker_dir, "probe-asyncrewake-exit.json")

    # Step 1: write spawn marker immediately
    spawn_data = {
        "spawn_ts": iso_now(),
        "pid": os.getpid(),
        "rand_token": rand_token,
    }
    with open(spawn_path, "w") as f:
        json.dump(spawn_data, f, indent=2)

    # Step 2: sleep 30s — long enough that the test session has entered standby
    time.sleep(30)

    # Step 3: write exit marker
    exit_data = {
        "exit_ts": iso_now(),
        "stderr_marker": stderr_marker,
    }
    with open(exit_path, "w") as f:
        json.dump(exit_data, f, indent=2)

    # Step 4: emit stderr marker — harness delivers this as system reminder on exit-2
    print(stderr_marker, file=sys.stderr)

    # Step 5: exit 2 — the asyncRewake wake signal
    sys.exit(2)


if __name__ == "__main__":
    main()
