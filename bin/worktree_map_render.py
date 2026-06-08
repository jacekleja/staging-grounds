"""worktree_map_render.py - Per-session worktree-map injection for Path C.

Testability rationale: the render function needs direct import access for
unit-style verification. Inlining inside claude-session (which is not
importable as a module due to the hyphen in its name) would block that. This
module is a parallel to bin/routing_table_render.py: pure render + I/O wrapper.

RESERVED SENTINEL STRINGS - do NOT use these literal strings in any agent
body, manifest, docs example, or knowledge file other than the orchestrator
prompt render target, its seeded fallback slot, tests, and this render module:
    <!-- WORKTREE_MAP_START -->
    <!-- WORKTREE_MAP_END -->
    <!-- PIPELINES_START -->
    <!-- PIPELINES_END -->
WORKTREE_MAP_* strings are load-bearing truncation markers.
PIPELINES_* strings are reserved for launch-value injection.

FAILURE POLICY: this capability is FAIL-LOUD-BUT-CONTINUE. On ANY failure,
inject a 3-part fallback text into the sentinel slot and continue session
launch. Do NOT call sys.exit(), do NOT silently skip or degrade.
"""

import os
import pathlib
import stat
import tempfile

_SENTINEL_START = "<!-- WORKTREE_MAP_START -->"
_SENTINEL_END = "<!-- WORKTREE_MAP_END -->"

# Exact 3-part fallback payload — byte-identical in this helper, in
# claude-session's outer fallback, and seeded in the S3 SOURCE sentinel slot.
# Three required parts:
#   (a) statement that auto-inject FAILED
#   (b) instruction to run `ls -la .claude/` manually
#   (c) instruction to investigate WHY rather than silently ignore
_FALLBACK_PAYLOAD = (
    "**WORKTREE MAP AUTO-INJECT FAILED.**\n"
    "Orchestrator: run `ls -la .claude/` manually before relying on the work-state map.\n"
    "Orchestrator: investigate why the live worktree-map injection failed; do not silently ignore this degraded prompt."
)

# Closed 7-member live-class vocabulary — no other label may be emitted.
_CLASS_SYMLINK_MAIN = "symlink -> main"
_CLASS_SYMLINK_EXTERNAL = "symlink -> external"
_CLASS_SYMLINK_UNRESOLVED = "symlink -> unresolved"
_CLASS_REAL_FILE = "real worktree file"
_CLASS_REAL_DIR = "real worktree dir"
_CLASS_MISSING = "missing"
_CLASS_OTHER = "other (non-regular)"

# Hard-coded extra paths always inspected, relative to worktree_root.
_EXTRA_PATHS = (
    ".agent_context/sessions",
    ".agent_context/logs",
    ".agent_context/audit",
)


class WorktreeMapRenderError(Exception):
    """Raised on structural problems composing the worktree map section."""


def _strip_outer_blank_lines(text: str) -> str:
    lines = text.splitlines()
    start_idx = 0
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    end_idx = len(lines)
    while end_idx > start_idx and not lines[end_idx - 1].strip():
        end_idx -= 1

    return "\n".join(lines[start_idx:end_idx])


def _classify_entry(path: pathlib.Path, main_root: pathlib.Path) -> str:
    """Classify a single filesystem path into exactly one live-class label.

    Uses os.lstat for symlink detection, os.readlink + pathlib.resolve for
    target resolution. Never hard-codes the result for any path.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return _CLASS_MISSING
    except OSError:
        # Non-FileNotFoundError OSError (e.g. PermissionError) — escalate
        # so the outer fallback catches the whole table and injects fail-loud.
        raise

    mode = st.st_mode
    if stat.S_ISLNK(mode):
        # Resolve to determine if the target is under main_root.
        try:
            # Use strict=True so resolve() raises OSError on a dangling link.
            resolved = path.resolve(strict=True)
            # Ensure main_root is also fully resolved before comparison to
            # prevent false negatives when main_root's own path contains symlinks.
            if resolved.is_relative_to(main_root.resolve()):
                return _CLASS_SYMLINK_MAIN
            return _CLASS_SYMLINK_EXTERNAL
        except (OSError, RuntimeError):
            return _CLASS_SYMLINK_UNRESOLVED
    elif stat.S_ISREG(mode):
        return _CLASS_REAL_FILE
    elif stat.S_ISDIR(mode):
        return _CLASS_REAL_DIR
    else:
        # Catches sockets, FIFOs, block/char devices — the closed catch-all.
        return _CLASS_OTHER


def _build_rows(worktree_root: pathlib.Path, main_root: pathlib.Path) -> list[tuple[str, str]]:
    """Return sorted (relative_path_label, live_class) pairs.

    Scans immediate children of <worktree_root>/.claude/ plus the three fixed
    extra paths under .agent_context/. Raises WorktreeMapRenderError on
    enumeration failure.
    """
    rows: list[tuple[str, str]] = []
    claude_dir = worktree_root / ".claude"

    # Root scan: immediate children of .claude/, sorted by name.
    try:
        with os.scandir(claude_dir) as it:
            claude_entries = sorted(it, key=lambda e: e.name)
    except OSError as exc:
        raise WorktreeMapRenderError(
            f"Failed to scan .claude/ directory: {exc}"
        ) from exc

    for entry in claude_entries:
        abs_path = pathlib.Path(entry.path)
        rel_label = f".claude/{entry.name}"
        # Append trailing slash for directories and symlinks to directories.
        is_dir = False
        try:
            lstat_mode = os.lstat(abs_path).st_mode
            if stat.S_ISDIR(lstat_mode):
                is_dir = True
            elif stat.S_ISLNK(lstat_mode):
                try:
                    resolved = abs_path.resolve()
                    if resolved.is_dir():
                        is_dir = True
                except (OSError, RuntimeError):
                    pass
        except OSError:
            pass
        if is_dir and not rel_label.endswith("/"):
            rel_label += "/"
        live_class = _classify_entry(abs_path, main_root)
        rows.append((rel_label, live_class))

    # Extra paths: always emit, even when absent.
    for extra_rel in _EXTRA_PATHS:
        abs_path = worktree_root / extra_rel
        label = extra_rel
        # Add trailing slash (these are always dirs/symlinks-to-dirs when present).
        if not label.endswith("/"):
            label += "/"
        live_class = _classify_entry(abs_path, main_root)
        rows.append((label, live_class))

    # Sort all rows together for stable, diff-friendly output.
    rows.sort(key=lambda r: r[0])
    return rows


def render_worktree_map_section(
    worktree_root: pathlib.Path,
    main_root: pathlib.Path,
) -> str:
    """Pure render. Returns the injected string including both sentinels.

    Executes live enumeration of <worktree_root>/.claude/ via os.scandir/lstat,
    classifies each entry using the closed 7-member vocabulary, and wraps the
    result in the WORKTREE_MAP_* sentinel pair.

    Raises:
        WorktreeMapRenderError: if enumeration fails, no rows are produced,
            or any generated row contains either sentinel literal (collision).
    """
    worktree_root = pathlib.Path(worktree_root)
    main_root = pathlib.Path(main_root)

    rows = _build_rows(worktree_root, main_root)
    if not rows:
        raise WorktreeMapRenderError(
            "Worktree map enumeration produced no rows — .claude/ may be empty or inaccessible."
        )

    # Build the markdown table body — no internal blank lines.
    table_lines = [
        "| Path | Live class |",
        "| --- | --- |",
    ]
    for rel_label, live_class in rows:
        table_lines.append(f"| `{rel_label}` | {live_class} |")

    table_body = "\n".join(table_lines)

    # Collision guard: generated rows must not contain either sentinel literal.
    if _SENTINEL_START in table_body or _SENTINEL_END in table_body:
        raise WorktreeMapRenderError(
            "Generated worktree-map rows contain a reserved sentinel literal — "
            "cannot inject; use fallback payload."
        )

    section = _SENTINEL_START + "\n" + table_body + "\n" + _SENTINEL_END + "\n"
    return section


def _build_fallback_section() -> str:
    """Return a complete sentinel-bounded fallback block."""
    return _SENTINEL_START + "\n" + _FALLBACK_PAYLOAD + "\n" + _SENTINEL_END + "\n"


def _atomic_write(target: pathlib.Path, content: str) -> None:
    """Write content to target atomically via temp-file + os.replace.

    The temp file is created in the same directory as target so os.replace
    is guaranteed to be atomic on the same filesystem. A mid-write exception
    leaves the original target bytes fully intact.
    """
    target_dir = target.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(target_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        # Clean up the temp file if the replace never ran.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_replace_section(
    target: pathlib.Path,
    new_section: str,
    logger=None,
) -> None:
    """Replace or append the WORKTREE_MAP sentinel-bounded section atomically.

    Contracts (per sketch R3 safe-replacement rules):
    - If START+END both present (END after START): replace the region atomically.
    - If START missing, END missing, or END <= START: preserve existing bytes,
      append the new_section at EOF atomically, log the condition. Never truncate.
    - Uses atomic temp-file + os.replace for ALL writes.
    """
    current_text = target.read_text(encoding="utf-8")

    start_idx = current_text.find(_SENTINEL_START)
    end_idx = current_text.find(_SENTINEL_END)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        # Normal replacement: excise the old sentinel-bounded region.
        end_of_end = end_idx + len(_SENTINEL_END)
        if end_of_end < len(current_text) and current_text[end_of_end] == "\n":
            end_of_end += 1
        final_text = current_text[:start_idx] + new_section + current_text[end_of_end:]
    else:
        # Malformed or absent sentinels: preserve existing bytes, append at EOF.
        if start_idx == -1 and end_idx == -1:
            condition = "WORKTREE_MAP sentinels absent from rendered prompt"
        elif start_idx == -1:
            condition = "WORKTREE_MAP_START absent (END present)"
        elif end_idx == -1:
            condition = "WORKTREE_MAP_END absent (START present)"
        else:
            condition = "WORKTREE_MAP_END appears before WORKTREE_MAP_START"
        if logger:
            logger(
                f"[worktree-map] WARNING: {condition}; "
                "appending fallback block at EOF (existing prompt bytes preserved)"
            )
        base = current_text.rstrip("\n") + "\n"
        final_text = base + new_section

    _atomic_write(target, final_text)


def compose_and_inject_worktree_map(
    rendered_prompt_path: pathlib.Path,
    worktree_root: pathlib.Path,
    main_root: pathlib.Path,
    logger=None,
) -> tuple[int, int]:
    """Read rendered prompt, replace prior worktree-map section, and write back.

    On ANY render/classification failure, injects the 3-part fallback payload
    into the sentinel slot instead. Never aborts (never calls sys.exit).

    Args:
        rendered_prompt_path: path to orchestrator-prompt.rendered.md.
        worktree_root: absolute path to the worktree root.
        main_root: absolute path to the main repo root.
        logger: callable(msg: str) -> None; e.g. lambda m: _log(state_dir, m).

    Returns:
        (final_size_bytes, section_size_bytes).

    The rendered_prompt_path must exist; if absent, the caller (claude-session's
    outer fallback) handles the absent-target no-op contract.
    """
    rendered_prompt_path = pathlib.Path(rendered_prompt_path)
    worktree_root = pathlib.Path(worktree_root)
    main_root = pathlib.Path(main_root)

    # Attempt live render; fall back to fallback payload on any render error.
    try:
        new_section = render_worktree_map_section(worktree_root, main_root)
    except WorktreeMapRenderError as exc:
        if logger:
            logger(
                f"[worktree-map] WARNING: live enumeration failed ({exc}); "
                "injecting fallback payload into sentinel slot"
            )
        new_section = _build_fallback_section()
    except Exception as exc:
        if logger:
            logger(
                f"[worktree-map] WARNING: unexpected render error ({exc}); "
                "injecting fallback payload into sentinel slot"
            )
        new_section = _build_fallback_section()

    _safe_replace_section(rendered_prompt_path, new_section, logger=logger)

    final_bytes = rendered_prompt_path.read_bytes()
    section_bytes = new_section.encode("utf-8")
    return (len(final_bytes), len(section_bytes))
