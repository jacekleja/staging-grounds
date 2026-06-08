#!/usr/bin/env python3
# OPT-IN: shared parser library imported by H2/H3/H4/H10 hooks; not a registered hook
"""Centralized delegation-prompt parser library.

Single parser for all delegation prompt shapes — JSON-mode (including
```json fenced blocks), falling back to prose-mode. Hooks H2, H3, H4, H10
import this instead of maintaining per-hook regex stacks.

Design contract: design-contract-schema-3a-i.md §iii (Cluster-A centralization)
and design-contract-schema-3a-ii.md §ii (canonical JSON envelope spec).

Public API:
    parse_prompt(prompt_text)         → ParsedPrompt
    validate_schema(parsed, subagent_type, matrix=None) → list[SchemaError]
    required_field(parsed, dotted_path)  → Any | None

Constants:
    CODE_RUBRICS — frozenset matching build-pass-gate._CODE_RUBRICS exactly.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import yaml


# ---------------------------------------------------------------------------
# Public constant — must match build-pass-gate._CODE_RUBRICS exactly.
# The regression test in test_delegation_prompt_parser.py pins both sets.
# ---------------------------------------------------------------------------

CODE_RUBRICS: frozenset = frozenset({"code-vs-spec", "constraint-compliance"})



# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaError:
    """Typed schema-validation error.  Callers join as f"{e.field}: {e.detail} — fix: {e.remediation}"."""
    field: str          # dotted path, e.g. "output_contract.artifact_path"
    reason: Literal[
        "missing-required", "prohibited-present", "wrong-type",
        "enum-violation", "relative-path-not-allowed",
        "cross-field-inconsistent", "matrix-degraded",
    ]
    detail: str         # human-readable specifics
    remediation: str    # one-line fix hint


# ---------------------------------------------------------------------------
# Parsed result
# ---------------------------------------------------------------------------

@dataclass
class ParsedPrompt:
    """Populated by parse_prompt().  All fields default to None/[]/{}."""

    # Universal core plus tolerated legacy/per-agent fields
    task_id: Optional[str] = None
    session_dir: Optional[str] = None
    round: Optional[int] = None
    round_cap: Optional[int] = None
    inputs: list = field(default_factory=list)
    success_criteria: list = field(default_factory=list)
    output_contract: dict = field(default_factory=dict)  # artifact_path, sidecar_path?, required_sections?
    active_rubrics: list = field(default_factory=list)
    target_artifact_type: Optional[str] = None
    final_round_escalation: Optional[bool] = None

    # Common conditional
    target_artifact_path: Optional[str] = None
    target_slug: Optional[str] = None
    generator_artifact_path: Optional[str] = None
    generator_slug: Optional[str] = None
    generator_type: Optional[str] = None
    changed_files_list: list = field(default_factory=list)
    prior_sidecars: list = field(default_factory=list)
    prior_rounds_summary: Optional[str] = None
    prior_validator_report_path: Optional[str] = None
    impl_report_path: Optional[str] = None
    fix_diffs: list = field(default_factory=list)
    knowledge_paths: list = field(default_factory=list)
    rubric_modifiers: dict = field(default_factory=dict)
    deferral_list_path: Optional[str] = None
    resketch_count: Optional[int] = None
    fix_count: Optional[int] = None

    # agent-content-author 4-tuple
    target_surface: Optional[str] = None
    mode: Optional[str] = None
    dispatch_mode: Optional[str] = None
    lifecycle_phase: Optional[str] = None

    # Path-C (universally optional)
    base_path: Optional[str] = None
    base_path_resolution: Optional[str] = None

    # V3-apparatus (H4 only)
    v3_apparatus: Optional[Any] = None
    cycle_number: Optional[int] = None
    mao_trigger: Optional[str] = None
    source_finding_ids: list = field(default_factory=list)
    drill_tier_rubric: Optional[str] = None
    form_4_eligible: Optional[bool] = None
    assigned_exp_nnn: Optional[str] = None
    parent_drill_tool_use_id: Optional[str] = None

    # Dispatch envelope
    subagent_type: Optional[str] = None
    agent_id: Optional[str] = None

    # Parse metadata
    raw_dict: Optional[dict] = None
    mode_detected: Literal["json", "prose", "json-fenced", "mixed"] = "prose"
    bypass_token: Optional[str] = None  # SCHEMA-BYPASS or V3-SCHEMA-BYPASS reason
    _raw_text: Optional[str] = None     # original prompt text for prose-mode field presence


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r'^```(?:json)?\s*\n(.*?)```\s*$', re.DOTALL)

_BYPASS_V2_RE = re.compile(r'^[ \t]*<!-- SCHEMA-BYPASS: ([^>]*) -->\s*$')
_BYPASS_V3_RE = re.compile(r'^[ \t]*<!-- V3-SCHEMA-BYPASS: ([^>]*) -->\s*$')


def _strip_fence(text: str) -> tuple[str, bool]:
    """Strip ```json ... ``` or ``` ... ``` fence.  Returns (inner_text, was_fenced)."""
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip(), True
    return stripped, False


def _detect_bypass(prompt: str) -> Optional[str]:
    """Return bypass reason from first 20 lines, or None.  Checks both V2 and V3 tokens."""
    lines = prompt.split("\n")[:20]
    for line in lines:
        m = _BYPASS_V2_RE.match(line) or _BYPASS_V3_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


def _coerce_list(value: Any) -> list:
    """Coerce JSON value to list: already-list pass-through; single scalar → [scalar]."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _populate_from_dict(parsed: ParsedPrompt, d: dict) -> None:
    """Populate ParsedPrompt fields from a parsed dict (JSON or YAML)."""
    # Universal core plus tolerated legacy/per-agent fields
    parsed.task_id = d.get("task_id") or parsed.task_id
    parsed.session_dir = d.get("session_dir") or parsed.session_dir
    parsed.round = _coerce_int(d.get("round")) if "round" in d else parsed.round
    parsed.round_cap = _coerce_int(d.get("round_cap")) if "round_cap" in d else parsed.round_cap
    if "inputs" in d:
        parsed.inputs = _coerce_list(d["inputs"])
    if "success_criteria" in d:
        parsed.success_criteria = _coerce_list(d["success_criteria"])
    if "output_contract" in d:
        raw_oc = d["output_contract"]
        if isinstance(raw_oc, dict):
            parsed.output_contract = raw_oc
        # Accept required_sections as comma-sep string (legacy emitter pattern)
        if isinstance(parsed.output_contract.get("required_sections"), str):
            raw_rs = parsed.output_contract["required_sections"]
            parsed.output_contract["required_sections"] = [
                s.strip().strip("\"'") for s in raw_rs.split(",") if s.strip()
            ]
    if "active_rubrics" in d:
        parsed.active_rubrics = _coerce_list(d["active_rubrics"])
    parsed.target_artifact_type = d.get("target_artifact_type") or parsed.target_artifact_type
    if "final_round_escalation" in d:
        parsed.final_round_escalation = _coerce_bool(d["final_round_escalation"])

    # Common conditional
    for attr in ("target_artifact_path", "target_slug", "generator_artifact_path",
                 "generator_slug", "generator_type", "prior_rounds_summary",
                 "prior_validator_report_path", "impl_report_path", "deferral_list_path"):
        val = d.get(attr)
        if val is not None:
            setattr(parsed, attr, val)
    for attr in ("changed_files_list", "prior_sidecars", "fix_diffs",
                 "knowledge_paths", "source_finding_ids"):
        if attr in d:
            setattr(parsed, attr, _coerce_list(d[attr]))
    if "rubric_modifiers" in d and isinstance(d["rubric_modifiers"], dict):
        parsed.rubric_modifiers = d["rubric_modifiers"]
    if "resketch_count" in d:
        parsed.resketch_count = _coerce_int(d["resketch_count"])
    if "fix_count" in d:
        parsed.fix_count = _coerce_int(d["fix_count"])

    # agent-content-author 4-tuple
    for attr in ("target_surface", "mode", "dispatch_mode", "lifecycle_phase"):
        val = d.get(attr)
        if val is not None:
            setattr(parsed, attr, val)

    # Path-C
    for attr in ("base_path", "base_path_resolution"):
        val = d.get(attr)
        if val is not None:
            setattr(parsed, attr, val)

    # V3-apparatus
    if "v3_apparatus" in d:
        parsed.v3_apparatus = d["v3_apparatus"]
    for attr in ("cycle_number",):
        if attr in d:
            setattr(parsed, attr, _coerce_int(d[attr]))
    for attr in ("mao_trigger", "drill_tier_rubric", "assigned_exp_nnn",
                 "parent_drill_tool_use_id"):
        val = d.get(attr)
        if val is not None:
            setattr(parsed, attr, val)
    if "form_4_eligible" in d:
        parsed.form_4_eligible = _coerce_bool(d["form_4_eligible"])

    # Dispatch envelope
    for attr in ("subagent_type", "agent_id"):
        val = d.get(attr)
        if val is not None:
            setattr(parsed, attr, val)


# ---------------------------------------------------------------------------
# Prose-mode extractors — best-effort for schema fields
# ---------------------------------------------------------------------------

_WORD_BOUNDARY_RE_CACHE: dict[str, re.Pattern] = {}
_INLINE_FILES_SENTINEL = "--- INLINED INPUT FILES BELOW ---"


def _strip_inline_files_tail(text: str) -> str:
    """Return only the delegation prompt body before dispatch_agent inline files."""
    marker = "\n" + _INLINE_FILES_SENTINEL
    idx = text.find(marker)
    if idx == -1:
        return text
    return text[:idx].rstrip()


def _word_re(field_name: str) -> re.Pattern:
    if field_name not in _WORD_BOUNDARY_RE_CACHE:
        _WORD_BOUNDARY_RE_CACHE[field_name] = re.compile(
            r'\b' + re.escape(field_name) + r'\b'
        )
    return _WORD_BOUNDARY_RE_CACHE[field_name]


def _field_to_heading(field_name: str) -> str:
    """Convert snake_case field name to heading/label text (e.g. 'success_criteria' -> 'success criteria')."""
    return field_name.replace("_", " ")


def _prose_field_present(text: str, field_name: str) -> bool:
    """Detect structured presence of field_name in prose text.

    Accepts three structured forms (all case-insensitive):
      - Inline colon:      field_name: value  (word-boundary + colon)
      - Markdown heading:  ## Field name      (heading text = underscores-as-spaces)
      - Bold-label:        **field_name:**    (or **Field name:** -- colon optional inside **)

    Does NOT match bare prose mentions (e.g. "the inputs are..."), so running-prose
    occurrences of a schema field name do not produce false-positive presence hits.
    """
    heading = _field_to_heading(field_name)

    # Inline colon form: word boundary + optional whitespace + colon.
    # re.IGNORECASE handles capitalised labels like "Inputs:".
    if re.search(r'\b' + re.escape(field_name) + r'\b\s*:', text, re.IGNORECASE):
        return True

    # Markdown heading form: one or more '#' chars + heading text (exact match, case-insensitive).
    if re.search(
        r'^#+\s+' + re.escape(heading) + r'\s*$',
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        return True

    # Bold-label form: **field_name:** or **Field name:** (colon optional inside **).
    # Requires the label text to be the ONLY content between the ** markers so that
    # dirty labels like "**Inputs (read first):**" do not produce false positives.
    if re.search(
        r'^[^\S\r\n]*(?:[-*+][^\S\r\n]+)?\*\*(?:'
        + re.escape(field_name)
        + r'|'
        + re.escape(heading)
        + r'):?\*\*',
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        return True

    return False


def _prose_scalar(prompt: str, field_name: str) -> Optional[str]:
    """Extract a scalar value from prose.

    Handles (in precedence order):
      bold:     **field_name:** value  or  **Field name:** value
      heading:  ## Field name followed by value on next line
      inline:   field_name: value  or  field_name = value
    """
    heading = _field_to_heading(field_name)

    # Bold-label form tried first: **field_name:** value  or  **Field name:** value.
    # Closing ** is consumed before the value capture to avoid capturing '*'.
    bold_pat = re.compile(
        r'^[^\S\r\n]*(?:[-*+][^\S\r\n]+)?\*\*(?:'
        + re.escape(field_name)
        + r'|'
        + re.escape(heading)
        + r'):?\*\*\s+([^\s"\',\]\[;}\n]+)',
        re.IGNORECASE | re.MULTILINE,
    )
    m = bold_pat.search(prompt)
    if m:
        return m.group(1).strip().strip("\"'")

    # Markdown heading form: ## Field name followed by non-list, non-heading value on next line.
    # First char of value line must not be a bullet (-/*) or heading (#) marker.
    heading_pat = re.compile(
        r'^#+\s+' + re.escape(heading) + r'[^\n]*\n\s*([^#\-*\s\n][^\n]*)',
        re.IGNORECASE | re.MULTILINE,
    )
    m = heading_pat.search(prompt)
    if m:
        val = m.group(1).strip().strip("\"'`")
        return val or None

    # Inline/JSON-style form: field_name: value  or  "field_name": "value"
    pat = re.compile(
        r'\b' + re.escape(field_name) + r'\b["\'\s:=]+([^\s"\',\]\[;}\n]+)',
        re.IGNORECASE,
    )
    m = pat.search(prompt)
    if m:
        return m.group(1).strip().strip("\"'")

    return None

def _prose_list(prompt: str, field_name: str) -> list:
    """Extract a list value from prose.

    Handles canonical shapes:
      JSON body:        "active_rubrics": ["code-vs-spec"]
      Prose:            active_rubrics: ["code-vs-spec"]
      Markdown-bold:    **active_rubrics**: ["code-vs-spec"]
      Table-cell:       | `active_rubrics` | ["code-vs-spec"] |
      Heading+bullets:  ## Active rubrics followed by bullet items
      Bold+bullets:     **active_rubrics:** followed by bullet items

    Separator class includes *, `, |, :, =, space, quotes to cover all bracket forms.
    """
    # Existing bracket form (also covers bold-bracket via * in separator class)
    pat = re.compile(
        r'\b' + re.escape(field_name) + r'\b[*`|"\'\s:=]+\[([^\]]+)\]',
        re.IGNORECASE | re.DOTALL,
    )
    m = pat.search(prompt)
    if m:
        raw = m.group(1)
        return [s.strip().strip("\"'") for s in raw.split(",") if s.strip().strip("\"'")]

    heading = _field_to_heading(field_name)

    # Markdown heading + bullet form: ## Field name followed by bullet items
    heading_pat = re.compile(
        r'^#+\s+' + re.escape(heading) + r'[^\n]*\n((?:[ \t]*[-*]\s+[^\n]+\n?)+)',
        re.IGNORECASE | re.MULTILINE,
    )
    m = heading_pat.search(prompt)
    if m:
        body = m.group(1)
        items = re.findall(r'[-*]\s+([^\n]+)', body)
        return [item.strip().strip("\"'`") for item in items if item.strip()]

    # Bold-label + bullet form: **field_name:** followed by bullet items
    bold_pat = re.compile(
        r'^[^\S\r\n]*(?:[-*+][^\S\r\n]+)?\*\*(?:'
        + re.escape(field_name)
        + r'|'
        + re.escape(heading)
        + r'):?\*\*\s*\n'
        r'((?:[ \t]*[-*]\s+[^\n]+\n?)+)',
        re.IGNORECASE | re.MULTILINE,
    )
    m = bold_pat.search(prompt)
    if m:
        body = m.group(1)
        items = re.findall(r'[-*]\s+([^\n]+)', body)
        return [item.strip().strip("\"'`") for item in items if item.strip()]

    return []

def _prose_populate(parsed: ParsedPrompt, prompt: str) -> None:
    """Best-effort prose extraction for schema fields."""
    for attr in ("task_id", "session_dir", "target_artifact_type"):
        val = _prose_scalar(prompt, attr)
        if val:
            setattr(parsed, attr, val)
    for attr in ("round", "round_cap"):
        val = _prose_scalar(prompt, attr)
        if val:
            setattr(parsed, attr, _coerce_int(val))
    for attr in ("inputs", "success_criteria", "active_rubrics"):
        lst = _prose_list(prompt, attr)
        if lst:
            setattr(parsed, attr, lst)
    # subagent_type and agent_id — useful for H2/H3
    val = _prose_scalar(prompt, "subagent_type")
    if val:
        parsed.subagent_type = val
    val = _prose_scalar(prompt, "agent_id")
    if val:
        parsed.agent_id = val
    # output_contract — best-effort sub-key extraction
    oc: dict = {}
    for sub_key in ("artifact_path", "sidecar_path"):
        # Use word-boundary to avoid 'artifact_path' matching inside 'target_artifact_path'
        sub_pat = re.compile(
            r'\b' + re.escape(sub_key) + r'\b["\'\s:`|]+([^\s"\',\]\[;`}\n]+)',
            re.IGNORECASE,
        )
        # Iterate all matches so a stop-word hit (e.g. "artifact_path below.") before
        # the real path doesn't shadow the real path — take first match containing '/'.
        for m in sub_pat.finditer(prompt):
            val = m.group(1).strip().strip("`\"'")
            if val and '/' in val:
                oc[sub_key] = val
                break
    rs = _prose_list(prompt, "required_sections")
    if rs:
        oc["required_sections"] = rs
    if oc:
        parsed.output_contract = oc
    # target_artifact_path — top-level for validator/CA recipes
    tap_pat = re.compile(
        r'\btarget_artifact_path\b["\'\s:`|]+([^\s"\',\]\[;`}\n]+)',
        re.IGNORECASE,
    )
    # Take first match containing '/' — stop-word captures (no '/') are skipped.
    for m in tap_pat.finditer(prompt):
        val = m.group(1).strip().strip("`\"'")
        if val and '/' in val:
            parsed.target_artifact_path = val
            break


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_prompt(prompt_text: str) -> ParsedPrompt:
    """Parse a delegation prompt into a ParsedPrompt.

    JSON-first (including ```json fenced blocks), prose fallback.
    Never raises on malformed input — returns a ParsedPrompt with
    mode_detected='prose' and best-effort field population.
    Bypass tokens in the first 20 lines populate bypass_token.
    """
    parsed = ParsedPrompt()

    if not isinstance(prompt_text, str) or not prompt_text:
        return parsed

    parsed.bypass_token = _detect_bypass(prompt_text)

    prompt_body = _strip_inline_files_tail(prompt_text)

    # JSON-mode: try to parse as dict (optionally fence-stripped)
    stripped, was_fenced = _strip_fence(prompt_body)
    try:
        d = json.loads(stripped)
        if isinstance(d, dict):
            parsed.raw_dict = d
            parsed.mode_detected = "json-fenced" if was_fenced else "json"
            _populate_from_dict(parsed, d)
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Prose fallback
    parsed.mode_detected = "prose"
    parsed._raw_text = prompt_body  # preserve for structured presence detection (_prose_field_present)
    _prose_populate(parsed, prompt_body)
    return parsed


def required_field(parsed: ParsedPrompt, dotted_path: str) -> Any:
    """Accessor: returns the resolved value or None on miss (never raises).

    Supports dotted paths like "output_contract.artifact_path".
    Used by H2 to read output_contract sub-keys without re-implementing
    dict traversal.
    """
    parts = dotted_path.split(".", 1)
    top = parts[0]
    val = getattr(parsed, top, None)
    if len(parts) == 1:
        return val
    # Recurse one level into dict sub-keys
    if isinstance(val, dict):
        return val.get(parts[1])
    return None


# ---------------------------------------------------------------------------
# Schema-gate matrix loader (re-exported so H3 can call via parser import)
# ---------------------------------------------------------------------------

_SCHEMA_DOC_REL = ".claude/knowledge/reference/delegation-prompt-schema.md"


def _load_matrix(schema_path: str) -> Optional[dict]:
    """Load YAML schema-gate matrix from delegation-prompt-schema.md.

    Returns dict with universal_required on success. Any agents block is ignored.
    Returns None on any failure — callers degrade to advisory mode.
    This is the same loader H3 uses; re-exported so H3 imports it from here.
    """
    try:
        with open(schema_path) as f:
            content = f.read()
    except OSError as e:
        print(f"[delegation_prompt_parser] cannot read schema doc ({e})", file=sys.stderr)
        return None

    begin_marker = "<!-- schema-gate-matrix:begin -->"
    end_marker = "<!-- schema-gate-matrix:end -->"
    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)
    if begin_idx == -1 or end_idx == -1 or end_idx <= begin_idx:
        print("[delegation_prompt_parser] YAML matrix markers not found", file=sys.stderr)
        return None

    yaml_text = content[begin_idx + len(begin_marker):end_idx]
    try:
        matrix = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        print(f"[delegation_prompt_parser] YAML parse error ({e})", file=sys.stderr)
        return None

    if not isinstance(matrix, dict):
        print("[delegation_prompt_parser] YAML matrix is not a dict", file=sys.stderr)
        return None
    if "universal_required" not in matrix:
        print("[delegation_prompt_parser] YAML matrix missing universal_required", file=sys.stderr)
        return None

    return matrix


def _find_schema_doc(cwd: Optional[str] = None) -> str:
    """Return absolute path to the schema doc from cwd or script-relative root."""
    if cwd:
        return os.path.join(cwd, _SCHEMA_DOC_REL)
    # Derive from this file's location: .claude/hooks/delegation_prompt_parser.py
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(hooks_dir))
    return os.path.join(project_root, _SCHEMA_DOC_REL)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

_BASE_PATH_RESOLUTION_VALUES = frozenset({"worktree-first", "main-only", "explicit"})


def validate_schema(
    parsed: ParsedPrompt,
    subagent_type: str,
    matrix: Optional[dict] = None,
) -> list[SchemaError]:
    """Universal schema check plus matrix-independent shape checks.

    When matrix=None, loads via _load_matrix(). Matrix unloadable →
    returns single SchemaError(reason='matrix-degraded').

    Returns empty list when all checks pass.
    """
    if matrix is None:
        schema_path = _find_schema_doc()
        matrix = _load_matrix(schema_path)
        if matrix is None:
            return [SchemaError(
                field="<matrix>",
                reason="matrix-degraded",
                detail="Schema matrix could not be loaded from delegation-prompt-schema.md",
                remediation="Verify .claude/knowledge/reference/delegation-prompt-schema.md exists and has valid YAML markers",
            )]

    errors: list[SchemaError] = []

    # Universal-required: field key must be PRESENT (mirroring the old word-boundary regex
    # behavior — presence of the token is sufficient; value type/coercion is not checked here).
    # JSON-mode: check key in raw_dict. Prose-mode: word-boundary search on preserved raw text.
    universal_required: list[str] = [
        field_name
        for field_name in matrix.get("universal_required", [])
        if field_name != "session_dir"
    ]

    for field_name in universal_required:
        if parsed.raw_dict is not None:
            # JSON-mode: key present in parsed dict = present (regardless of coerced type)
            present = field_name in parsed.raw_dict
        elif parsed._raw_text is not None:
            # Prose-mode: structured presence detection — accepts inline colon,
            # markdown heading, and bold-label forms; rejects bare prose mentions.
            present = _prose_field_present(parsed._raw_text, field_name)
        else:
            # No source available — conservative miss
            present = False
        if not present:
            errors.append(SchemaError(
                field=field_name,
                reason="missing-required",
                detail=f"Universal-required field '{field_name}' is absent or empty",
                remediation=f"Add '{field_name}' to the delegation prompt",
            ))

    # Cross-field: base_path / base_path_resolution
    if parsed.base_path is not None:
        if not isinstance(parsed.base_path, str) or not parsed.base_path:
            errors.append(SchemaError(
                field="base_path",
                reason="wrong-type",
                detail=f"base_path must be a non-empty string (got {type(parsed.base_path).__name__!r})",
                remediation="Set base_path to a non-empty string",
            ))
        elif not os.path.isabs(parsed.base_path):
            errors.append(SchemaError(
                field="base_path",
                reason="relative-path-not-allowed",
                detail=f"base_path must be absolute (got relative {parsed.base_path!r})",
                remediation="Use an absolute path for base_path",
            ))
    if parsed.base_path_resolution is not None:
        # Guard unhashable types (e.g. list) before set membership check
        bpr = parsed.base_path_resolution
        if not isinstance(bpr, str) or bpr not in _BASE_PATH_RESOLUTION_VALUES:
            errors.append(SchemaError(
                field="base_path_resolution",
                reason="enum-violation",
                detail=f"base_path_resolution must be one of {sorted(_BASE_PATH_RESOLUTION_VALUES)} (got {bpr!r})",
                remediation=f"Set base_path_resolution to one of: {', '.join(sorted(_BASE_PATH_RESOLUTION_VALUES))}",
            ))
    if parsed.base_path_resolution == "explicit" and parsed.base_path is None:
        errors.append(SchemaError(
            field="base_path_resolution",
            reason="cross-field-inconsistent",
            detail="base_path_resolution='explicit' requires base_path to be set (no fallback root)",
            remediation="Add base_path when using base_path_resolution='explicit'",
        ))
    if (parsed.base_path_resolution == "worktree-first"
            and parsed.base_path is not None
            and isinstance(parsed.base_path, str)
            and os.path.isabs(parsed.base_path)):
        # Semantic check: worktree-first with a main-only base_path
        try:
            hooks_dir = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, hooks_dir)
            from caa_paths import is_under_main_only  # type: ignore
            import pathlib
            if is_under_main_only(pathlib.Path(parsed.base_path)):
                errors.append(SchemaError(
                    field="base_path_resolution",
                    reason="cross-field-inconsistent",
                    detail=(
                        f"base_path_resolution='worktree-first' but base_path {parsed.base_path!r} "
                        f"resolves under main-only territory"
                    ),
                    remediation="Use the worktree root, or set base_path_resolution='main-only' if main was intended",
                ))
        except ImportError:
            pass  # caa_paths unavailable — skip semantic check; type/enum checks above still ran

    return errors
