#!/usr/bin/env python3
"""V3 apparatus delegation-prompt schema gate — PreToolUse:Agent hard-block hook.

Mechanism: exit-2 + stderr (narrow-exemption class).
Platform: Claude Code >= v2.1.90 required for exit-2 to block PreToolUse/Agent.

Narrow-exemption justification (per .claude/knowledge/constraints/platform/
hooks-soft-warning-requirement.md § Narrow exemption):
  1. Structurally-broken delegation prompt: apparatus delegations missing required
     V3 schema fields cannot be correctly processed by driller/synthesizer/critic.
  2. Downstream failure guaranteed: synthesizer Stage 0/0.5 verification fails in
     misleading ways when delegation-time fields are absent.
  3. No in-hook recovery: the hook cannot synthesize a cycle number or MAO trigger
     it does not know.

Scope: fires ONLY on V3_APPARATUS_AGENTS. Silent early-return for all other agents.

Block conditions (for agents in V3_APPARATUS_AGENTS):
  - Any of the five universal-required fields missing (v3_apparatus added in V3.1)
  - assigned_exp_nnn missing when form_4_eligible: true (critic-driller only)
  - parent_drill_tool_use_id missing for all critic-driller dispatches (V31-3)

Fail-open conditions (degrade to advisory, exit 0):
  - Claude Code version below v2.1.90 (version probe writes sentinel and emits advisory)
  - Bypass token present (<!-- V3-SCHEMA-BYPASS: <reason> --> within first 20 lines,
    exactly once)

Bypass token is distinct from the V2 gate's token (SCHEMA-BYPASS) so V3 bypasses
never silently disable the V2 schema-gate.

See: unbraked-deepening/V3-arch.md § 2.7.1 for schema specification
     unbraked-deepening/V3-arch.md § 3.1 for apparatus build targets
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


# V3 apparatus agent types — scope predicate per DS-1 Q2.
# Only delegations targeting these agents trigger schema checks.
# [verified: v3.1-design-master.md § 1. Executive Summary]
V3_APPARATUS_AGENTS = {"critic-driller", "synthesizer"}

# Five universal-required fields present in every apparatus delegation.
# v3_apparatus added as fifth field in V3.1 (hard-block; elevates advisory → exit-2).
# Names chosen as snake_case identifiers for word-boundary regex matching.
# [verified: v3.1-design-master.md § 4. §4 V31-1]
V3_UNIVERSAL_REQUIRED = [
    "cycle_number",
    "mao_trigger",
    "source_finding_ids",
    "drill_tier_rubric",
    "v3_apparatus",
]

# Conditional required field: only checked for critic-driller when form_4_eligible: true.
# [verified: V3-arch.md § 2.7.1 Apparatus-surface scope]
V3_CONDITIONAL_EXP_FIELD = "assigned_exp_nnn"
V3_FORM4_ELIGIBLE_FIELD = "form_4_eligible"

# Critic-specific required field: parent_drill_tool_use_id must be present in every
# critic-driller delegation (links critic back to the driller that spawned it).
# [verified: plan-v3.1-impl-v2-subtask-4.md § Change — Add critic-specific parent_drill_tool_use_id check]
V3_CRITIC_PARENT_FIELD = "parent_drill_tool_use_id"

# Bypass token for V3 gate — distinct from V2's SCHEMA-BYPASS token.
_BYPASS_TOKEN_RE = re.compile(
    r'^[ \t]*<!-- V3-SCHEMA-BYPASS: ([^>]*) -->\s*$'
)

# Set to True by the version probe when Claude Code is below v2.1.90 or undetectable.
# Causes the failure path to degrade to advisory (_warn) instead of hard-block (_block).
_DEGRADED = False


def _detect_bypass(prompt: str):
    """Check for V3 bypass token in first 20 lines of prompt.

    Returns (bypass_active: bool, reason: str).
    - 0 matches → (False, "")
    - 1 match   → (True, reason_string)
    - 2+ matches → (False, "") and emits stderr warning
    """
    lines = prompt.split("\n")[:20]
    matches = []
    for line in lines:
        m = _BYPASS_TOKEN_RE.match(line)
        if m:
            matches.append(m.group(1).strip())

    if len(matches) == 0:
        return False, ""
    if len(matches) == 1:
        return True, matches[0]
    # Multiple occurrences: ignore all — ambiguous intent, no bypass granted
    print("[v3-schema-gate] multiple V3-SCHEMA-BYPASS tokens detected; ignoring all",
          file=sys.stderr)
    return False, ""


def _write_bypass_record(event: dict, tool_input: dict, reason: str, session_dir: str) -> None:
    """Write a bypass audit record to {session_dir}/delegation-trace.jsonl.

    Fail-quiet on any error: write to stderr but do not crash.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")

    record = {
        "kind": "v3-bypass",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "v3-schema-gate-bypass",
        "session_id": session_id,
        "agent_type": tool_input.get("subagent_type", ""),
        "schema_bypass_reason": reason,
        "tool_call_id": event.get("tool_use_id", None),
    }

    # try: recovering from filesystem error (session_dir not yet created or read-only)
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(trace_file, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"[v3-schema-gate] bypass record write failed: {e}", file=sys.stderr)


def _write_non_apparatus_synthesizer_record(tool_use_id: str, session_dir: str) -> None:
    """Write an audit record for synthesizer dispatches that lack v3_apparatus.

    These are non-V3-apparatus dispatches (e.g. §B.6 post-audit C1 hooks) that
    share the subagent_type="synthesizer" label but are out of V3 apparatus scope.
    Tagged kind "v3-non-apparatus-synthesizer" so mis-routes are detectable.

    Fail-quiet on any error: write to stderr but do not crash.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")

    record = {
        "kind": "v3-non-apparatus-synthesizer",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "v3-schema-gate",
        "session_id": session_id,
        "agent_type": "synthesizer",
        "tool_call_id": tool_use_id or None,
    }

    # try: recovering from filesystem error (session_dir not yet created or read-only)
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(trace_file, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"[v3-schema-gate] non-apparatus-synthesizer record write failed: {e}", file=sys.stderr)


def _write_block_record(subagent_type: str, tool_use_id: str, session_dir: str, details: str) -> None:
    """Write a v3-block audit record to {session_dir}/delegation-trace.jsonl.

    Uses kind "v3-block" (distinct from V2's "block") so the synthesizer's
    MAO Telemetry section can count V3-gate blocks per cycle independently.
    [verified: V3-sketch-DS1-schema-gate.md § Q3 — Block audit record]

    Fail-quiet on any error: write to stderr but do not crash.
    """
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")

    record = {
        "kind": "v3-block",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "subagent_type": subagent_type,
        "tool_use_id": tool_use_id,
        "violation_detail": details,
        "session_dir": session_dir,
    }

    # try: recovering from filesystem error (session_dir not yet created or read-only)
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(trace_file, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"[v3-schema-gate] block record write failed: {e}", file=sys.stderr)


def _warn(message: str) -> dict:
    """Build an additionalContext warning response (degraded-mode path)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }


def _block(error_text: str) -> None:
    """Write error_text to stderr and exit with code 2 (hard block)."""
    print(error_text, file=sys.stderr)
    sys.exit(2)


def _run_version_probe(session_dir: str) -> None:
    """Check Claude Code version; set _DEGRADED if below v2.1.90.

    Idempotent per session: writes {session_dir}/.v3-schema-gate-version-warning
    sentinel on first probe so subsequent fires skip the subprocess call.
    Mirrors the same pattern in delegation-prompt-schema-gate.py (_run_version_probe).
    """
    global _DEGRADED
    sentinel = os.path.join(session_dir, ".v3-schema-gate-version-warning")

    if os.path.exists(sentinel):
        # Read sentinel to restore degraded state from previous probe
        # try: recovering from a partially written sentinel file
        try:
            content = open(sentinel).read().strip()
            if content == "degraded":
                _DEGRADED = True
        except OSError:
            pass
        return

    min_version = (2, 1, 90)
    degraded = False
    # try: recovering from subprocess failure (claude not on PATH, timeout, permission denied)
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5
        )
        stdout = result.stdout.strip()
        m = re.search(r'(\d+)\.(\d+)\.(\d+)', stdout)
        if m:
            parsed = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if parsed < min_version:
                degraded = True
                print(
                    f"[v3-schema-gate] advisory: Claude Code {'.'.join(str(x) for x in parsed)} < v2.1.90; "
                    "exit-2 blocking may not be enforced — gate degraded to advisory mode.",
                    file=sys.stderr
                )
        else:
            degraded = True
            print(
                f"[v3-schema-gate] advisory: could not parse claude --version output ({stdout!r}); "
                "gate degraded to advisory mode.",
                file=sys.stderr
            )
    except Exception as e:
        degraded = True
        print(
            f"[v3-schema-gate] advisory: claude --version probe failed ({e}); "
            "gate degraded to advisory mode.",
            file=sys.stderr
        )

    # Write sentinel (best-effort)
    # try: recovering from filesystem error writing the sentinel
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(sentinel, "w") as f:
            f.write("degraded" if degraded else "ok")
    except OSError:
        pass

    if degraded:
        _DEGRADED = True


def _check_field_present(prompt: str, field: str) -> bool:
    """Return True if field token is present in prompt via word-boundary regex.

    Works for both prose-mode (field: value) and JSON-mode ("field": value) prompts.
    [verified: V3-sketch-DS1-schema-gate.md § Q1 — all five are word-boundary-greppable]
    """
    pattern = re.compile(r"\b" + re.escape(field) + r"\b")
    return bool(pattern.search(prompt))


def main():
    # try: recovering from malformed stdin (hook receives non-JSON from platform)
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    # Only act on Agent tool calls
    if event.get("tool_name") != "Agent":
        return

    tool_input = event.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type", "")
    prompt = tool_input.get("prompt", "")

    # Scope filter: silent early-return for all non-V3-apparatus agent types.
    # [verified: V3-sketch-DS1-schema-gate.md § Q2 — explicit allowlist on subagent_type]
    if subagent_type not in V3_APPARATUS_AGENTS:
        return

    cwd = event.get("cwd", os.getcwd())
    tool_use_id = event.get("tool_use_id", "") or ""

    # Secondary exemption: synthesizer dispatches that lack v3_apparatus are
    # non-V3-apparatus dispatches (e.g. §B.6 post-audit C1 hooks) and must not
    # be blocked. Genuine V3-apparatus synthesizer dispatches carry v3_apparatus
    # in their prompt body and fall through to full field checks below.
    # [verified: constraints/v3-schema-gate-overfires-on-b6-synthesizer.md § Fix candidate]
    if subagent_type == "synthesizer" and not _check_field_present(prompt, "v3_apparatus"):
        session_id = os.environ.get("CLAUDE_SESSION_ID", "")
        if session_id:
            _non_apparatus_session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
        else:
            _non_apparatus_session_dir = os.path.join(cwd, ".agent_context", "audit")
        _write_non_apparatus_synthesizer_record(tool_use_id, _non_apparatus_session_dir)
        return

    # Resolve session_dir for trace records and version-probe sentinel
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    else:
        session_dir = os.path.join(cwd, ".agent_context", "audit")

    # Version probe — idempotent per session; sets _DEGRADED if below v2.1.90
    _run_version_probe(session_dir)

    # Bypass token detection
    bypass_active, bypass_reason = _detect_bypass(prompt)
    if bypass_active:
        _write_bypass_record(event, tool_input, bypass_reason, session_dir)
        print(json.dumps(_warn(
            f"v3-protocol schema gate — {subagent_type}: "
            f"BYPASS active (reason: {bypass_reason!r}). "
            f"Schema checks skipped. See unbraked-deepening/V3-arch.md § 2.7.1."
        )))
        return

    # --- Field checks ---

    # Universal-required fields: word-boundary regex, works for prose and JSON prompts.
    missing_universal = [
        field for field in V3_UNIVERSAL_REQUIRED
        if not _check_field_present(prompt, field)
    ]

    # Conditional checks: fields required only for critic-driller dispatches.
    # synthesizer does not emit form-4 and is not a critic-driller dispatch.
    # [verified: V3-arch.md § 2.7.1 Apparatus-surface scope]
    missing_conditional = []
    if subagent_type == "critic-driller":
        # assigned_exp_nnn required when form_4_eligible: true.
        form4_eligible = _check_field_present(prompt, "form_4_eligible: true")
        if form4_eligible and not _check_field_present(prompt, V3_CONDITIONAL_EXP_FIELD):
            missing_conditional.append(V3_CONDITIONAL_EXP_FIELD)

        # parent_drill_tool_use_id required for all critic dispatches (links critic
        # back to the driller tool_use_id that spawned it; required unconditionally).
        # [verified: plan-v3.1-impl-v2-subtask-4.md § Change — Add critic-specific parent_drill_tool_use_id check]
        if not _check_field_present(prompt, V3_CRITIC_PARENT_FIELD):
            missing_conditional.append(V3_CRITIC_PARENT_FIELD)

    # Emit result
    if missing_universal or missing_conditional:
        sections = []
        if missing_universal:
            sections.append(f"missing required field(s): {', '.join(missing_universal)}")
        if missing_conditional:
            # Separate the two categories of conditional failures for clarity.
            exp_missing = [f for f in missing_conditional if f == V3_CONDITIONAL_EXP_FIELD]
            parent_missing = [f for f in missing_conditional if f == V3_CRITIC_PARENT_FIELD]
            if exp_missing:
                sections.append(
                    f"missing conditional field(s): {', '.join(exp_missing)} "
                    f"(required because form_4_eligible: true)"
                )
            if parent_missing:
                sections.append(
                    f"missing critic-required field(s): {', '.join(parent_missing)} "
                    f"(required for all critic-driller dispatches)"
                )
        details = "; ".join(sections)

        all_missing = missing_universal + missing_conditional
        expected_fields = ", ".join(V3_UNIVERSAL_REQUIRED)
        if subagent_type == "critic-driller":
            expected_fields += f"[, {V3_CONDITIONAL_EXP_FIELD}], {V3_CRITIC_PARENT_FIELD}"
        present_fields = ", ".join(
            f for f in V3_UNIVERSAL_REQUIRED + [V3_CONDITIONAL_EXP_FIELD, V3_CRITIC_PARENT_FIELD]
            if _check_field_present(prompt, f)
        ) or "(none of the required fields)"

        error_text = (
            f"v3-protocol schema gate — {subagent_type}: {details}.\n"
            f"Expected fields: {expected_fields}.\n"
            f"Found in prompt: {present_fields}.\n"
            f"See unbraked-deepening/V3-arch.md § 2.7.1 for schema. "
            f"To bypass for emergency dispatch, prepend "
            f"'<!-- V3-SCHEMA-BYPASS: <reason> -->' as a top-of-prompt line "
            f"(within first 20 lines, exactly once)."
        )

        if _DEGRADED:
            print(json.dumps(_warn(error_text)))
            sys.exit(0)
        else:
            _write_block_record(subagent_type, tool_use_id, session_dir, details)
            _block(error_text)


if __name__ == "__main__":
    # try: recovering from any unhandled exception (hook must not crash Claude Code)
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
