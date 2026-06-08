"""launch_value_render.py - Per-session launch-value injection for Path C.

Testability rationale: these render functions need direct import access for
unit-style verification. Inlining inside claude-session (which is not
importable as a module due to the hyphen in its name) would block that. This
module is a parallel to bin/worktree_map_render.py: pure render + I/O wrapper.

RESERVED SENTINEL STRINGS - do NOT use these literal strings in any agent
body, manifest, docs example, or knowledge file other than the orchestrator
prompt render target, its seeded fallback slots, tests, and this render module:
    <!-- PIPELINES_START -->
    <!-- PIPELINES_END -->
PIPELINES_* strings are load-bearing truncation markers.
"""

import json
import os
import pathlib
import tempfile
from typing import Any

_PIPELINES_SENTINEL_START = "<!-- PIPELINES_START -->"
_PIPELINES_SENTINEL_END = "<!-- PIPELINES_END -->"

_PIPELINES_FAILURE_PAYLOAD = (
    "**SESSION PIPELINES AUTO-INJECT FAILED.** The launcher failed to produce "
    "valid pipeline metadata ({session_dir}/pipelines.json missing or malformed "
    "at render time). There is NO safe default pipeline set — do NOT assume an "
    "empty or full set, and do NOT read pipelines.json (the launcher failed to "
    "write it). Investigate the launcher failure before relying on any "
    "pipeline-residue attribution."
)


class LaunchValueRenderError(Exception):
    """Raised on structural problems composing a launch-value section."""


def _build_section(sentinel_start: str, payload: str, sentinel_end: str) -> str:
    """Return a complete sentinel-bounded launch-value block."""
    if sentinel_start in payload or sentinel_end in payload:
        raise LaunchValueRenderError(
            "Generated launch-value payload contains its reserved sentinel literal."
        )
    return sentinel_start + "\n" + payload + "\n" + sentinel_end + "\n"


def _load_pipelines_payload(session_dir: pathlib.Path) -> tuple[list[Any], list[Any]] | None:
    pipelines_path = pathlib.Path(session_dir) / "pipelines.json"
    if not pipelines_path.is_file():
        return None

    try:
        payload = json.loads(pipelines_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    active_pipelines = payload.get("active_pipelines")
    registry = payload.get("registry")
    if not isinstance(active_pipelines, list) or not isinstance(registry, list):
        return None

    return active_pipelines, registry


def _json_list(value: list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_session_pipelines_section(session_dir: pathlib.Path) -> str:
    """Pure render. Returns the injected PIPELINES section.

    Missing or malformed pipeline metadata resolves to the byte-frozen
    AUTO-INJECT-FAILED text because there is no safe default pipeline set.
    """
    resolved = _load_pipelines_payload(session_dir)
    if resolved is None:
        payload = _PIPELINES_FAILURE_PAYLOAD
    else:
        active_pipelines, registry = resolved
        payload = (
            "**Session pipelines (resolved at launch):** active_pipelines = "
            f"`{_json_list(active_pipelines)}`; registry = `{_json_list(registry)}`."
        )

    return _build_section(
        _PIPELINES_SENTINEL_START,
        payload,
        _PIPELINES_SENTINEL_END,
    )


def _atomic_write(target: pathlib.Path, content: str) -> None:
    """Write content to target atomically via temp-file + os.replace."""
    target = pathlib.Path(target)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_replace_section(
    target: pathlib.Path,
    new_section: str,
    sentinel_start: str,
    sentinel_end: str,
    label: str,
    logger=None,
) -> None:
    """Replace or append one sentinel-bounded section atomically.

    If the sentinel pair is malformed or absent, existing bytes are preserved and
    the new section is appended at EOF.
    """
    target = pathlib.Path(target)
    current_text = target.read_text(encoding="utf-8")

    start_idx = current_text.find(sentinel_start)
    end_idx = current_text.find(sentinel_end)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        end_of_end = end_idx + len(sentinel_end)
        if end_of_end < len(current_text) and current_text[end_of_end] == "\n":
            end_of_end += 1
        final_text = current_text[:start_idx] + new_section + current_text[end_of_end:]
    else:
        if logger:
            logger(
                f"[launch-value] WARNING: {label} sentinels malformed/absent; "
                "appending block at EOF (existing prompt bytes preserved)"
            )
        base = current_text.rstrip("\n") + "\n"
        final_text = base + new_section

    _atomic_write(target, final_text)


def compose_and_inject_session_pipelines(
    rendered_prompt_path: pathlib.Path,
    session_dir: pathlib.Path,
    logger=None,
) -> tuple[int, int]:
    """Read rendered prompt, replace PIPELINES section, and write back."""
    rendered_prompt_path = pathlib.Path(rendered_prompt_path)
    session_dir = pathlib.Path(session_dir)

    try:
        new_section = render_session_pipelines_section(session_dir)
    except LaunchValueRenderError as exc:
        if logger:
            logger(
                f"[launch-value] WARNING: session-pipelines render failed ({exc}); "
                "injecting AUTO-INJECT-FAILED payload"
            )
        new_section = _build_section(
            _PIPELINES_SENTINEL_START,
            _PIPELINES_FAILURE_PAYLOAD,
            _PIPELINES_SENTINEL_END,
        )
    _safe_replace_section(
        rendered_prompt_path,
        new_section,
        _PIPELINES_SENTINEL_START,
        _PIPELINES_SENTINEL_END,
        "session-pipelines",
        logger=logger,
    )

    final_bytes = rendered_prompt_path.read_bytes()
    section_bytes = new_section.encode("utf-8")
    return (len(final_bytes), len(section_bytes))
