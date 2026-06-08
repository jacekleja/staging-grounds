"""Shared utilities for the parent-messages L2 channel.

Used by:
  bin/l2-parent-messages-poll.py       — tails parent-messages.jsonl, writes queue entries
  bin/l2-parent-messages-await-line.py — blocks until a new queue entry with seq > since-seq

Wire format on disk: {seq, event_id, ts, body, child_id} per queue entry.
Sentinel names match SKILL.md §K.1/§K.3 prose exactly.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile


def queue_file_path(
    child_session_dir: pathlib.Path,
    seq: int,
    event_id: str,
) -> pathlib.Path:
    """Return the canonical path for a queue file given its seq and event_id."""
    return child_session_dir / "parent-wake-queue" / f"{seq}-{event_id}.json"


def wake_pending_path(child_session_dir: pathlib.Path) -> pathlib.Path:
    """Return the path to the parent-wake-pending sentinel file."""
    return child_session_dir / "parent-wake-pending"


def abort_sentinel_path(child_session_dir: pathlib.Path) -> pathlib.Path:
    """Return the path to the abort-now sentinel file."""
    return child_session_dir / "abort-now"


def atomic_write_json(path: pathlib.Path, payload: object) -> None:
    """Write *payload* as JSON to *path* via an atomic rename.

    Uses a sibling temp file + os.replace so readers never see a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        # Clean up the temp file on any failure; re-raise so the caller sees the error.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_queue_max_seq(queue_dir: pathlib.Path) -> int:
    """Return the maximum seq value across all .json files in queue_dir.

    Returns 0 when the directory is absent or contains no readable queue entries.
    Each file is expected to carry a top-level ``seq`` integer key; files that
    cannot be parsed are silently skipped.
    """
    if not queue_dir.exists():
        return 0
    max_seq = 0
    for entry in queue_dir.iterdir():
        if entry.suffix != ".json":
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            seq = int(data.get("seq", 0))
            if seq > max_seq:
                max_seq = seq
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return max_seq
