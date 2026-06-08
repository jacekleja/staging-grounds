#!/usr/bin/env python3
"""_codex_transcript_coerce.py — Translate codex-native JSONL events to Claude-schema events.

Called from bin/caa-session _dispatch_codex() as a post-process stage after run_session()
returns.  Pure-function core: coerce_codex_to_claude(raw_bytes, session_id) -> bytes.

Branch A (axis-11) coercion: the launcher boundary is the single translation locus,
keeping all downstream consumers (TUI, pipeline_prune, cycle-hook, smoke harness) oblivious
to codex-native event shapes.

Canonical codex event shapes handled (5 shapes):
  1. thread.started   — maps to Claude session-init record (sessionId = thread_id)
  2. turn.started     — no output (turn boundary; Claude schema has no equivalent)
  3. item.completed   type=agent_message -> Claude assistant record
  4. item.completed   type=<other>       -> skipped with stderr warning (v1 scope)
  5. turn.completed                      -> Claude assistant usage record
  6. <unknown type>                      -> skipped with stderr warning

Lossy mappings (documented; irreversible per-transcript after coercion):
  - usage.cached_input_tokens     -> mapped to cache_read_input_tokens (no direct Claude analog)
  - usage.reasoning_output_tokens -> collapsed into output_tokens (codex-specific field)
  - auth_mode                     -> not present in Claude schema; dropped
  - item.id granular lineage      -> item.id preserved as message id prefix only

Raw codex JSONL durable archival is handled by the caller (_dispatch_codex()) which writes
{child_sidecar_dir}/codex-raw.jsonl before invoking coerce_codex_to_claude().
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_thread_id(raw_bytes: bytes) -> str | None:
    """Return the codex thread_id from the first thread.started event.

    Used to derive the Claude session_id so it is consistent with the codex
    namespace.  Codex thread-ids are UUIDv7-shaped and structurally compatible
    with Claude session UUIDs — no consumer assumes session-id provenance.

    Returns None when raw_bytes contains no thread.started event.
    """
    for line in raw_bytes.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            return event.get("thread_id")
    return None


def coerce_codex_to_claude(raw_bytes: bytes, session_id: str) -> bytes:
    """Translate codex-native JSONL bytes to Claude-schema JSONL bytes.

    Pure function: deterministic, no I/O.  session_id should be the codex
    thread_id for namespace-compatible session keying (call extract_thread_id()
    first to derive it from the raw stream).

    Returns empty bytes when raw_bytes contains no translatable events.
    """
    events: list[dict] = []
    for line in raw_bytes.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(
                f"[_codex_transcript_coerce] malformed JSON line skipped: {e}",
                file=sys.stderr,
            )

    output_lines = [
        json.dumps(record, ensure_ascii=False)
        for record in _coerce_events(events, session_id)
    ]
    if not output_lines:
        return b""
    return ("\n".join(output_lines) + "\n").encode("utf-8")


def coerce_file(
    raw_path: str | Path,
    output_path: str | Path,
    session_id: str | None = None,
) -> None:
    """Read raw codex JSONL from raw_path; write Claude-schema JSONL to output_path.

    If session_id is None, derive from thread_id in the raw bytes.
    Writes atomically via a .tmp rename to prevent partial-read races.
    """
    raw_path = Path(raw_path)
    output_path = Path(output_path)

    raw_bytes = raw_path.read_bytes()
    if session_id is None:
        session_id = extract_thread_id(raw_bytes) or raw_path.stem

    coerced = coerce_codex_to_claude(raw_bytes, session_id)
    if not coerced:
        return
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_bytes(coerced)
    tmp.rename(output_path)


# ---------------------------------------------------------------------------
# Internal: event-by-event coercion
# ---------------------------------------------------------------------------

def _coerce_events(events: list[dict], session_id: str) -> Iterator[dict]:
    """Yield Claude-schema records for each codex event.

    Processes events in order.  thread.started MUST appear as the first event
    per codex wire format; if absent, session_id is used directly and no
    summary record is emitted.
    """
    for event in events:
        event_type = event.get("type")

        # ── Shape 1: thread.started ────────────────────────────────────────
        if event_type == "thread.started":
            # Use the codex thread_id as sessionId so the Claude-path filename
            # (~/.claude/projects/<slug>/<thread_id>.jsonl) matches the session_id
            # derived from extract_thread_id() at the call site.
            thread_id = event.get("thread_id") or session_id
            yield {
                "sessionId": thread_id,
                "type": "summary",
                "summary": {
                    "costUSD": 0.0,
                    "durationMs": 0,
                    # Forensic tag: lets readers know this is a coerced codex transcript.
                    "family": "codex",
                },
                "cwd": "",
            }

        # ── Shape 2: turn.started ──────────────────────────────────────────
        elif event_type == "turn.started":
            # Turn boundary has no Claude analog; emit nothing.
            pass

        # ── Shapes 3 & 4: item.completed ──────────────────────────────────
        elif event_type == "item.completed":
            item = event.get("item") or {}
            item_type = item.get("type")

            if item_type == "agent_message":
                # Shape 3: agent text output → Claude assistant record.
                yield {
                    "sessionId": session_id,
                    "type": "assistant",
                    "message": {
                        "id": f"msg_{item.get('id', 'unknown')}",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": item.get("text", "")}
                        ],
                    },
                    "costUSD": 0.0,
                    "cwd": "",
                }
            else:
                # Shape 4: non-agent_message items (function_call, tool outputs, etc.)
                # are skipped in v1 scope.  v2 candidate: map function_call →
                # Claude tool_use record, function_call_output → tool_result record.
                print(
                    f"[_codex_transcript_coerce] item type={item_type!r} skipped (v1 scope)",
                    file=sys.stderr,
                )

        # ── Shape 5: turn.completed ────────────────────────────────────────
        elif event_type == "turn.completed":
            usage = event.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            # reasoning_output_tokens: codex-specific (o-series reasoning); no Claude
            # analog, collapsed into output_tokens so total counts are not understated.
            reasoning_output_tokens = int(usage.get("reasoning_output_tokens") or 0)
            # cached_input_tokens: no direct Claude analog; mapped to
            # cache_read_input_tokens so extract_total_tokens() in cycle-hook can
            # include them when summing context usage.
            cached_input_tokens = int(usage.get("cached_input_tokens") or 0)

            yield {
                "sessionId": session_id,
                "type": "assistant",
                "message": {
                    "id": "usage",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens + reasoning_output_tokens,
                        "cache_read_input_tokens": cached_input_tokens,
                        "cache_creation_input_tokens": 0,
                    },
                },
                "costUSD": 0.0,
                "cwd": "",
            }

        # ── Shape 6: unknown event type ────────────────────────────────────
        else:
            print(
                f"[_codex_transcript_coerce] unknown event type={event_type!r} skipped",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# CLI entry point (for manual coercion and smoke testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Coerce codex-native JSONL to Claude-schema JSONL"
    )
    parser.add_argument("raw", help="Raw codex JSONL input path")
    parser.add_argument("output", help="Claude-schema JSONL output path")
    parser.add_argument(
        "--session-id",
        default=None,
        help="Override session_id (default: extract from thread.started event)",
    )
    ns = parser.parse_args()
    coerce_file(ns.raw, ns.output, session_id=ns.session_id)
    print(f"[_codex_transcript_coerce] coerced {ns.raw} -> {ns.output}")
