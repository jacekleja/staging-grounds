#!/usr/bin/env python3
# OPT-IN: set HOOK_DEBUG=1 before launching Claude Code; without this env var hook is a complete no-op
"""
Debug hook: dumps raw hook event payloads to .agent_context/hook-debug.jsonl.

ACTIVATION:
  Set HOOK_DEBUG=1 in the environment before launching Claude Code:
    HOOK_DEBUG=1 claude ...
  Without this variable the hook is a complete no-op.

OUTPUT:
  File:   <project-root>/.agent_context/hook-debug.jsonl  (append-only JSONL)
  Format: one JSON object per line:
    {
      "ts":             "2026-03-31T11:22:33.456789",   // ISO-8601 UTC
      "hook_type":      "PreToolUse",                   // detected event type
      "env_session_id": "...",                           // CLAUDE_SESSION_ID env var
      "payload":        { ...raw event dict... }
    }

SIZE CAP:
  5 MB per file, 3 rotations max (~20 MB total).
  Rotation uses the stdlib RotatingFileHandler.

REGISTRATION (do NOT modify settings.json -- opt-in only):
  To register, add to .claude/settings.json under each hook type you want:
  {
    "hooks": {
      "PreToolUse": [
        {"matcher": "", "hooks": [{"type": "command", "command": "python3 \"$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/.claude/hooks/debug-hook.py\""}]}
      ],
      "PostToolUse": [
        {"matcher": "", "hooks": [{"type": "command", "command": "python3 \"$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/.claude/hooks/debug-hook.py\""}]}
      ],
      "SubagentStop": [
        {"matcher": "", "hooks": [{"type": "command", "command": "python3 \"$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/.claude/hooks/debug-hook.py\""}]}
      ]
    }
  }
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


def get_project_root() -> str:
    """Resolve the CAA project root via __file__-based walk (3 parents up from .claude/hooks/script.py).

    Uses __file__-based walk-up since debug-hook.py may be invoked via a bare path
    without a shell-substituted project root. Other hooks rely on cwd (settings.json
    interpolates the project root via shell expansion before invocation). Does NOT use
    git rev-parse --show-toplevel, which would break in nested git repos.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def detect_hook_type(event: dict) -> str:
    """
    Determine the hook event type.

    Prefer the explicit 'hook_event_name' field if present,
    then fall back to structural detection.
    """
    explicit = event.get('hook_event_name')
    if explicit:
        return str(explicit)

    # Structural fallback: PostToolUse has tool_response; PreToolUse has tool_input
    if 'tool_response' in event:
        return 'PostToolUse'
    if 'tool_input' in event:
        return 'PreToolUse'
    if 'subagent_result' in event or 'stop_reason' in event:
        return 'SubagentStop'

    return 'Unknown'


def build_logger(log_path: str) -> logging.Logger:
    """Build a rotating-file logger writing raw lines (no timestamps from logging)."""
    logger = logging.getLogger('hook_debug')
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,   # 5 MB per file
            backupCount=3,               # 3 rotations -> ~20 MB total
            encoding='utf-8',
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    return logger


def main() -> None:
    # Activation guard -- complete no-op if HOOK_DEBUG is not set to "1"
    if os.environ.get('HOOK_DEBUG') != '1':
        return

    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except Exception:
        return

    try:
        project_root = get_project_root()
        output_dir = os.path.join(project_root, '.agent_context')
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, 'hook-debug.jsonl')

        record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'hook_type': detect_hook_type(event),
            'env_session_id': os.environ.get('CLAUDE_SESSION_ID', ''),
            'env_orchestrator_depth': os.environ.get('CLAUDE_HOOK_ORCHESTRATOR_DEPTH', ''),
            'payload': event,
        }

        logger = build_logger(log_path)
        logger.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        # Hook must never crash Claude Code -- swallow all exceptions silently
        pass

    # No stdout output -> no-op from Claude Code's perspective


if __name__ == '__main__':
    main()
