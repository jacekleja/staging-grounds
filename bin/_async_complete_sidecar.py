# bin/_async_complete_sidecar.py
"""Shared writer for {parent_session_dir}/children/{child_id}/async-complete.json.

Two L2-side callers import and use this module:
  - bin/l2_terminal_event.py (HK-1b completed/failed path via the cycling skill)
  - bin/suborch-watchdog.py  (wait-timeout failed path, watchdog-side)

Schema mirrors the subprocess-route producer in
.claude/mcp/context-tools/src/tools/dispatch-agent.ts (runClaudeSubprocess);
the `route` field distinguishes "l2-dispatch" (this helper) from
"claude-subprocess" (the TS writer). Atomic tmp+rename matches the TS writer's
fs.renameSync pattern so dispatch-completion-watcher.py and dispatch-stale-sweep.py
never observe a partial write.

Two-sentinel layering across files: the `.claim` sentinel below guards the
async-complete.json write; a separate `wait-timeout-emitted` sentinel inside
bin/suborch-watchdog.py guards that script's events.jsonl wait-timeout append.
The two coexist because they protect different files — neither subsumes the
other.
"""
import json
import os
from datetime import datetime, timezone


def write_async_complete_sidecar(
    sidecar_dir: str | os.PathLike,
    *,
    spawn_id: str,
    kind: str,                       # "completed" or "failed"
    failure_class: str = "",
    completed_at: str | None = None,
) -> None:
    """Atomically write async-complete.json under sidecar_dir.

    First-writer-wins: claims an O_EXCL sentinel at path+'.claim' before
    composing the payload. Subsequent callers (e.g. the watchdog's wait-timeout
    path racing the child's HK-1b completed/failed emit) hit FileExistsError
    on the claim and return silently — only one async-complete.json sidecar
    lands per child.

    Raises OSError on filesystem failure (other than the claim's FileExistsError,
    which is consumed as a no-op return); callers should wrap in try/except
    when the write is best-effort (events.jsonl is the authoritative record).

    completed_at defaults to a Z-suffixed millisecond ISO-8601 string matching
    the subprocess-route producer convention (JavaScript .toISOString()).
    Callers may pass an explicit value to keep symmetry with an already-emitted
    events.jsonl record's ts field.
    """
    path = os.path.join(os.fspath(sidecar_dir), "async-complete.json")
    claim = path + ".claim"
    # O_EXCL claim: first writer wins atomically; subsequent writers no-op.
    # This resolves the watchdog-vs-HK-1b race on async-complete.json under
    # the residual class-mismatch window the events.jsonl-side sentinel at
    # bin/suborch-watchdog.py does not coordinate across (the HK-1b path
    # does not check or create that sentinel).
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return
    os.close(fd)

    if completed_at is None:
        completed_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    payload = {
        "spawn_id": spawn_id,
        "exit_code": 0 if kind == "completed" else 1,
        "signal": None,
        "elapsed_seconds": 0.0,
        "stderr_tail": failure_class,
        "captured_to": None,
        "output_text": None,
        "output_truncated": False,
        "completed_at": completed_at,
        "route": "l2-dispatch",
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)
