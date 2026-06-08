#!/usr/bin/env python3
# dispatch-child-safe: false
"""Post-stop verification hook: PreToolUse/Agent + PostToolUse/Agent.

PreToolUse: captures pending state (subagent_type, output_contract, start_ts) keyed by tool_use_id.
PostToolUse: runs cheap + expensive checks per recipe, writes .verify.json sidecar, emits
             additionalContext advisory on stdout when verdict is warn or fail.

Background-spawn exception: when the Agent tool input has `run_in_background: true`,
PostToolUse:Agent fires at SPAWN-RETURN time (when the platform returns the agentId),
NOT at agent-completion time. The artifact/sidecar/git-diff checks would all fail
because the agent has not run yet. This hook SHORT-CIRCUITS for background spawns
on both PreToolUse and PostToolUse: no pending state is captured, no .verify.json
sidecar is written. The orchestrator-prompt §B.0 instructs the orchestrator to use
the completion-notification return value as the sole signal for background spawns.

State file:  {session_dir}/post-stop-verify-pending.json
Sidecar:     {session_dir}/post-stop-verify-{tool_use_id}.verify.json

Advisory-only: never blocks Claude Code; exits 0 always.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# Ensure sibling modules resolve regardless of cwd at hook-fire time.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HOOKS_DIR)

from delegation_prompt_parser import parse_prompt, required_field as _parser_required_field
from _dispatch_child_guard import exit_if_dispatched_child
from post_stop_verify_core import (
    RECIPES,
    DEFAULT_FALLBACK,
    PLATFORM_MISROUTE_PREFIX,
    VALID_VERDICT_ENUM,
    REDISPATCH_HINTS,
    CHEAP_CHECK_DISPATCH,
    EXPENSIVE_CHECK_DISPATCH,
    _substitute_session_dir,
    _get_binary_target_path,
    _get_semantic_artifact_path,
    _get_sidecar_path,
    run_check_artifact_exists,
    run_check_artifact_min_size,
    run_check_required_sections_present,
    run_check_sidecar_exists,
    run_check_sidecar_parses_as_json,
    run_check_verdict_enum_valid,
    run_check_min_word_count,
    run_check_git_diff_nonempty,
    run_check_gateway_compliance,
    run_check_claimed_grep_reproducible,
    run_check_citations_resolve,
    compute_verdict,
    extract_dispositions,
    get_redispatch_hint,
    run_recipe,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POST_STOP_VERIFY_PENDING = "post-stop-verify-pending.json"
VERIFY_FILENAME_FMT = "post-stop-verify-{tool_use_id}.verify.json"
VERIFIER_VERSION = 1

# Pending-dict hygiene: entries older than this are stale (subagent hung / session
# crashed before PostToolUse fired). 30 min is longer than any realistic subagent run.
PENDING_TTL_SECONDS = 1800

# Hard cap on pending-dict size. LRU-evict (oldest start_ts first) when exceeded.
PENDING_MAX_ENTRIES = 100

# Sentinel filename written to session_dir after the first sweep fires, so the
# TTL sweep runs at most once per session start rather than on every PreToolUse.
PENDING_SWEEP_DONE_SENTINEL = "post-stop-verify-sweep-done"

# ---------------------------------------------------------------------------
# Session dir helper
# ---------------------------------------------------------------------------

def derive_session_dir(event):
    """Return the session-scoped directory for state/sidecar files.

    Uses CLAUDE_SESSION_ID env var + event cwd. Falls back to .agent_context/audit/.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    cwd = event.get("cwd", os.getcwd())
    if session_id:
        return os.path.join(cwd, ".agent_context", "sessions", session_id)
    sys.stderr.write("[post-stop-verify] CLAUDE_SESSION_ID unset; writing to audit dir\n")
    return os.path.join(cwd, ".agent_context", "audit")

# ---------------------------------------------------------------------------
# Pending-dict I/O (atomic)
# ---------------------------------------------------------------------------

def read_pending_dict(session_dir):
    """Load pending state dict. Returns {} on any error."""
    path = os.path.join(session_dir, POST_STOP_VERIFY_PENDING)
    try:
        with open(path, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def write_pending_dict(session_dir, pending):
    """Atomically write pending state dict."""
    try:
        os.makedirs(session_dir, exist_ok=True)
        path = os.path.join(session_dir, POST_STOP_VERIFY_PENDING)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pending, f)
        os.replace(tmp, path)
    except (IOError, OSError):
        pass


def sweep_pending_dict(session_dir, pending):
    """Remove expired entries (TTL) and enforce size cap (LRU-evict oldest).

    Returns (cleaned_dict, removed_count) where removed_count is the number of
    entries removed so the caller can emit a structured log line.

    TTL: entries older than PENDING_TTL_SECONDS from now are stale (the agent
    that produced them hung or the session crashed before PostToolUse fired).
    Size cap: when len > PENDING_MAX_ENTRIES after TTL eviction, evict oldest
    by start_ts until at or below the cap.
    """
    now = datetime.now(timezone.utc)
    removed = 0

    # TTL pass: drop entries whose start_ts parses and is older than the TTL.
    cleaned = {}
    for key, entry in pending.items():
        start_ts = entry.get("start_ts", "")
        try:
            ts = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            if age > PENDING_TTL_SECONDS:
                removed += 1
                continue
        except (ValueError, AttributeError, TypeError):
            # Unparseable timestamp: keep the entry rather than silently drop it.
            pass
        cleaned[key] = entry

    # Size-cap pass: evict oldest by start_ts when still over the cap.
    if len(cleaned) > PENDING_MAX_ENTRIES:
        # Sort by start_ts ascending; entries with unparseable ts sort last (kept).
        def _ts_sort_key(item):
            ts_str = item[1].get("start_ts", "")
            try:
                return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except (ValueError, AttributeError, TypeError):
                return float("inf")

        sorted_entries = sorted(cleaned.items(), key=_ts_sort_key)
        evict_count = len(cleaned) - PENDING_MAX_ENTRIES
        removed += evict_count
        cleaned = dict(sorted_entries[evict_count:])

    return cleaned, removed


def maybe_sweep_pending_dict(session_dir, pending):
    """Run sweep at most once per session (sentinel-gated).

    Writes a sentinel file after the first sweep so subsequent PreToolUse fires
    in the same session skip the sweep entirely.  Returns the (possibly pruned)
    pending dict; writes it back to disk when entries were removed.
    """
    sentinel_path = os.path.join(session_dir, PENDING_SWEEP_DONE_SENTINEL)
    if os.path.exists(sentinel_path):
        return pending  # Already swept this session.

    cleaned, removed = sweep_pending_dict(session_dir, pending)

    # Write sentinel regardless of whether anything was removed.
    try:
        os.makedirs(session_dir, exist_ok=True)
        with open(sentinel_path, "w") as f:
            f.write("")
    except (IOError, OSError):
        pass  # Best-effort; if sentinel write fails we'll sweep again next fire.

    sys.stderr.write(
        f"[post-stop-verify] pending-dict-sweep: removed {removed} expired entries"
        f" (ttl={PENDING_TTL_SECONDS}s cap={PENDING_MAX_ENTRIES})\n"
    )

    if removed:
        write_pending_dict(session_dir, cleaned)

    return cleaned


# ---------------------------------------------------------------------------
# PreToolUse handler
# ---------------------------------------------------------------------------

def handle_pre_tool_use(event, session_dir):
    """Capture pending state keyed by tool_use_id for this subagent spawn."""
    tool_use_id = event.get("tool_use_id", "")
    if not tool_use_id:
        return  # Cannot track without a correlation key

    tool_input = event.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type", "unknown")
    agent_id = tool_input.get("agent_id", "")

    # Parse output_contract from the delegation prompt text via centralized parser.
    # parse_prompt() handles JSON-mode (incl. fenced blocks) and prose fallback.
    # required_field() provides dotted-path access with None-on-miss semantics.
    prompt_text = tool_input.get("prompt", "") or tool_input.get("description", "")
    parsed_pp = parse_prompt(prompt_text)

    # Build output_contract dict from parsed fields (None keys are preserved so
    # downstream checks can distinguish "key absent" from "key present but empty").
    artifact_path = _parser_required_field(parsed_pp, "output_contract.artifact_path")
    sidecar_path = _parser_required_field(parsed_pp, "output_contract.sidecar_path")
    required_sections = _parser_required_field(parsed_pp, "output_contract.required_sections") or []
    oc: dict | None = None
    if artifact_path is not None or sidecar_path is not None or required_sections:
        oc = {}
        if artifact_path is not None:
            oc["artifact_path"] = artifact_path
        if sidecar_path is not None:
            oc["sidecar_path"] = sidecar_path
        if required_sections:
            oc["required_sections"] = required_sections
    # If output_contract was a dict in the raw parsed result, use it directly
    # (preserves any extra sub-keys the parser populated).
    if oc is None and parsed_pp.output_contract:
        oc = parsed_pp.output_contract

    # Top-level sidecar_path: some dispatch shapes (cycle-mode.md, terminal-mode.md
    # for cycling-promoter) name sidecar_path at the JSON top level instead of
    # nesting it under output_contract. The parser does not currently surface
    # this as a named attribute; pull it directly from raw_dict so _get_sidecar_path
    # can find it. Mirrors the top-level-vs-nested precedence already established
    # by target_artifact_path / output_contract.artifact_path.
    top_level_sidecar_path = None
    if parsed_pp.raw_dict and isinstance(parsed_pp.raw_dict, dict):
        sp = parsed_pp.raw_dict.get("sidecar_path")
        if isinstance(sp, str) and sp:
            top_level_sidecar_path = sp

    # Prefer subagent_type / agent_id from tool_input (more reliable than prompt extraction)
    resolved_subagent_type = subagent_type if (subagent_type and subagent_type != "unknown") else (parsed_pp.subagent_type or subagent_type)
    resolved_agent_id = agent_id or parsed_pp.agent_id or ""

    start_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    pending_entry = {
        "tool_use_id": tool_use_id,
        "subagent_type": resolved_subagent_type,
        "agent_id": resolved_agent_id,
        "output_contract": oc,
        "target_artifact_path": parsed_pp.target_artifact_path,
        "sidecar_path": top_level_sidecar_path,
        "required_sections": required_sections,
        "claims": {},  # claims parsing not in scope for this port; preserved as empty
        "start_ts": start_ts,
    }

    pending = read_pending_dict(session_dir)
    # Session-start sweep: TTL eviction + size cap, at most once per session.
    pending = maybe_sweep_pending_dict(session_dir, pending)
    pending[tool_use_id] = pending_entry
    write_pending_dict(session_dir, pending)

# ---------------------------------------------------------------------------
# Sidecar + advisory-output helpers (restored from pre-7198f4a2 state)
# ---------------------------------------------------------------------------

def emit_verify_sidecar(session_dir, tool_use_id, subagent_type, agent_id, verdict,
                        verdict_reason, checks, failures, warnings,
                        redispatch_hint, session_id, advisories=None,
                        peer_review_disposition="not-applicable",
                        completeness_disposition="not-applicable"):
    """Atomically write the .verify.json sidecar.

    failures[] and warnings[] carry {name, evidence} objects derived from checks[].
    advisories[] carries delegation-shape notices (field not declared in prompt) that
    do not escalate the verdict — the work was fine; the dispatch was incomplete.
    """
    if advisories is None:
        advisories = []
    filename = VERIFY_FILENAME_FMT.format(tool_use_id=tool_use_id)
    path = os.path.join(session_dir, filename)
    payload = {
        "verifier_version": VERIFIER_VERSION,
        "tool_use_id": tool_use_id,
        "subagent_type": subagent_type,
        "agent_id": agent_id,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "advisories": advisories,
        "peer_review_disposition": peer_review_disposition,
        "completeness_disposition": completeness_disposition,
        "redispatch_hint": redispatch_hint,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "session_id": session_id,
    }
    os.makedirs(session_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def emit_additional_context(verdict, failures, warnings, advisories, redispatch_hint,
                             subagent_type, tool_use_id, hook_event_name):
    """Print canonical hook output shape on stdout when verdict is warn or fail.

    Renders only non-pass checks, grouped by status, with one evidence line each.
    advisories (delegation-shape notices) are rendered separately and never promoted
    to failures/warnings in this output.
    """
    if verdict not in ("fail", "warn"):
        # Emit advisories even on pass if any exist, so the orchestrator sees them.
        if advisories:
            lines = [f"--- POST-STOP VERIFICATION ADVISORY ---",
                     f"subagent={subagent_type} tool_use_id={tool_use_id}",
                     "Advisory (dispatch did not declare these fields; verification skipped):"]
            for a in advisories:
                lines.append(f"  {a['name']}: {a['evidence']}")
            lines.append("--- END POST-STOP VERIFICATION ---")
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": hook_event_name,
                    "additionalContext": "\n".join(lines),
                }
            }))
        return

    lines = []
    if verdict == "fail":
        lines.append("--- POST-STOP VERIFICATION FAILED ---")
    else:
        lines.append("--- POST-STOP VERIFICATION WARNING ---")
    lines.append(f"subagent={subagent_type} tool_use_id={tool_use_id}")

    if failures:
        lines.append("Failures:")
        for f in failures:
            lines.append(f"  {f['name']}: {f['evidence']}")
        if redispatch_hint:
            lines.append(f"redispatch_hint: {redispatch_hint}")

    if warnings:
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  {w['name']}: {w['evidence']}")

    if advisories:
        lines.append("Advisory (dispatch did not declare these fields; verification skipped):")
        for a in advisories:
            lines.append(f"  {a['name']}: {a['evidence']}")

    lines.append("--- END POST-STOP VERIFICATION ---")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "additionalContext": "\n".join(lines),
        }
    }))


# ---------------------------------------------------------------------------
# PostToolUse handler
# ---------------------------------------------------------------------------

def handle_post_tool_use(event, session_dir, session_id):
    """Run verification checks and write .verify.json sidecar."""
    tool_use_id = event.get("tool_use_id", "")
    tool_input = event.get("tool_input", {})
    subagent_type_from_event = tool_input.get("subagent_type", "unknown")
    cwd = event.get("cwd", os.getcwd())
    hook_event_name = event.get("hook_event_name", "PostToolUse")

    # Load and pop pending entry
    pending = read_pending_dict(session_dir)
    verdict_reason_prefix = ""

    if tool_use_id and tool_use_id in pending:
        pending_entry = pending.pop(tool_use_id)
        write_pending_dict(session_dir, pending)
    else:
        # PreToolUse hook missed this spawn — use default fallback
        pending_entry = {
            "tool_use_id": tool_use_id,
            "subagent_type": subagent_type_from_event,
            "agent_id": "",
            "output_contract": None,
            "required_sections": [],
            "claims": {},
            "start_ts": "",
        }
        verdict_reason_prefix = "pending-dict-miss-fallback"

    subagent_type = pending_entry.get("subagent_type") or subagent_type_from_event
    agent_id = pending_entry.get("agent_id", "")

    # ST-5 MISROUTE detection: when the platform's activeAgents.find() fails for the
    # requested subagent_type it falls through to general-purpose, which emits this prefix.
    # Detect early; skip normal checks; surface redispatch_hint via sidecar.
    tool_response = event.get("tool_response", "") or ""
    if isinstance(tool_response, str) and tool_response.lstrip().startswith(PLATFORM_MISROUTE_PREFIX):
        # Extract the subagent_type named in the misroute message for the hint.
        requested_type_in_msg = tool_response.lstrip()[len(PLATFORM_MISROUTE_PREFIX):].split()[0].rstrip(".")
        redispatch_hint = (
            f"platform-misroute: general-purpose executed instead of '{subagent_type}'; "
            f"message names '{requested_type_in_msg}'; re-spawn subagent_type={subagent_type} "
            f"(cap 3 retries per §B.5 before escalating to inline orchestrator action)"
        )
        dispositions = extract_dispositions(subagent_type, tool_response)
        emit_verify_sidecar(
            session_dir=session_dir,
            tool_use_id=tool_use_id,
            subagent_type=subagent_type,
            agent_id=agent_id,
            verdict="fail",
            verdict_reason="platform-misroute",
            checks=[],
            failures=[{"name": "platform-misroute", "evidence": redispatch_hint}],
            warnings=[],
            advisories=[],
            peer_review_disposition=dispositions["peer_review_disposition"],
            completeness_disposition=dispositions["completeness_disposition"],
            redispatch_hint=redispatch_hint,
            session_id=session_id,
        )
        emit_additional_context("fail", [{"name": "platform-misroute", "evidence": redispatch_hint}], [], [], redispatch_hint, subagent_type, tool_use_id, hook_event_name)
        return

    # Substitute {session_dir} in all path-shaped fields via single chokepoint.
    # _substitute_session_dir returns a warn check-result when session_dir is
    # missing so the orchestrator knows the downstream stat calls are unreliable.
    # Covers Pattern-6 (iss_916e70123198, iss_96f409495f2d).
    _template_warns = []
    contract = pending_entry.get("output_contract") or {}
    _oc_changed = False
    for _field in ("sidecar_path", "artifact_path"):
        val = contract.get(_field, "")
        if val:
            subst, w = _substitute_session_dir(val, session_dir)
            if subst != val:
                contract[_field] = subst
                _oc_changed = True
            if w is not None:
                _template_warns.append(w)
    if _oc_changed:
        pending_entry["output_contract"] = contract
    tap = pending_entry.get("target_artifact_path") or ""
    if tap:
        subst_tap, w_tap = _substitute_session_dir(tap, session_dir)
        if subst_tap != tap:
            pending_entry["target_artifact_path"] = subst_tap
        if w_tap is not None:
            _template_warns.append(w_tap)
    tsp = pending_entry.get("sidecar_path") or ""
    if tsp:
        subst_tsp, w_tsp = _substitute_session_dir(tsp, session_dir)
        if subst_tsp != tsp:
            pending_entry["sidecar_path"] = subst_tsp
        if w_tsp is not None:
            _template_warns.append(w_tsp)

    # Use DEFAULT_FALLBACK when PreToolUse hook missed this spawn (no parsed data).
    # run_recipe handles recipe selection and unknown-subagent-type fallback internally;
    # the pending-dict-miss case needs the subagent_type forced to DEFAULT_FALLBACK here.
    if verdict_reason_prefix == "pending-dict-miss-fallback":
        # Force default-fallback recipe by using a sentinel type not in RECIPES.
        effective_subagent_type = "__pending-dict-miss__"
    else:
        effective_subagent_type = subagent_type

    result = run_recipe(pending_entry, cwd, effective_subagent_type, returned_text=tool_response)

    # Prepend any template-substitution warnings so they appear before per-check results.
    checks = list(_template_warns) + result["checks"]
    verdict = result["verdict"]
    failures = result["failures"]
    warnings = result["warnings"]
    advisories = result["advisories"]
    redispatch_hint = result["redispatch_hint"]
    verdict_reason = result["verdict_reason"]

    # Override verdict_reason when pending-dict miss triggered the fallback.
    if verdict_reason_prefix == "pending-dict-miss-fallback":
        verdict_reason = "pending-dict-miss-fallback"

    # Write sidecar
    emit_verify_sidecar(
        session_dir=session_dir,
        tool_use_id=tool_use_id,
        subagent_type=subagent_type,
        agent_id=agent_id,
        verdict=verdict,
        verdict_reason=verdict_reason,
        checks=checks,
        failures=failures,
        warnings=warnings,
        advisories=advisories,
        peer_review_disposition=result.get("peer_review_disposition", "not-applicable"),
        completeness_disposition=result.get("completeness_disposition", "not-applicable"),
        redispatch_hint=redispatch_hint,
        session_id=session_id,
    )

    # Emit advisory output if non-pass
    emit_additional_context(verdict, failures, warnings, advisories, redispatch_hint, subagent_type, tool_use_id, hook_event_name)

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    exit_if_dispatched_child("post-stop-verify")
    try:
        raw = sys.stdin.read()
    except (IOError, OSError):
        sys.exit(0)

    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    try:
        # Defensive: only act on Agent tool calls
        if event.get("tool_name") != "Agent":
            sys.exit(0)

        # Background-spawn short-circuit: PostToolUse:Agent fires at SPAWN-RETURN
        # time for run_in_background=true (NOT at agent completion); every
        # artifact/sidecar/git-diff check would false-positive against an agent
        # that has not run yet. Skip both PreToolUse capture and PostToolUse
        # verification — see module docstring for the orchestrator-side contract.
        # Mirrors cycle-hook.py Option D: both top-level and tool_input locations
        # checked because the platform's field placement is not version-stable.
        run_in_background = (event.get("run_in_background")
                             or event.get("tool_input", {}).get("run_in_background"))
        if run_in_background:
            sys.exit(0)

        session_id = os.environ.get("CLAUDE_SESSION_ID", "")
        session_dir = derive_session_dir(event)
        hook_event = event.get("hook_event_name", "")

        if hook_event == "PreToolUse":
            handle_pre_tool_use(event, session_dir)
        elif hook_event == "PostToolUse":
            handle_post_tool_use(event, session_dir, session_id)
        else:
            # Detect from payload structure: PostToolUse has tool_response
            if "tool_response" in event:
                handle_post_tool_use(event, session_dir, session_id)
            else:
                handle_pre_tool_use(event, session_dir)

    except Exception as e:
        # Never crash Claude Code (axis B convention #7); log so refactor-induced errors are visible.
        print(f'[post-stop-verify error] {type(e).__name__}: {e}', file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
