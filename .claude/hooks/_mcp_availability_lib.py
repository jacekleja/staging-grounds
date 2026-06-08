#!/usr/bin/env python3
"""MCP availability probe library.

Self-contained, stdlib-only library that probes the `context-tools` MCP server
to verify it is healthy AND exposes the full required tool roster. Consumed by:

  - .claude/hooks/mcp-availability-gate.py (Subtask 3) — SessionStart hook.
  - bin/claude-session (Subtask 2)         — pre-launch smoke fragment.
  - Standalone CLI (`__main__`)            — `python3 _mcp_availability_lib.py`.

Public surface:
  REQUIRED_TOOLS                : tuple[str, ...]   — locked-roster constant.
  ProbeResult                   : dataclass         — structured probe output.
  probe_mcp_server(...)         : ProbeResult       — runs the probe.
  format_diagnostic(result)     : str               — operator-facing text.
  write_sidecar(result, path)   : None              — JSON sidecar write.

Design constraints (locked by Subtask 1 plan):
  - stdlib-only (no third-party Python deps): hooks must not require an extra
    install step beyond a stock Python 3 interpreter.
  - Newline-delimited JSON-RPC (no Content-Length headers): MCP SDK's
    StdioServerTransport uses newline-delimited framing per spec.
  - Two-frame handshake: `initialize` (with protocolVersion) MUST precede
    `tools/list` because per-spec the server rejects tools/list when the
    session is uninitialized.
  - 5-second timeout: tsx cold-start is ~2s observed; double for safety
    (CI/laptop variance) but not so long the operator waits forever on a
    truly broken install.
  - Project-root resolution mirrors mcp-retry-warn.py: three os.path.dirname
    calls from __file__, with override via MCP_AVAILABILITY_GATE_PROJECT_ROOT
    env var for testability.

Sentinel: MCP-AVAILABILITY-GATE (referenced by format_diagnostic and Subtasks 3, 6).
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Locked roster — DO NOT modify in this subtask. Bare tool names (no
# `mcp__context-tools__` prefix) because that prefix is the harness's
# advertise-name namespace and is not present in the server's tools/list
# response.
REQUIRED_TOOLS: tuple[str, ...] = (
    "smart_bash",
    "smart_read",
    "smart_grep",
    "smart_glob",
    "smart_write",
    "knowledge",
    "findings",
)

# JSON-RPC protocol version. MCP SDK 1.12.0 ships LATEST_PROTOCOL_VERSION =
# "2025-03-26" (verified from
# .claude/mcp/context-tools/node_modules/@modelcontextprotocol/sdk/dist/cjs/types.d.ts).
# Server negotiates: it returns the requested version if supported, else its
# own latest. Sending the latest is the safest default.
PROTOCOL_VERSION = "2025-03-26"

# Sentinel grep'd by Subtask 3's hook tests and Subtask 6's lint.
DIAGNOSTIC_SENTINEL = "MCP-AVAILABILITY-GATE"

# .mcp.json server invocation (verified from .mcp.json L4-L8).
PROBE_COMMAND_DESCRIPTION = (
    'bash -c "cd .claude/mcp/context-tools && exec node_modules/.bin/tsx src/index.ts"'
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """Structured outcome of a single probe invocation.

    Frozen so callers cannot mutate fields after the fact (this matters
    for the sidecar serializer and the diagnostic formatter — they treat
    the result as a read-only view).
    """

    ok: bool
    failure_reason: Optional[str]
    expected_tools: tuple[str, ...]
    observed_tools: tuple[str, ...]
    missing_tools: tuple[str, ...]
    probe_command: str
    stderr_excerpt: str
    exit_code: Optional[int]
    duration_ms: int

    def to_dict(self) -> dict:
        """Plain dict for JSON serialization."""
        return {
            "ok": self.ok,
            "failure_reason": self.failure_reason,
            "expected_tools": list(self.expected_tools),
            "observed_tools": list(self.observed_tools),
            "missing_tools": list(self.missing_tools),
            "probe_command": self.probe_command,
            "stderr_excerpt": self.stderr_excerpt,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# Project-root resolution
# ---------------------------------------------------------------------------


def _resolve_project_root() -> pathlib.Path:
    """Resolve the project root using the mcp-retry-warn.py pattern.

    Three `os.path.dirname` calls from __file__ walks: hooks/ -> .claude/ ->
    project root. The override env var MCP_AVAILABILITY_GATE_PROJECT_ROOT
    exists for testability (so tests can stand up a temp dir with a
    deliberately-broken .mcp.json without modifying the live one).
    """
    override = os.environ.get("MCP_AVAILABILITY_GATE_PROJECT_ROOT")
    if override:
        return pathlib.Path(override).resolve()
    return pathlib.Path(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        )
    ).resolve()


# ---------------------------------------------------------------------------
# JSON-RPC frame builders
# ---------------------------------------------------------------------------


def _build_initialize_frame() -> bytes:
    """Build the JSON-RPC `initialize` request as a newline-terminated frame.

    Byte-equality contract — Subtask 3's hook MUST replicate these bytes
    exactly. Schema:

      {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2025-03-26",
          "capabilities": {},
          "clientInfo": {"name": "mcp-availability-probe", "version": "1.0.0"}
        }
      }\n

    Notes:
      - id=1 (request id; reused by tools/list as id=2).
      - capabilities is an empty object — the probe needs no client
        capabilities (no roots, no sampling, no resource subscriptions).
      - clientInfo identifies this probe in server logs for debuggability.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": "mcp-availability-probe",
                "version": "1.0.0",
            },
        },
    }
    # json.dumps with default settings produces compact-enough single-line
    # output; we explicitly avoid extra whitespace via separators=(",", ":")
    # to keep the byte-equality contract minimal-surface.
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _build_initialized_notification_frame() -> bytes:
    """Build the `notifications/initialized` notification frame.

    Per MCP spec, after the server returns the initialize result, the client
    MUST send a `notifications/initialized` notification BEFORE issuing
    further requests. Some servers tolerate skipping this; the SDK server
    in use here is permissive, but we send it anyway for protocol
    correctness and Subtask-3 byte-equality reproducibility.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _build_tools_list_frame() -> bytes:
    """Build the `tools/list` request frame.

    id=2 (initialize is id=1). Empty params (no cursor; we want the full
    list in a single response).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def _extract_tool_names(stdout_bytes: bytes) -> tuple[Optional[tuple[str, ...]], Optional[str]]:
    """Parse newline-delimited JSON-RPC frames; return (tool_names, error).

    Returns (tuple_of_names, None) on success, or (None, reason) on
    parse/protocol failure. The parser is permissive of unknown frames
    (e.g. log notifications) — it simply walks every line, JSON-decodes,
    and looks for a response whose `id == 2` (the tools/list request id)
    with a `result.tools` array.
    """
    tools_response = None
    initialize_response = None
    parse_errors = 0

    for raw_line in stdout_bytes.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            parse_errors += 1
            continue
        if not isinstance(frame, dict):
            continue
        # Match by id rather than method-name — JSON-RPC responses don't
        # carry the original method.
        frame_id = frame.get("id")
        if frame_id == 1 and "result" in frame:
            initialize_response = frame
        elif frame_id == 2 and "result" in frame:
            tools_response = frame
        elif frame_id == 2 and "error" in frame:
            err = frame.get("error", {})
            return None, f"tools/list returned error: {err.get('message', repr(err))}"

    if initialize_response is None:
        if parse_errors > 0 and tools_response is None:
            return None, f"no parseable JSON-RPC responses (parse_errors={parse_errors})"
        return None, "no initialize response received"
    if tools_response is None:
        return None, "no tools/list response received"

    result = tools_response.get("result", {})
    tools = result.get("tools")
    if not isinstance(tools, list):
        return None, "tools/list result missing tools[] array"
    names: list[str] = []
    for entry in tools:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                names.append(name)
    return tuple(names), None


# ---------------------------------------------------------------------------
# Main probe entry point
# ---------------------------------------------------------------------------


def probe_mcp_server(
    project_root: pathlib.Path,
    timeout_seconds: float = 5.0,
) -> ProbeResult:
    """Spawn the MCP server and probe it for tool availability.

    Sends `initialize` -> `notifications/initialized` -> `tools/list`,
    closes stdin to signal end-of-input, then waits (up to timeout_seconds)
    for the process to drain stdout/stderr and exit. On TimeoutExpired the
    process is killed and a timeout failure is reported.

    Returns a ProbeResult with ok=True iff every name in REQUIRED_TOOLS is
    present in the server's tools/list response.
    """
    # Use the SAME bash invocation shape as .mcp.json — the cwd is the
    # project root so the relative `cd .claude/mcp/context-tools` path
    # resolves correctly.
    cmd = [
        "bash",
        "-c",
        'cd ".claude/mcp/context-tools" && exec node_modules/.bin/tsx src/index.ts',
    ]
    probe_command = PROBE_COMMAND_DESCRIPTION

    start = time.monotonic()
    stdout_bytes = b""
    stderr_bytes = b""
    exit_code: Optional[int] = None
    failure_reason: Optional[str] = None
    observed_tools: tuple[str, ...] = ()

    proc: Optional[subprocess.Popen] = None
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            failure_reason = f"failed to spawn probe subprocess: {type(e).__name__}: {e}"
            duration_ms = int((time.monotonic() - start) * 1000)
            return ProbeResult(
                ok=False,
                failure_reason=failure_reason,
                expected_tools=REQUIRED_TOOLS,
                observed_tools=(),
                missing_tools=REQUIRED_TOOLS,
                probe_command=probe_command,
                stderr_excerpt="",
                exit_code=None,
                duration_ms=duration_ms,
            )

        # Construct the request payload: initialize + initialized
        # notification + tools/list, then close stdin so the server's
        # stdio transport sees EOF and exits cleanly after responding.
        payload = (
            _build_initialize_frame()
            + _build_initialized_notification_frame()
            + _build_tools_list_frame()
        )

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                input=payload, timeout=timeout_seconds
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Kill and drain — we still want partial stdout/stderr for the
            # diagnostic excerpt.
            proc.kill()
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                stdout_bytes, stderr_bytes = b"", b""
            exit_code = proc.returncode
            failure_reason = f"probe timeout after {timeout_seconds}s"
    except Exception as e:  # pragma: no cover — defensive catch-all
        failure_reason = f"unexpected probe error: {type(e).__name__}: {e}"
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass

    duration_ms = int((time.monotonic() - start) * 1000)
    stderr_excerpt = stderr_bytes[:500].decode("utf-8", errors="replace")

    if failure_reason is None:
        # Parse the tool list out of stdout.
        names, parse_err = _extract_tool_names(stdout_bytes)
        if parse_err is not None:
            failure_reason = parse_err
        else:
            assert names is not None  # for type-checkers
            observed_tools = names

    if failure_reason is None:
        observed_set = set(observed_tools)
        missing = tuple(t for t in REQUIRED_TOOLS if t not in observed_set)
        if missing:
            failure_reason = (
                f"missing required tools: {', '.join(missing)}"
            )
            return ProbeResult(
                ok=False,
                failure_reason=failure_reason,
                expected_tools=REQUIRED_TOOLS,
                observed_tools=observed_tools,
                missing_tools=missing,
                probe_command=probe_command,
                stderr_excerpt=stderr_excerpt,
                exit_code=exit_code,
                duration_ms=duration_ms,
            )
        return ProbeResult(
            ok=True,
            failure_reason=None,
            expected_tools=REQUIRED_TOOLS,
            observed_tools=observed_tools,
            missing_tools=(),
            probe_command=probe_command,
            stderr_excerpt=stderr_excerpt,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )

    # Failure path with no observed_tools (transport / parse error).
    return ProbeResult(
        ok=False,
        failure_reason=failure_reason,
        expected_tools=REQUIRED_TOOLS,
        observed_tools=observed_tools,
        missing_tools=tuple(
            t for t in REQUIRED_TOOLS if t not in set(observed_tools)
        ),
        probe_command=probe_command,
        stderr_excerpt=stderr_excerpt,
        exit_code=exit_code,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Diagnostic formatter
# ---------------------------------------------------------------------------


def format_diagnostic(result: ProbeResult) -> str:
    """Human-readable diagnostic. Includes the load-bearing sentinel."""
    if result.ok:
        return (
            f"--- {DIAGNOSTIC_SENTINEL} ---\n"
            f"MCP probe OK. {len(result.observed_tools)} tools advertised, "
            f"all {len(result.expected_tools)} required tools present "
            f"(duration={result.duration_ms}ms).\n"
            f"--- END {DIAGNOSTIC_SENTINEL} ---"
        )

    lines = [
        f"--- {DIAGNOSTIC_SENTINEL} ---",
        f"MCP probe FAILED: {result.failure_reason}",
        f"Probe command: {result.probe_command}",
        f"Exit code: {result.exit_code}",
        f"Duration: {result.duration_ms}ms",
        f"Expected tools ({len(result.expected_tools)}): "
        f"{', '.join(result.expected_tools)}",
        f"Observed tools ({len(result.observed_tools)}): "
        f"{', '.join(result.observed_tools) if result.observed_tools else '(none)'}",
        f"Missing tools ({len(result.missing_tools)}): "
        f"{', '.join(result.missing_tools) if result.missing_tools else '(none)'}",
    ]
    if result.stderr_excerpt:
        lines.append("Stderr (first 500 bytes):")
        lines.append(result.stderr_excerpt.rstrip())
    lines.append(f"--- END {DIAGNOSTIC_SENTINEL} ---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sidecar writer
# ---------------------------------------------------------------------------


def write_sidecar(result: ProbeResult, sidecar_path: pathlib.Path) -> None:
    """Write a structured JSON sidecar capturing the probe outcome.

    Schema (locked — Subtasks 3 and 6 read these exact field names):
      failure_reason, probe_command, expected_tools, observed_tools,
      missing_tools, stderr_excerpt, exit_code, timestamp_iso.
    """
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "failure_reason": result.failure_reason,
        "probe_command": result.probe_command,
        "expected_tools": list(result.expected_tools),
        "observed_tools": list(result.observed_tools),
        "missing_tools": list(result.missing_tools),
        "stderr_excerpt": result.stderr_excerpt,
        "exit_code": result.exit_code,
        "timestamp_iso": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    sidecar_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main() -> int:
    """CLI entry: run probe, print JSON ProbeResult, exit 0 if ok else 1."""
    project_root = _resolve_project_root()
    result = probe_mcp_server(project_root)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(_main())
