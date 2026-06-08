#!/usr/bin/env python3
"""Pre-launch validator for L2 child-dispatch manifests (child-profile.json).

Implements the v4 reduced check set: C1, C2, C4, C6, C7, C8, C10, C11, C12.
Writes a verdict sidecar JSON at the caller-supplied path.

Exit codes:
  0 — manifest passes ALL checks; sidecar written with verdict: pass
  1 — manifest fails >= 1 check; sidecar written with verdict: fail
  2 — invocation error (bad args, unparseable JSON, project root not found)

Canonical-JSON convention (C6):
  json.dumps(manifest_minus_hash, sort_keys=True, separators=(',', ':')).encode('utf-8')
  (no trailing newline; manifest_minus_hash is the full manifest dict with
   compose_metadata.manifest_hash_sha256 removed)
  The parent-side B1/D1 composer MUST use the same convention when writing
  the hash to the manifest.

Run examples:
  python3 bin/child_profile_validate.py \\
      --manifest-path /path/to/children/c-xxx/child-profile.json \\
      --out-sidecar-path /path/to/children/c-xxx/profile-validate.json

  python3 bin/child_profile_validate.py --self-test
  python3 bin/child_profile_validate.py --help
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# C1: known base-set values
_V4_BASE_SET_VALID: set[str] = {"full-install-v1"}

# C10: v1 max_depth cap
_V1_MAX_DEPTH_CAP: int = 1

# C11: valid family values (v4.2-compatible extension; dispatch mode only)
_FAMILY_VALID: frozenset[str] = frozenset({"claude", "codex", "gemini"})

# C12: valid capability token keys (v4.2-compatible extension; dispatch mode only).
# Source-of-truth: .claude/knowledge/reference/child-capabilities.md.
# Update that file AND this constant together when adding/removing capability tokens.
_CAPABILITIES_VALID: frozenset[str] = frozenset({
    "session.resume",
    "smart_read.sidecars",
    "events.jsonl.append",
    "skill.invoke",
    "monitor.parent_messages",
    "env.caa_child_sidecar_dir",
    "hook.posttooluse",
})

# C7: forbidden subjective words in completion criteria.
# Source: plan-quality-requirements.md (examples) — extend as new patterns surface.
_C7_FORBIDDEN_WORDS: frozenset[str] = frozenset(
    {"clean", "comprehensive", "appropriate", "robust", "good", "nice", "elegant"}
)

# C8: forbidden internal tokens in user_surface.elevator_pitch.
# Patterns inlined from scripts/check_episode_narrative_tokens.py (top ~90 lines).
# Source: §K language filter in orchestrator-prompt.md § User-facing language
# — translation discipline.
_C8_PATTERNS: list[tuple[re.Pattern[str], str]] = []
for _raw, _label in [
    (r"\bS\d{1,2}\b",                  "subtask-id"),
    (r"\bR\d{1,2}\b",                  "round-id"),
    (r"\bG-R\d+-V-\d+\b",              "gap-id"),
    (r"\b\S+\.\S+:\d+(-\d+)?\b",       "file-line-citation"),
    (r"\b[0-9a-f]{7,}\b",              "commit-sha"),
    (r"coherence-auditor",              "agent-name"),
    (r"pre-flight-gate",                "agent-name"),
    (r"solution-designer",              "agent-name"),
    (r"\barchitect\b",                  "agent-name"),
    (r"\bplanner\b",                    "agent-name"),
    (r"\bresearcher\b",                 "agent-name"),
    (r"\bimplementer\b",                "agent-name"),
    (r"\bvalidator\b",                  "agent-name"),
    (r"\bsynthesizer\b",                "agent-name"),
    (r"\bdiagnostician\b",              "agent-name"),
]:
    _C8_PATTERNS.append((re.compile(_raw), _label))

# C4: render-target agent files that must contain <!-- IF <name> --> markers.
# Source: bin/caa/launcher.py (_render_targets) — authoritative list.
# Matches: orchestrator-prompt.md + 5 agent files + dispatch-l2/SKILL.md (axis-7, CL-3).
_C4_RENDER_TARGET_RELPATHS: list[str] = [
    ".claude/orchestrator-prompt.md",
    ".claude/agents/planner.md",
    ".claude/agents/solution-designer.md",
    ".claude/agents/implementer.md",
    ".claude/agents/validator.md",
    ".claude/agents/pre-flight-gate.md",
    ".claude/skills/dispatch-l2/SKILL.md",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_root(start: Path) -> Path:
    """Walk up from start until we find a directory containing bin/claude-session."""
    for parent in [start, *start.parents]:
        if (parent / "bin" / "claude-session").exists():
            return parent
    raise FileNotFoundError(
        f"Could not locate project root (no bin/claude-session found) "
        f"searching upward from {start}"
    )


def _read_bootstrap_config(root: Path) -> dict:
    """Read and return .claude/bootstrap-config.json as a dict."""
    config_path = root / ".claude" / "bootstrap-config.json"
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _canonical_json_for_hash(manifest: dict) -> bytes:
    """Produce canonical JSON bytes for C6 hash comparison.

    Removes compose_metadata.manifest_hash_sha256, then:
      json.dumps(manifest_minus_hash, sort_keys=True, separators=(',', ':'))
    encoded as UTF-8. No trailing newline. This convention MUST match whatever
    the parent-side B1/D1 composer uses when writing the hash field.
    """
    manifest_copy = copy.deepcopy(manifest)
    manifest_copy.get("compose_metadata", {}).pop("manifest_hash_sha256", None)
    return json.dumps(manifest_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Failure record type
# ---------------------------------------------------------------------------

def _failure(check_id: str, detail: str) -> dict:
    return {"check_id": check_id, "detail": detail}


# ---------------------------------------------------------------------------
# Per-check functions
# ---------------------------------------------------------------------------

def _check_c1(manifest: dict) -> Optional[dict]:
    """C1: profile.base_set must be a known v1 value."""
    base_set = manifest.get("profile", {}).get("base_set")
    if base_set not in _V4_BASE_SET_VALID:
        valid_str = ", ".join(sorted(_V4_BASE_SET_VALID))
        return _failure("C1", f"profile.base_set is {base_set!r}; valid values: {valid_str}")
    return None


def _check_c2(manifest: dict, registry: list[str]) -> Optional[dict]:
    """C2: each declared pipeline must be in pipelines_available AND registry."""
    pipelines_available: list[str] = manifest.get("pipelines_available", [])
    declared: list[str] = manifest.get("profile", {}).get("additions", {}).get("pipelines", [])
    for p in declared:
        if p not in pipelines_available:
            return _failure(
                "C2",
                f"pipeline {p!r} declared in profile.additions.pipelines "
                f"but absent from pipelines_available"
            )
        if p not in registry:
            return _failure(
                "C2",
                f"pipeline {p!r} declared in profile.additions.pipelines "
                f"but absent from bootstrap-config.json:pipelines.registry"
            )
    return None


def _check_c4(manifest: dict, project_root: Path) -> Optional[dict]:
    """C4: each declared pipeline must have <!-- IF <name> --> marker in a render-target file."""
    declared: list[str] = manifest.get("profile", {}).get("additions", {}).get("pipelines", [])
    for p in declared:
        marker = f"<!-- IF {p} -->"
        found_in_any = False
        for relpath in _C4_RENDER_TARGET_RELPATHS:
            target_path = project_root / relpath
            if not target_path.exists():
                continue
            try:
                content = target_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if marker in content:
                found_in_any = True
                break
        if not found_in_any:
            targets_str = ", ".join(_C4_RENDER_TARGET_RELPATHS)
            return _failure(
                "C4",
                f"pipeline {p!r} has no '<!-- IF {p} -->' marker in any render-target "
                f"file ({targets_str})"
            )
    return None


def _check_c6(manifest: dict, raw_bytes: bytes) -> Optional[dict]:
    """C6: manifest_hash_sha256 must match SHA-256 over canonical JSON (minus hash field)."""
    stored_hash: str = manifest.get("compose_metadata", {}).get("manifest_hash_sha256", "")
    canonical = _canonical_json_for_hash(manifest)
    derived_hash = hashlib.sha256(canonical).hexdigest()
    if derived_hash != stored_hash:
        return _failure(
            "C6",
            f"manifest_hash_sha256 mismatch: stored={stored_hash!r}, "
            f"re-derived={derived_hash!r}"
        )
    return None


def _check_c7(manifest: dict) -> Optional[dict]:
    """C7: completion_criteria must not contain subjective language."""
    criteria: list[str] = manifest.get("task", {}).get("completion_criteria", [])
    for idx, criterion in enumerate(criteria):
        lower = criterion.lower()
        for word in _C7_FORBIDDEN_WORDS:
            if word in lower:
                return _failure(
                    "C7",
                    f"completion_criteria[{idx}] contains subjective word {word!r}: "
                    f"{criterion!r}"
                )
    return None


def _check_c8(manifest: dict) -> Optional[dict]:
    """C8: elevator_pitch must pass the §K language filter (no forbidden internal tokens)."""
    pitch: str = manifest.get("user_surface", {}).get("elevator_pitch", "")
    for pattern, label in _C8_PATTERNS:
        m = pattern.search(pitch)
        if m:
            return _failure(
                "C8",
                f"elevator_pitch contains forbidden token (category: {label!r}): "
                f"matched {m.group()!r}"
            )
    return None


def _check_c10(manifest: dict) -> Optional[dict]:
    """C10: depth <= max_depth AND max_depth <= v1 cap (1)."""
    depth = manifest.get("depth")
    max_depth = manifest.get("max_depth")
    if max_depth is None or max_depth > _V1_MAX_DEPTH_CAP:
        return _failure(
            "C10",
            f"max_depth is {max_depth!r}; v1 cap is {_V1_MAX_DEPTH_CAP}"
        )
    if depth is None or depth > max_depth:
        return _failure(
            "C10",
            f"depth ({depth!r}) exceeds max_depth ({max_depth!r})"
        )
    return None


def _check_c11(manifest: dict) -> Optional[dict]:
    """C11: family must be present and a member of {claude, codex, gemini}.

    Applies to dispatch mode only; skipped for dispatch-subprocess.
    v4.2-compatible extension per synthesis axis-2 OQ-1 resolution.
    """
    family = manifest.get("family")
    if family is None:
        return _failure("C11", "family field is absent; required for dispatch mode")
    if family not in _FAMILY_VALID:
        valid_str = ", ".join(sorted(_FAMILY_VALID))
        return _failure("C11", f"family is {family!r}; valid values: {valid_str}")
    return None


def _check_c12(manifest: dict) -> Optional[dict]:
    """C12: required_capabilities must be present, a list, and each entry a known token.

    Applies to dispatch mode only; skipped for dispatch-subprocess (mirrors C11).
    v4.2-compatible extension per synthesis axis-8 (c) OQ-1 resolution.
    Source-of-truth for valid tokens: .claude/knowledge/reference/child-capabilities.md.
    """
    caps = manifest.get("required_capabilities")
    if caps is None:
        return _failure("C12", "required_capabilities field is absent; required for dispatch mode")
    if not isinstance(caps, list):
        return _failure(
            "C12",
            f"required_capabilities must be a list; got {type(caps).__name__}",
        )
    unknown = [c for c in caps if c not in _CAPABILITIES_VALID]
    if unknown:
        valid_str = ", ".join(sorted(_CAPABILITIES_VALID))
        return _failure(
            "C12",
            f"required_capabilities contains unknown token(s): {unknown!r}; "
            f"valid values: {valid_str}",
        )
    return None


# ---------------------------------------------------------------------------
# Core: validate manifest
# ---------------------------------------------------------------------------

# Full v4 dispatch check set (C11, C12 are v4.2-compatible additions; dispatch mode only).
_CHECKS_DISPATCH = ["C1", "C2", "C4", "C6", "C7", "C8", "C10", "C11", "C12"]
# v4.1 reduced check set for mode="dispatch-subprocess".
# Omits C1/C2/C4/C8 (profile-pipeline-related, user-surface-related) — vacuous
# for a bounded single-shot subprocess. See decisions/dispatch-subprocess-mode-
# v4-1-validator-amendment.md.
_CHECKS_DISPATCH_SUBPROCESS = ["C6", "C7", "C10"]


def validate_manifest(
    manifest_path: Path,
    project_root: Path,
    registry_override: Optional[list[str]] = None,
    render_target_dir_override: Optional[Path] = None,
) -> dict:
    """Run checks against the manifest at manifest_path.

    For mode="dispatch-subprocess" (v4.1 amendment), runs only C6/C7/C10.
    For mode="dispatch" or absent (back-compat for v4 manifests), runs the
    full C1/C2/C4/C6/C7/C8/C10 set.

    Returns a verdict dict matching the sidecar JSON shape.
    registry_override and render_target_dir_override are for self-test use only.
    """
    raw_bytes = manifest_path.read_bytes()
    manifest = json.loads(raw_bytes)

    mode = manifest.get("mode", "dispatch")
    is_dispatch_subprocess = mode == "dispatch-subprocess"

    if is_dispatch_subprocess:
        # v4.1 reduced check set: C6/C7/C10 only.
        checks_run = _CHECKS_DISPATCH_SUBPROCESS
        failed_checks: list[dict] = []
        for check_fn, extra_args in [
            (_check_c6,  (manifest, raw_bytes)),
            (_check_c7,  (manifest,)),
            (_check_c10, (manifest,)),
        ]:
            result = check_fn(*extra_args)
            if result is not None:
                failed_checks.append(result)
    else:
        # Full v4 dispatch check set (mode="dispatch" or missing — back-compat).
        checks_run = _CHECKS_DISPATCH

        # Registry for C2
        if registry_override is not None:
            registry = registry_override
        else:
            bootstrap = _read_bootstrap_config(project_root)
            registry = bootstrap["pipelines"]["registry"]

        # For C4 render-target scanning, allow override of the base root
        c4_root = render_target_dir_override if render_target_dir_override is not None else project_root

        failed_checks = []
        for check_fn, extra_args in [
            (_check_c1,  (manifest,)),
            (_check_c2,  (manifest, registry)),
            (_check_c4,  (manifest, c4_root)),
            (_check_c6,  (manifest, raw_bytes)),
            (_check_c7,  (manifest,)),
            (_check_c8,  (manifest,)),
            (_check_c10, (manifest,)),
            (_check_c11, (manifest,)),
            (_check_c12, (manifest,)),
        ]:
            result = check_fn(*extra_args)
            if result is not None:
                failed_checks.append(result)

    child_id = manifest.get("child_id", "")
    verdict_str = "pass" if not failed_checks else "fail"

    return {
        "schema_version": "1",
        "manifest_path": str(manifest_path.resolve()),
        "child_id": child_id,
        "validated_at_ms": int(time.time() * 1000),
        "verdict": verdict_str,
        "failed_checks": failed_checks,
        "checks_run": checks_run,
    }


def write_sidecar(verdict: dict, out_sidecar_path: Path) -> None:
    """Write the verdict dict as JSON to out_sidecar_path."""
    out_sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with out_sidecar_path.open("w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Self-test fixtures and runner
# ---------------------------------------------------------------------------

def _make_good_manifest(pipeline_additions: list[str] | None = None) -> dict:
    """Return a minimal manifest that passes all checks."""
    additions = pipeline_additions if pipeline_additions is not None else []
    m: dict = {
        "schema_version": "4",
        "family": "claude",
        "required_capabilities": [],
        "child_id": "c-1778000000-00001-0001",
        "parent_session_id": "1778000000-00001-abc",
        "parent_session_dir": "/tmp/test/sessions/parent",
        "child_session_dir": "/tmp/test/sessions/child",
        "depth": 1,
        "max_depth": 1,
        "mode": "dispatch",
        "profile_name": "default",
        "delegate_authority_envelope": "all",
        "task": {
            "title": "test-task",
            "bounded_description": "A test task for the self-test fixture.",
            "completion_criteria": ["bin/foo.py exits 0", "output file exists"],
            "deadline_hint": "none",
            "round_budget_hint": "3",
            "requires_user_confirmation_for_kappa_care": False,
            "kappa_care_rationale": None,
            "return_contract": {
                "artifact_path": "/tmp/test/out.md",
                "sidecar_path": "/tmp/test/out-verdict.json",
                "required_sections": ["Results"],
            },
        },
        "profile": {
            "base_set": "full-install-v1",
            "additions": {"pipelines": additions},
        },
        "pipelines_available": ["ux", "hygiene", "bootstrap", "asset"],
        "ipc": {
            "events_path": "/tmp/test/sessions/child/events.jsonl",
            "parent_messages_path": "/tmp/test/sessions/parent/parent-messages/c-001/parent-messages.jsonl",
        },
        "lifecycle": {"max_episodes": 10, "cycle_authority": "self"},
        "phase_checkpoint": {
            "default_on": True,
            "opt_out_for_this_dispatch": False,
            "opt_out_rationale": None,
        },
        "user_surface": {
            "elevator_pitch": "Adding a cache layer to the knowledge read path.",
            "raise_hand_translation_template": "Task needs your input: {question}. Reply with: {response_shape_hint}.",
        },
        "compose_metadata": {
            "composed_by_parent_session_id": "1778000000-00001-abc",
            "composed_at_ms": 1715123456789,
            "manifest_hash_sha256": "",  # filled below
        },
    }
    # Compute correct hash so C6 passes
    canonical = _canonical_json_for_hash(m)
    m["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(canonical).hexdigest()
    return m


def _run_self_test() -> int:
    """Run inline fixtures through validate_manifest(). Returns 0 on pass, 2 on failure."""
    failures = 0

    fake_registry = ["ux", "hygiene", "bootstrap", "asset"]

    def _run_case(
        label: str,
        manifest: dict,
        expected_check_id: Optional[str],
        *,
        registry: list[str] = fake_registry,
        render_dir: Optional[Path] = None,
    ) -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mf_path = tmp_path / "child-profile.json"
            mf_path.write_text(json.dumps(manifest), encoding="utf-8")

            # Use provided render_dir or create empty one (no IF markers → any pipeline fails C4)
            effective_render_dir = render_dir if render_dir is not None else tmp_path

            verdict = validate_manifest(
                manifest_path=mf_path,
                project_root=tmp_path,  # no real bootstrap-config needed; registry_override used
                registry_override=registry,
                render_target_dir_override=effective_render_dir,
            )

        failed_ids = [f["check_id"] for f in verdict["failed_checks"]]
        if expected_check_id is None:
            # Expect clean pass
            if verdict["verdict"] != "pass":
                print(
                    f"  FAIL [{label}]: expected pass but got fail; "
                    f"failed_checks={failed_ids}",
                    file=sys.stderr,
                )
                failures += 1
            else:
                print(f"  PASS [{label}]", file=sys.stderr)
        else:
            if expected_check_id not in failed_ids:
                print(
                    f"  FAIL [{label}]: expected {expected_check_id} to fail "
                    f"but got failed_checks={failed_ids}",
                    file=sys.stderr,
                )
                failures += 1
            else:
                print(f"  PASS [{label}]", file=sys.stderr)

    # --- Fixture 0: known-good manifest (no pipelines, so C4 vacuously passes) ---
    good = _make_good_manifest(pipeline_additions=[])
    _run_case("good manifest", good, expected_check_id=None)

    # --- Fixture C1: bad base_set ---
    bad_c1 = _make_good_manifest()
    bad_c1["profile"]["base_set"] = "full-install-v99"
    # Hash is now stale but C1 fires first; still recompute so only C1 fires
    bad_c1["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(bad_c1)
    ).hexdigest()
    _run_case("C1 bad base_set", bad_c1, expected_check_id="C1")

    # --- Fixture C2: pipeline not in pipelines_available ---
    bad_c2 = _make_good_manifest(pipeline_additions=["unknown-pipeline"])
    bad_c2["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(bad_c2)
    ).hexdigest()
    _run_case("C2 pipeline not in pipelines_available", bad_c2, expected_check_id="C2")

    # --- Fixture C4: pipeline in registry/available but no marker file ---
    # We use a real pipeline name that's in registry, but no IF-marker files
    bad_c4 = _make_good_manifest(pipeline_additions=["ux"])
    bad_c4["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(bad_c4)
    ).hexdigest()
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_render_dir = Path(tmpdir)
        # No marker files at all → C4 should fail
        with tempfile.TemporaryDirectory() as td2:
            mf_path = Path(td2) / "child-profile.json"
            mf_path.write_text(json.dumps(bad_c4), encoding="utf-8")
            verdict_c4 = validate_manifest(
                manifest_path=mf_path,
                project_root=Path(td2),
                registry_override=fake_registry,
                render_target_dir_override=empty_render_dir,
            )
        failed_ids = [f["check_id"] for f in verdict_c4["failed_checks"]]
        if "C4" not in failed_ids:
            print(
                f"  FAIL [C4 no marker files]: expected C4 to fail but got "
                f"failed_checks={failed_ids}",
                file=sys.stderr,
            )
            failures += 1
        else:
            print("  PASS [C4 no marker files]", file=sys.stderr)

    # --- Fixture C4 pass: pipeline with marker present ---
    good_c4 = _make_good_manifest(pipeline_additions=["ux"])
    good_c4["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(good_c4)
    ).hexdigest()
    with tempfile.TemporaryDirectory() as tmpdir:
        render_dir = Path(tmpdir)
        # Create a fake orchestrator-prompt.md with the IF marker
        claude_dir = render_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "orchestrator-prompt.md").write_text(
            "<!-- IF ux -->\nsome content\n<!-- /IF ux -->\n",
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as td2:
            mf_path = Path(td2) / "child-profile.json"
            mf_path.write_text(json.dumps(good_c4), encoding="utf-8")
            verdict_c4_pass = validate_manifest(
                manifest_path=mf_path,
                project_root=Path(td2),
                registry_override=fake_registry,
                render_target_dir_override=render_dir,
            )
        failed_ids = [f["check_id"] for f in verdict_c4_pass["failed_checks"]]
        if "C4" in failed_ids:
            print(
                f"  FAIL [C4 marker present, should pass]: got failed_checks={failed_ids}",
                file=sys.stderr,
            )
            failures += 1
        else:
            print("  PASS [C4 marker present, should pass]", file=sys.stderr)

    # --- Fixture C6: bad hash ---
    bad_c6 = _make_good_manifest()
    bad_c6["compose_metadata"]["manifest_hash_sha256"] = "deadbeef" * 8
    _run_case("C6 bad hash", bad_c6, expected_check_id="C6")

    # --- Fixture C7: subjective criterion ---
    bad_c7 = _make_good_manifest()
    bad_c7["task"]["completion_criteria"] = ["code is clean and correct"]
    bad_c7["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(bad_c7)
    ).hexdigest()
    _run_case("C7 subjective criterion", bad_c7, expected_check_id="C7")

    # --- Fixture C8: elevator_pitch contains agent name ---
    bad_c8 = _make_good_manifest()
    bad_c8["user_surface"]["elevator_pitch"] = "The implementer will add a cache layer."
    bad_c8["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(bad_c8)
    ).hexdigest()
    _run_case("C8 agent name in elevator_pitch", bad_c8, expected_check_id="C8")

    # --- Fixture C10: depth exceeds max_depth ---
    bad_c10 = _make_good_manifest()
    bad_c10["depth"] = 2
    bad_c10["max_depth"] = 2
    bad_c10["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(bad_c10)
    ).hexdigest()
    _run_case("C10 depth exceeds v1 cap", bad_c10, expected_check_id="C10")

    # --- Regression: dispatch-subprocess mode skips C1/C2/C4/C8 ---
    # Build a dispatch-subprocess manifest: intentionally bad base_set (would fail C1)
    # and no pipelines_available (would fail C2), but correct C6/C7/C10. Should PASS.
    def _make_dispatch_subprocess_manifest() -> dict:
        m: dict = {
            "schema_version": "4",
            "child_id": "1",
            "parent_session_id": "sess-abc",
            "parent_session_dir": "/tmp/test/sessions/parent",
            "child_session_dir": "/tmp/test/sessions/child",
            "depth": 1,
            "max_depth": 1,
            "mode": "dispatch-subprocess",
            "task": {
                "title": "impl",
                "bounded_description": "subprocess dispatch",
                "completion_criteria": ["subprocess exits 0"],
                "deadline_hint": "none",
                "round_budget_hint": "1",
                "requires_user_confirmation_for_kappa_care": False,
                "kappa_care_rationale": None,
                "return_contract": {
                    "artifact_path": "/tmp/out.txt",
                    "sidecar_path": "/tmp/verdict.json",
                    "required_sections": [],
                },
            },
            "ipc": {
                "events_path": "/tmp/test/sessions/child/events.jsonl",
                "parent_messages_path": "/tmp/pm.jsonl",
            },
            "compose_metadata": {
                "composed_by_parent_session_id": "sess-abc",
                "composed_at_ms": 0,
                "manifest_hash_sha256": "",
            },
        }
        canonical = _canonical_json_for_hash(m)
        m["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(canonical).hexdigest()
        return m

    # Regression T1: dispatch-subprocess passes even though C1/C2/C8-required fields absent.
    ds_good = _make_dispatch_subprocess_manifest()
    _run_case("dispatch-subprocess good manifest passes C6/C7/C10", ds_good, expected_check_id=None)

    # Regression T2: dispatch-subprocess checks_run matches amendment (C6/C7/C10 only).
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mf_path = tmp_path / "child-profile.json"
        mf_path.write_text(json.dumps(_make_dispatch_subprocess_manifest()), encoding="utf-8")
        verdict_ds = validate_manifest(
            manifest_path=mf_path,
            project_root=tmp_path,
            registry_override=[],
        )
        actual_checks = sorted(verdict_ds["checks_run"])
        expected_checks = sorted(_CHECKS_DISPATCH_SUBPROCESS)
        if actual_checks != expected_checks:
            print(
                f"  FAIL [dispatch-subprocess checks_run]: expected {expected_checks}, "
                f"got {actual_checks}",
                file=sys.stderr,
            )
            failures += 1
        else:
            print("  PASS [dispatch-subprocess checks_run == amendment skip-list]", file=sys.stderr)

    # Regression T3: dispatch-subprocess C1/C2/C4/C8 NOT in checks_run.
    skipped = [c for c in ["C1", "C2", "C4", "C8"] if c in verdict_ds["checks_run"]]
    if skipped:
        print(
            f"  FAIL [dispatch-subprocess must skip C1/C2/C4/C8]: these appear in checks_run: {skipped}",
            file=sys.stderr,
        )
        failures += 1
    else:
        print("  PASS [dispatch-subprocess skips C1/C2/C4/C8]", file=sys.stderr)

    # Regression T4: mode absent (back-compat v4) still runs full set.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mf_path = tmp_path / "child-profile.json"
        v4_compat = _make_good_manifest()
        del v4_compat["mode"]  # simulate v4 manifest without mode field
        v4_compat["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
            _canonical_json_for_hash(v4_compat)
        ).hexdigest()
        mf_path.write_text(json.dumps(v4_compat), encoding="utf-8")
        verdict_v4 = validate_manifest(
            manifest_path=mf_path,
            project_root=tmp_path,
            registry_override=[],
        )
        actual_v4_checks = sorted(verdict_v4["checks_run"])
        expected_v4_checks = sorted(_CHECKS_DISPATCH)
        if actual_v4_checks != expected_v4_checks:
            print(
                f"  FAIL [v4 back-compat checks_run]: expected {expected_v4_checks}, "
                f"got {actual_v4_checks}",
                file=sys.stderr,
            )
            failures += 1
        else:
            print("  PASS [v4 back-compat: full check set when mode absent]", file=sys.stderr)

    # --- C11 fixtures: 3 valid families, 1 invalid, 1 missing ---

    # C11 pass: family=claude (explicit; the good manifest already covers this,
    # but we verify C11 specifically does not fire on a claude manifest)
    c11_claude = _make_good_manifest()
    _run_case("C11 family=claude (explicit)", c11_claude, expected_check_id=None)

    # C11 pass: family=codex
    c11_codex = _make_good_manifest()
    c11_codex["family"] = "codex"
    c11_codex["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c11_codex)
    ).hexdigest()
    _run_case("C11 family=codex", c11_codex, expected_check_id=None)

    # C11 pass: family=gemini
    c11_gemini = _make_good_manifest()
    c11_gemini["family"] = "gemini"
    c11_gemini["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c11_gemini)
    ).hexdigest()
    _run_case("C11 family=gemini", c11_gemini, expected_check_id=None)

    # C11 fail: invalid family value
    c11_bad = _make_good_manifest()
    c11_bad["family"] = "gpt-4"
    c11_bad["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c11_bad)
    ).hexdigest()
    _run_case("C11 invalid family value", c11_bad, expected_check_id="C11")

    # C11 fail: family field absent
    c11_missing = _make_good_manifest()
    del c11_missing["family"]
    c11_missing["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c11_missing)
    ).hexdigest()
    _run_case("C11 family absent", c11_missing, expected_check_id="C11")

    # --- C12 fixtures: empty list (pass), valid single (pass), valid multi (pass),
    #     invalid token (fail), wrong type / non-list (fail), absent field (fail) ---

    # C12 pass: empty list (valid — no capabilities declared; default for claude-subprocess)
    c12_empty = _make_good_manifest()
    # required_capabilities: [] already in _make_good_manifest(); re-hash for clarity
    c12_empty["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c12_empty)
    ).hexdigest()
    _run_case("C12 required_capabilities=[] (empty list)", c12_empty, expected_check_id=None)

    # C12 pass: single valid capability token
    c12_single = _make_good_manifest()
    c12_single["required_capabilities"] = ["session.resume"]
    c12_single["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c12_single)
    ).hexdigest()
    _run_case("C12 single valid capability", c12_single, expected_check_id=None)

    # C12 pass: multiple valid capability tokens
    c12_multi = _make_good_manifest()
    c12_multi["required_capabilities"] = [
        "session.resume",
        "smart_read.sidecars",
        "events.jsonl.append",
        "skill.invoke",
    ]
    c12_multi["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c12_multi)
    ).hexdigest()
    _run_case("C12 multiple valid capabilities", c12_multi, expected_check_id=None)

    # C12 fail: unknown capability token
    c12_bad_token = _make_good_manifest()
    c12_bad_token["required_capabilities"] = ["session.resume", "unknown.capability"]
    c12_bad_token["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c12_bad_token)
    ).hexdigest()
    _run_case("C12 unknown capability token", c12_bad_token, expected_check_id="C12")

    # C12 fail: non-list type (string instead of array)
    c12_wrong_type = _make_good_manifest()
    c12_wrong_type["required_capabilities"] = "session.resume"
    c12_wrong_type["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c12_wrong_type)
    ).hexdigest()
    _run_case("C12 required_capabilities is a string (wrong type)", c12_wrong_type, expected_check_id="C12")

    # C12 fail: field absent
    c12_missing = _make_good_manifest()
    del c12_missing["required_capabilities"]
    c12_missing["compose_metadata"]["manifest_hash_sha256"] = hashlib.sha256(
        _canonical_json_for_hash(c12_missing)
    ).hexdigest()
    _run_case("C12 required_capabilities absent", c12_missing, expected_check_id="C12")

    # C12 pass: dispatch-subprocess mode omits required_capabilities — C12 not run
    ds_no_caps = _make_dispatch_subprocess_manifest()
    # dispatch-subprocess manifest does NOT include required_capabilities; C12 skipped
    _run_case("C12 dispatch-subprocess omits required_capabilities (skipped)", ds_no_caps, expected_check_id=None)

    n_total = 26  # 0 + C1 + C2 + C4-fail + C4-pass + C6 + C7 + C8 + C10
    #               + ds-good + ds-checks_run + ds-skip-list + v4-back-compat
    #               + C11-claude + C11-codex + C11-gemini + C11-invalid + C11-missing
    #               + C12-empty + C12-single + C12-multi + C12-bad-token
    #               + C12-wrong-type + C12-missing + C12-ds-skip
    n_pass = n_total - failures
    if failures:
        print(
            f"\nSelf-test FAILED: {failures}/{n_total} case(s) failed.",
            file=sys.stderr,
        )
        return 2

    print(f"OK: self-test passed ({n_total} cases)")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="child_profile_validate.py",
        description=(
            "Pre-launch validator for L2 child-dispatch manifests (child-profile.json). "
            "Implements v4 check set: C1, C2, C4, C6, C7, C8, C10, C11, C12."
        ),
    )
    parser.add_argument(
        "--manifest-path",
        metavar="PATH",
        help="Absolute path to child-profile.json to validate.",
    )
    parser.add_argument(
        "--out-sidecar-path",
        metavar="PATH",
        help="Absolute path where the verdict sidecar JSON will be written.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run inline self-test fixtures and exit (0=pass, 2=fail).",
    )

    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    if not args.manifest_path or not args.out_sidecar_path:
        print(
            "ERROR: --manifest-path and --out-sidecar-path are required.",
            file=sys.stderr,
        )
        parser.print_usage(sys.stderr)
        return 2

    manifest_path = Path(args.manifest_path)
    out_sidecar_path = Path(args.out_sidecar_path)

    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        raw_bytes = manifest_path.read_bytes()
        json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: failed to read/parse manifest: {e}", file=sys.stderr)
        return 2

    try:
        project_root = _find_project_root(Path.cwd())
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        verdict = validate_manifest(manifest_path=manifest_path, project_root=project_root)
    except Exception as e:
        print(f"ERROR: validation failed unexpectedly: {e}", file=sys.stderr)
        return 2

    try:
        write_sidecar(verdict, out_sidecar_path)
    except OSError as e:
        print(f"ERROR: could not write sidecar to {out_sidecar_path}: {e}", file=sys.stderr)
        return 2

    if verdict["verdict"] == "fail":
        n_failed = len(verdict["failed_checks"])
        failed_ids = [f["check_id"] for f in verdict["failed_checks"]]
        print(
            f"FAIL: {n_failed} check(s) failed: {', '.join(failed_ids)} "
            f"(sidecar written to {out_sidecar_path})",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: manifest valid (sidecar written to {out_sidecar_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
