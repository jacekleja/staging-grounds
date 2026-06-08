"""Core check-runner for post-stop verification.

Extracted from post-stop-verify.py so the recipe engine is callable from both
the native PostToolUse:Agent hook (in-process import) and the
bin/post-stop-verify-runner.py subprocess wrapper (invoked by dispatch-agent.ts).

Public entry point
------------------
  run_recipe(pending_entry, cwd, subagent_type, returned_text="") -> dict
    pending_entry : fully-substituted dict in the shape produced by handle_pre_tool_use
                    (keys: tool_use_id, subagent_type, agent_id, output_contract,
                     target_artifact_path, sidecar_path, required_sections, claims, start_ts)
    cwd           : absolute path of the project root (worktree root)
    subagent_type : canonical subagent type string (may differ from pending_entry value
                    when a pending-dict miss triggers the DEFAULT_FALLBACK path)

    Returns {verdict, verdict_reason, checks, failures, warnings, advisories, redispatch_hint, dispositions}.
"""
import json
import os
import re
import subprocess
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# §4.10 default fallback applied when subagent_type not present in registry
DEFAULT_FALLBACK = {"cheap": ["artifact-exists", "artifact-min-size"], "expensive": []}

# Literal prefix emitted by general-purpose.md when a platform misroute occurs.
PLATFORM_MISROUTE_PREFIX = "MISROUTE: This request should dispatch subagent_type="

# Valid verdict enum values for sidecar-producing agents.
VALID_VERDICT_ENUM = {"approve", "request-changes", "block", "skip", "clean", "gaps", "fail-closed"}

PEER_REVIEW_DISPOSITION_PRODUCERS = {
    "agent-content-author",
    "architect",
    "design-planner",
    "diagnostician",
    "implementer",
    "planner",
    "researcher",
    "solution-designer",
    "synthesizer",
}

COMPLETENESS_DISPOSITION_PRODUCERS = {
    "agent-content-author",
    "architect",
    "diagnostician",
    "implementer",
    "researcher",
    "solution-designer",
    "synthesizer",
}

PEER_REVIEW_APPLIES_TOKEN = "Peer-review: applies"
PEER_REVIEW_SKIPPED_TOKEN = "Skipped /peer-review:"
COMPLETENESS_NONE_TOKEN = "Completeness-risk: none"
COMPLETENESS_SELF_FLAG_TOKEN = "Completeness-risk-self-flag:"

# ---------------------------------------------------------------------------
# Recipe registry
# ---------------------------------------------------------------------------

RECIPES = {
    # §4.1 implementer
    "implementer": {
        "cheap": [
            "artifact-exists",
            "artifact-min-size",
            "required-sections-present",
            "peer-review-disposition-present",
            "completeness-disposition-present",
        ],
        "expensive": ["git-diff-nonempty", "gateway-compliance", "claimed-grep-reproducible"],
    },

    # §4.2 validator + coherence-auditor (same recipe)
    "validator": {
        "cheap": ["sidecar-exists", "sidecar-parses-as-json", "verdict-enum-valid"],
        "expensive": ["citations-resolve"],
    },
    "coherence-auditor": {
        "cheap": ["sidecar-exists", "sidecar-parses-as-json", "verdict-enum-valid"],
        "expensive": ["citations-resolve"],
    },

    # §4.3 pre-flight-gate
    "pre-flight-gate": {
        "cheap": ["sidecar-exists", "sidecar-parses-as-json", "verdict-enum-valid"],
        "expensive": [],
    },

    # §4.4 designer-group: design/analysis artifacts; cheap-only
    "architect":          {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present", "completeness-disposition-present"], "expensive": []},
    "researcher":         {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present", "completeness-disposition-present"], "expensive": []},
    "synthesizer":        {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present", "completeness-disposition-present"], "expensive": []},
    "solution-designer":  {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present", "completeness-disposition-present"], "expensive": []},
    "planner":            {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present"], "expensive": []},
    "ux-aesthetic-critic": {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},  # formerly ux-designer; renamed UX-AESTHETIC-A3
    "diagnostician":      {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present", "completeness-disposition-present"], "expensive": []},  # added per drift-finding 3

    # §4.5 agent-content-author: produces substantive impl-report-style output artifacts
    "agent-content-author": {
        "cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present", "completeness-disposition-present"],
        "expensive": [],
    },

    # §4.6 surface-gate: verdict sidecar at output_contract.sidecar_path
    "surface-gate": {
        "cheap": ["sidecar-exists", "sidecar-parses-as-json", "verdict-enum-valid"],
        "expensive": [],
    },

    # §4.7 cycling-promoter: appends to {session_dir}/promoted-findings.jsonl
    # (append-only JSONL, not the JSON-verdict sidecar shape) and returns a
    # one-line digest. No verdict enum check is meaningful here. The dispatch
    # names sidecar_path at the TOP LEVEL of the delegation JSON (see
    # `.claude/skills/cycling/cycle-mode.md § Step 5` and `terminal-mode.md
    # § Step 4a`), NOT nested under output_contract; the _get_sidecar_path
    # helper accepts both shapes so sidecar-exists resolves the JSONL path
    # correctly. Rationale: .claude/knowledge/decisions/post-stop-verify-recipes-agent-content-author-surface-gate.md
    "cycling-promoter": {
        "cheap": ["sidecar-exists"],
        "expensive": [],
    },

    # §4.9 design-planner: produces design-plan-<TASK_ID>.md artifact in session_dir;
    # same artifact-exists + required-sections + min-word-count shape as designer-group.
    "design-planner":     {"cheap": ["artifact-exists", "required-sections-present", "min-word-count", "peer-review-disposition-present"], "expensive": []},

    # §4.10 V3-apparatus driller agents: each writes a named artifact file.
    # driller: cycle-{N}-drill-{Q}.md; critic-driller: {drill-stem}-critic.md.
    # Same artifact-exists + required-sections + min-word-count shape as designer-group.
    "driller":            {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},
    "critic-driller":     {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},

    # §4.11 UX / asset pipeline agents: all three are markdown-artifact producers
    # (no JSON verdict sidecar), so the designer-group shape applies. asset-critic
    # carries its action-enum verdict (STOP|CONTINUE|BACKTRACK|RESTART|ESCALATE)
    # INSIDE the markdown body's `## Verdict` section, NOT in a JSON sidecar — so
    # VALID_VERDICT_ENUM is NOT extended for the action enum.
    # - ux-designer:    {session_dir}/{TASK}-R{N}-ux-sketch.md
    # - asset-designer: {session_dir}/{TASK}-R{N}-asset-sketch.md
    # - asset-critic:   {session_dir}/{TASK}-R{N}-asset-critique.md (both modes)
    # - brand-designer: {session_dir}/{TASK}-brand-rubric.md (frozen rubric + metadata conformance;
    #   no JSON verdict sidecar, no pixels — metadata-only producer)
    "ux-designer":     {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},
    "asset-designer":  {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},
    "asset-critic":    {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},
    "brand-designer":  {"cheap": ["artifact-exists", "required-sections-present", "min-word-count"], "expensive": []},
}

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _substitute_session_dir(path_str, session_dir):
    """Substitute the literal token '{session_dir}' in a path string.

    Returns (substituted_str, warn_or_none).
    """
    if not isinstance(path_str, str) or "{session_dir}" not in path_str:
        return path_str, None
    if not session_dir:
        return path_str, {
            "name": "session-dir-token-unresolved",
            "status": "warn",
            "evidence": (
                f"path contains '{{session_dir}}' but session_dir is empty; "
                f"path={path_str!r} left unsubstituted"
            ),
        }
    return path_str.replace("{session_dir}", session_dir), None


def _get_binary_target_path(pending_entry):
    """For artifact-exists / artifact-min-size: target_artifact_path is the canonical
    binary-presence target for validator/CA recipes; falls back to output_contract.artifact_path
    for implementer/designer recipes that do not declare target_artifact_path."""
    top = pending_entry.get("target_artifact_path")
    if top:
        return top if '/' in top else ''
    contract = pending_entry.get("output_contract") or {}
    val = contract.get("artifact_path", "")
    return val if not val or '/' in val else ''


def _get_semantic_artifact_path(pending_entry):
    """For required-sections-present / claimed-grep-reproducible: output_contract.artifact_path
    is the canonical impl-report target for implementer recipes (where section checks belong);
    falls back to target_artifact_path for non-implementer paths."""
    contract = pending_entry.get("output_contract") or {}
    artifact_path = contract.get("artifact_path", "")
    if artifact_path:
        return artifact_path
    return pending_entry.get("target_artifact_path") or ""


def _get_sidecar_path(pending_entry):
    """Resolve the dispatch's declared sidecar path, accepting either shape.

    Two dispatch shapes coexist in the system:
      - output_contract.sidecar_path  — used by verdict-emitting agents
      - top-level sidecar_path        — used by cycling-promoter and similar

    Top-level wins when both present.
    Returns '' when neither is declared.
    """
    top = pending_entry.get("sidecar_path")
    if top:
        return top if '/' in top else ''
    contract = pending_entry.get("output_contract") or {}
    val = contract.get("sidecar_path", "")
    return val if not val or '/' in val else ''


def _resolve_sidecar_for_read(pending_entry, cwd):
    """Resolve, canonicalize, and read the JSON sidecar with robustness measures.

    Steps:
      1. Get declared sidecar path (raw).
      2. Make absolute (join cwd when relative).
      3. realpath-canonicalize so symlink-traversing session paths resolve to
         their physical target (fixes #5, #14).
      4. .json-preference: if the resolved path ends in .md AND a .json sibling
         exists, switch to the .json sibling (fixes #1, #2, #13).
         Rationale: verdict-bearing artifacts are always .json; the .md is a
         human-readable sibling. Guard: sibling must exist before switching.
      5. retry-on-empty: up to 3 attempts with 100ms backoff when the file
         stat's as 0 bytes or read returns empty (fixes #9, #10, #12, #15a).

    Returns (resolved_abs_path, read_text_or_None, status_hint) where
    status_hint is one of: 'ok', 'not-declared', 'not-found', 'empty-after-retry'.
    """
    raw = _get_sidecar_path(pending_entry)
    if not raw:
        return ("", None, "not-declared")

    full = raw if os.path.isabs(raw) else os.path.join(cwd, raw)

    # Step 3: realpath so symlinks to session dirs resolve to physical location
    full = os.path.realpath(full)

    # Step 4: prefer .json sibling over declared .md path
    if full.endswith(".md"):
        json_sibling = full[:-3] + ".json"
        if os.path.exists(json_sibling):
            full = json_sibling

    if not os.path.exists(full):
        return (full, None, "not-found")

    # Step 5: retry-on-empty for write-race mitigation
    # Cap: 3 × 100ms = 300ms max additional latency on a genuinely-missing file
    for attempt in range(3):
        try:
            size = os.path.getsize(full)
            if size > 0:
                with open(full, "r") as f:
                    text = f.read()
                if text.strip():
                    return (full, text, "ok")
        except (OSError, IOError):
            return (full, None, "not-found")
        if attempt < 2:
            time.sleep(0.1)

    return (full, None, "empty-after-retry")


def _returned_text_as_string(returned_text):
    if isinstance(returned_text, str):
        return returned_text
    return ""


def extract_peer_review_disposition(subagent_type, returned_text):
    """Extract the peer-review disposition enum by literal token match only."""
    if subagent_type not in PEER_REVIEW_DISPOSITION_PRODUCERS:
        return "not-applicable"
    text = _returned_text_as_string(returned_text)
    if PEER_REVIEW_APPLIES_TOKEN in text:
        return "applies"
    if PEER_REVIEW_SKIPPED_TOKEN in text:
        return "skipped"
    return "missing"


def extract_completeness_disposition(subagent_type, returned_text):
    """Extract the completeness disposition enum by literal token-prefix match only."""
    if subagent_type not in COMPLETENESS_DISPOSITION_PRODUCERS:
        return "not-applicable"
    text = _returned_text_as_string(returned_text)
    if COMPLETENESS_SELF_FLAG_TOKEN in text:
        return "self-flag"
    if COMPLETENESS_NONE_TOKEN in text:
        return "none"
    return "missing"


def extract_dispositions(subagent_type, returned_text):
    return {
        "peer_review_disposition": extract_peer_review_disposition(subagent_type, returned_text),
        "completeness_disposition": extract_completeness_disposition(subagent_type, returned_text),
    }

# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def run_check_peer_review_disposition_present(pending_entry, cwd, returned_text="", subagent_type=""):
    """Check for the literal peer-review disposition token in the producer return."""
    del pending_entry, cwd
    disposition = extract_peer_review_disposition(subagent_type, returned_text)
    if disposition == "applies":
        return {
            "name": "peer-review-disposition-present",
            "status": "warn",
            "evidence": f"{PEER_REVIEW_APPLIES_TOKEN} found. ACTION: peer-review due",
        }
    if disposition == "skipped":
        return {
            "name": "peer-review-disposition-present",
            "status": "pass",
            "evidence": f"{PEER_REVIEW_SKIPPED_TOKEN} token found",
        }
    if disposition == "missing":
        return {
            "name": "peer-review-disposition-present",
            "status": "warn",
            "evidence": f"missing peer-review disposition token; expected {PEER_REVIEW_APPLIES_TOKEN!r} or {PEER_REVIEW_SKIPPED_TOKEN!r}",
        }
    return {
        "name": "peer-review-disposition-present",
        "status": "pass",
        "evidence": "subagent not in peer-review disposition producer set",
    }


def run_check_completeness_disposition_present(pending_entry, cwd, returned_text="", subagent_type=""):
    """Check for the literal completeness disposition token in the producer return."""
    del pending_entry, cwd
    disposition = extract_completeness_disposition(subagent_type, returned_text)
    if disposition == "none":
        return {
            "name": "completeness-disposition-present",
            "status": "pass",
            "evidence": f"{COMPLETENESS_NONE_TOKEN} token found",
        }
    if disposition == "self-flag":
        return {
            "name": "completeness-disposition-present",
            "status": "pass",
            "evidence": f"{COMPLETENESS_SELF_FLAG_TOKEN} token found",
        }
    if disposition == "missing":
        return {
            "name": "completeness-disposition-present",
            "status": "warn",
            "evidence": f"missing completeness disposition token; expected {COMPLETENESS_NONE_TOKEN!r} or {COMPLETENESS_SELF_FLAG_TOKEN!r}",
        }
    return {
        "name": "completeness-disposition-present",
        "status": "pass",
        "evidence": "subagent not in completeness disposition producer set",
    }

def run_check_artifact_exists(pending_entry, cwd):
    """Check that the artifact file exists on disk."""
    artifact_path = _get_binary_target_path(pending_entry)
    if not artifact_path:
        return {"name": "artifact-exists", "status": "advisory", "evidence": "artifact_path/target_artifact_path not in delegation prompt — dispatch did not declare this field, verification skipped"}
    full = artifact_path if os.path.isabs(artifact_path) else os.path.join(cwd, artifact_path)
    if os.path.exists(full):
        return {"name": "artifact-exists", "status": "pass", "evidence": f"stat {full} = ok"}
    return {"name": "artifact-exists", "status": "fail", "evidence": f"stat {full} = missing"}


def run_check_artifact_min_size(pending_entry, cwd, min_bytes=200):
    """Check that the artifact file is >= min_bytes."""
    artifact_path = _get_binary_target_path(pending_entry)
    if not artifact_path:
        return {"name": "artifact-min-size", "status": "advisory", "evidence": "artifact_path/target_artifact_path not in delegation prompt — dispatch did not declare this field, verification skipped"}
    full = artifact_path if os.path.isabs(artifact_path) else os.path.join(cwd, artifact_path)
    try:
        size = os.path.getsize(full)
        if size >= min_bytes:
            return {"name": "artifact-min-size", "status": "pass", "evidence": f"size={size} bytes"}
        return {"name": "artifact-min-size", "status": "warn", "evidence": f"size={size} bytes < {min_bytes} threshold"}
    except (OSError, IOError):
        return {"name": "artifact-min-size", "status": "warn", "evidence": f"could not stat {full}"}


# Recipes whose impl-report sidecar carries the required sections, not the
# rewritten artifact body. For these recipes, required-sections-present must
# read sidecar_path (the impl-report) rather than output_contract.artifact_path
# (the rewritten agent body or code file). Fixes #7, #15b.
_IMPL_REPORT_SIDECAR_RECIPES = {"agent-content-author", "implementer"}


def _get_sections_target_path(pending_entry):
    """Return the path that required-sections-present should read.

    For impl-report-bearing recipes, prefer the declared sidecar_path (the
    impl-report) over output_contract.artifact_path (the rewritten body).
    For all other recipes, fall through to the standard semantic target.
    """
    recipe = pending_entry.get("subagent_type", "")
    if recipe in _IMPL_REPORT_SIDECAR_RECIPES:
        sidecar = _get_sidecar_path(pending_entry)
        if sidecar:
            return sidecar
    return _get_semantic_artifact_path(pending_entry)


def run_check_required_sections_present(pending_entry, cwd):
    """Check that required H2 sections appear in the artifact.

    For impl-report-bearing recipes (agent-content-author, implementer), reads
    the sidecar_path (impl-report) not output_contract.artifact_path (the
    rewritten body) so section checks land on the right file (fixes #7, #15b).
    """
    contract = pending_entry.get("output_contract") or {}
    artifact_path = _get_sections_target_path(pending_entry)
    required = pending_entry.get("required_sections") or contract.get("required_sections") or []
    if not required:
        return {"name": "required-sections-present", "status": "pass", "evidence": "no required_sections declared"}
    if not artifact_path:
        return {"name": "required-sections-present", "status": "advisory", "evidence": "artifact_path/target_artifact_path not in delegation prompt — dispatch did not declare this field, verification skipped"}
    full = artifact_path if os.path.isabs(artifact_path) else os.path.join(cwd, artifact_path)
    try:
        with open(full, "r", errors="replace") as f:
            content = f.read()
    except (IOError, OSError):
        return {"name": "required-sections-present", "status": "warn", "evidence": f"could not read {full}"}
    missing = []
    for section in required:
        pattern = re.compile(r"^##\s+" + re.escape(section), re.MULTILINE | re.IGNORECASE)
        if not pattern.search(content):
            missing.append(section)
    if missing:
        return {"name": "required-sections-present", "status": "warn",
                "evidence": f"missing sections: {missing}"}
    return {"name": "required-sections-present", "status": "pass",
            "evidence": f"all {len(required)} required sections found"}


def run_check_sidecar_exists(pending_entry, cwd):
    """Check that the sidecar file exists on disk.

    Uses _resolve_sidecar_for_read so symlink paths resolve to physical targets
    and .json siblings are preferred over declared .md paths (fixes #1, #5, #14).
    """
    resolved_path, text, hint = _resolve_sidecar_for_read(pending_entry, cwd)
    if hint == "not-declared":
        return {"name": "sidecar-exists", "status": "advisory",
                "evidence": "sidecar_path not declared at top level or in output_contract — dispatch did not declare this field, verification skipped"}
    if hint in ("not-found", "empty-after-retry"):
        return {"name": "sidecar-exists", "status": "fail", "evidence": f"stat {resolved_path} = missing"}
    return {"name": "sidecar-exists", "status": "pass", "evidence": f"stat {resolved_path} = ok"}


def run_check_sidecar_parses_as_json(pending_entry, cwd):
    """Check that the sidecar file is valid JSON.

    Uses _resolve_sidecar_for_read for realpath, .json-preference, and
    retry-on-empty (fixes #1, #2, #5, #9, #10, #12, #13, #14, #15a).
    """
    resolved_path, text, hint = _resolve_sidecar_for_read(pending_entry, cwd)
    if hint == "not-declared":
        return {"name": "sidecar-parses-as-json", "status": "advisory",
                "evidence": "sidecar_path not declared at top level or in output_contract — dispatch did not declare this field, verification skipped"}
    if hint == "not-found":
        return {"name": "sidecar-parses-as-json", "status": "fail",
                "evidence": f"read error: file not found: {resolved_path}"}
    if hint == "empty-after-retry":
        return {"name": "sidecar-parses-as-json", "status": "fail",
                "evidence": f"sidecar empty after 3 read attempts (write-race): {resolved_path}"}
    try:
        json.loads(text)
        return {"name": "sidecar-parses-as-json", "status": "pass", "evidence": f"json.load({resolved_path}) ok"}
    except (json.JSONDecodeError, ValueError) as e:
        return {"name": "sidecar-parses-as-json", "status": "fail", "evidence": f"json parse error: {e}"}


def run_check_verdict_enum_valid(pending_entry, cwd):
    """Check that the sidecar's verdict field is in the valid enum.

    Uses _resolve_sidecar_for_read so the same resolved file used by the parse
    check is read here (fixes the file-selection half of #9).
    """
    resolved_path, text, hint = _resolve_sidecar_for_read(pending_entry, cwd)
    if hint == "not-declared":
        return {"name": "verdict-enum-valid", "status": "advisory",
                "evidence": "sidecar_path not declared at top level or in output_contract — dispatch did not declare this field, verification skipped"}
    if hint in ("not-found", "empty-after-retry"):
        return {"name": "verdict-enum-valid", "status": "fail",
                "evidence": f"could not read sidecar: {resolved_path} ({hint})"}
    try:
        data = json.loads(text)
        verdict = data.get("verdict", "")
        if verdict in VALID_VERDICT_ENUM:
            return {"name": "verdict-enum-valid", "status": "pass",
                    "evidence": f"verdict={verdict!r} in {VALID_VERDICT_ENUM}"}
        return {"name": "verdict-enum-valid", "status": "fail",
                "evidence": f"verdict={verdict!r} not in {VALID_VERDICT_ENUM}"}
    except (json.JSONDecodeError, ValueError) as e:
        return {"name": "verdict-enum-valid", "status": "fail", "evidence": f"could not parse sidecar: {e}"}


def run_check_min_word_count(pending_entry, cwd, min_words=200):
    """Check that the artifact has >= min_words words."""
    contract = pending_entry.get("output_contract") or {}
    artifact_path = contract.get("artifact_path", "")
    if not artifact_path:
        return {"name": "min-word-count", "status": "advisory", "evidence": "artifact_path not in output_contract — dispatch did not declare this field, verification skipped"}
    full = artifact_path if os.path.isabs(artifact_path) else os.path.join(cwd, artifact_path)
    try:
        with open(full, "r", errors="replace") as f:
            content = f.read()
        word_count = len(content.split())
        if word_count >= min_words:
            return {"name": "min-word-count", "status": "pass", "evidence": f"word_count={word_count}"}
        return {"name": "min-word-count", "status": "warn",
                "evidence": f"word_count={word_count} < {min_words} floor"}
    except (IOError, OSError):
        return {"name": "min-word-count", "status": "warn", "evidence": f"could not read {full}"}


def _run_git_diff_probes(artifact_path, cwd):
    """Run the three git diff/ls-files probes for a given artifact_path and cwd.

    Returns a result dict with name='git-diff-nonempty' on any non-empty output,
    or None if all probes return empty (caller decides fail/warn).
    """
    result = subprocess.run(
        ["git", "diff", "HEAD", "--stat", "--", artifact_path],
        capture_output=True, text=True, cwd=cwd, timeout=15
    )
    diff_output = result.stdout.strip()
    if diff_output:
        return {"name": "git-diff-nonempty", "status": "pass",
                "evidence": f"git diff HEAD --stat: {diff_output[:200]}"}
    result2 = subprocess.run(
        ["git", "diff", "--stat", "--", artifact_path],
        capture_output=True, text=True, cwd=cwd, timeout=15
    )
    diff_output2 = result2.stdout.strip()
    if diff_output2:
        return {"name": "git-diff-nonempty", "status": "pass",
                "evidence": f"git diff --stat: {diff_output2[:200]}"}
    result3 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", artifact_path],
        capture_output=True, text=True, cwd=cwd, timeout=15
    )
    if result3.stdout.strip():
        return {"name": "git-diff-nonempty", "status": "pass",
                "evidence": f"new untracked file: {artifact_path}"}
    return None


def _resolve_artifact_worktree_toplevel(abs_artifact):
    """Resolve the git worktree toplevel that owns abs_artifact.

    Uses `git -C <dir> rev-parse --show-toplevel` against the directory of the
    realpath'd artifact. Handles both symlinked ancestors and non-symlinked
    worktree files when hook cwd is main (c2 fix, #4, #14).

    Returns (toplevel_str, rel_artifact_path, hint) where hint is one of:
      'ok'             -- resolved successfully
      'broken-symlink' -- realpath traversed a broken symlink (target dir absent)
      'not-in-git'     -- directory exists but is not under a git repo
      'relpath-error'  -- cannot compute relpath
    On hint != 'ok', toplevel and rel_path are None.
    """
    real_artifact = os.path.realpath(abs_artifact)
    artifact_dir = os.path.dirname(real_artifact)
    if not os.path.isdir(artifact_dir):
        # realpath resolved but parent dir does not exist => broken symlink ancestor
        return None, None, "broken-symlink"
    git_top_result = subprocess.run(
        ["git", "-C", artifact_dir, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=10
    )
    if git_top_result.returncode != 0 or not git_top_result.stdout.strip():
        return None, None, "not-in-git"
    toplevel = git_top_result.stdout.strip()
    try:
        rel_path = os.path.relpath(real_artifact, toplevel)
    except ValueError:
        return None, None, "relpath-error"
    return toplevel, rel_path, "ok"


def run_check_git_diff_nonempty(pending_entry, cwd):
    """Check that the subagent actually changed files (git diff not empty).

    Resolution order:
      1. c2 (#4, #14): resolve the owning worktree toplevel from the artifact's
         own directory. This handles both symlinked ancestors AND non-symlinked
         worktree files when hook cwd is main.
      2. c4 (#6): when success_criteria names a removal target AND that path is
         now absent on disk, return pass. Git cannot see untracked-dir removal;
         intent is the only signal.
      3. c5 (#11): when deliverable_profile=remote-push is declared AND
         result.json carries branch_sha or tag, return pass.
      4. Fallback: probe using hook's cwd (original behaviour).
    """
    contract = pending_entry.get("output_contract") or {}
    artifact_path = contract.get("artifact_path", "")
    if not artifact_path:
        return {"name": "git-diff-nonempty", "status": "advisory", "evidence": "artifact_path not in output_contract — dispatch did not declare this field, verification skipped"}
    try:
        abs_artifact = artifact_path if os.path.isabs(artifact_path) else os.path.join(cwd, artifact_path)

        # c4: untracked-dir removal — gated on explicit success_criteria intent
        # because git status cannot see untracked-dir removal at all (#6)
        success_criteria = pending_entry.get("success_criteria") or []
        if isinstance(success_criteria, list):
            removal_verbs = {"remov", "delet", "clean", "purg"}
            for criterion in success_criteria:
                if not isinstance(criterion, str):
                    continue
                lower = criterion.lower()
                if any(v in lower for v in removal_verbs):
                    for word in criterion.split():
                        if "/" in word or word.endswith(".md") or word.endswith(".py"):
                            candidate = word.strip(".,;\"'`")
                            check_path = candidate if os.path.isabs(candidate) else os.path.join(cwd, candidate)
                            if not os.path.exists(check_path):
                                return {"name": "git-diff-nonempty", "status": "pass",
                                        "evidence": f"named path {candidate!r} removed per success_criteria (untracked-dir removal evidence)"}

        # c5: remote-push profile — gated on declared profile + result.json evidence (#11)
        deliverable_profile = pending_entry.get("deliverable_profile", "")
        if deliverable_profile == "remote-push":
            result_json_path = contract.get("result_json_path", "")
            if result_json_path:
                full_result = result_json_path if os.path.isabs(result_json_path) else os.path.join(cwd, result_json_path)
                try:
                    with open(full_result, "r") as f:
                        result_data = json.load(f)
                    if result_data.get("branch_sha") or result_data.get("tag"):
                        return {"name": "git-diff-nonempty", "status": "pass",
                                "evidence": f"remote-push profile: branch_sha={result_data.get('branch_sha')!r} tag={result_data.get('tag')!r}"}
                except (IOError, OSError, json.JSONDecodeError):
                    pass  # Fall through to standard probes

        # c2: resolve the owning worktree toplevel from the artifact's directory
        toplevel, rel_path, resolve_hint = _resolve_artifact_worktree_toplevel(abs_artifact)
        if resolve_hint == "broken-symlink":
            # Broken symlink ancestor: cannot probe git at all — warn, not fail
            return {"name": "git-diff-nonempty", "status": "warn",
                    "evidence": "git diff error: realpath outside git tree"}
        if toplevel is not None:
            probe_result = _run_git_diff_probes(rel_path, toplevel)
            if probe_result is not None:
                return probe_result

        # Fallback: probe using the hook's cwd (preserves original behaviour
        # when the artifact is not under a git repo reachable from its own dir)
        local_result = _run_git_diff_probes(artifact_path, cwd)
        if local_result is not None:
            return local_result

        return {"name": "git-diff-nonempty", "status": "fail",
                "evidence": "git diff HEAD = empty; no staged or unstaged changes detected"}
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return {"name": "git-diff-nonempty", "status": "warn", "evidence": f"git diff error: {e}"}

def run_check_gateway_compliance(pending_entry, cwd, session_id=""):
    """Check that knowledge writes claimed in impl report appear in the change-log."""
    claims = pending_entry.get("claims", {})
    knowledge_writes_claimed = claims.get("knowledge_writes_count", 0)
    if not knowledge_writes_claimed:
        return {"name": "gateway-compliance", "status": "pass",
                "evidence": "no knowledge writes claimed in impl report"}
    start_ts = pending_entry.get("start_ts", "")
    agent_id = pending_entry.get("agent_id", "")
    change_log_path = os.path.join(cwd, ".claude", "knowledge-log", ".change-log.jsonl")
    try:
        found_count = 0
        with open(change_log_path, "r", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if start_ts and entry.get("ts", "") < start_ts:
                        continue
                    if (agent_id and entry.get("actor", "").endswith(agent_id)
                            or entry.get("actor", "").startswith("agent:")):
                        file_path = entry.get("file", "")
                        if file_path.startswith(".claude/knowledge/"):
                            found_count += 1
                except (json.JSONDecodeError, ValueError):
                    continue
        if found_count >= knowledge_writes_claimed:
            return {"name": "gateway-compliance", "status": "pass",
                    "evidence": f"change-log shows {found_count} entries >= claimed {knowledge_writes_claimed}"}
        return {"name": "gateway-compliance", "status": "fail",
                "evidence": f"claimed {knowledge_writes_claimed} knowledge writes; change-log shows {found_count} entries since {start_ts}"}
    except (IOError, OSError):
        return {"name": "gateway-compliance", "status": "warn",
                "evidence": "change-log not readable; skipping gateway compliance check"}


def run_check_claimed_grep_reproducible(pending_entry, cwd):
    """Spot-check that grep-output blocks in the impl report are reproducible."""
    artifact_path = _get_semantic_artifact_path(pending_entry)
    if not artifact_path:
        return {"name": "claimed-grep-reproducible", "status": "advisory",
                "evidence": "artifact_path/target_artifact_path not in delegation prompt — dispatch did not declare this field, verification skipped"}
    full = artifact_path if os.path.isabs(artifact_path) else os.path.join(cwd, artifact_path)
    try:
        with open(full, "r", errors="replace") as f:
            content = f.read()
    except (IOError, OSError):
        return {"name": "claimed-grep-reproducible", "status": "warn",
                "evidence": f"could not read artifact {full}"}
    grep_pattern = re.findall(r'`grep[^`]+`', content)
    if not grep_pattern:
        return {"name": "claimed-grep-reproducible", "status": "pass",
                "evidence": "no inline grep commands found in artifact"}
    cmd_str = grep_pattern[0].strip("`").strip()
    try:
        result = subprocess.run(
            cmd_str, shell=True, capture_output=True, text=True, cwd=cwd, timeout=10
        )
        if result.returncode == 0:
            return {"name": "claimed-grep-reproducible", "status": "pass",
                    "evidence": f"grep reproduced: {cmd_str[:100]}"}
        return {"name": "claimed-grep-reproducible", "status": "warn",
                "evidence": f"grep returned non-zero for: {cmd_str[:100]}"}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"name": "claimed-grep-reproducible", "status": "warn",
                "evidence": f"could not run grep: {e}"}


def run_check_citations_resolve(pending_entry, cwd):
    """Check that [verified: file:line] citations in the sidecar point to existing files."""
    sidecar_path = _get_sidecar_path(pending_entry)
    if not sidecar_path:
        return {"name": "citations-resolve", "status": "warn",
                "evidence": "sidecar_path not declared at top level or in output_contract"}
    full = sidecar_path if os.path.isabs(sidecar_path) else os.path.join(cwd, sidecar_path)
    try:
        with open(full, "r", errors="replace") as f:
            content = f.read()
    except (IOError, OSError):
        return {"name": "citations-resolve", "status": "warn", "evidence": f"could not read sidecar {full}"}
    citations = re.findall(r'\[verified:\s*([^\]]+)\]', content)
    if not citations:
        return {"name": "citations-resolve", "status": "pass", "evidence": "no [verified: ...] citations found"}
    missing = []
    for citation in citations:
        citation = citation.strip()
        file_part = citation.split()[0] if citation.split() else ""
        file_part = re.split(r':\d+', file_part)[0].strip()
        if not file_part or file_part.startswith("observed") or file_part.startswith("http"):
            continue
        file_full = file_part if os.path.isabs(file_part) else os.path.join(cwd, file_part)
        if not os.path.exists(file_full):
            missing.append(file_part)
    if missing:
        return {"name": "citations-resolve", "status": "warn",
                "evidence": f"missing cited files: {missing[:5]}"}
    return {"name": "citations-resolve", "status": "pass",
            "evidence": f"all {len(citations)} citations resolve"}

# ---------------------------------------------------------------------------
# Verdict computation
# ---------------------------------------------------------------------------

def compute_verdict(checks):
    """Compute overall verdict from list of check results.

    Tier rule: any fail → fail; any warn → warn; advisory-only → pass.

    Returns (verdict, failures, warnings, advisories, verdict_reason).
    """
    failures = [{"name": c["name"], "evidence": c.get("evidence", "")}
                for c in checks if c["status"] == "fail"]
    warnings = [{"name": c["name"], "evidence": c.get("evidence", "")}
                for c in checks if c["status"] == "warn"]
    advisories = [{"name": c["name"], "evidence": c.get("evidence", "")}
                  for c in checks if c["status"] == "advisory"]
    if failures:
        return "fail", failures, warnings, advisories, failures[0]["name"]
    if warnings:
        return "warn", failures, warnings, advisories, warnings[0]["name"]
    return "pass", [], [], advisories, "ok"

# ---------------------------------------------------------------------------
# MCP-audit check helpers (artifact-3b, C6/C7/C8/C9)
# ---------------------------------------------------------------------------

def _repo_root(cwd):
    """Return the repository root. cwd is the worktree root (passed by runRecipeChecks);
    for this project they are identical. Named helper for intent clarity."""
    return cwd


def _read_jsonl(path):
    """Read a JSONL file; return a list of decoded dicts. Silently skips
    malformed lines (partial JSON from an in-flight grandchild MCP-server flush
    is not an error)."""
    records = []
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # partial flush or non-JSON diagnostic line
    except (IOError, OSError):
        pass
    return records


def _await_audit_file_stable(audit_file, *, interval_s=0.25, max_wait_s=2.0):
    """Poll until the grandchild MCP server stops appending to audit_file — i.e.
    the file's (size, mtime_ns) is unchanged across one full poll interval — or
    max_wait_s elapses (then proceed with on-disk content, accepted residual).

    Family-agnostic: needs NO grandchild PID. Bounded so the worker never hangs.
    max_wait_s=2.0 is well below the invokeHookWorker IPC timeout of 120 s
    (dispatch-agent.ts:1185-1189). See design § Open Question C (Y_1)."""
    deadline = time.monotonic() + max_wait_s
    try:
        st = os.stat(audit_file)
        prev_sig = (st.st_size, st.st_mtime_ns)
    except OSError:
        return  # caller's existence guard owns absence
    while time.monotonic() < deadline:
        time.sleep(interval_s)
        try:
            st = os.stat(audit_file)
        except OSError:
            return
        cur_sig = (st.st_size, st.st_mtime_ns)
        if cur_sig == prev_sig:
            return  # one quiet interval → flush settled
        prev_sig = cur_sig
    # hard bound hit: proceed with whatever is on disk


def _is_mcp_tool(name):
    """Return True when name identifies an MCP tool (context-tools server).

    Two calling contexts:
    - agent frontmatter tools: list → canonical form 'mcp__context-tools__smart_grep'
      OR platform tool names like 'WebSearch', 'Skill', 'Bash' (CapitalCase).
    - auto-events.jsonl 'tool' field → short snake_case names like 'smart_grep',
      'knowledge' (all are context-tools MCP calls; first char is lowercase).

    Rule: names that start with 'mcp__' are always MCP; names whose first char is
    lowercase are short MCP names from the audit log; names starting with uppercase
    are platform tools (Read, Write, Bash, WebSearch, Skill, …) and are NOT MCP."""
    if not name:
        return False
    if name.startswith("mcp__"):
        return True
    # Short-form MCP names (from auto-events.jsonl) are snake_case.
    # Platform tool names are CamelCase — reject them.
    return name[0].islower()


def _normalize_tool_name(name):
    """Strip the 'mcp__<server>__' prefix so frontmatter names and audit-log
    short names both reduce to the same short form (e.g. 'smart_grep')."""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return name


def _parse_frontmatter_tools_list(agent_md):
    """Extract the 'tools:' YAML list from a .claude/agents/*.md frontmatter.

    Returns [] (never None) for a tools-less or unreadable agent file.
    Mirrors the logic of parseFrontmatterToolsList in dispatch-agent.ts:2476."""
    try:
        with open(agent_md, "r", errors="replace") as f:
            content = f.read()
    except (IOError, OSError):
        return []
    if not content.startswith("---\n"):
        return []
    closing = content.find("\n---\n", 4)
    raw_fm = (
        content[4:closing] if closing != -1
        else (content[4:-4] if content.endswith("\n---") else "")
    )
    tools = []
    in_tools = False
    for line in raw_fm.split("\n"):
        if re.match(r"^tools:\s*$", line):
            in_tools = True
            continue
        if in_tools:
            m = re.match(r"^\s+-\s+(.+)$", line)
            if m:
                tools.append(m.group(1).strip())
            elif line and not line.startswith(" ") and not line.startswith("\t"):
                break  # new top-level key ends the tools section
    return tools


def _reconstruct_skill_granted_tools(audit_file):
    """Return tools granted by skills the child invoked (from audit log skill_invoke events).

    Returns [] (never None) per C8 helper contract. Skill-grant frontmatter
    surface not yet fully traced; conservative stub returns empty list. Any tool
    a child used that is in a skill grant but not in base tools: will show as a
    violation — treat as warn, not fail, for ambiguous grant cases."""
    # TODO(artifact-3b-followup): parse skill_invoke events from audit_file,
    # read each skill's frontmatter grant list, and return the union.
    return []


def check_mcp_audit_vs_declared_tools(pending_entry, cwd):
    """Post-hoc check: verify a gpt/gemini child only called MCP tools within its
    effective allowed set (base tools: ∪ skill-granted tools). Coverage scope:
    MCP-tool action class observable in auto-events.jsonl only (C8 — NOT a
    universal prevention claim; CLI-native fs/shell/network actions are invisible
    to the MCP server boundary). Fail-open: always returns pass when the audit
    file is absent or the policy cannot be resolved."""
    name = "mcp-audit-vs-declared-tools"
    audit_file = pending_entry.get("mcp_audit_file")
    subagent_type = pending_entry.get("subagent_type")
    model_route = pending_entry.get("model_route")

    # Fail-open guards — missing observation surface is never a blocker.
    if not audit_file or not os.path.exists(audit_file):
        return {"name": name, "status": "pass",
                "evidence": "no child audit file present; nothing to audit (fail-open)"}

    # Y_1: settle the grandchild MCP-server flush before reading (bounded).
    _await_audit_file_stable(audit_file)

    agent_md = os.path.join(_repo_root(cwd), ".claude", "agents", f"{subagent_type}.md")
    if not subagent_type or not os.path.exists(agent_md):
        return {"name": name, "status": "warn",
                "evidence": (f"cannot resolve policy for subagent_type={subagent_type!r} "
                             f"(agent file absent)")}

    # Effective-allowed scoped to MCP-tool namespace.
    # Both helpers return [] (never None) per C8 contract; the or [] coercion is
    # the defensive second line against a None regression (design FINDING 3).
    base_tools = _parse_frontmatter_tools_list(agent_md)
    skill_granted = _reconstruct_skill_granted_tools(audit_file)
    effective_mcp = {
        _normalize_tool_name(t)
        for t in (set(base_tools or []) | set(skill_granted or []))
        if _is_mcp_tool(t)
    }

    # Observed MCP tool calls in the child's audit log.
    observed_mcp = set()
    for rec in _read_jsonl(audit_file):
        tool = rec.get("tool")
        if tool and _is_mcp_tool(tool):
            observed_mcp.add(_normalize_tool_name(tool))

    # Diff: tools observed but not in effective-allowed.
    violations = sorted(observed_mcp - effective_mcp)
    if violations:
        return {"name": name, "status": "fail",
                "evidence": (f"child subagent_type={subagent_type} route={model_route} called "
                             f"out-of-policy MCP tools: {violations}; "
                             f"effective-allowed(MCP)={sorted(effective_mcp)}")}
    return {"name": name, "status": "pass",
            "evidence": (f"all {len(observed_mcp)} observed MCP tool(s) within "
                         f"effective-allowed for {subagent_type}")}

# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

CHEAP_CHECK_DISPATCH = {
    "artifact-exists": run_check_artifact_exists,
    "artifact-min-size": run_check_artifact_min_size,
    "required-sections-present": run_check_required_sections_present,
    "sidecar-exists": run_check_sidecar_exists,
    "sidecar-parses-as-json": run_check_sidecar_parses_as_json,
    "verdict-enum-valid": run_check_verdict_enum_valid,
    "min-word-count": run_check_min_word_count,
    "peer-review-disposition-present": run_check_peer_review_disposition_present,
    "completeness-disposition-present": run_check_completeness_disposition_present,
    "gateway-compliance": run_check_gateway_compliance,
    "mcp-audit-vs-declared-tools": check_mcp_audit_vs_declared_tools,
}

EXPENSIVE_CHECK_DISPATCH = {
    "git-diff-nonempty": run_check_git_diff_nonempty,
    "gateway-compliance": run_check_gateway_compliance,
    "claimed-grep-reproducible": run_check_claimed_grep_reproducible,
    "citations-resolve": run_check_citations_resolve,
}

# ---------------------------------------------------------------------------
# Redispatch hints
# ---------------------------------------------------------------------------

REDISPATCH_HINTS = {
    "artifact-exists": "Artifact missing from expected path; redispatch implementer with explicit path confirmation requirement",
    "artifact-min-size": "Artifact is suspiciously small; redispatch with explicit content-completeness requirement",
    "sidecar-exists": "Sidecar missing; agent did not write its verdict/sidecar file; redispatch with explicit sidecar-write requirement",
    "sidecar-parses-as-json": "Sidecar is malformed JSON; redispatch agent with explicit JSON-validity requirement",
    "verdict-enum-valid": "Verdict field out of enum range; redispatch agent with explicit verdict-enum constraint",
    "git-diff-nonempty": "Implementer report claims edits but git diff empty; redispatch with explicit closure-evidence requirement",
    "gateway-compliance": "Implementer claimed knowledge writes not found in change-log; redispatch with explicit gateway-route requirement",
    "pending-dict-miss-fallback": "PreToolUse hook missed this spawn; verify hook registration; re-run if needed",
    "output-contract-unparseable": "Output contract could not be parsed from delegation prompt; verify prompt schema",
    "unknown-subagent-type-fallback": "Subagent type not in recipe registry; check subagent_type field in delegation prompt",
    "platform-misroute": "Platform routed dispatch to general-purpose instead of the requested subagent_type; re-spawn the same subagent_type (cap: 3 retries per §B.5 handler before escalating to inline orchestrator action)",
}


def get_redispatch_hint(failures, verdict_reason):
    """Return a redispatch hint string for the given failures."""
    if not failures:
        return ""
    primary = failures[0]
    return REDISPATCH_HINTS.get(primary, f"Verification failed on {primary}; review sidecar failures[] for details")

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_recipe(pending_entry, cwd, subagent_type, returned_text=""):
    """Run checks for the given pending_entry using the subagent_type recipe.

    pending_entry must already have {session_dir} tokens substituted in all
    path-shaped fields before calling. The hook and runner both own the
    substitution step; this function treats paths as final.

    Returns a dict:
      {verdict, verdict_reason, checks, failures, warnings, advisories, redispatch_hint}
    """
    verdict_reason_prefix = ""

    # Route-alone gate (FINDING 1): gpt/gemini children run ONLY the MCP-audit
    # check and return early — they NEVER reach the per-agent claude-contract
    # recipes below. Campaign-live route set is {"gemini"} (gpt/codex is
    # operator-paused this campaign); "gpt" is listed at the route-class level
    # so a gpt extension is a clean follow-on with NO control-flow change.
    # The absent-mcp_audit_file case (Y_0f env-not-populated) is handled inside
    # check_mcp_audit_vs_declared_tools (fail-open to pass), so a route-set
    # child with no audit file passes cleanly rather than tripping claude
    # output-contract checks (which would produce spurious failures).
    route = pending_entry.get("model_route")
    if route in ("gemini", "gpt"):
        checks = [check_mcp_audit_vs_declared_tools(pending_entry, cwd)]
        verdict, failures, warnings, advisories, verdict_reason = compute_verdict(checks)
        return {
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "checks": checks,
            "failures": failures,
            "warnings": warnings,
            "advisories": advisories,
            "redispatch_hint": "",
            **extract_dispositions(subagent_type, returned_text),
        }

    # Determine recipe (claude / claude-subprocess path — byte-unchanged)
    if subagent_type in RECIPES:
        recipe = RECIPES[subagent_type]
    else:
        recipe = DEFAULT_FALLBACK
        verdict_reason_prefix = "unknown-subagent-type-fallback"

    disposition_checks = {
        "peer-review-disposition-present",
        "completeness-disposition-present",
    }

    # Run cheap checks
    checks = []
    for check_name in recipe.get("cheap", []):
        fn = CHEAP_CHECK_DISPATCH.get(check_name)
        if fn:
            if check_name in disposition_checks:
                checks.append(fn(pending_entry, cwd, returned_text, subagent_type))
            else:
                checks.append(fn(pending_entry, cwd))

    # Run expensive checks (always, even if cheap failed — full audit visibility)
    for check_name in recipe.get("expensive", []):
        fn = EXPENSIVE_CHECK_DISPATCH.get(check_name)
        if fn:
            checks.append(fn(pending_entry, cwd))

    verdict, failures, warnings, advisories, verdict_reason = compute_verdict(checks)

    # Apply prefix reason if fallback was triggered
    if verdict_reason_prefix:
        verdict_reason = verdict_reason_prefix

    failure_names = [f["name"] for f in failures]
    redispatch_hint = get_redispatch_hint(failure_names, verdict_reason) if verdict == "fail" else ""

    dispositions = extract_dispositions(subagent_type, returned_text)
    return {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "advisories": advisories,
        "redispatch_hint": redispatch_hint,
        **dispositions,
    }
