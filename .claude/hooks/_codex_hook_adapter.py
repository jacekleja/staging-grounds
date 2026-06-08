"""Shared adapter library: codex ↔ Claude hook payload translation.

Exports five entry points (one per codex hook event) plus emit_codex_output.

Usage pattern in each shim:
    payload = adapt_<event>(raw_codex_payload, helper_path)  # → (claude_payload, env, cwd)
    stdout, returncode = run_helper(helper_path, payload, env, cwd)
    emit_codex_output(event_name, stdout, returncode)

Design: codex payloads share ~80% of field names with Claude (session_id,
transcript_path, cwd, hook_event_name, tool_name, tool_input, tool_use_id,
prompt). This module captures the ~20% per-event divergence in one place so a
helper-input-schema change is a one-place edit.

Class C hard-gaps documented in decisions/codex-hook-shim-design.md:
  1. PreToolUse/Agent — no codex equivalent of Claude Task-tool subagent dispatch.
     delegation-prompt-schema-gate short-circuits at tool_name!=\"Agent\"; shim
     fires on every tool but helper no-ops unless tool_name==\"Agent\".
  2. UserPromptSubmit — no updatedInput. Codex output_parser.rs explicitly rejects
     it (codex GitHub #18491). user-intent-capture is observe-only today so no
     functional gap; future rewrite-helpers cannot port to codex.
  3. Stop — no stop_hook_active re-fire protection. Codex block→continuation-prompt
     (new turn) instead of block→model-continues-current-turn (Claude). Shim
     hardcodes stop_hook_active=False, so the helper's stop_hook_active=True
     re-fire-exhaustion branch never fires; other safety branches still apply.
  4. No mcp__alpha-pipeline__dispatch / no Agent matcher on PreToolUse for schema gate.
  5. CLAUDE_HOOK_ORCHESTRATOR_DEPTH and CLAUDE_SESSION_DEPTH are not codex-provided;
     shim hardcodes depth=1 and inherits CLAUDE_SESSION_DEPTH when CAA_CHILD_SIDECAR_DIR
     is set (falls back to 0) to satisfy cycle-hook.py:1543-1546 guard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


# CAA_SHIM_VERSION — bumped when shim API surface changes; launcher probes this.
CAA_SHIM_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _base_env(payload: dict[str, Any]) -> dict[str, str]:
    """Build subprocess env: inherit operator env, overlay synthesized CAA vars."""
    env = os.environ.copy()
    session_id = payload.get("session_id", "")
    env["CLAUDE_SESSION_ID"] = session_id
    # Propagate codex session UUID for cycle-hook.py Tier -1 transcript discovery.
    # setdefault preserves any CODEX_SESSION_ID already injected by the codex runtime.
    # [verified: coupling/codex-session-id-cycle-pending-creation-gap.md]
    env.setdefault("CODEX_SESSION_ID", session_id)
    # CLAUDE_HOOK_ORCHESTRATOR_DEPTH satisfies cycle-hook.py:1543-1546 depth guard.
    env["CLAUDE_HOOK_ORCHESTRATOR_DEPTH"] = "1"
    # When running as a CAA child sidecar, the caller already set CLAUDE_SESSION_DEPTH;
    # inherit it so the depth guard sees the real nesting level.  Without a sidecar dir
    # codex has no nested-Agent context (Class C gap #5) so "0" is correct.
    env["CLAUDE_SESSION_DEPTH"] = (
        os.environ.get("CLAUDE_SESSION_DEPTH", "0")
        if os.environ.get("CAA_CHILD_SIDECAR_DIR")
        else "0"
    )
    # Propagate project-dir env for helpers that resolve session_dir from it.
    cwd = payload.get("cwd", "")
    if cwd:
        env.setdefault("CLAUDE_PROJECT_DIR", cwd)
        env.setdefault("MCP_AVAILABILITY_GATE_PROJECT_ROOT", cwd)
    return env


def _run_helper(
    helper_path: str,
    payload: dict[str, Any],
    env: dict[str, str],
    cwd: str,
) -> tuple[str, int]:
    """Invoke a Claude helper as a subprocess, return (stdout_text, returncode)."""
    try:
        result = subprocess.run(
            [sys.executable, helper_path],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
        )
        return result.stdout, result.returncode
    except Exception as exc:
        # Fail-open: log to stderr, return empty output with code 0 so the
        # shim's outer fail-safe can continue and exit 0.
        print(
            f"_codex_hook_adapter: helper invocation error ({helper_path}): {exc}",
            file=sys.stderr,
        )
        return "", 0


# ---------------------------------------------------------------------------
# Public: emit_codex_output
# ---------------------------------------------------------------------------

def emit_codex_output(
    event_name: str,
    helper_stdout: str,
    helper_returncode: int,
    *,
    pre_tool: bool = False,
) -> int:
    """Translate Claude helper output to codex-shaped output on sys.stdout.

    Returns the exit code the shim should pass to sys.exit().

    Translation rules (per sketch § Per-event sub-designs):
    - PostToolUse / SessionStart / UserPromptSubmit / Stop: pass-through JSON
      verbatim; codex reads hookSpecificOutput.additionalContext directly.
    - PreToolUse (pre_tool=True): translate Claude \"block\" decision to codex
      permissionDecision shape ({hookSpecificOutput:{permissionDecision:\"deny\",...}}).
    - UserPromptSubmit: helper is silent-tracker; no stdout expected.
    """
    stdout_text = helper_stdout.strip() if helper_stdout else ""

    if not stdout_text:
        # Silent output — exit 0 (or propagate non-zero returncode for block).
        if helper_returncode == 2:
            sys.exit(2)
        return 0

    try:
        data = json.loads(stdout_text)
    except json.JSONDecodeError:
        # Non-JSON helper output: wrap in additionalContext so codex surfaces it.
        output = {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": stdout_text,
            }
        }
        print(json.dumps(output))
        if helper_returncode == 2:
            sys.exit(2)
        return 0

    if pre_tool:
        # PreToolUse output: translate Claude "block" + reason →
        # codex permissionDecision shape (per cross-family-codex-hook-taxonomy.md
        # § Hook output schemas and sketch § PreToolUse sub-design (d)).
        if "decision" in data and data["decision"] == "block":
            reason = data.get("reason", "")
            codex_output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
            print(json.dumps(codex_output))
            if helper_returncode == 2:
                sys.exit(2)
            return 0

    # All other events (including PreToolUse advisory/warn path): pass through verbatim.
    print(json.dumps(data))
    if helper_returncode == 2:
        sys.exit(2)
    return 0


# ---------------------------------------------------------------------------
# Public: per-event adapt_* entry points
# ---------------------------------------------------------------------------

def adapt_post_tool_use(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """PostToolUse → cycle-hook.py.

    Returns (claude_payload, env, cwd).
    TPM-cycling thread: shim chdir's to payload["cwd"] so cycle-hook.py's
    get_threshold(cwd) resolves against the project root, not ~/.codex/hooks/.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "tool_name": raw.get("tool_name", ""),
        "tool_input": raw.get("tool_input", {}),
        "tool_use_id": raw.get("tool_use_id", ""),
        # outputPreview maps to tool_response for helpers that inspect it.
        "tool_response": raw.get("outputPreview", ""),
    }
    return payload, env, cwd


def adapt_pre_tool_use(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """PreToolUse → delegation-prompt-schema-gate.py.

    Class C hard-gap #1: codex has no Agent tool. The helper short-circuits at
    tool_name != "Agent" check (delegation-prompt-schema-gate.py:277-278).
    Shim registered for completeness and future MCP-dispatch matchers.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    env["CLAUDE_PROJECT_DIR"] = cwd
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "tool_name": raw.get("tool_name", ""),
        "tool_input": raw.get("tool_input", {}),
    }
    return payload, env, cwd


def adapt_user_prompt_submit(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """UserPromptSubmit → user-intent-capture.py (observe-only).

    Class C hard-gap #2: codex output_parser.rs explicitly rejects updatedInput
    (codex GitHub #18491). user-intent-capture.py is observe-only today so this
    is not a functional gap; however any future prompt-rewrite helper CANNOT port
    to codex via UserPromptSubmit — codex simply does not support it.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    env["CLAUDE_PROJECT_DIR"] = cwd
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "prompt": raw.get("prompt", ""),
    }
    return payload, env, cwd


def adapt_session_start(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """SessionStart → mcp-availability-gate.py.

    MCP_AVAILABILITY_GATE_PROJECT_ROOT must be set explicitly — the helper's
    __file__-walk from ~/.codex/hooks/ would resolve the wrong project root
    when subprocess-invoked from the deployed location.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    env["MCP_AVAILABILITY_GATE_PROJECT_ROOT"] = cwd
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "source": raw.get("source", "startup"),
        "model": raw.get("model", ""),
    }
    return payload, env, cwd


def adapt_stop(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """Stop → turn-continuity-block.py.

    Class C hard-gap #3: codex does NOT re-fire Stop after a block-decision —
    instead block creates a continuation prompt (new turn). Claude's
    stop_hook_active=True re-fire-protection branch never fires on codex.
    The shim passes stop_hook_active=False always; helper's other safety
    branches (sentinel-absent, background-agent-active) still apply.
    If a sentinel bug causes infinite block-via-continuation, only Ctrl-C breaks it.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    payload = {
        "hook_event_name": "Stop",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        # stop_hook_active is a Claude-only re-fire-protection field.
        # Codex never sets it; default False passes the helper's .get() guard.
        "stop_hook_active": False,
    }
    return payload, env, cwd


def run_helper(
    helper_path: str,
    payload: dict[str, Any],
    env: dict[str, str],
    cwd: str,
) -> tuple[str, int]:
    """Public wrapper around _run_helper for use by shim scripts."""
    return _run_helper(helper_path, payload, env, cwd)
