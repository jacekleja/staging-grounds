#!/usr/bin/env python3
"""Preprocessor for compose-time layering markup.

Strips inactive <!-- IF name -->...<!-- /IF name --> blocks from any source
file matching the grammar and writes the rendered result to an output file.
"""
# Conditional content in source files is always-inline and gated by
# registry-named pipelines or reserved non-pipeline activation flags. Add a new
# pipeline only via the 2-step recipe in .claude/knowledge/meta/prompt-design.md
# § Compose-Time Layering Markup (Adding a new pipeline).

import re
import sys

_MARKER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_OPEN_RE = re.compile(r"^\s*<!--\s*IF\s+(\S+)\s*-->\s*$")
_CLOSE_RE = re.compile(r"^\s*<!--\s*/IF\s+(\S+)\s*-->\s*$")
_ANY_MARKER_RE = re.compile(r"<!--\s*/?IF\b.*?-->")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_MIN_OUTPUT_CHARS = 500
_RENDER_FAILURE_MARKERS = ("AUTO-INJECT FAILED", "DEGRADATION:")

# Scope flags are session-scope activation switches (root vs child orchestrator mode)
# and family flags (claude | codex | gemini) for family-conditional content in
# dispatch-l2/SKILL.md (axis-7, CL-3).
# They are NOT pipeline names and MUST NOT appear in bootstrap-config.json:pipelines.registry.
# See design-SUBORCH-v6-total.md §6.2-6.4 (namespace-separation invariant).
_SCOPE_FLAGS = frozenset({"root", "child", "claude", "codex", "gemini"})
_FEATURE_FLAGS = frozenset({"ux_aesthetic_loop"})

_RENDER_INTEGRITY_SENTINELS = {
    "ROUTING_TABLE": ("<!-- ROUTING_TABLE_START -->", "<!-- ROUTING_TABLE_END -->"),
    "WORKTREE_MAP": ("<!-- WORKTREE_MAP_START -->", "<!-- WORKTREE_MAP_END -->"),
    "PIPELINES": ("<!-- PIPELINES_START -->", "<!-- PIPELINES_END -->"),
}


def _degradation_message(block_name: str) -> str:
    if block_name not in _RENDER_INTEGRITY_SENTINELS:
        raise ValueError(f"unknown render-integrity block: {block_name}")
    return (
        f"DEGRADATION: {block_name} failed to render — surface to operator "
        f"immediately, halt {block_name}-dependent work."
    )


def _render_integrity_block(
    block_name: str,
    resolved_content: str,
    *,
    failed: bool,
) -> str:
    """Return a sentinel-bounded slot carrying resolved content or degradation."""
    try:
        sentinel_start, sentinel_end = _RENDER_INTEGRITY_SENTINELS[block_name]
    except KeyError as exc:
        raise ValueError(f"unknown render-integrity block: {block_name}") from exc

    payload = _degradation_message(block_name) if failed else resolved_content.strip("\n")
    if sentinel_start in payload or sentinel_end in payload:
        raise ValueError(
            f"{block_name} payload contains its reserved sentinel literal."
        )
    return sentinel_start + "\n" + payload + "\n" + sentinel_end + "\n"


def _render_integrity_failed(payload: str) -> bool:
    stripped = payload.strip()
    return not stripped or any(marker in stripped for marker in _RENDER_FAILURE_MARKERS)


def _normalize_render_integrity_block(text: str, block_name: str) -> str:
    sentinel_start, sentinel_end = _RENDER_INTEGRITY_SENTINELS[block_name]
    output_parts: list[str] = []
    cursor = 0

    while True:
        start_idx = text.find(sentinel_start, cursor)
        if start_idx == -1:
            output_parts.append(text[cursor:])
            return "".join(output_parts)

        end_idx = text.find(sentinel_end, start_idx + len(sentinel_start))
        if end_idx == -1:
            raise ValueError(
                f"render-integrity block {block_name} has START without matching END"
            )

        payload_start = start_idx + len(sentinel_start)
        payload = text[payload_start:end_idx]
        region_end = end_idx + len(sentinel_end)
        if region_end < len(text) and text[region_end] == "\n":
            region_end += 1

        output_parts.append(text[cursor:start_idx])
        output_parts.append(
            _render_integrity_block(
                block_name,
                payload,
                failed=_render_integrity_failed(payload),
            )
        )
        cursor = region_end


def _normalize_render_integrity_blocks(text: str) -> str:
    for block_name in _RENDER_INTEGRITY_SENTINELS:
        text = _normalize_render_integrity_block(text, block_name)
    return text


def _classify(name: str, active_flags: set, registry: set, lineno: int) -> tuple:
    """Return (keep_inner, preserve_markers); exit non-zero if name unknown.

    Unknown pipeline name is a hard error (typo catches at CI time) per
    pipeline-conditional-content-discipline.md. The fail-open behavior was
    replaced with sys.exit(1) so that CI catches registry mismatches pre-merge.

    Scope and feature flags are resolved first before the registry lookup —
    they are valid IF-block names but are NOT pipelines.
    """
    if not _MARKER_NAME_RE.match(name):
        print(
            f"ERROR: marker name '{name}' on line {lineno} is malformed;"
            " expected [a-z][a-z0-9_-]*.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Reserved non-pipeline flags are resolved by active_flags membership.
    if name in _SCOPE_FLAGS or name in _FEATURE_FLAGS:
        return (True, False) if name in active_flags else (False, False)
    if name not in registry:
        print(
            f"ERROR: marker references unknown pipeline '{name}' (line {lineno});"
            f" not in registry. Registry: [{', '.join(sorted(registry))}]",
            file=sys.stderr,
        )
        sys.exit(1)
    return (True, False) if name in active_flags else (False, False)


def render_string(
    template_text: str,
    active_flags: set,
    registry: set,
) -> str:
    """Strip inactive <!-- IF name -->...<!-- /IF name --> blocks from template_text.

    Pure string-in / string-out function — performs zero file I/O.  The
    caller is responsible for reading the template and writing the output.

    Returns the rendered content as a string.

    Raises:
        ValueError: nested IF block, unclosed block, stray close marker,
                    mismatched close name, inline marker, or rendered < 500 chars.
    """
    active_flags, registry = set(active_flags), set(registry)  # defensive cast

    # splitlines(keepends=True) + rstrip("\n") matches open()-iteration behaviour:
    # each yielded line has its trailing \n stripped, preserving mid-line content.
    lines = [ln.rstrip("\n") for ln in template_text.splitlines(keepends=True)]

    state = "OUT"
    current_name: str | None = None
    open_line: int | None = None
    keep_current, preserve_markers = True, False
    output_lines: list = []

    for lineno, line in enumerate(lines, 1):
        if _ANY_MARKER_RE.search(line) and not (_OPEN_RE.match(line) or _CLOSE_RE.match(line)):
            raise ValueError(
                f"inline IF//IF marker on line {lineno};"
                f" only whole-line markers supported in v1: {line!r}"
            )
        open_m = _OPEN_RE.match(line)
        close_m = _CLOSE_RE.match(line)

        if state == "OUT":
            if open_m:
                name = open_m.group(1)
                state, current_name, open_line = "IN", name, lineno
                keep_current, preserve_markers = _classify(name, active_flags, registry, lineno)
                if preserve_markers:
                    output_lines.append(line)
            elif close_m:
                raise ValueError(
                    f"stray close marker '/IF {close_m.group(1)}' on line {lineno}; no matching open"
                )
            else:
                output_lines.append(line)
        else:  # state == "IN"
            if open_m:
                raise ValueError(
                    f"nested block '{open_m.group(1)}' on line {lineno} inside open"
                    f" block '{current_name}' (opened line {open_line}); v1 grammar is flat-only"
                )
            if close_m:
                close_name = close_m.group(1)
                if close_name != current_name:
                    raise ValueError(
                        f"mismatched close '/IF {close_name}' on line {lineno};"
                        f" expected '/IF {current_name}' (opened line {open_line})"
                    )
                if preserve_markers:
                    output_lines.append(line)
                state, current_name, open_line = "OUT", None, None
                keep_current, preserve_markers = True, False
            elif keep_current:
                output_lines.append(line)

    if state == "IN":
        raise ValueError(
            f"unclosed IF block '{current_name}' opened on line {open_line}; EOF reached"
        )

    rendered = _BLANK_RUN_RE.sub("\n\n", "\n".join(output_lines))
    rendered = _normalize_render_integrity_blocks(rendered)
    rendered = _BLANK_RUN_RE.sub("\n\n", rendered)

    if len(rendered.strip()) < _MIN_OUTPUT_CHARS:
        raise ValueError(
            f"rendered output is {len(rendered.strip())} chars"
            f" (< {_MIN_OUTPUT_CHARS}); catastrophic truncation suspected"
        )

    return rendered


def render(
    template_path: str,
    active_flags: set,
    registry: set,
    output_path: str,
) -> str:
    """Thin file-I/O wrapper around render_string.

    Reads template_path, calls render_string, writes result to output_path.
    Returns the rendered content as a string.

    The string-return supports the argv-fallback path: if Claude Code removes
    --append-system-prompt-file, the caller can invoke
    subprocess.Popen(['claude', '--append-system-prompt', rendered, ...]).
    List-form argv avoids shell-quoting concerns; an 8K-token prompt is ~32 KB
    UTF-8, well under Linux ARG_MAX (>=128 KB on every supported kernel).
    """
    with open(template_path, encoding="utf-8") as fh:
        template_text = fh.read()
    rendered = render_string(template_text, active_flags, registry)
    # Write only on success — a ValueError in render_string leaves output_path untouched.
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return rendered


# Backcompat alias — callers that imported the old name continue to work.
# The alias binds both names to the same function object, so kwargs
# (`template_path=...`) passed to either name reach the same signature.
render_orchestrator_prompt = render


if __name__ == "__main__":
    # --scope <name> form: additive alias for scope-flag activation.
    # Positional CSV invocation (<template> <output> <active-csv> <registry-csv>) is
    # preserved unchanged for backcompat with bin/claude-session (B5) and CI invocations.
    import json as _json
    from pathlib import Path

    argv = sys.argv

    if len(argv) >= 2 and argv[1] == "--scope":
        # --scope <name> [<template> <output>] form.
        # Validates that name is in _SCOPE_FLAGS; loads registry from bootstrap-config.json.
        if len(argv) < 3:
            print(
                f"usage: {argv[0]} --scope <name> [<template_path> <output_path>]\n"
                f"  valid scope names: {sorted(_SCOPE_FLAGS)}",
                file=sys.stderr,
            )
            sys.exit(2)
        _scope_name = argv[2]
        if _scope_name not in _SCOPE_FLAGS:
            print(
                f"ERROR: invalid scope name '{_scope_name}'; "
                f"valid scope names are: {sorted(_SCOPE_FLAGS)}",
                file=sys.stderr,
            )
            sys.exit(1)
        _active = {_scope_name}

        # Template and output paths are optional positional args after --scope <name>.
        if len(argv) >= 5:
            _template, _output = argv[3], argv[4]
        elif len(argv) == 4:
            print(
                f"usage: {argv[0]} --scope <name> <template_path> <output_path>",
                file=sys.stderr,
            )
            sys.exit(2)
        else:
            # No template/output given; derive from canonical locations relative to this script.
            _bin_dir = Path(__file__).resolve().parent
            _root = _bin_dir.parent
            _template = str(_root / ".claude" / "orchestrator-prompt.md")
            _output = str(_root / ".claude" / "orchestrator-prompt-rendered.md")

        # Load registry from bootstrap-config.json adjacent to the project root.
        _bin_dir = Path(__file__).resolve().parent
        _config = _bin_dir.parent / ".claude" / "bootstrap-config.json"
        if _config.exists():
            with open(_config, encoding="utf-8") as _fh:
                _cfg = _json.load(_fh)
            _registry = set(_cfg.get("pipelines", {}).get("registry", []))
        else:
            _registry = set()

        try:
            render_orchestrator_prompt(
                template_path=_template,
                active_flags=_active,
                registry=_registry,
                output_path=_output,
            )
        except ValueError as e:
            print(f"render error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        # Original positional CSV form: <template> <output> <active-csv> <registry-csv>
        # CSV parsing uses str.split(","), not shlex — names match [a-z][a-z0-9_-]*
        # and cannot contain commas, so csv.reader is unnecessary.
        if len(argv) != 5:
            print(
                f"usage: {argv[0]} <template_path> <output_path> <active-csv> <registry-csv>\n"
                f"  empty CSV = empty set (pass '' for no flags / no registry)\n"
                f"  --scope form: {argv[0]} --scope <name> [<template_path> <output_path>]",
                file=sys.stderr,
            )
            sys.exit(2)
        _template, _output, _active_csv, _registry_csv = argv[1], argv[2], argv[3], argv[4]
        _active = {s for s in _active_csv.split(",") if s}
        _registry = {s for s in _registry_csv.split(",") if s}
        try:
            # Kwargs: CLI order (template, output, active, registry) differs from signature order;
            # explicit kwargs survive future signature additions.
            render_orchestrator_prompt(
                template_path=_template,
                active_flags=_active,
                registry=_registry,
                output_path=_output,
            )
        except ValueError as e:
            print(f"render error: {e}", file=sys.stderr)
            sys.exit(1)
