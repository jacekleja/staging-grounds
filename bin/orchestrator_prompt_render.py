#!/usr/bin/env python3
"""Preprocessor for compose-time layering markup.

Strips inactive <!-- IF name -->...<!-- /IF name --> blocks from any source
file matching the grammar and writes the rendered result to an output file.
"""
# Conditional content in source files is always-inline and gated by
# registry-named pipelines. Add a new pipeline only via the 2-step recipe in
# .claude/knowledge/meta/prompt-design.md § Compose-Time Layering Markup
# (Adding a new pipeline).

import re
import sys

_OPEN_RE = re.compile(r"^\s*<!--\s*IF\s+([a-z][a-z0-9_-]*)\s*-->\s*$")
_CLOSE_RE = re.compile(r"^\s*<!--\s*/IF\s+([a-z][a-z0-9_-]*)\s*-->\s*$")
_ANY_MARKER_RE = re.compile(r"<!--\s*/?IF\s+[a-z][a-z0-9_-]*\s*-->")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_MIN_OUTPUT_CHARS = 500


def _classify(name: str, active_flags: set, registry: set, lineno: int) -> tuple:
    """Return (keep_inner, preserve_markers); emit stderr warning if name unknown."""
    if name not in registry:
        print(
            f"WARNING: marker references unknown pipeline '{name}' (line {lineno});"
            f" preserving content (fail-open). Registry: [{', '.join(sorted(registry))}]",
            file=sys.stderr,
        )
        return (True, True)
    return (True, False) if name in active_flags else (False, False)


def render(
    template_path: str,
    active_flags: set,
    registry: set,
    output_path: str,
) -> str:
    """Strip inactive <!-- IF name -->...<!-- /IF name --> blocks from any source file.

    Returns the rendered content as a string (also written to output_path).
    The string-return supports the argv-fallback path: if Claude Code removes
    --append-system-prompt-file, the caller can invoke
    subprocess.Popen(['claude', '--append-system-prompt', rendered, ...]).
    List-form argv avoids shell-quoting concerns; an 8K-token prompt is ~32 KB
    UTF-8, well under Linux ARG_MAX (>=128 KB on every supported kernel).

    Raises:
        ValueError: nested IF block, unclosed block, stray close marker,
                    mismatched close name, inline marker, or rendered < 500 chars.
    """
    active_flags, registry = set(active_flags), set(registry)  # defensive cast

    with open(template_path, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh]

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

    if len(rendered.strip()) < _MIN_OUTPUT_CHARS:
        raise ValueError(
            f"rendered output is {len(rendered.strip())} chars"
            f" (< {_MIN_OUTPUT_CHARS}); catastrophic truncation suspected"
        )

    # Write only on success — a ValueError above leaves output_path untouched.
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return rendered


# Backcompat alias — callers that imported the old name continue to work.
# The alias binds both names to the same function object, so kwargs
# (`template_path=...`) passed to either name reach the same signature.
render_orchestrator_prompt = render


if __name__ == "__main__":
    # CSV parsing uses str.split(","), not shlex — names match [a-z][a-z0-9_-]*
    # and cannot contain commas, so csv.reader is unnecessary.
    argv = sys.argv
    if len(argv) != 5:
        print(
            f"usage: {argv[0]} <template_path> <output_path> <active-csv> <registry-csv>\n"
            f"  empty CSV = empty set (pass '' for no flags / no registry)",
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
