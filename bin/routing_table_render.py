"""routing_table_render.py - Per-session routing table injection for Path C.

Testability rationale: the render function needs direct import access for
unit-style verification. Inlining inside claude-session (which is not
importable as a module due to the hyphen in its name) would block that. This
module is a parallel to bin/planner_synth.py: pure render + I/O wrapper.

RESERVED SENTINEL STRINGS - do NOT use these literal strings in any agent
body, manifest, docs example, or knowledge file other than the orchestrator
prompt render target, legacy records that still carry retired render-injection
markers, tests, and this render module:
    <!-- ROUTING_TABLE_START -->
    <!-- ROUTING_TABLE_END -->
    <!-- RENDER_INJECT_START -->
    <!-- RENDER_INJECT_END -->
ROUTING_TABLE_* strings are load-bearing truncation markers.
RENDER_INJECT_* strings are legacy-reserved: they are no longer extraction
markers, but the renderer still collision-guards them so legacy marker literals
cannot enter the rendered prompt body.
"""

import pathlib

_SENTINEL_START = "<!-- ROUTING_TABLE_START -->"
_SENTINEL_END = "<!-- ROUTING_TABLE_END -->"
_RENDER_INJECT_START = "<!-- RENDER_INJECT_START -->"
_RENDER_INJECT_END = "<!-- RENDER_INJECT_END -->"

_REQUIRED_HEADINGS = ("### Producers", "### Critics / gates")
_EXPECTED_AGENT_ROWS = (
    "agent-content-author",
    "architect",
    "design-planner",
    "diagnostician",
    "implementer",
    "planner",
    "researcher",
    "solution-designer",
    "synthesizer",
    "coherence-auditor",
    "pre-flight-gate",
    "surface-gate",
    "validator",
)
_RESERVED_SOURCE_LITERALS = (
    _SENTINEL_START,
    _SENTINEL_END,
    _RENDER_INJECT_START,
    _RENDER_INJECT_END,
)


class RoutingTableRenderError(Exception):
    """Raised on structural problems composing the routing table section."""


def _strip_outer_blank_lines(text: str) -> str:
    lines = text.splitlines()
    start_idx = 0
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    end_idx = len(lines)
    while end_idx > start_idx and not lines[end_idx - 1].strip():
        end_idx -= 1

    return "\n".join(lines[start_idx:end_idx])


def render_routing_table_section(source_path: pathlib.Path) -> str:
    """Pure render. Returns the injected string including both sentinels.

    Raises:
        RoutingTableRenderError: if the source file is missing, cannot be read,
            has malformed record metadata, fails required body assertions, or
            contains a reserved sentinel literal.
    """
    source_path = pathlib.Path(source_path)
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RoutingTableRenderError(
            f"Routing table source file not found: {source_path}"
        ) from exc
    except OSError as exc:
        raise RoutingTableRenderError(
            f"Routing table source file could not be read: {source_path}: {exc}"
        ) from exc

    frontmatter_candidate = source_text.lstrip()
    if frontmatter_candidate.startswith("<!-- record-meta"):
        close_idx = frontmatter_candidate.find("-->")
        if close_idx == -1:
            raise RoutingTableRenderError(
                "Routing table source has malformed record-meta frontmatter: "
                "missing closing '-->'."
            )
        source_body = frontmatter_candidate[close_idx + len("-->"):]
    else:
        source_body = source_text

    source_body = _strip_outer_blank_lines(source_body)
    if not source_body.strip():
        raise RoutingTableRenderError(
            "Routing table source body is empty after record-meta stripping."
        )

    missing_headings = [
        heading for heading in _REQUIRED_HEADINGS if heading not in source_body
    ]
    if missing_headings:
        raise RoutingTableRenderError(
            "Routing table source body is missing required heading(s): "
            + ", ".join(repr(heading) for heading in missing_headings)
            + "."
        )

    missing_agents = [
        agent for agent in _EXPECTED_AGENT_ROWS if agent not in source_body
    ]
    if missing_agents:
        raise RoutingTableRenderError(
            "Routing table source body is missing expected agent row(s): "
            + ", ".join(repr(agent) for agent in missing_agents)
            + "."
        )

    # Sentinel false-match guard: only the extracted body is copied verbatim
    # between routing markers, so reserved literals must not appear there.
    sentinel_collisions = [
        literal for literal in _RESERVED_SOURCE_LITERALS if literal in source_body
    ]
    if sentinel_collisions:
        raise RoutingTableRenderError(
            "Routing table body contains reserved sentinel string(s): "
            + ", ".join(repr(literal) for literal in sentinel_collisions)
            + ". These strings are reserved for the routing-table compose "
            "mechanism and must not appear in rendered source content."
        )

    section = _SENTINEL_START + "\n" + source_body
    if not section.endswith("\n"):
        section += "\n"
    section += _SENTINEL_END + "\n"
    return section


def compose_and_inject_routing_table(
    rendered_prompt_path: pathlib.Path,
    source_path: pathlib.Path,
    fail_open: bool = False,
    logger=None,
) -> tuple[int, int]:
    """Read rendered prompt, replace prior routing section, and write back.

    Args:
        rendered_prompt_path: path to orchestrator-prompt.rendered.md.
        source_path: path to the routing-table source knowledge record.
        fail_open: if True, compose-time RoutingTableRenderError logs a warning
            before re-raising for the launcher to degrade visibly.
        logger: callable(msg: str) -> None; e.g. lambda m: _log(state_dir, m).

    Returns:
        (final_size_bytes, append_size_bytes).

    Raises:
        RoutingTableRenderError: on compose-time structural errors.
        FileNotFoundError: if rendered_prompt_path does not exist.
    """
    rendered_prompt_path = pathlib.Path(rendered_prompt_path)
    current_text = rendered_prompt_path.read_text(encoding="utf-8")

    try:
        new_section = render_routing_table_section(source_path)
    except RoutingTableRenderError as _exc:
        if fail_open and logger:
            logger(
                f"routing-table injection failed ({_exc}); "
                f"fail_open=true - using unmodified rendered orchestrator prompt"
            )
        raise

    insertion_idx = current_text.find(_SENTINEL_START)
    base_text = _truncate_prior_section(current_text, fail_open=fail_open, logger=logger)
    if insertion_idx == -1:
        insertion_idx = len(base_text.rstrip("\n"))
        base_text = base_text.rstrip("\n") + "\n"

    final_text = base_text[:insertion_idx] + new_section + base_text[insertion_idx:]
    final_bytes = final_text.encode("utf-8")
    append_bytes = new_section.encode("utf-8")

    rendered_prompt_path.write_text(final_text, encoding="utf-8")

    return (len(final_bytes), len(append_bytes))


def _truncate_prior_section(text: str, *, fail_open: bool, logger) -> str:
    """Remove any prior ROUTING_TABLE sentinel-bounded section from text.

    If START is absent: text is pristine - return as-is.
    If START+END both present (END after START): strip [START..END] inclusive.
    If START present but END absent (or END before START): treat as corrupted.
        fail_open=False: raise RoutingTableRenderError.
        fail_open=True: log warning, truncate from START to EOF (best-effort).
    """
    start_idx = text.find(_SENTINEL_START)
    if start_idx == -1:
        return text

    end_idx = text.find(_SENTINEL_END)
    if end_idx != -1 and end_idx > start_idx:
        end_of_end = end_idx + len(_SENTINEL_END)
        if end_of_end < len(text) and text[end_of_end] == "\n":
            end_of_end += 1
        return text[:start_idx] + text[end_of_end:]

    msg = (
        "orchestrator-prompt rendered sentinel corrupted: ROUTING_TABLE_START "
        "found without a matching ROUTING_TABLE_END. This indicates a partial "
        "write from a previous run."
    )
    if fail_open:
        if logger:
            logger(f"WARNING: {msg} Truncating at sentinel (fail_open=true).")
        return text[:start_idx]
    raise RoutingTableRenderError(msg)
