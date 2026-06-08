#!/usr/bin/env python3
# dispatch-child-safe: false
"""SessionStart hook: MCP availability gate. Sentinel: MCP-AVAILABILITY-GATE

Purpose: runs the MCP probe at session start via `_mcp_availability_lib`;
fails closed (exit 2) when MCP is unavailable or the library cannot be
imported. Silently exits 0 on ok/bypass/no-session/parse-fail conditions.

Bypass mechanism:
  env var:  CAA_MCP_GATE_BYPASS
  trigger:  == "1" (strict equality — NOT truthy; "true"/"yes"/"on"/"0" do
            NOT bypass)
  precedes: the probe (bypass takes priority over the probe result)
  mirrors:  the launcher's --no-mcp-gate flag — same semantic: skip the gate
            when the operator has confirmed MCP is intentionally unavailable
            or when a false-positive must be recovered without restarting the
            launcher (IDE / claude-resume / direct-claude paths that bypass
            the launcher entirely)
  audit:    when bypassed, a mandatory stderr line is emitted so any bypass
            is recorded in the operator's terminal log and the harness's hook
            log; the verbatim line is:
              [mcp-availability-gate] BYPASS: CAA_MCP_GATE_BYPASS=1 set; skipping probe
  no-sidecar: bypass does NOT write a sidecar; it exits 0 silently after the
            audit stderr line.

Exit-code semantics:
  0 — ok, OR bypass, OR no CLAUDE_SESSION_ID, OR malformed stdin
  2 — probe failure, OR _mcp_availability_lib import failure
      Justified narrow exemption per hooks-soft-warning-requirement.md:
        1. MCP unavailable = structural breakage of the agent's tool environment
        2. Downstream failure is guaranteed (silent built-in fallback, context blowout)
        3. No in-hook recovery is possible (the hook cannot install npm deps)

Reference: .claude/knowledge/constraints/platform/mcp-availability-gate.md
  (Subtask 7 will author this file; docstring forward-references it for
  operators encountering this gate for the first time.)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from _dispatch_child_guard import exit_if_dispatched_child


def main() -> None:
    exit_if_dispatched_child("mcp-availability-gate")
    # Step 1. stdin parse — malformed/empty/EOF → exit 0 silently
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    # Step 2. CLAUDE_SESSION_ID presence — absent → exit 0 silently
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return

    # Step 3. BYPASS branch — PRECEDES probe (strict equality "1", not truthy)
    if os.environ.get("CAA_MCP_GATE_BYPASS") == "1":
        sys.stderr.write(
            "[mcp-availability-gate] BYPASS: CAA_MCP_GATE_BYPASS=1 set; skipping probe\n"
        )
        return

    # Step 4. project_root resolution — env override for testability, else
    # three dirname calls from __file__ (mirrors mcp-retry-warn.py pattern)
    override = os.environ.get("MCP_AVAILABILITY_GATE_PROJECT_ROOT")
    if override:
        project_root = override
    else:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    # Step 5. session_dir derivation
    session_dir = os.path.join(project_root, ".agent_context", "sessions", session_id)

    # Step 6. Library import (defensive — half-deployed installs may be missing the lib)
    try:
        from _mcp_availability_lib import (  # type: ignore[import]
            REQUIRED_TOOLS,
            format_diagnostic,
            probe_mcp_server,
            write_sidecar,
        )
        import pathlib as _pathlib
    except ImportError as e:
        # Step 6a. Degraded-import branch — lib missing or broken
        _emit_degraded_import_failure(session_dir, repr(e))
        sys.exit(2)

    # Step 7. Probe execution (lib imported successfully)
    result = probe_mcp_server(_pathlib.Path(project_root))

    # Step 7a. Success branch
    if result.ok:
        return

    # Step 7b. Failure branch — sidecar write
    sidecar_path = _pathlib.Path(session_dir) / ".mcp-availability-failed.json"
    write_sidecar(result, sidecar_path)

    # Step 7c. Failure branch — additionalContext + stderr emit
    diagnostic = format_diagnostic(result)
    sys.stderr.write(diagnostic + "\n")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": diagnostic,
        }
    }))

    # Step 7d. Failure branch — exit 2 (narrow-exemption hard-block)
    sys.exit(2)


def _emit_degraded_import_failure(session_dir: str, import_err_repr: str) -> None:
    """Write a degraded sidecar and emit stderr + additionalContext for import failure.

    Called when _mcp_availability_lib itself cannot be imported (half-deployed
    install). The gate fails closed: we cannot verify availability, so we treat
    it as unavailable.
    """
    import pathlib

    timestamp_iso = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    sidecar_data = {
        "exit_code": None,
        "expected_tools": [],
        "failure_reason": f"_mcp_availability_lib import failed: {import_err_repr}",
        "missing_tools": [],
        "observed_tools": [],
        "probe_command": "(library missing — could not run probe)",
        "stderr_excerpt": "",
        "timestamp_iso": timestamp_iso,
    }
    sidecar_path = pathlib.Path(session_dir) / ".mcp-availability-failed.json"
    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            json.dumps(sidecar_data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # Session dir may not exist yet in extreme degraded scenarios;
        # still exit 2 — the gate must fail closed even without a sidecar.
        pass

    diagnostic = (
        f"--- MCP-AVAILABILITY-GATE ---\n"
        f"MCP availability gate FAILED: _mcp_availability_lib could not be imported.\n"
        f"Import error: {import_err_repr}\n"
        f"This indicates a half-deployed install. Run:\n"
        f"  ls .claude/hooks/_mcp_availability_lib.py\n"
        f"to confirm the library file is present.\n"
        f"--- END MCP-AVAILABILITY-GATE ---"
    )
    sys.stderr.write(diagnostic + "\n")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": diagnostic,
        }
    }))


if __name__ == "__main__":
    main()
    sys.exit(0)
