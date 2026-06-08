#!/usr/bin/env python3
"""Optional post-spawn dispatch-verifier observer for L2 child sessions.

Implements checks V-D1..V-D5 over the composed dispatch surface (manifest +
dispatch-prompt + spawning-session-env + sidecar-directory state + tool-surface
declaration).

Positioned as OPTIONAL POST-SPAWN OBSERVER, not as a required pre-spawn gate.
The L1 dispatch flow does not require this verifier; it exists as a structured
audit signal the operator may run after a spawn lands.

Double-spawn protection lives in bin/parent_messages_register_child.py
(registry-row check + O_EXCL sentinel creation, both returning exit code 3 on
collision). An earlier V-D4 sub-check that rejected on children-active sentinel
presence has been removed — it conflicted with the canonical register-first
flow by design. See .claude/skills/dispatch-l2/SKILL.md § About dispatch_verifier.py
for the canonical positioning and the recommended post-spawn invocation form.

Exit codes:
  0 — all block-severity checks pass; sidecar written with verdict: approve
  1 — >=1 block-severity check fails; sidecar written with verdict: reject
  2 — invocation error (bad args, unreadable manifest, project root not found)

Run examples:
  python3 bin/dispatch_verifier.py \\
      --manifest-path /path/to/children/c-xxx/child-profile.json \\
      --dispatch-prompt-path /path/to/children/c-xxx/child-dispatch-prompt.md \\
      --out-sidecar-path /path/to/children/c-xxx/dispatch-verifier-verdict.json

  python3 bin/dispatch_verifier.py --self-test
  python3 bin/dispatch_verifier.py --help
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "1.0.0"
_VERIFIER_VERSION = "v1"

# V-D3: freshness window (seconds).  Rejects dispatches older than 1 hour.
_FRESHNESS_WINDOW_SECONDS = 3600

# V-D5: tools the L2 child's §K.1 startup requires.
# Source: .claude/skills/dispatch-l2/SKILL.md frontmatter allowed-tools +
# session(action='resume') and knowledge calls in §K.1.
_REQUIRED_TOOLS: list[str] = [
    "mcp__context-tools__session",
    "mcp__context-tools__knowledge",
    "mcp__context-tools__smart_read",
    "mcp__context-tools__smart_bash",
    "Skill",
]

# V-D4: state files whose presence in a sidecar dir indicates stale reuse.
_STALE_STATE_FILES: list[str] = ["events.jsonl"]


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


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 with millisecond precision."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------------
# Per-check functions
# Return: None on pass, or a dict with keys {check_id, severity, message}.
# ---------------------------------------------------------------------------

def _reason(check_id: str, severity: str, message: str) -> dict:
    return {"check_id": check_id, "severity": severity, "message": message}


def _check_vd1(env: Optional[dict] = None) -> Optional[dict]:
    """V-D1: Depth-cap gate-time enforcement.

    The spawning session MUST be L1-root (CLAUDE_SESSION_DEPTH == 0 or absent).
    Reads CLAUDE_SESSION_DEPTH from the process environment (injected by
    bin/claude-session at spawn time, claude-session:5587-5595).

    If the env var is absent, default to 0 (L1-root assumed — the launcher only
    injects it for child sessions; absence at L1-root is the expected state).
    If the env var is present and >=1, this verifier is running inside an L2
    child, which is forbidden.
    """
    if env is None:
        env = dict(os.environ)
    depth_str = env.get("CLAUDE_SESSION_DEPTH", "0")
    try:
        depth = int(depth_str)
    except ValueError:
        return _reason(
            "V-D1", "block",
            f"CLAUDE_SESSION_DEPTH is set but not a valid integer: {depth_str!r}; "
            "cannot verify spawning depth"
        )
    if depth >= 1:
        return _reason(
            "V-D1", "block",
            f"Spawning session depth is {depth} (CLAUDE_SESSION_DEPTH={depth_str!r}); "
            "only L1-root sessions (depth 0) may dispatch L2 children"
        )
    return None


def _check_vd2(manifest: dict, dispatch_prompt_path: Path) -> Optional[dict]:
    """V-D2: Dispatch-prompt existence and shape.

    Verifies child-dispatch-prompt.md exists at the supplied path, is
    non-empty, and contains a Frame Block anchor.  Also checks the prompt
    references the manifest's task.bounded_description content (by substring
    presence) — structural consistency check, not semantic.
    """
    if not dispatch_prompt_path.exists():
        return _reason(
            "V-D2", "block",
            f"dispatch-prompt file not found: {dispatch_prompt_path}"
        )
    try:
        content = dispatch_prompt_path.read_text(encoding="utf-8")
    except OSError as e:
        return _reason("V-D2", "block", f"could not read dispatch-prompt: {e}")

    if not content.strip():
        return _reason("V-D2", "block", "dispatch-prompt file is empty")

    # Frame Block anchor — matches either FRAME-BLOCK-DECLARED or a §-framing header.
    if "FRAME-BLOCK" not in content and "bounded_description" not in content:
        # Lenient: either a literal FRAME-BLOCK marker OR the manifest's
        # bounded_description content is echoed into the prompt.
        task_title = manifest.get("task", {}).get("title", "")
        if task_title and task_title not in content:
            return _reason(
                "V-D2", "block",
                f"dispatch-prompt does not contain the task title {task_title!r} "
                "or a FRAME-BLOCK marker; prompt may not be aligned with the manifest"
            )
    return None


def _check_vd3(manifest: dict) -> Optional[dict]:
    """V-D3: Compose-metadata freshness.

    compose_metadata.composed_at_ms must be within _FRESHNESS_WINDOW_SECONDS
    of now.  Guards against accidental reuse of a stale child-profile.json
    from a prior session.
    """
    composed_at_ms = manifest.get("compose_metadata", {}).get("composed_at_ms")
    if composed_at_ms is None:
        return _reason(
            "V-D3", "block",
            "compose_metadata.composed_at_ms is missing; cannot verify freshness"
        )
    if not isinstance(composed_at_ms, (int, float)):
        return _reason(
            "V-D3", "block",
            f"compose_metadata.composed_at_ms is not a number: {composed_at_ms!r}"
        )
    age_seconds = (time.time() * 1000 - float(composed_at_ms)) / 1000.0
    if age_seconds > _FRESHNESS_WINDOW_SECONDS:
        age_minutes = int(age_seconds / 60)
        return _reason(
            "V-D3", "block",
            f"manifest composed_at_ms is {age_minutes} minutes old "
            f"(limit: {_FRESHNESS_WINDOW_SECONDS // 60} minutes); "
            "this looks like an accidentally reused stale manifest"
        )
    return None


def _check_vd4(
    manifest: dict,
    sidecar_dir: Path,
    parent_session_dir: Optional[Path] = None,
) -> Optional[dict]:
    """V-D4: Sidecar-directory writability and isolation.

    (a) sidecar_dir must exist and be writable.
    (b) No pre-existing events.jsonl (child-state reuse footgun).

    Note: prior versions of V-D4 also rejected on `children-active/{child_id}`
    sentinel presence as a "double-spawn prevention" check. That sub-check was
    removed because it structurally conflicted with the canonical register-first
    dispatch flow — `bin/parent_messages_register_child.py` writes that sentinel
    during Step 1 (register), before any spawn, so the sub-check rejected every
    legitimate dispatch. Double-spawn protection is now solely owned by
    `register_child`'s idempotency checks (registry-row check + O_EXCL sentinel
    creation, returning exit code 3 on collision). See
    .claude/skills/dispatch-l2/SKILL.md § About dispatch_verifier.py for the
    architectural reasoning.
    """
    # `manifest` and `parent_session_dir` are no longer load-bearing for V-D4
    # after the sub-check (c) removal. They're retained in the signature to
    # avoid call-site churn; `verify_dispatch` may pass any value or None.
    del manifest, parent_session_dir  # explicit: unused after sub-check (c) removal
    if not sidecar_dir.exists():
        return _reason(
            "V-D4", "block",
            f"sidecar directory does not exist: {sidecar_dir}"
        )
    # Writability: try to stat a hypothetical temp name (cheaper than opening).
    if not os.access(sidecar_dir, os.W_OK):
        return _reason(
            "V-D4", "block",
            f"sidecar directory is not writable: {sidecar_dir}"
        )
    # (b) Stale events.jsonl
    for stale_file in _STALE_STATE_FILES:
        if (sidecar_dir / stale_file).exists():
            return _reason(
                "V-D4", "block",
                f"sidecar directory contains pre-existing {stale_file!r}; "
                "this child_session_dir was used in a prior dispatch — "
                "increment child_id or use a fresh session directory"
            )
    return None


def _check_vd5(project_root: Path) -> Optional[dict]:
    """V-D5: Tool-surface declarability (warn-only).

    Reads dispatch-l2/SKILL.md allowed-tools frontmatter and warns when a
    §K.1-required tool is not in the resolved tool surface.  Advisory only —
    the tools-frontmatter work (Y_0l) is downstream and not all environments
    have it yet.
    """
    skill_path = project_root / ".claude" / "skills" / "dispatch-l2" / "SKILL.md"
    if not skill_path.exists():
        return _reason(
            "V-D5", "warn",
            f"dispatch-l2/SKILL.md not found at {skill_path}; "
            "cannot verify tool-surface declarability"
        )
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError as e:
        return _reason("V-D5", "warn", f"could not read SKILL.md: {e}")

    # Extract allowed-tools frontmatter block.
    declared_tools: list[str] = []
    in_frontmatter = False
    in_tools = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                break  # end of frontmatter
        if not in_frontmatter:
            continue
        if stripped.startswith("allowed-tools:"):
            in_tools = True
            continue
        if in_tools:
            if stripped.startswith("- "):
                declared_tools.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                # Non-list line ends the allowed-tools block.
                in_tools = False

    if not declared_tools:
        return _reason(
            "V-D5", "warn",
            "dispatch-l2/SKILL.md has no allowed-tools frontmatter; "
            "cannot verify tool-surface against §K.1 requirements"
        )

    missing = [t for t in _REQUIRED_TOOLS if t not in declared_tools]
    if missing:
        return _reason(
            "V-D5", "warn",
            f"§K.1-required tools not declared in SKILL.md allowed-tools: "
            f"{', '.join(missing)}"
        )
    return None


# ---------------------------------------------------------------------------
# Core: run all checks
# ---------------------------------------------------------------------------

_CHECKS_RUN = ["V-D1", "V-D2", "V-D3", "V-D4", "V-D5"]


def verify_dispatch(
    manifest_path: Path,
    dispatch_prompt_path: Path,
    project_root: Path,
    sidecar_dir: Optional[Path] = None,
    parent_session_dir_override: Optional[Path] = None,
    env_override: Optional[dict] = None,
    now_ms_override: Optional[float] = None,
) -> dict:
    """Run V-D1..V-D5 and return a verdict dict.

    sidecar_dir: the ${CAA_CHILD_SIDECAR_DIR} for the child being dispatched.
        When None, derived from manifest.child_session_dir.
    parent_session_dir_override: for testing only — overrides the parent
        session dir used in V-D4 sentinel collision detection.
    env_override: for testing only — overrides os.environ for V-D1.
    now_ms_override: for testing only — overrides time.time() for V-D3.
    """
    manifest = json.loads(manifest_path.read_bytes())

    if sidecar_dir is None:
        child_session_dir = manifest.get("child_session_dir", "")
        sidecar_dir = Path(child_session_dir) if child_session_dir else manifest_path.parent

    parent_session_dir: Optional[Path]
    if parent_session_dir_override is not None:
        parent_session_dir = parent_session_dir_override
    else:
        parent_session_dir_raw = manifest.get("parent_session_dir", "")
        parent_session_dir = Path(parent_session_dir_raw) if parent_session_dir_raw else None

    reasons: list[dict] = []

    # V-D1
    r = _check_vd1(env=env_override)
    if r:
        reasons.append(r)

    # V-D2
    r = _check_vd2(manifest, dispatch_prompt_path)
    if r:
        reasons.append(r)

    # V-D3 — honour now_ms_override for testing
    if now_ms_override is not None:
        # Temporarily patch time.time via the module reference.
        original_time = time.time
        time.time = lambda: now_ms_override / 1000.0  # type: ignore[method-assign]
        try:
            r = _check_vd3(manifest)
        finally:
            time.time = original_time  # type: ignore[method-assign]
    else:
        r = _check_vd3(manifest)
    if r:
        reasons.append(r)

    # V-D4
    r = _check_vd4(manifest, sidecar_dir, parent_session_dir=parent_session_dir)
    if r:
        reasons.append(r)

    # V-D5
    r = _check_vd5(project_root)
    if r:
        reasons.append(r)

    # Verdict: reject iff any block-severity reason exists.
    has_block = any(entry["severity"] == "block" for entry in reasons)
    verdict_str = "reject" if has_block else "approve"

    return {
        "schema_version": _SCHEMA_VERSION,
        "verifier_version": _VERIFIER_VERSION,
        "ts": _now_iso(),
        "verdict": verdict_str,
        "reasons": reasons,
        "checks_run": _CHECKS_RUN,
        "manifest_path": str(manifest_path.resolve()),
        "dispatch_prompt_path": str(dispatch_prompt_path.resolve()),
    }


# ---------------------------------------------------------------------------
# Sidecar writer
# ---------------------------------------------------------------------------

def write_sidecar(verdict: dict, out_path: Path) -> None:
    """Write verdict dict as JSON to out_path; create parent dirs if needed."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _make_good_manifest(tmp_dir: Path, composed_at_ms: Optional[int] = None) -> dict:
    """Return a minimal manifest with correct hash, using current timestamp."""
    if composed_at_ms is None:
        composed_at_ms = int(time.time() * 1000)
    m: dict = {
        "schema_version": "4",
        "child_id": "c-1778000000-00001-0001",
        "parent_session_id": "1778000000-00001-abc",
        "parent_session_dir": str(tmp_dir / "parent-session"),
        "child_session_dir": str(tmp_dir / "child-session"),
        "depth": 1,
        "max_depth": 1,
        "mode": "dispatch",
        "profile_name": "default",
        "delegate_authority_envelope": "all",
        "task": {
            "title": "test-dispatch-task",
            "bounded_description": "A test dispatch task for the self-test fixture.",
            "completion_criteria": ["bin/foo.py exits 0", "output file exists"],
            "deadline_hint": "none",
            "round_budget_hint": "3",
            "requires_user_confirmation_for_kappa_care": False,
            "kappa_care_rationale": None,
            "return_contract": {
                "artifact_path": str(tmp_dir / "out.md"),
                "sidecar_path": str(tmp_dir / "out-verdict.json"),
                "required_sections": ["Results"],
            },
        },
        "profile": {
            "base_set": "full-install-v1",
            "additions": {"pipelines": []},
        },
        "pipelines_available": ["ux", "hygiene"],
        "ipc": {
            "events_path": str(tmp_dir / "child-session" / "events.jsonl"),
            "parent_messages_path": str(tmp_dir / "parent-session" / "parent-messages" / "c-001" / "parent-messages.jsonl"),
        },
        "lifecycle": {"max_episodes": 10, "cycle_authority": "self"},
        "phase_checkpoint": {
            "default_on": True,
            "opt_out_for_this_dispatch": False,
            "opt_out_rationale": None,
        },
        "user_surface": {
            "elevator_pitch": "A test task.",
            "raise_hand_translation_template": "Task needs input: {question}. Reply: {response_shape_hint}.",
        },
        "compose_metadata": {
            "composed_by_parent_session_id": "1778000000-00001-abc",
            "composed_at_ms": composed_at_ms,
            "manifest_hash_sha256": "",
        },
    }
    return m


def _make_good_dispatch_prompt(tmp_dir: Path, task_title: str = "test-dispatch-task") -> Path:
    """Write a minimal valid dispatch-prompt.md and return its path."""
    content = (
        f"FRAME-BLOCK-DECLARED\n\n"
        f"# Dispatch: {task_title}\n\n"
        f"bounded_description: A test dispatch task.\n"
    )
    p = tmp_dir / "child-dispatch-prompt.md"
    p.write_text(content, encoding="utf-8")
    return p


def _run_self_test() -> int:
    """Run inline fixtures through verify_dispatch(). Returns 0 on pass, 2 on fail."""
    failures = 0
    n_total = 0

    def _run(
        label: str,
        expected_check_id: Optional[str],
        *,
        manifest_extra: Optional[dict] = None,
        prompt_content: Optional[str] = None,
        env_override: Optional[dict] = None,
        now_ms_override: Optional[float] = None,
        skip_sidecar: bool = False,
    ) -> None:
        nonlocal failures, n_total
        n_total += 1
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            child_session_dir = tmp / "child-session"
            child_session_dir.mkdir(parents=True)
            parent_session_dir = tmp / "parent-session"
            parent_session_dir.mkdir(parents=True)

            manifest = _make_good_manifest(tmp)
            if manifest_extra:
                for k, v in manifest_extra.items():
                    if isinstance(v, dict) and isinstance(manifest.get(k), dict):
                        manifest[k].update(v)
                    else:
                        manifest[k] = v

            mf_path = tmp / "child-profile.json"
            mf_path.write_text(json.dumps(manifest), encoding="utf-8")

            if prompt_content is not None:
                dp_path = tmp / "child-dispatch-prompt.md"
                dp_path.write_text(prompt_content, encoding="utf-8")
            else:
                dp_path = _make_good_dispatch_prompt(tmp)

            # Build a minimal fake project root with the dispatch-l2 SKILL.md.
            fake_root = tmp / "project"
            skill_dir = fake_root / ".claude" / "skills" / "dispatch-l2"
            skill_dir.mkdir(parents=True)
            (fake_root / "bin").mkdir(parents=True)
            (fake_root / "bin" / "claude-session").write_text("#!/bin/bash\n")
            good_skill_content = (
                "---\n"
                "name: dispatch-l2\n"
                "allowed-tools:\n"
                "  - mcp__context-tools__session\n"
                "  - mcp__context-tools__knowledge\n"
                "  - mcp__context-tools__smart_read\n"
                "  - mcp__context-tools__smart_bash\n"
                "  - Skill\n"
                "---\n"
                "## Purpose\n"
            )
            (skill_dir / "SKILL.md").write_text(good_skill_content)

            verdict = verify_dispatch(
                manifest_path=mf_path,
                dispatch_prompt_path=dp_path,
                project_root=fake_root,
                sidecar_dir=child_session_dir,
                parent_session_dir_override=parent_session_dir,
                env_override=env_override if env_override is not None else {},
                now_ms_override=now_ms_override,
            )

        block_ids = [r["check_id"] for r in verdict["reasons"] if r["severity"] == "block"]
        warn_ids  = [r["check_id"] for r in verdict["reasons"] if r["severity"] == "warn"]
        all_ids   = block_ids + warn_ids

        if expected_check_id is None:
            if verdict["verdict"] != "approve":
                print(
                    f"  FAIL [{label}]: expected approve but got {verdict['verdict']}; "
                    f"reasons={all_ids}",
                    file=sys.stderr,
                )
                failures += 1
            else:
                print(f"  PASS [{label}]", file=sys.stderr)
        else:
            if expected_check_id not in all_ids:
                print(
                    f"  FAIL [{label}]: expected {expected_check_id} in reasons "
                    f"but got {all_ids}",
                    file=sys.stderr,
                )
                failures += 1
            else:
                print(f"  PASS [{label}]", file=sys.stderr)

    # --- Good: should approve with no block reasons ---
    _run("good dispatch", expected_check_id=None)

    # --- V-D1: depth >= 1 in env ---
    _run(
        "V-D1 depth=1 in env",
        expected_check_id="V-D1",
        env_override={"CLAUDE_SESSION_DEPTH": "1"},
    )

    # --- V-D2: prompt file missing (inline block) ---
    n_total += 1
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        child_session_dir = tmp / "child-session"
        child_session_dir.mkdir()
        parent_session_dir = tmp / "parent-session"
        parent_session_dir.mkdir()
        manifest = _make_good_manifest(tmp)
        mf_path = tmp / "child-profile.json"
        mf_path.write_text(json.dumps(manifest), encoding="utf-8")
        fake_root = tmp / "project"
        skill_dir = fake_root / ".claude" / "skills" / "dispatch-l2"
        skill_dir.mkdir(parents=True)
        (fake_root / "bin").mkdir(parents=True)
        (fake_root / "bin" / "claude-session").write_text("#!/bin/bash\n")
        (skill_dir / "SKILL.md").write_text("---\nname: dispatch-l2\n---\n")
        missing_prompt = tmp / "nonexistent-prompt.md"
        verdict = verify_dispatch(
            manifest_path=mf_path,
            dispatch_prompt_path=missing_prompt,
            project_root=fake_root,
            sidecar_dir=child_session_dir,
            parent_session_dir_override=parent_session_dir,
            env_override={},
        )
        block_ids = [r["check_id"] for r in verdict["reasons"] if r["severity"] == "block"]
        if "V-D2" not in block_ids:
            print(f"  FAIL [V-D2 missing file]: expected V-D2 in {block_ids}", file=sys.stderr)
            failures += 1
        else:
            print("  PASS [V-D2 missing file]", file=sys.stderr)

    # --- V-D3: stale manifest (composed 2 hours ago) ---
    stale_ms = int((time.time() - 7200) * 1000)
    _run(
        "V-D3 stale manifest",
        expected_check_id="V-D3",
        manifest_extra={"compose_metadata": {"composed_at_ms": stale_ms}},
    )

    # --- V-D4: stale events.jsonl in sidecar dir ---
    n_total += 1
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        child_session_dir = tmp / "child-session"
        child_session_dir.mkdir()
        (child_session_dir / "events.jsonl").write_text(
            '{"kind":"started"}\n', encoding="utf-8"
        )
        parent_session_dir = tmp / "parent-session"
        parent_session_dir.mkdir()
        manifest = _make_good_manifest(tmp)
        manifest["child_session_dir"] = str(child_session_dir)
        mf_path = tmp / "child-profile.json"
        mf_path.write_text(json.dumps(manifest), encoding="utf-8")
        dp_path = _make_good_dispatch_prompt(tmp)
        fake_root = tmp / "project"
        skill_dir = fake_root / ".claude" / "skills" / "dispatch-l2"
        skill_dir.mkdir(parents=True)
        (fake_root / "bin").mkdir(parents=True)
        (fake_root / "bin" / "claude-session").write_text("#!/bin/bash\n")
        (skill_dir / "SKILL.md").write_text("---\nname: dispatch-l2\n---\n")
        verdict = verify_dispatch(
            manifest_path=mf_path,
            dispatch_prompt_path=dp_path,
            project_root=fake_root,
            sidecar_dir=child_session_dir,
            parent_session_dir_override=parent_session_dir,
            env_override={},
        )
        block_ids = [r["check_id"] for r in verdict["reasons"] if r["severity"] == "block"]
        if "V-D4" not in block_ids:
            print(f"  FAIL [V-D4 stale events.jsonl]: expected V-D4 in {block_ids}", file=sys.stderr)
            failures += 1
        else:
            print("  PASS [V-D4 stale events.jsonl]", file=sys.stderr)

    # --- V-D5: SKILL.md missing allowed-tools (warn-only) ---
    n_total += 1
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        child_session_dir = tmp / "child-session"
        child_session_dir.mkdir()
        parent_session_dir = tmp / "parent-session"
        parent_session_dir.mkdir()
        manifest = _make_good_manifest(tmp)
        mf_path = tmp / "child-profile.json"
        mf_path.write_text(json.dumps(manifest), encoding="utf-8")
        dp_path = _make_good_dispatch_prompt(tmp)
        fake_root = tmp / "project"
        skill_dir = fake_root / ".claude" / "skills" / "dispatch-l2"
        skill_dir.mkdir(parents=True)
        (fake_root / "bin").mkdir(parents=True)
        (fake_root / "bin" / "claude-session").write_text("#!/bin/bash\n")
        # SKILL.md with no allowed-tools block → V-D5 warns
        (skill_dir / "SKILL.md").write_text("---\nname: dispatch-l2\n---\n## Purpose\n")
        verdict = verify_dispatch(
            manifest_path=mf_path,
            dispatch_prompt_path=dp_path,
            project_root=fake_root,
            sidecar_dir=child_session_dir,
            parent_session_dir_override=parent_session_dir,
            env_override={},
        )
        warn_ids  = [r["check_id"] for r in verdict["reasons"] if r["severity"] == "warn"]
        block_ids = [r["check_id"] for r in verdict["reasons"] if r["severity"] == "block"]
        if "V-D5" not in warn_ids:
            print(f"  FAIL [V-D5 no allowed-tools → warn]: expected V-D5 warn, got warn={warn_ids} block={block_ids}", file=sys.stderr)
            failures += 1
        elif verdict["verdict"] != "approve":
            print(f"  FAIL [V-D5 warn-only must not block]: verdict={verdict['verdict']}", file=sys.stderr)
            failures += 1
        else:
            print("  PASS [V-D5 warn-only, verdict still approve]", file=sys.stderr)

    n_pass = n_total - failures
    if failures:
        print(f"\nSelf-test FAILED: {failures}/{n_total} case(s) failed.", file=sys.stderr)
        return 2
    print(f"OK: self-test passed ({n_total} cases)")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="dispatch_verifier.py",
        description=(
            "Optional post-spawn dispatch-verifier observer for L2 child sessions. "
            "Implements V-D1..V-D5 checks over the composed dispatch surface. "
            "See .claude/skills/dispatch-l2/SKILL.md § About dispatch_verifier.py "
            "for the canonical positioning (NOT a required pre-spawn gate — V-D4 "
            "rejects the canonical register-first flow by design)."
        ),
    )
    parser.add_argument(
        "--manifest-path",
        metavar="PATH",
        help="Absolute path to child-profile.json to verify.",
    )
    parser.add_argument(
        "--dispatch-prompt-path",
        metavar="PATH",
        help="Absolute path to child-dispatch-prompt.md.",
    )
    parser.add_argument(
        "--out-sidecar-path",
        metavar="PATH",
        help=(
            "Absolute path where dispatch-verifier-verdict.json will be written "
            "(typically ${CAA_CHILD_SIDECAR_DIR}/dispatch-verifier-verdict.json)."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run inline self-test fixtures and exit (0=pass, 2=fail).",
    )

    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    if not args.manifest_path or not args.dispatch_prompt_path or not args.out_sidecar_path:
        print(
            "ERROR: --manifest-path, --dispatch-prompt-path, and --out-sidecar-path "
            "are all required.",
            file=sys.stderr,
        )
        parser.print_usage(sys.stderr)
        return 2

    manifest_path = Path(args.manifest_path)
    dispatch_prompt_path = Path(args.dispatch_prompt_path)
    out_sidecar_path = Path(args.out_sidecar_path)

    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        json.loads(manifest_path.read_bytes())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: failed to read/parse manifest: {e}", file=sys.stderr)
        return 2

    try:
        project_root = _find_project_root(Path.cwd())
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        verdict = verify_dispatch(
            manifest_path=manifest_path,
            dispatch_prompt_path=dispatch_prompt_path,
            project_root=project_root,
        )
    except Exception as e:
        print(f"ERROR: verification failed unexpectedly: {e}", file=sys.stderr)
        return 2

    try:
        write_sidecar(verdict, out_sidecar_path)
    except OSError as e:
        print(
            f"ERROR: could not write sidecar to {out_sidecar_path}: {e}",
            file=sys.stderr,
        )
        return 2

    if verdict["verdict"] == "reject":
        block_reasons = [r for r in verdict["reasons"] if r["severity"] == "block"]
        failed_ids = [r["check_id"] for r in block_reasons]
        n_failed = len(failed_ids)
        print(
            f"REJECT: {n_failed} check(s) failed: {', '.join(failed_ids)} "
            f"(sidecar written to {out_sidecar_path})",
            file=sys.stderr,
        )
        return 1

    print(f"APPROVE: all checks passed (sidecar written to {out_sidecar_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
