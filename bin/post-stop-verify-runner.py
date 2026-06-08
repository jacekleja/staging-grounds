#!/usr/bin/env python3
"""CLI runner: invoke post_stop_verify_core.run_recipe from a subprocess.

Reads JSON from stdin:
  {raw_prompt, cwd, subagent_type, agent_id, tool_use_id, returned_text}

Runs delegation_prompt_parser.parse_prompt server-side on raw_prompt to derive
the full pending_entry (mirroring handle_pre_tool_use's decoration logic), then
calls run_recipe and writes the result dict as JSON to stdout. Exits 0 always
(fail-open: spawn errors produce a degraded checks array, same as schema-gate).

Path resolution: delegation_prompt_parser and post_stop_verify_core live in
.claude/hooks/. We locate _HOOKS_DIR relative to this script's own path, then
walk up to the repo root to find it regardless of cwd at invocation time.
"""
import json
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Smoke-test imports at top — fail loud on missing modules so the caller's
# spawnSync sees the error in stderr and logs it (fail-open pattern).
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)  # bin/ is one level below repo root
_HOOKS_DIR = os.path.join(_REPO_ROOT, ".claude", "hooks")

sys.path.insert(0, _HOOKS_DIR)

from delegation_prompt_parser import parse_prompt, required_field as _required_field  # noqa: E402
from post_stop_verify_core import run_recipe, _substitute_session_dir  # noqa: E402


def _build_pending_entry(raw_prompt, cwd, subagent_type, agent_id, tool_use_id):
    """Replicate handle_pre_tool_use's pending_entry construction from parse_prompt output.

    This is the server-side Option-A decoration: same parser the hook uses, so
    every pending_entry key consumed by run_check_* is populated identically.
    """
    parsed_pp = parse_prompt(raw_prompt)

    # Build output_contract dict from parsed fields (mirrors handle_pre_tool_use:228-243)
    artifact_path = _required_field(parsed_pp, "output_contract.artifact_path")
    sidecar_path_oc = _required_field(parsed_pp, "output_contract.sidecar_path")
    required_sections = _required_field(parsed_pp, "output_contract.required_sections") or []
    oc = None
    if artifact_path is not None or sidecar_path_oc is not None or required_sections:
        oc = {}
        if artifact_path is not None:
            oc["artifact_path"] = artifact_path
        if sidecar_path_oc is not None:
            oc["sidecar_path"] = sidecar_path_oc
        if required_sections:
            oc["required_sections"] = required_sections
    if oc is None and parsed_pp.output_contract:
        oc = parsed_pp.output_contract

    # Top-level sidecar_path (mirrors handle_pre_tool_use:251-255)
    top_level_sidecar_path = None
    if parsed_pp.raw_dict and isinstance(parsed_pp.raw_dict, dict):
        sp = parsed_pp.raw_dict.get("sidecar_path")
        if isinstance(sp, str) and sp:
            top_level_sidecar_path = sp

    # Resolve subagent_type: payload value wins over parser extraction (mirrors :258)
    if not subagent_type or subagent_type == "unknown":
        subagent_type = parsed_pp.subagent_type or subagent_type

    # Resolve agent_id
    resolved_agent_id = agent_id or parsed_pp.agent_id or ""

    return {
        "tool_use_id": tool_use_id,
        "subagent_type": subagent_type,
        "agent_id": resolved_agent_id,
        "output_contract": oc,
        "target_artifact_path": parsed_pp.target_artifact_path,
        "sidecar_path": top_level_sidecar_path,
        "required_sections": required_sections,
        "claims": {},  # claims parsing not in scope; preserved as empty per hook convention
        "start_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }


def _substitute_paths(pending_entry, session_dir):
    """Substitute {session_dir} in all path-shaped fields (mirrors handle_post_tool_use:346-377).

    Returns (updated_pending_entry, template_warns[]).
    """
    template_warns = []
    contract = pending_entry.get("output_contract") or {}
    oc_changed = False
    for field in ("sidecar_path", "artifact_path"):
        val = contract.get(field, "")
        if val:
            subst, w = _substitute_session_dir(val, session_dir)
            if subst != val:
                contract[field] = subst
                oc_changed = True
            if w is not None:
                template_warns.append(w)
    if oc_changed:
        pending_entry["output_contract"] = contract

    tap = pending_entry.get("target_artifact_path") or ""
    if tap:
        subst_tap, w_tap = _substitute_session_dir(tap, session_dir)
        if subst_tap != tap:
            pending_entry["target_artifact_path"] = subst_tap
        if w_tap is not None:
            template_warns.append(w_tap)

    tsp = pending_entry.get("sidecar_path") or ""
    if tsp:
        subst_tsp, w_tsp = _substitute_session_dir(tsp, session_dir)
        if subst_tsp != tsp:
            pending_entry["sidecar_path"] = subst_tsp
        if w_tsp is not None:
            template_warns.append(w_tsp)

    return pending_entry, template_warns


def run_gate(payload: dict) -> dict:

    raw_prompt = payload.get("raw_prompt", "")
    cwd = payload.get("cwd", os.getcwd())
    subagent_type = payload.get("subagent_type", "unknown")
    agent_id = payload.get("agent_id", "")
    tool_use_id = payload.get("tool_use_id", "")
    returned_text = payload.get("returned_text", "")
    mcp_audit_file = payload.get("mcp_audit_file")   # artifact-3b: threaded audit path
    model_route = payload.get("model_route")           # artifact-3b: route class

    # Derive session_dir from cwd (matches derive_session_dir logic in the hook).
    # The runner receives cwd = getWorktreeRoot() from the TS wrapper; session_dir
    # follows the same CLAUDE_SESSION_ID-based path the hook uses.
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    else:
        session_dir = ""

    # Route-alone gate (FINDING 1 — parallel to run_recipe gate): gpt/gemini children
    # bypass parse_prompt and _build_pending_entry entirely. The MCP-audit check needs
    # none of the output-contract fields. Built even when mcp_audit_file is absent (Y_0f:
    # check_mcp_audit_vs_declared_tools fail-opens to pass in that case), so a route-set
    # child with no audit file NEVER falls through to the claude parse_prompt path.
    if model_route in ("gemini", "gpt"):
        minimal_entry = {
            "subagent_type": subagent_type,
            "agent_id": agent_id,
            "tool_use_id": tool_use_id,
            "mcp_audit_file": mcp_audit_file,
            "model_route": model_route,
        }
        try:
            result = run_recipe(minimal_entry, cwd, subagent_type, returned_text=returned_text)
        except Exception as e:  # noqa: BLE001 — fail-open; never block a gpt/gemini dispatch
            sys.stderr.write(f"[post-stop-verify-runner] gpt/gemini run_recipe error: {e}\n")
            result = {
                "verdict": "pass",
                "verdict_reason": "runner-recipe-error",
                "checks": [],
                "failures": [],
                "warnings": [],
                "advisories": [],
                "peer_review_disposition": "not-applicable",
                "completeness_disposition": "not-applicable",
                "redispatch_hint": "",
            }
        return {"_action": "pass", "stdout_json": result}

    try:
        pending_entry = _build_pending_entry(raw_prompt, cwd, subagent_type, agent_id, tool_use_id)
        pending_entry, template_warns = _substitute_paths(pending_entry, session_dir)
        result = run_recipe(pending_entry, cwd, pending_entry["subagent_type"], returned_text=returned_text)

        # Prepend template-substitution warnings so they appear before per-check results.
        result["checks"] = template_warns + result["checks"]
        if template_warns:
            # Re-derive failures/warnings to account for prepended warns.
            # (template_warns are already {name, status, evidence} dicts; the verdict
            # may need updating if a previously-pass result now has warns prepended.)
            from post_stop_verify_core import compute_verdict  # noqa: PLC0415 (late import fine)
            verdict, failures, warnings, advisories, verdict_reason = compute_verdict(result["checks"])
            result["verdict"] = verdict
            result["failures"] = failures
            result["warnings"] = warnings
            result["advisories"] = advisories
            if verdict_reason != "ok" and not result.get("verdict_reason") or result["verdict_reason"] == "ok":
                result["verdict_reason"] = verdict_reason

    except Exception as e:  # noqa: BLE001 — fail-open on any unexpected error
        # Fail-open: degrade to empty checks rather than crashing the dispatch.
        sys.stderr.write(f"[post-stop-verify-runner] run_recipe error: {e}\n")
        result = {
            "verdict": "pass",
            "verdict_reason": "runner-recipe-error",
            "checks": [],
            "failures": [],
            "warnings": [],
            "advisories": [],
            "peer_review_disposition": "not-applicable",
            "completeness_disposition": "not-applicable",
            "redispatch_hint": "",
        }

    return {"_action": "pass", "stdout_json": result}



def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except (IOError, OSError, json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"[post-stop-verify-runner] stdin read/parse error: {e}\n")
        sys.stdout.write(json.dumps({
            "verdict": "pass",
            "verdict_reason": "runner-stdin-error",
            "checks": [],
            "failures": [],
            "warnings": [],
            "advisories": [],
            "peer_review_disposition": "not-applicable",
            "completeness_disposition": "not-applicable",
            "redispatch_hint": "",
        }))
        sys.exit(0)
        
    res = run_gate(payload)
    if res.get("_action") == "pass":
        sys.stdout.write(json.dumps(res.get("stdout_json", {})) + "\n")
    sys.exit(0)

if __name__ == "__main__":
    main()
