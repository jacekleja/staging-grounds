"""Shared adapter library: gemini ↔ Claude hook payload translation.

Exports five entry points (one per gemini hook event) plus emit_gemini_output.

Usage pattern in each shim:
    payload = adapt_<event>(raw_gemini_payload)   # → Claude-shaped dict + env
    stdout, returncode = run_helper(helper_path, payload, env)
    emit_gemini_output(event_name, stdout, returncode)

Design: gemini payloads share ~90% of field names with Claude (via EVENT_MAPPING
migration layer). This module captures the ~10% divergence in one place so a
helper-input-schema change is a one-place edit.

Class C hard-gaps documented in decisions/gemini-hook-shim-design.md:
  1. BeforeAgent lacks updatedInput — append-only via additionalContext only.
     Future prompt-rewrite helpers must register a separate BeforeModel shim per
     Q-18 workaround.
  2. No Agent tool on gemini — delegation schema gate is structurally no-op today.
     Orchestrator-side prompt-construction discipline must backstop the gate.
  3. No tool_name=\"Agent\" matcher on PreToolUse — even MCP-dispatched L2 children
     won't fire gemini BeforeTool with tool_name=\"Agent\".
  4. CLAUDE_HOOK_ORCHESTRATOR_DEPTH / CLAUDE_SESSION_DEPTH synthesized (no gemini equiv);
     hardcoded depth=1/session_depth=0 to satisfy cycle-hook.py:1664-1667 guard.
  5. Higher TPM-exhaustion rate under gemini-as-orchestrator-host. Not a shim gap —
     the shim correctly threads the threshold; documented as operator constraint per
     constraints/gemini-orchestrator-host-tpm-exhaustion.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


# CAA_SHIM_VERSION — bumped when shim API surface changes; launcher probes this.
CAA_SHIM_VERSION = "1.0.1"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _base_env(payload: dict[str, Any]) -> dict[str, str]:
    """Build subprocess env: inherit operator env, overlay synthesized CAA vars."""
    env = os.environ.copy()
    session_id = payload.get("session_id", "")
    env["CLAUDE_SESSION_ID"] = session_id
    # Depth vars satisfy cycle-hook.py:1543-1546 depth guard.
    # On gemini there is no nested-Agent context (Class C gap #4), so
    # hardcoding depth=1/session_depth=0 correctly passes the guard.
    env["CLAUDE_HOOK_ORCHESTRATOR_DEPTH"] = "1"
    env["CLAUDE_SESSION_DEPTH"] = "0"
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
            f"_gemini_hook_adapter: helper invocation error ({helper_path}): {exc}",
            file=sys.stderr,
        )
        return "", 0


# ---------------------------------------------------------------------------
# Public: emit_gemini_output
# ---------------------------------------------------------------------------

def emit_gemini_output(
    event_name: str,
    helper_stdout: str,
    helper_returncode: int,
    *,
    before_tool: bool = False,
    after_agent: bool = False,
) -> int:
    """Translate Claude helper output to gemini-shaped output on sys.stdout.

    Returns the exit code the shim should pass to sys.exit().

    Translation rules (per sketch § Per-event sub-designs):
    - PostToolUse / SessionStart: pass-through JSON verbatim; runtime applies
      <hook_context> tags — do NOT double-wrap.
    - PreToolUse (before_tool=True): translate "block" → "deny" decision key.
    - AfterAgent / Stop (after_agent=True): pass-through verbatim; Stop-block
      reason is NOT wrapped in <hook_context> (it's a continuation prompt, not
      additionalContext).
    - BeforeAgent (UserPromptSubmit): helper is silent-tracker; no stdout.
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
        # Non-JSON helper output: pass through as raw text wrapped in
        # additionalContext so gemini can surface it to the model.
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

    if before_tool:
        # BeforeTool output: translate Claude "block" → gemini "deny"
        # (both are accepted per Q-17 but "deny" is the documented gemini term).
        if "decision" in data and data["decision"] == "block":
            data["decision"] = "deny"
        print(json.dumps(data))
        if helper_returncode == 2:
            sys.exit(2)
        return 0

    # All other events: pass through verbatim.
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
    """AfterTool (=PostToolUse) → cycle-hook.py.

    Returns (claude_payload, env, cwd).
    TPM-cycling thread: shim chdir's to payload["cwd"] so cycle-hook.py's
    get_threshold(cwd) resolves against the project root, not ~/.gemini/hooks/.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "timestamp": raw.get("timestamp", ""),
        "tool_name": raw.get("tool_name", ""),
        "tool_input": raw.get("tool_input", {}),
        "tool_response": raw.get("tool_response", ""),
        "tool_use_id": raw.get("tool_use_id", ""),
    }
    return payload, env, cwd


def adapt_pre_tool_use(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """BeforeTool (=PreToolUse) → delegation-prompt-schema-gate.py.

    Class C hard-gap #2: no Agent tool on gemini — helper short-circuits at
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
        "timestamp": raw.get("timestamp", ""),
        "tool_name": raw.get("tool_name", ""),
        "tool_input": raw.get("tool_input", {}),
    }
    return payload, env, cwd


def adapt_user_prompt_submit(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """BeforeAgent (=UserPromptSubmit) → user-intent-capture.py.

    Class C hard-gap #1: BeforeAgent lacks updatedInput. If this shim ever
    needs to rewrite the prompt, register a separate BeforeModel shim per Q-18
    workaround — BeforeAgent is APPEND-ONLY via additionalContext.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    env["CLAUDE_PROJECT_DIR"] = cwd
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "timestamp": raw.get("timestamp", ""),
        "prompt": raw.get("prompt", ""),
    }
    return payload, env, cwd


def adapt_session_start(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """SessionStart → mcp-availability-gate.py.

    MCP_AVAILABILITY_GATE_PROJECT_ROOT must be set explicitly — the helper's
    __file__-walk from ~/.gemini/hooks/ would resolve the wrong project root
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
        "timestamp": raw.get("timestamp", ""),
        "source": raw.get("source", "startup"),
        "model": raw.get("model", ""),
    }
    return payload, env, cwd


def adapt_stop(
    raw: dict[str, Any],
    helper_path: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """AfterAgent (=Stop) → turn-continuity-block.py.

    Structural symmetry win vs codex: gemini AfterAgent DOES carry
    stop_hook_active (Q-16), so the one-shot-retry safety branch works.
    """
    cwd = raw.get("cwd", os.getcwd())
    env = _base_env(raw)
    payload = {
        "hook_event_name": "Stop",
        "session_id": raw.get("session_id", ""),
        "transcript_path": raw.get("transcript_path", ""),
        "cwd": cwd,
        "timestamp": raw.get("timestamp", ""),
        "prompt": raw.get("prompt", ""),
        "prompt_response": raw.get("prompt_response", ""),
        "stop_hook_active": raw.get("stop_hook_active", False),
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
