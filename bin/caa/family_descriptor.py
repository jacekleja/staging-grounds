"""family_descriptor.py — FamilyDescriptor dataclass + JSON Schema validator.

Loads and validates .claude/families/<family>.json against the six required
fields defined in the S14 architect design. Schema validation rejects unknown
enum values with a hard error (no silent degradation).

Usage:
    from caa.family_descriptor import FamilyDescriptor, load_family
    desc = load_family('claude', main_root)
"""

import json
import pathlib
from dataclasses import dataclass
from typing import Any

# ── JSON Schema for family.json (closed enum sets per design §family.json schema) ──

_FAMILY_JSON_SCHEMA = {
    "type": "object",
    "required": [
        "schema_version",
        "family",
        "cli_command",
        "system_prompt_delivery_mode",
        "mcp_registration_file_path",
        "agent_registry_mode",
        "token_measurement_mechanism",
        "auth_lifecycle",
    ],
    "additionalProperties": True,
    "properties": {
        "schema_version": {"type": "string"},
        "family": {"type": "string", "enum": ["claude", "codex", "gemini"]},
        "cli_command": {
            "type": "object",
            "required": ["binary", "invocation_args", "prompt_mode", "family_disambiguator"],
            "properties": {
                "binary": {"type": "string"},
                "invocation_args": {"type": "array", "items": {"type": "string"}},
                "prompt_mode": {"type": "string", "enum": ["print", "exec", "prompt-arg"]},
                "family_disambiguator": {"type": "string"},
            },
        },
        "system_prompt_delivery_mode": {
            "type": "object",
            "required": ["mode", "flag_template", "notes"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [
                        "cli_flag_file",
                        "model_instructions_file_c_override",
                        "workspace_geminimd",
                        "stdin_degraded",
                    ],
                },
                "flag_template": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "mcp_registration_file_path": {
            "type": "object",
            "required": ["path_template", "format", "notes"],
            "properties": {
                "path_template": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["json_object", "toml_array_blocks", "json_nested_object"],
                },
                "install_script": {"type": ["string", "null"]},
                "notes": {"type": "string"},
            },
        },
        "agent_registry_mode": {
            "type": "object",
            "required": ["mode", "notes"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [
                        "filesystem_scan_claude_native",
                        "prerendered_in_orchestrator_prompt",
                        "list_agents_mcp_tool",
                    ],
                },
                "scan_root_template": {"type": ["string", "null"]},
                "prerender_section": {"type": ["string", "null"]},
                "notes": {"type": "string"},
            },
        },
        "token_measurement_mechanism": {
            "type": "object",
            "required": ["mechanism", "source_path_template", "parse_strategy", "polling_interval_ms"],
            "properties": {
                "mechanism": {
                    "type": "string",
                    "enum": [
                        "transcript_jsonl_poll",
                        "rollout_jsonl_token_count_events",
                        "stream_json_token_events",
                    ],
                },
                "source_path_template": {"type": "string"},
                "parse_strategy": {"type": "string"},
                "polling_interval_ms": {"type": "number"},
            },
        },
        "auth_lifecycle": {
            "type": "object",
            "required": ["mode", "required_env_vars", "refresh_strategy", "notes"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [
                        "claude_oauth_platform_managed",
                        "codex_api_key_env",
                        "gemini_oauth_proactive_refresh",
                    ],
                },
                "required_env_vars": {"type": "array", "items": {"type": "string"}},
                "refresh_strategy": {
                    "type": "string",
                    "enum": ["platform_managed", "none_static_key", "pre_launch_refresh_recommended"],
                },
                "pre_launch_check": {"type": ["string", "null"]},
                "notes": {"type": "string"},
            },
        },
    },
}


class FamilyDescriptorError(Exception):
    """Raised when family.json fails validation."""


def _validate_schema(data: dict[str, Any], schema: dict, path: str = "") -> list[str]:
    """Minimal JSON Schema validator — handles type, required, enum, properties.

    Returns list of error strings. An empty list means valid.
    Only covers the subset of JSON Schema used in _FAMILY_JSON_SCHEMA.
    """
    errors: list[str] = []

    # type check
    if "type" in schema:
        expected_types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        type_map = {
            "object": dict,
            "array": list,
            "string": str,
            "number": (int, float),
            "null": type(None),
        }
        ok = False
        for et in expected_types:
            py_type = type_map.get(et)
            if py_type is not None and isinstance(data, py_type):
                ok = True
                break
        if not ok:
            errors.append(f"{path or '.'}: expected type(s) {expected_types}, got {type(data).__name__}")
            return errors  # no point checking sub-constraints when type is wrong

    # enum check
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path or '.'}: {data!r} not in allowed values {schema['enum']}")

    # required fields
    if "required" in schema and isinstance(data, dict):
        for req in schema["required"]:
            if req not in data:
                errors.append(f"{path or '.'}: missing required field '{req}'")

    # properties
    if "properties" in schema and isinstance(data, dict):
        for key, sub_schema in schema["properties"].items():
            if key in data:
                sub_errors = _validate_schema(
                    data[key], sub_schema, path=f"{path}.{key}" if path else key
                )
                errors.extend(sub_errors)

    # items (for arrays)
    if "items" in schema and isinstance(data, list):
        for i, item in enumerate(data):
            sub_errors = _validate_schema(item, schema["items"], path=f"{path}[{i}]")
            errors.extend(sub_errors)

    return errors


@dataclass(frozen=True)
class CliCommand:
    binary: str
    invocation_args: tuple[str, ...]
    prompt_mode: str  # "print" | "exec" | "prompt-arg"
    family_disambiguator: str


@dataclass(frozen=True)
class SystemPromptDeliveryMode:
    mode: str   # "cli_flag_file" | "model_instructions_file_c_override" | etc.
    flag_template: str
    notes: str


@dataclass(frozen=True)
class McpRegistrationFilePath:
    path_template: str
    format: str   # "json_object" | "toml_array_blocks" | "json_nested_object"
    install_script: str | None
    notes: str


@dataclass(frozen=True)
class AgentRegistryMode:
    mode: str   # "filesystem_scan_claude_native" | "prerendered_in_orchestrator_prompt" | ...
    scan_root_template: str | None
    prerender_section: str | None
    notes: str


@dataclass(frozen=True)
class TokenMeasurementMechanism:
    mechanism: str  # "transcript_jsonl_poll" | "rollout_jsonl_token_count_events" | ...
    source_path_template: str
    parse_strategy: str
    polling_interval_ms: int


@dataclass(frozen=True)
class AuthLifecycle:
    mode: str   # "claude_oauth_platform_managed" | "codex_api_key_env" | ...
    required_env_vars: tuple[str, ...]
    refresh_strategy: str
    pre_launch_check: str | None
    notes: str


@dataclass(frozen=True)
class FamilyDescriptor:
    """Parsed and schema-validated representation of .claude/families/<family>.json.

    Frozen (immutable) — the descriptor is read-only at runtime per design.
    """

    schema_version: str
    family: str
    cli_command: CliCommand
    system_prompt_delivery_mode: SystemPromptDeliveryMode
    mcp_registration_file_path: McpRegistrationFilePath
    agent_registry_mode: AgentRegistryMode
    token_measurement_mechanism: TokenMeasurementMechanism
    auth_lifecycle: AuthLifecycle
    # Raw dict preserved for forward-compat: future fields not yet modelled survive.
    _raw: dict = None  # type: ignore[assignment]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FamilyDescriptor":
        """Construct a FamilyDescriptor from a parsed dict (already validated)."""
        cli = data["cli_command"]
        spd = data["system_prompt_delivery_mode"]
        mcp = data["mcp_registration_file_path"]
        arm = data["agent_registry_mode"]
        tok = data["token_measurement_mechanism"]
        auth = data["auth_lifecycle"]
        return cls(
            schema_version=data["schema_version"],
            family=data["family"],
            cli_command=CliCommand(
                binary=cli["binary"],
                invocation_args=tuple(cli["invocation_args"]),
                prompt_mode=cli["prompt_mode"],
                family_disambiguator=cli["family_disambiguator"],
            ),
            system_prompt_delivery_mode=SystemPromptDeliveryMode(
                mode=spd["mode"],
                flag_template=spd["flag_template"],
                notes=spd["notes"],
            ),
            mcp_registration_file_path=McpRegistrationFilePath(
                path_template=mcp["path_template"],
                format=mcp["format"],
                install_script=mcp.get("install_script"),
                notes=mcp["notes"],
            ),
            agent_registry_mode=AgentRegistryMode(
                mode=arm["mode"],
                scan_root_template=arm.get("scan_root_template"),
                prerender_section=arm.get("prerender_section"),
                notes=arm["notes"],
            ),
            token_measurement_mechanism=TokenMeasurementMechanism(
                mechanism=tok["mechanism"],
                source_path_template=tok["source_path_template"],
                parse_strategy=tok["parse_strategy"],
                polling_interval_ms=int(tok["polling_interval_ms"]),
            ),
            auth_lifecycle=AuthLifecycle(
                mode=auth["mode"],
                required_env_vars=tuple(auth.get("required_env_vars", [])),
                refresh_strategy=auth["refresh_strategy"],
                pre_launch_check=auth.get("pre_launch_check"),
                notes=auth["notes"],
            ),
            _raw=data,
        )


def load_family(family_name: str, main_root: pathlib.Path) -> FamilyDescriptor:
    """Read .claude/families/<family_name>.json, validate, and return a FamilyDescriptor.

    Exits with a non-zero status before any worktree is created if validation fails.
    Raises FamilyDescriptorError on validation failure (callers convert to sys.exit).
    """
    family_path = main_root / ".claude" / "families" / f"{family_name}.json"
    if not family_path.exists():
        raise FamilyDescriptorError(
            f"Family descriptor not found: {family_path}. "
            f"Create .claude/families/{family_name}.json or choose a supported family."
        )

    try:
        with open(family_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        raise FamilyDescriptorError(f"Invalid JSON in {family_path}: {e}") from e

    errors = _validate_schema(data, _FAMILY_JSON_SCHEMA)
    if errors:
        err_lines = "\n  ".join(errors)
        raise FamilyDescriptorError(
            f"family.json validation failed for {family_name!r}:\n  {err_lines}"
        )

    return FamilyDescriptor.from_dict(data)
