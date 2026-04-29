"""pipeline_manifest.py — Pipeline manifest loader.

Each pipeline has a manifest at .claude/pipelines/<name>/manifest.json that
declares the agents, hooks, MCP servers, orchestrator-prompt IF-block name,
and an optional planner synthesis snippet owned by that pipeline.

Exports:
    HookEntry                     — dataclass for a single hook entry
    PipelineManifest              — dataclass for a complete pipeline manifest
    PipelineManifestError         — raised on malformed or missing manifests
    DEFAULT_RESERVED_AGENTS       — frozenset of universal-agent basenames
    DEFAULT_RESERVED_HOOKS        — frozenset of universal-hook basenames
    DEFAULT_RESERVED_RUBRICS      — frozenset of universal-rubric basenames
    load_pipeline_manifests       — parse manifests for a set of registry names
    iter_runtime_gated_hooks      — yield hook basenames where runtime_gate=True
    validate_rules_skills_exist   — check that declared rules/skills files exist
    validate_rubrics_exist        — check that declared rubric files exist on disk
"""

import json
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Iterable

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {"name", "orchestrator_prompt_block"}
_VALID_PROMPT_BLOCK_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_PLANNER_SNIPPET_MAX_BYTES = 4096  # 4 KB

# Universal (non-pipeline-owned) agents — adding a new one here prevents a
# malicious manifest from shadow-stubbing it.
# NOTE: .claude/session-cycling.json is git-tracked and checked out by every
# worktree; it must NOT be added to any blanket `.claude/*.json` prune rule,
# as that would silently break cycle-hook sentinel-TTL reads in Path C sessions.
DEFAULT_RESERVED_AGENTS: frozenset[str] = frozenset({
    "architect.md",
    "coherence-auditor.md",
    "diagnostician.md",
    "implementer.md",
    "planner.md",
    "pre-flight-gate.md",
    "researcher.md",
    "solution-designer.md",
    "synthesizer.md",
    "validator.md",
})

# Universal (non-pipeline-owned) rubrics — pipeline manifests may not claim
# these. They live in .claude/rubrics/ and are never pruned.
DEFAULT_RESERVED_RUBRICS: frozenset[str] = frozenset({
    "code-vs-spec.json",
    "code-review.json",
    "generator-artifact.json",
    "connections-graph.json",
    "cross-knowledge-coherence.json",
})

# Universal (non-pipeline-owned) safety hooks — pipeline manifests must never
# claim these. They are registered globally in .claude/settings.json and guard
# session-cycling, context hygiene, knowledge-store integrity, orchestrator
# delegation, and build-pass invariants. Claiming one in a manifest would
# allow pipeline_prune.py to delete it from the worktree on pipeline-disable,
# silently breaking the invariant it enforces.
# [verified: .claude/settings.json:13/96/251 (cycle-hook.py); :87 (truncate-bash.py);
#  :213/222/231/240 (knowledge-write-guard.py); :186/195/204 (write-gate.py); :132 (build-pass-gate.py)]
DEFAULT_RESERVED_HOOKS: frozenset[str] = frozenset({
    "cycle-hook.py",            # session-cycling sentinel
    "truncate-bash.py",         # context-hygiene invariant
    "knowledge-write-guard.py", # knowledge-store gateway
    "write-gate.py",            # orchestrator delegation guard
    "build-pass-gate.py",       # build-pass invariant
})

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookEntry:
    """A single hook entry from a pipeline manifest."""

    path: str
    runtime_gate: bool = False


@dataclass(frozen=True)
class PipelineManifest:
    """Parsed representation of a .claude/pipelines/<name>/manifest.json."""

    name: str
    summary: str
    agents: tuple[str, ...]
    hooks: tuple[HookEntry, ...]
    mcp_servers: tuple[str, ...]
    orchestrator_prompt_block: str
    planner_snippet: str | None
    rules: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    rubrics: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class PipelineManifestError(Exception):
    """Raised when a manifest is missing or malformed.

    Callers should catch this and exit 2 with the manifest path named.
    """


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_pipeline_manifests(
    registry: set[str],
    pipelines_dir: str = ".claude/pipelines",
    *,
    reserved_agents: frozenset[str] = DEFAULT_RESERVED_AGENTS,
    reserved_hooks: frozenset[str] = DEFAULT_RESERVED_HOOKS,
) -> dict[str, "PipelineManifest"]:
    """Parse manifests for every name in *registry*.

    Args:
        registry: set of pipeline names declared in bootstrap-config.json.
        pipelines_dir: path to the directory containing per-pipeline dirs.
            Defaults to .claude/pipelines (relative to cwd, suitable for
            callers in bin/ that run from the project root).
        reserved_agents: frozenset of agent basenames that no pipeline may
            claim (universal-agent guard). Defaults to DEFAULT_RESERVED_AGENTS.
        reserved_hooks: frozenset of hook basenames that no pipeline may
            claim (universal-hook guard). Defaults to DEFAULT_RESERVED_HOOKS.

    Returns:
        dict mapping pipeline name → PipelineManifest for every name in registry.

    Raises:
        PipelineManifestError: if a registry name has no manifest file, or if
            any manifest is malformed.

    NOTE: This function does NOT verify that rules/skills basenames resolve to
    existing files. Callers in production contexts MUST additionally call
    validate_rules_skills_exist(manifest, project_root) to satisfy the
    load-time existence invariant. Tests using tmp_path may skip this call.
    """
    root = pathlib.Path(pipelines_dir)

    # Forward-compat: warn for manifest directories not in the registry.
    if root.is_dir():
        for entry in root.iterdir():
            if entry.is_dir() and (entry / "manifest.json").exists():
                if entry.name not in registry:
                    print(
                        f"[pipeline_manifest] WARNING: pipeline directory "
                        f"{entry.name!r} exists but is not in registry {sorted(registry)}. "
                        f"Ignoring for forward compatibility.",
                        file=sys.stderr,
                    )

    result: dict[str, PipelineManifest] = {}
    for name in sorted(registry):  # deterministic order
        manifest_path = root / name / "manifest.json"
        if not manifest_path.exists():
            raise PipelineManifestError(
                f"Pipeline {name!r} is in the registry but has no manifest at "
                f"{manifest_path}. Create the manifest or remove {name!r} from "
                f".claude/bootstrap-config.json pipelines.registry."
            )
        result[name] = _parse_manifest(manifest_path, reserved_agents, reserved_hooks)

    return result


def _parse_manifest(
    manifest_path: pathlib.Path,
    reserved_agents: frozenset[str],
    reserved_hooks: frozenset[str],
) -> PipelineManifest:
    """Parse a single manifest.json file and return a PipelineManifest.

    Raises:
        PipelineManifestError: on any parse or validation failure.
    """
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineManifestError(
            f"Manifest at {manifest_path} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise PipelineManifestError(
            f"Cannot read manifest at {manifest_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise PipelineManifestError(
            f"Manifest at {manifest_path} must be a JSON object at the top level."
        )

    # Warn on unknown top-level fields (forward compat).
    _KNOWN_FIELDS = {
        "name", "summary", "agents", "hooks", "mcp_servers",
        "orchestrator_prompt_block", "planner_snippet",
        "rules", "skills", "rubrics",
    }
    for key in raw:
        if key not in _KNOWN_FIELDS:
            print(
                f"[pipeline_manifest] WARNING: unknown field {key!r} in "
                f"{manifest_path} — ignored for forward compatibility.",
                file=sys.stderr,
            )

    # Required fields.
    for field in _REQUIRED_FIELDS:
        if field not in raw:
            raise PipelineManifestError(
                f"Manifest at {manifest_path} is missing required field {field!r}."
            )

    name = str(raw["name"])

    # Registry-name / manifest-name mismatch check.
    # The manifest's own name field must match the directory name.
    declared_dir_name = manifest_path.parent.name
    if name != declared_dir_name:
        raise PipelineManifestError(
            f"Manifest at {manifest_path} declares name={name!r} but its "
            f"directory name is {declared_dir_name!r}. They must match."
        )

    summary = str(raw.get("summary", ""))

    # agents
    agents_raw = raw.get("agents", [])
    if not isinstance(agents_raw, list):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'agents' must be a list."
        )
    agents: list[str] = []
    for a in agents_raw:
        if not isinstance(a, str):
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: each entry in 'agents' must be a string."
            )
        # Universal-agent guard: case-insensitive whole-basename match.
        if a.lower() in {r.lower() for r in reserved_agents}:
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: agent {a!r} is a universal "
                f"(reserved) agent and may not be claimed by a pipeline. "
                f"Universal agents: {sorted(reserved_agents)}."
            )
        agents.append(a)

    # hooks
    hooks_raw = raw.get("hooks", [])
    if not isinstance(hooks_raw, list):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'hooks' must be a list."
        )
    hooks: list[HookEntry] = []
    for h in hooks_raw:
        entry = _parse_hook_entry(h, manifest_path)
        # Universal-hook guard: case-insensitive whole-basename match.
        basename = pathlib.Path(entry.path).name
        if basename.lower() in {r.lower() for r in reserved_hooks}:
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: hook {entry.path!r} is a universal "
                f"(reserved) hook and may not be claimed by a pipeline. "
                f"Universal hooks: {sorted(reserved_hooks)}."
            )
        hooks.append(entry)

    # mcp_servers
    mcp_servers_raw = raw.get("mcp_servers", [])
    if not isinstance(mcp_servers_raw, list):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'mcp_servers' must be a list."
        )
    mcp_servers: list[str] = []
    for s in mcp_servers_raw:
        if not isinstance(s, str):
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: each entry in 'mcp_servers' must be a string."
            )
        mcp_servers.append(s)

    # rules — flat basenames only (path-traversal guard per invariant 9)
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'rules' must be a list."
        )
    rules: list[str] = []
    for r in rules_raw:
        if not isinstance(r, str):
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: each entry in 'rules' must be a string."
            )
        _enforce_flat_basename(r, "rules", manifest_path)
        rules.append(r)

    # skills — flat basenames only (path-traversal guard per invariant 9)
    skills_raw = raw.get("skills", [])
    if not isinstance(skills_raw, list):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'skills' must be a list."
        )
    skills: list[str] = []
    for sk in skills_raw:
        if not isinstance(sk, str):
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: each entry in 'skills' must be a string."
            )
        _enforce_flat_basename(sk, "skills", manifest_path)
        skills.append(sk)

    # rubrics — pipeline-owned rubric basenames only (flat, non-universal)
    rubrics_raw = raw.get("rubrics", [])
    if not isinstance(rubrics_raw, list):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'rubrics' must be a list."
        )
    rubrics: list[str] = []
    for rb in rubrics_raw:
        if not isinstance(rb, str):
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: each entry in 'rubrics' must be a string."
            )
        _enforce_flat_basename(rb, "rubrics", manifest_path)
        # Universal-rubric reserved-set guard: reject claims on universal rubrics.
        if rb.lower() in {r.lower() for r in DEFAULT_RESERVED_RUBRICS}:
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: rubric {rb!r} is a universal "
                f"(reserved) rubric and may not be claimed by a pipeline. "
                f"Universal rubrics: {sorted(DEFAULT_RESERVED_RUBRICS)}."
            )
        rubrics.append(rb)

    # orchestrator_prompt_block
    opb = str(raw["orchestrator_prompt_block"])
    if not _VALID_PROMPT_BLOCK_RE.match(opb):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: 'orchestrator_prompt_block' must match "
            f"[a-z][a-z0-9_-]*; got {opb!r}."
        )

    # planner_snippet (optional, max 4KB)
    planner_snippet: str | None = None
    if "planner_snippet" in raw:
        ps = str(raw["planner_snippet"])
        ps_bytes = len(ps.encode("utf-8"))
        if ps_bytes > _PLANNER_SNIPPET_MAX_BYTES:
            raise PipelineManifestError(
                f"Manifest at {manifest_path}: 'planner_snippet' exceeds the "
                f"{_PLANNER_SNIPPET_MAX_BYTES}-byte limit "
                f"(actual: {ps_bytes} bytes)."
            )
        planner_snippet = ps

    return PipelineManifest(
        name=name,
        summary=summary,
        agents=tuple(agents),
        hooks=tuple(hooks),
        mcp_servers=tuple(mcp_servers),
        orchestrator_prompt_block=opb,
        planner_snippet=planner_snippet,
        rules=tuple(rules),
        skills=tuple(skills),
        rubrics=tuple(rubrics),
    )


def _enforce_flat_basename(entry: str, field: str, manifest_path: pathlib.Path) -> None:
    """Reject any rules/skills entry that is not a flat basename.

    A flat basename contains no '/' or '\\' and does not start with '..'.
    This prevents path-traversal via naive join (invariant 9).
    """
    if not entry or entry in (".", "/") or "/" in entry or "\\" in entry or entry.startswith(".."):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: invalid {field!r} entry {entry!r} — "
            f"entries must be flat basenames (no '/', no '\\\\', no leading '..'); "
            f"got {entry!r}."
        )


def _parse_hook_entry(raw: object, manifest_path: pathlib.Path) -> HookEntry:
    """Parse one entry from the 'hooks' list.

    Accepts:
      - plain string → HookEntry(path=<string>, runtime_gate=False)
      - object {path: str, runtime_gate: bool, ...} → unknown extra fields warned
    """
    if isinstance(raw, str):
        return HookEntry(path=raw, runtime_gate=False)

    if not isinstance(raw, dict):
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: each hook entry must be a string "
            f"or an object with a 'path' field; got {type(raw).__name__}."
        )

    if "path" not in raw:
        raise PipelineManifestError(
            f"Manifest at {manifest_path}: hook object entry is missing required "
            f"'path' field: {raw!r}."
        )

    path = str(raw["path"])
    runtime_gate = bool(raw.get("runtime_gate", False))

    # Warn on unknown fields in hook objects (forward compat).
    _KNOWN_HOOK_FIELDS = {"path", "runtime_gate"}
    for key in raw:
        if key not in _KNOWN_HOOK_FIELDS:
            print(
                f"[pipeline_manifest] WARNING: unknown field {key!r} in hook "
                f"object {raw!r} in {manifest_path} — ignored for forward compatibility.",
                file=sys.stderr,
            )

    return HookEntry(path=path, runtime_gate=runtime_gate)


# ---------------------------------------------------------------------------
# Helper: iter_runtime_gated_hooks
# ---------------------------------------------------------------------------


def iter_runtime_gated_hooks(manifest: PipelineManifest) -> Iterable[str]:
    """Yield hook basenames where runtime_gate is True.

    Empty iterable on initial S1 state (no Fallback-1 activation).
    If S5 Fallback 1 activates, hooks carrying the # PIPELINE_RUNTIME_GATE_V1
    preamble are declared with runtime_gate=True in their pipeline manifest;
    this helper enumerates them for validator cross-checks.
    """
    for h in manifest.hooks:
        if h.runtime_gate:
            yield h.path


# ---------------------------------------------------------------------------
# Helper: validate_rules_skills_exist
# ---------------------------------------------------------------------------


def validate_rules_skills_exist(
    manifest: PipelineManifest,
    project_root: "str | pathlib.Path",
) -> None:
    """Verify that every rules/skills basename declared in *manifest* resolves
    to an existing file under project_root/.claude/rules/ or
    project_root/.claude/skills/ respectively.

    This function is the enforcement point for the load-time existence invariant.
    load_pipeline_manifests alone does NOT check file existence — callers in
    production contexts MUST call this function after loading. Tests using
    tmp_path may skip this call if they are testing parse-only behaviour.

    Raises:
        PipelineManifestError: naming the manifest, field, and the missing
            basename if any declared file does not exist on disk.
    """
    root = pathlib.Path(project_root)
    for basename in manifest.rules:
        target = root / ".claude" / "rules" / basename
        if not target.exists():
            raise PipelineManifestError(
                f"Pipeline {manifest.name!r} manifest declares rules entry "
                f"{basename!r} but no file exists at {target}."
            )
    for basename in manifest.skills:
        target = root / ".claude" / "skills" / basename
        if not target.exists():
            raise PipelineManifestError(
                f"Pipeline {manifest.name!r} manifest declares skills entry "
                f"{basename!r} but no file exists at {target}."
            )


def validate_rubrics_exist(
    manifest: PipelineManifest,
    project_root: "str | pathlib.Path",
) -> None:
    """Verify that every rubric basename declared in *manifest* resolves to an
    existing file under project_root/.claude/pipelines/<manifest.name>/rubrics/.

    Parallel contract to validate_rules_skills_exist. Callers in production
    contexts MUST call this function after loading an active manifest to satisfy
    the load-time existence invariant. Tests using tmp_path may skip this call.

    Raises:
        PipelineManifestError: naming the manifest, field, and the missing
            basename if any declared file does not exist on disk.

    Non-goals:
      - Does NOT check universal rubrics (.claude/rubrics/*.json). Those are
        project-level invariants, not pipeline-manifest invariants.
      - Does NOT validate JSON well-formedness of rubric file contents; that
        surfaces as verdict: skip, reason: malformed-input at agent load time.
    """
    root = pathlib.Path(project_root)
    for basename in manifest.rubrics:
        target = root / ".claude" / "pipelines" / manifest.name / "rubrics" / basename
        if not target.exists():
            raise PipelineManifestError(
                f"Pipeline {manifest.name!r} manifest declares rubrics entry "
                f"{basename!r} but no file exists at {target}."
            )
