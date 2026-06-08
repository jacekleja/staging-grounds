#!/usr/bin/env python3
# dispatch-child-safe: false
"""
user-intent-capture.py — Auto-capture substantive user prompts to a user-intent artifact.

Fires on UserPromptSubmit. Applies a heuristic to determine whether the prompt
is substantive (architectural, directional, intent-bearing). On pass: appends a
verbatim quote-block to the user-intent artifact. On skip: writes one-line entry
to a skip-log for operator audit.

Silent-tracker discipline: always exits 0; never writes to stdout; never raises
an unhandled exception.

Schema compliance: first-write produces a schema-compliant artifact per
.claude/knowledge/decisions/user-intent-capture-discipline.md § Artifact schema.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys

from _dispatch_child_guard import exit_if_dispatched_child

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)
from path_c_shared_state_guard import ensure_parent_for_path  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# FRAMING_TOKENS_LIST_BEGIN
FRAMING_TOKENS = [
    "we need",
    "i would expect",
    "i expect",
    "first-class",
    "the question is",
    "non-negotiable",
    "architectural",
    "vision",
    "the real concern",
    "i'd like",
    "i would like",
    "let's go with",
    "what i want",
    "the goal is",
]
# FRAMING_TOKENS_LIST_END

# ACK_PATTERNS_LIST_BEGIN
ACK_PATTERNS = {
    "ok",
    "okay",
    "yes",
    "no",
    "continue",
    "proceed",
    "go ahead",
    "sounds good",
    "looks good",
    "lgtm",
    "thanks",
    "thank you",
}
# ACK_PATTERNS_LIST_END

RECENT_HASHES_CAP = 50
SKIP_LOG_LINE_CAP = 1000
SKIP_LOG_BYTE_CAP = 200 * 1024  # 200 KB

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
HASH_LINE_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Project-root resolution
# ---------------------------------------------------------------------------

def _resolve_project_root() -> str:
    """Resolve the project root: env-var first, then __file__-walk (3 parents up)."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if env_root and os.path.isdir(os.path.join(env_root, ".claude")):
        return env_root
    # __file__ is at <root>/.claude/hooks/user-intent-capture.py → 3 parents up
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


# ---------------------------------------------------------------------------
# Campaign-id discovery
# ---------------------------------------------------------------------------

def _discover_campaign_id(project_root: str) -> str | None:
    """Return the active campaign-id slug from CAA_CAMPAIGN_ID env, or None."""
    content = os.environ.get('CAA_CAMPAIGN_ID', '').strip()
    if not content or not SLUG_RE.match(content):
        return None
    # Guard against path traversal: env-var content is operator-controlled
    campaigns_dir = os.path.join(project_root, ".agent_context", "campaigns")
    resolved = os.path.normpath(os.path.join(campaigns_dir, content))
    if not resolved.startswith(os.path.normpath(campaigns_dir)):
        return None
    return content


def _artifact_root(project_root: str, campaign_id: str | None, session_id: str) -> str:
    """Return the directory for all three artifact files (user-intent.md, skip-log, .recent-hashes)."""
    if campaign_id:
        root = os.path.join(project_root, ".agent_context", "campaigns", campaign_id)
    else:
        root = os.path.join(project_root, ".agent_context", "sessions", session_id)
    ensure_parent_for_path(
        os.path.join(root, ".path-c-root-check"),
        main_root=project_root,
        worktree_root=os.environ.get("CAA_WORKTREE_ROOT") or os.getcwd(),
    )
    return root


# ---------------------------------------------------------------------------
# Substantive-message heuristic
# ---------------------------------------------------------------------------

def _count_sentences(text: str) -> int:
    return len(re.findall(r"[.!?]+(?:\s|$)", text))


def _is_substantive(prompt_stripped: str) -> tuple[bool, str]:
    """Return (is_substantive, skip_reason). skip_reason non-empty when not substantive."""
    try:
        length = len(prompt_stripped)

        # LENGTH_FALLBACK: any message > 600 chars is captured regardless
        if length > 600:
            return True, ""

        sentences = _count_sentences(prompt_stripped)
        lower = prompt_stripped.lower()

        # PURE_ACK_GATE: entire message is an ack
        collapsed = lower.rstrip(".!?").strip()
        # collapse internal whitespace for multi-word acks
        collapsed = re.sub(r"\s+", " ", collapsed)
        if collapsed in ACK_PATTERNS:
            return False, "pure_ack"

        # LENGTH_GATE: at least 3 sentences
        has_length = sentences >= 3

        # FRAMING_TOKEN_GATE: at least one framing token present
        has_framing = any(token in lower for token in FRAMING_TOKENS)

        if has_length and has_framing:
            return True, ""

        parts = []
        if sentences < 3:
            parts.append("length_lt_3_sentences")
        if not has_framing:
            parts.append("no_framing_token")
        if length <= 600:
            parts.append("len_le_600")
        return False, " AND ".join(parts)

    except Exception:
        # Heuristic must never raise; treat as not-substantive on crash
        return False, "heuristic_error"


# ---------------------------------------------------------------------------
# Dedup mechanism
# ---------------------------------------------------------------------------

def _read_recent_hashes(hashes_path: str) -> list[str]:
    """Read .recent-hashes; return list of valid 64-char hex strings."""
    try:
        raw = open(hashes_path, encoding="utf-8").read().splitlines()
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        raw = []
    return [line for line in raw if HASH_LINE_RE.match(line)]


def _append_and_cap_hashes(hashes_path: str, new_hash: str, existing: list[str]) -> None:
    """Append new_hash to .recent-hashes and enforce the cap."""
    # Append first
    try:
        fd = os.open(hashes_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.write(fd, (new_hash + "\n").encode("utf-8"))
        os.close(fd)
    except OSError:
        return

    # Enforce cap: if existing + 1 > CAP, rewrite with tail
    all_hashes = existing + [new_hash]
    if len(all_hashes) > RECENT_HASHES_CAP:
        tail = all_hashes[-RECENT_HASHES_CAP:]
        try:
            with open(hashes_path, "w", encoding="utf-8") as f:
                f.write("\n".join(tail) + "\n")
        except OSError:
            pass  # cap-rewrite failure is non-fatal; file stays over-cap until next write


# ---------------------------------------------------------------------------
# Skip-log rotation
# ---------------------------------------------------------------------------

def _maybe_rotate_skip_log(path: str) -> None:
    """Read line / byte counts; if either cap exceeded, truncate to tail-by-line-count."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return  # no file or unreadable — nothing to rotate
    over_byte_cap = size > SKIP_LOG_BYTE_CAP
    over_line_cap = False
    if not over_byte_cap:
        try:
            with open(path, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
        except (OSError, UnicodeDecodeError):
            return  # unreadable → leave alone
        over_line_cap = line_count > SKIP_LOG_LINE_CAP
    if not (over_byte_cap or over_line_cap):
        return  # under both caps; no rotation needed
    # Rotate: read all lines, keep tail (last half by line count)
    try:
        with open(path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return  # unreadable → cannot rotate; leave for next fire
    keep_count = max(1, len(all_lines) // 2)
    tail = all_lines[-keep_count:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(tail)
    except OSError:
        return  # disk-full mid-rotate → leave file as-is


# ---------------------------------------------------------------------------
# Artifact write helpers
# ---------------------------------------------------------------------------

def _quote_block(prompt: str, ts: str) -> str:
    """Format the verbatim quote-block for appending to the artifact."""
    lines = prompt.split("\n")
    if len(lines) == 1:
        body = lines[0]
    else:
        # First line follows directly after the opening > "; continuation lines
        # get their own > prefix so the whole block is a valid Markdown blockquote.
        continuation = "\n".join(">" if l == "" else f"> {l}" for l in lines[1:])
        body = lines[0] + "\n" + continuation
    return f'> "{body}"\n>\n> — {ts}\n\n'


def _first_write_template(
    campaign_id: str | None,
    session_id: str,
    created_date: str,
    initial_quote_block: str,
) -> str:
    """Return the full schema-compliant initial artifact content."""
    campaign_val = campaign_id if campaign_id else "session-scoped"
    return (
        "---\n"
        f"campaign: {campaign_val}\n"
        f"session: {session_id}\n"
        f"created: {created_date}\n"
        "status: live\n"
        "purpose: User-intent capture (auto-generated by .claude/hooks/user-intent-capture.py)\n"
        "re-read-required-at: []\n"
        "quote-sources: []\n"
        "---\n"
        "\n"
        "# User Intent\n"
        "\n"
        "## Topic 1: (untriaged)\n"
        "\n"
        + initial_quote_block
        + "## Disposition tracker\n"
        "\n"
        "| concept-id | Topic | Coverage status | Plan section / rationale |\n"
        "|---|---|---|---|\n"
    )


def _write_artifact(artifact_path: str, quote_block: str, campaign_id: str | None, session_id: str) -> None:
    """Write or append to the user-intent artifact."""
    if not os.path.exists(artifact_path):
        # First write: emit full schema-compliant template via exclusive-create
        created_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        content = _first_write_template(campaign_id, session_id, created_date, quote_block)
        try:
            fd = os.open(artifact_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            return
        except FileExistsError:
            # A concurrent hook won the create race; fall through to append
            pass
        except OSError:
            return  # write failure is non-fatal

    # Append quote-block BEFORE the ## Disposition tracker heading so the tracker
    # table is never fragmented.  Fall back to end-of-file append only when the
    # heading is absent (corrupt or manually edited artifact).
    try:
        with open(artifact_path, "r", encoding="utf-8", errors="replace") as f:
            existing = f.read()
    except OSError:
        existing = None

    if existing is not None:
        marker = "## Disposition tracker"
        idx = existing.find(marker)
        if idx != -1:
            new_content = existing[:idx] + quote_block + existing[idx:]
            try:
                with open(artifact_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except OSError:
                pass  # write failure is non-fatal
            return
        # Heading absent — fall through to plain end-of-file append.

    try:
        fd = os.open(artifact_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.write(fd, quote_block.encode("utf-8"))
        os.close(fd)
    except OSError:
        pass  # append failure is non-fatal


def _write_skip_log(skip_log_path: str, ts: str, prompt_stripped: str, sentences: int, skip_reason: str) -> None:
    """Append one JSON entry to the skip log."""
    entry = {
        "ts": ts,
        "first_80": prompt_stripped[:80],
        "len": len(prompt_stripped),
        "sentences": sentences,
        "skip_reason": skip_reason,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        fd = os.open(skip_log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.write(fd, line.encode("utf-8"))
        os.close(fd)
    except OSError:
        return  # skip-log write is best-effort

    # Rotate after write (rotate-after avoids pre-flight stat on every fire)
    try:
        _maybe_rotate_skip_log(skip_log_path)
    except (OSError, IOError):
        pass  # rotation failure is non-fatal


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    exit_if_dispatched_child("user-intent-capture")
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Malformed or empty stdin — nothing to do.
        return

    # Canonical strip step: executed FIRST; reused for both heuristic AND hash
    raw_prompt = payload.get("prompt", "")
    if not raw_prompt:
        return
    prompt_stripped = raw_prompt.strip()
    if not prompt_stripped:
        return

    project_root = _resolve_project_root()
    session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")

    campaign_id = _discover_campaign_id(project_root)
    art_root = _artifact_root(project_root, campaign_id, session_id)

    artifact_path = os.path.join(art_root, "user-intent.md")
    skip_log_path = os.path.join(art_root, "user-intent-skipped.jsonl")
    hashes_path = os.path.join(art_root, ".recent-hashes")

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Heuristic evaluation ---
    substantive, skip_reason = _is_substantive(prompt_stripped)
    if not substantive:
        sentences = _count_sentences(prompt_stripped)
        try:
            _write_skip_log(skip_log_path, ts, prompt_stripped, sentences, skip_reason)
        except Exception:
            pass  # skip-log is best-effort
        return

    # --- Dedup check ---
    h = hashlib.sha256(prompt_stripped.encode("utf-8")).hexdigest()
    existing_hashes = _read_recent_hashes(hashes_path)
    if h in existing_hashes:
        # Dedup hit: silent exit, no skip-log entry (per upstream §5.4)
        return

    # --- Append hash ---
    try:
        _append_and_cap_hashes(hashes_path, h, existing_hashes)
    except Exception:
        pass  # hash write failure is non-fatal

    # --- Write artifact ---
    quote_block = _quote_block(prompt_stripped, ts)
    try:
        _write_artifact(artifact_path, quote_block, campaign_id, session_id)
    except Exception:
        pass  # artifact write failure is non-fatal


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # outer safety net: any unhandled error exits 0
    sys.exit(0)
