"""planner_synth.py — Per-session planner prompt synthesis for Path C.

Testability rationale: the render function needs pytest access without a
filesystem. Inlining inside claude-session (which is not importable as a
module due to the hyphen in its name) would block that. This module is a
parallel to bin/orchestrator_prompt_render.py — pure render + I/O wrapper.

Parity note: mirrors the orchestrator_prompt_render.py pattern (pure
function + compose wrapper + PlannerSynthError).

RESERVED SENTINEL STRINGS — do NOT use these literal strings in any
pipeline manifest, docs example, or agent file:
    <!-- PATH_C_PLANNER_SECTION_START -->
    <!-- PATH_C_PLANNER_SECTION_END -->
These strings are load-bearing truncation markers. Collision causes
compose_and_append_planner to truncate mid-content on the next re-render.

fail_open controls compose-time defensive fallback ONLY. Loader-level
malformed manifests (e.g., missing required fields, path traversal in
rules/skills) always fail closed at session start regardless of this flag
— see bin/pipeline_manifest.py:load_pipeline_manifests.
"""

import pathlib
from pipeline_manifest import PipelineManifest

_SENTINEL_START = "<!-- PATH_C_PLANNER_SECTION_START -->"
_SENTINEL_END = "<!-- PATH_C_PLANNER_SECTION_END -->"


class PlannerSynthError(Exception):
    """Raised on structural problems composing the Active Pipelines section."""


def render_active_pipelines_section(
    ordered_names: list[str],
    manifests: dict[str, PipelineManifest],
) -> str:
    """Pure render. Returns the append-string including both sentinels,
    or '' when ordered_names is empty (empty-set contract).

    Raises:
        PlannerSynthError: if an active name is missing from manifests,
            a manifest field has the wrong type, or a planner_snippet
            contains a reserved sentinel literal.
    """
    if not ordered_names:
        return ""

    lines: list[str] = [_SENTINEL_START, "", "## Active Pipelines", ""]

    for name in ordered_names:
        if name not in manifests:
            raise PlannerSynthError(
                f"Active pipeline {name!r} not found in manifests dict."
            )
        m = manifests[name]

        # Defensive type checks — loader should have caught these, but
        # compose-time surprises (e.g. mock data with wrong types) are
        # still possible.
        if not isinstance(m.summary, str):
            raise PlannerSynthError(
                f"Pipeline {name!r}: manifest.summary must be str, "
                f"got {type(m.summary).__name__}."
            )
        if not isinstance(m.agents, (list, tuple)):
            raise PlannerSynthError(
                f"Pipeline {name!r}: manifest.agents must be list/tuple, "
                f"got {type(m.agents).__name__}."
            )
        if m.planner_snippet is not None and not isinstance(m.planner_snippet, str):
            raise PlannerSynthError(
                f"Pipeline {name!r}: manifest.planner_snippet must be str or None, "
                f"got {type(m.planner_snippet).__name__}."
            )

        # Sentinel false-match guard (Flag 4): reject any snippet containing
        # either reserved sentinel literal.
        if m.planner_snippet is not None:
            if _SENTINEL_START in m.planner_snippet or _SENTINEL_END in m.planner_snippet:
                raise PlannerSynthError(
                    f"Pipeline {name!r}: planner_snippet contains a reserved sentinel "
                    f"string ({_SENTINEL_START!r} or {_SENTINEL_END!r}). "
                    f"These strings are reserved for the Path C compose mechanism and "
                    f"must not appear in manifest content."
                )

        # Subsection: ### <name>
        lines.append(f"### {name}")
        lines.append("")

        # Summary paragraph
        if m.summary:
            lines.append(m.summary)
            lines.append("")

        # Agents owned line (.md suffix stripped)
        agents_csv = ", ".join(
            a[: -len(".md")] if a.endswith(".md") else a for a in m.agents
        )
        lines.append(f"**Agents owned:** {agents_csv}")
        lines.append("")

        # Optional planner_snippet verbatim
        if m.planner_snippet:
            lines.append(m.planner_snippet)
            lines.append("")

    lines.append(_SENTINEL_END)
    return "\n".join(lines) + "\n"


def compose_and_append_planner(
    active_pipelines: list[str],
    manifests: dict[str, PipelineManifest],
    worktree_planner_path: pathlib.Path,
    fail_open: bool = False,
    logger=None,
) -> tuple[int, int]:
    """Read worktree planner.md, truncate any prior PATH_C section at sentinel,
    append the newly rendered section (if non-empty), and write back.

    Args:
        active_pipelines: ordered list of active pipeline names.
        manifests: dict mapping name -> PipelineManifest.
        worktree_planner_path: path to the worktree-local planner.md.
        fail_open: if True, compose-time PlannerSynthError logs a warning
            and leaves the file untouched rather than re-raising.
        logger: callable(msg: str) -> None; e.g. lambda m: _log(state_dir, m).

    Returns:
        (final_size_bytes, append_size_bytes).
        When active_pipelines is empty, returns (size_of_current_file, 0) and
        does NOT touch the file (byte-identity contract, Criterion 4).
        When fail_open=True and a PlannerSynthError occurs, returns
        (size_of_current_file, 0) and leaves the file untouched.

    Raises:
        PlannerSynthError: on compose-time errors when fail_open=False.
        FileNotFoundError: if worktree_planner_path does not exist (always
            re-raised — missing file is a hard error regardless of fail_open).
    """
    worktree_planner_path = pathlib.Path(worktree_planner_path)

    # Read current file content — always required, even for empty active list.
    current_text = worktree_planner_path.read_text(encoding="utf-8")
    current_size = len(current_text.encode("utf-8"))

    # Empty active set — byte-identity contract: do not touch the file.
    if not active_pipelines:
        return (current_size, 0)

    try:
        new_section = render_active_pipelines_section(active_pipelines, manifests)
    except PlannerSynthError as _exc:
        if fail_open and logger:
            logger(
                f"planner synthesis failed ({_exc}); "
                f"fail_open=true — using unmodified worktree planner.md"
            )
        # Always re-raise: call site in claude-session is responsible for
        # emitting the stderr warning (fail_open=True) or hard-exiting (False).
        raise

    # Truncate any prior sentinel-bounded section.
    base_text = _truncate_prior_section(current_text, fail_open=fail_open, logger=logger)

    # Normalize trailing newline before append.
    base_text = base_text.rstrip("\n") + "\n"

    final_text = base_text + new_section
    final_bytes = final_text.encode("utf-8")
    append_bytes = new_section.encode("utf-8")

    worktree_planner_path.write_text(final_text, encoding="utf-8")

    return (len(final_bytes), len(append_bytes))


def _truncate_prior_section(text: str, *, fail_open: bool, logger) -> str:
    """Remove any prior PATH_C sentinel-bounded section from text.

    If START is absent: text is pristine — return as-is.
    If START+END both present (END after START): strip [START..END] inclusive.
    If START present but END absent (or END before START): treat as corrupted.
        fail_open=False: raise PlannerSynthError.
        fail_open=True: log warning, truncate from START to EOF (best-effort).
    """
    start_idx = text.find(_SENTINEL_START)
    if start_idx == -1:
        return text

    end_idx = text.find(_SENTINEL_END)
    if end_idx != -1 and end_idx > start_idx:
        # Clean truncation: strip from START to end of END line (including trailing \n).
        end_of_end = end_idx + len(_SENTINEL_END)
        # Consume one trailing newline if present.
        if end_of_end < len(text) and text[end_of_end] == "\n":
            end_of_end += 1
        return text[:start_idx]

    # Corrupted sentinel: START without END (or END before START).
    msg = (
        "planner.md sentinel corrupted: PATH_C_PLANNER_SECTION_START found "
        "without a matching PATH_C_PLANNER_SECTION_END. "
        "This indicates a partial write from a previous run."
    )
    if fail_open:
        if logger:
            logger(f"WARNING: {msg} Truncating at sentinel (fail_open=true).")
        return text[:start_idx]
    raise PlannerSynthError(msg)
