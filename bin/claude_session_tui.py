"""claude_session_tui — interactive TUI for claude-session.

Public API:
    run_tui(main_root, pipeline_registry, pipeline_defaults, pipeline_summaries)
        -> LaunchDecision

Called by bin/claude-session (Subtask 14) when no positional args, no mode flags,
and stdin/stdout are both TTYs.  Falls back to line-mode when curses is unavailable
(TERM=dumb, non-TTY, ImportError).
"""

import contextlib
import fcntl
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Set, Dict, List


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class LaunchDecision:
    """Returned by run_tui(); consumed by bin/claude-session dispatcher."""

    kind: str  # 'exit_with_launch' | 'exit_abort' | 'exit_refused_dirty_state'
    active_pipelines: Optional[Set] = None
    resume_id: Optional[str] = None
    # NOW means "commit AND push succeeded" (not just commit).
    # Currently observed only by tests; the load-bearing decision lives in `kind`.
    git_commit_performed: bool = False
    equivalent_cli: str = ""
    push_outcome_message: str = ""           # push success SHA or failure message
    push_conflict_path: Optional[pathlib.Path] = None  # path to diagnostic file on push failure


# ---------------------------------------------------------------------------
# Screen transition types
# ---------------------------------------------------------------------------


class TransitionKind(str, Enum):
    PUSH = "push"
    POP = "pop"
    REPLACE = "replace"
    EXIT_WITH_LAUNCH = "exit_with_launch"
    EXIT_ABORT = "exit_abort"
    NONE = "none"


@dataclass
class ScreenTransition:
    """Control-flow value returned by Screen.handle_key()."""

    kind: TransitionKind
    payload: object = None  # Screen instance or LaunchDecision depending on kind


NO_TRANSITION = ScreenTransition(TransitionKind.NONE)


# ---------------------------------------------------------------------------
# Abstract Screen base
# ---------------------------------------------------------------------------


class Screen:
    """Abstract base; subclasses override render() and handle_key()."""

    def on_enter(self) -> None:
        """Called once when the screen is pushed onto the stack."""

    def render(self, stdscr) -> None:  # noqa: ANN001
        raise NotImplementedError

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared rendering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_addstr(stdscr, row: int, col: int, text: str, attr: int = 0) -> None:
        """addstr that silently ignores out-of-bounds writes (small terminal)."""
        try:
            stdscr.addstr(row, col, text, attr)
        except Exception:  # curses.error on OOB  # noqa: BLE001
            pass

    @staticmethod
    def _draw_box(stdscr, height: int, width: int) -> None:
        """Draw ASCII box border; 80×24 safe."""
        try:
            top = "+" + "-" * (width - 2) + "+"
            mid = "|" + " " * (width - 2) + "|"
            stdscr.addstr(0, 0, top[:width])
            for r in range(1, height - 1):
                stdscr.addstr(r, 0, mid[:width])
            stdscr.addstr(height - 1, 0, top[:width])
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _header(stdscr, width: int, left: str, right: str = "") -> None:
        """Render a one-line header inside the box (row 1)."""
        right_pad = right.rjust(width - 2 - len(left) - 2)
        line = " " + left + right_pad + " "
        Screen._safe_addstr(stdscr, 1, 0, "|" + line[: width - 2] + "|")

    @staticmethod
    def _footer(stdscr, height: int, width: int, text: str) -> None:
        """Render hint text in the last interior row (height-2)."""
        padded = " " + text + " " * (width - 3 - len(text))
        Screen._safe_addstr(stdscr, height - 2, 0, "|" + padded[: width - 2] + "|")

    @staticmethod
    def _separator(stdscr, row: int, width: int) -> None:
        Screen._safe_addstr(stdscr, row, 0, "+" + "-" * (width - 2) + "+")


# ---------------------------------------------------------------------------
# Shell (landing screen)
# ---------------------------------------------------------------------------


class Shell(Screen):
    """Landing screen: p / w / s / q."""

    def __init__(
        self,
        main_root: pathlib.Path,
        registry_empty: bool = False,
        pipeline_defaults: dict | None = None,
        accept_dirty: bool = False,
        worktree_session_id: str = "",
    ) -> None:
        self.main_root = main_root
        self.registry_empty = registry_empty
        self._defaults: dict = pipeline_defaults or {}
        self.accept_dirty = accept_dirty
        self.worktree_session_id = worktree_session_id

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._separator(stdscr, 2, width)
        self._header(stdscr, width, "claude-session -- interactive launcher")
        wt_suffix = "  (no worktrees yet)" if self.registry_empty else ""
        rows = [
            "  [l] Launch   -- use default pipelines",
            "  [p] Pipelines -- select & launch a new session",
            f"  [w] Worktrees -- manage existing sessions{wt_suffix}",
            "  [s] Settings  -- inspect & edit bootstrap-config.json",
            "  [i] Issues    -- view & triage open issue queue",
            "  [q] Quit      -- exit without launching",
        ]
        for i, row in enumerate(rows):
            self._safe_addstr(stdscr, 3 + i, 0, "|" + row.ljust(width - 2)[:width - 2] + "|")
        self._footer(stdscr, height, width, "[l] launch  |  [p] pipelines  |  [w] worktrees  |  [s] settings  |  [i] issues  |  [q] quit")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if key == ord("l"):
            active = {k for k, v in self._defaults.items() if v}
            return ScreenTransition(
                TransitionKind.REPLACE,
                _GitPreLaunchPlaceholder(
                    self.main_root, active,
                    accept_dirty=self.accept_dirty,
                    worktree_session_id=self.worktree_session_id,
                ),
            )
        if key == ord("p"):
            return ScreenTransition(TransitionKind.PUSH, _PUSH_PIPELINE_SELECT)
        if key == ord("w"):
            return ScreenTransition(TransitionKind.PUSH, _PUSH_WORKTREE_MANAGE)
        if key == ord("s"):
            return ScreenTransition(TransitionKind.PUSH, _PUSH_SETTINGS)
        if key == ord("i"):
            return ScreenTransition(TransitionKind.PUSH, _PUSH_ISSUE_QUEUE)
        if key in (ord("q"), 27):  # q or Esc
            return ScreenTransition(TransitionKind.EXIT_ABORT)
        return NO_TRANSITION


# Sentinel objects resolved at run_tui() call site; avoids circular imports.
_PUSH_PIPELINE_SELECT = object()
_PUSH_WORKTREE_MANAGE = object()
_PUSH_SETTINGS = object()


# ---------------------------------------------------------------------------
# PipelineSelect
# ---------------------------------------------------------------------------


class PipelineSelect(Screen):
    """Select & toggle pipelines; Save defaults; Launch."""

    def __init__(
        self,
        main_root: pathlib.Path,
        pipeline_registry: set,
        pipeline_defaults: dict,
        pipeline_summaries: dict,
        accept_dirty: bool = False,
        worktree_session_id: str = "",
    ) -> None:
        self.main_root = main_root
        self.registry = sorted(pipeline_registry)
        self.summaries = pipeline_summaries
        self.active: set = {k for k, v in pipeline_defaults.items() if v}
        self.cursor = 0
        self._status_msg = ""
        self._error: Optional[str] = None
        self.accept_dirty = accept_dirty
        self.worktree_session_id = worktree_session_id

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Pipelines -- Select & Launch",
                     f"{len(self.registry)} pipelines")
        self._separator(stdscr, 2, width)

        if not self.registry:
            self._safe_addstr(stdscr, 4, 2, "No pipelines registered.")
            self._safe_addstr(stdscr, 6, 2, "Run 'caa-setup pipelines' to register one, or press q to quit.")
            self._safe_addstr(stdscr, 8, 2, "Bypass TUI entirely: claude-session --raw")
            self._footer(stdscr, height, width, "[w] worktrees  |  [s] settings  |  [Esc] back  |  [q] quit")
            stdscr.refresh()
            return

        visible = max(1, height - 6)
        start = max(0, self.cursor - visible + 1)
        for i, name in enumerate(self.registry[start: start + visible]):
            abs_i = start + i
            sel = "[>]" if abs_i == self.cursor else "   "
            tog = "[x]" if name in self.active else "[ ]"
            summary_full = self.summaries.get(name, "(summary unavailable)")
            interior = max(0, width - 2)
            # 1 + 3 + 1 + 3 + 1 + 12 + 1 = 22 chars before summary; leave 1 for ellipsis guard
            sum_avail = max(0, interior - 23)
            summary = summary_full if len(summary_full) <= sum_avail else summary_full[:max(0, sum_avail - 1)] + "…"
            line = f" {sel} {tog} {name:<12} {summary}"
            self._safe_addstr(stdscr, 3 + i, 0, "|" + line.ljust(interior)[:interior] + "|")

        remaining = len(self.registry) - (start + visible)
        if remaining > 0:
            self._safe_addstr(stdscr, 3 + visible, 2,
                              f"...{remaining} more below -- down-arrow to scroll")

        if self._status_msg:
            self._safe_addstr(stdscr, height - 3, 2, self._status_msg[:width - 4])

        self._footer(stdscr, height, width,
                     "[up/dn] scroll  |  [space] toggle  |  [l] launch  |  [S] save-defaults  |  [Esc] back  |  [q] quit")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        try:
            import curses
            up_keys = (curses.KEY_UP, ord("k"))
            down_keys = (curses.KEY_DOWN, ord("j"))
            enter_keys = (curses.KEY_ENTER, 10, 13)
        except ImportError:
            up_keys = (ord("k"),)
            down_keys = (ord("j"),)
            enter_keys = (10, 13)

        if key in up_keys:
            self.cursor = max(0, self.cursor - 1)
            return NO_TRANSITION
        if key in down_keys:
            self.cursor = min(len(self.registry) - 1, self.cursor + 1)
            return NO_TRANSITION
        if key == ord(" ") and self.registry:
            name = self.registry[self.cursor]
            if name in self.active:
                self.active.discard(name)
            else:
                self.active.add(name)
            return NO_TRANSITION
        if key == ord("S"):  # uppercase-S = SaveDefaults
            try:
                _save_defaults_to_bootstrap_config(
                    self.main_root,
                    {n: (n in self.active) for n in self.registry},
                )
                self._status_msg = "Defaults saved."
            except OSError as exc:
                self._status_msg = f"Save failed: {exc}"
            return NO_TRANSITION
        if key in (*enter_keys, ord("l")):
            return ScreenTransition(
                TransitionKind.REPLACE,
                _GitPreLaunchPlaceholder(
                    self.main_root, set(self.active),
                    accept_dirty=self.accept_dirty,
                    worktree_session_id=self.worktree_session_id,
                ),
            )
        if key in (27, ord("q")):
            return ScreenTransition(TransitionKind.POP)
        return NO_TRANSITION


class _GitPreLaunchPlaceholder:
    """Used as payload marker so run_tui() can instantiate with correct args."""

    def __init__(
        self,
        main_root: pathlib.Path,
        active: set,
        accept_dirty: bool = False,
        worktree_session_id: str = "",
    ) -> None:
        self.main_root = main_root
        self.active = active
        self.accept_dirty = accept_dirty
        self.worktree_session_id = worktree_session_id


# ---------------------------------------------------------------------------
# WorktreeManage
# ---------------------------------------------------------------------------


class WorktreeManage(Screen):
    """List registered worktrees; resume/delete/view-state."""

    def __init__(self, main_root: pathlib.Path) -> None:
        self.main_root = main_root
        self.rows: list = []
        self.cursor = 0
        self._error: Optional[str] = None

    def on_enter(self) -> None:
        self._load()

    def _load(self) -> None:
        try:
            import sys as _sys
            _sys.path.insert(0, str(pathlib.Path(__file__).parent))
            import session_registry  # type: ignore[import]
            data = session_registry.read_registry(self.main_root)
            self.rows = sorted(
                data.values(),
                key=lambda r: r.get("last_touched", ""),
                reverse=True,
            )
            self._error = None
        except Exception as exc:  # MalformedRegistryError or any parse error  # noqa: BLE001
            self._error = str(exc)
            self.rows = []
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Worktrees -- Manage",
                     f"{len(self.rows)} active")
        self._separator(stdscr, 2, width)

        if self._error:
            self._safe_addstr(stdscr, 4, 2, "Could not read worktree registry:")
            self._safe_addstr(stdscr, 5, 4, self._error[:width - 6])
            self._safe_addstr(stdscr, 7, 2, "Press r to retry, q to quit.")
            self._safe_addstr(stdscr, 9, 2, "Workaround: claude-session --list")
            self._footer(stdscr, height, width, "[r] retry  |  [Esc] back  |  [q] quit")
            stdscr.refresh()
            return

        if not self.rows:
            self._safe_addstr(stdscr, 4, 2, "No active worktrees.")
            self._safe_addstr(stdscr, 6, 2, "Press p to launch a new session, or q to quit.")
            self._footer(stdscr, height, width, "[p] pipelines  |  [s] settings  |  [Esc] back  |  [q] quit")
            stdscr.refresh()
            return

        visible = max(1, height - 6)
        start = max(0, self.cursor - visible + 1)
        for i, row in enumerate(self.rows[start: start + visible]):
            abs_i = start + i
            sel = "[>]" if abs_i == self.cursor else "   "
            wt = row.get("worktree_path", "?")
            short_wt = "..." + wt[-20:] if len(wt) > 23 else wt
            display_name = row.get("name") or row.get("id") or "?"
            last_touched = row.get("last_touched") or "?"
            status = row.get("status") or "?"
            try:
                line = (f" {sel} {display_name:<22} "
                        f"{last_touched[:16]}  "
                        f"{status:<10} {short_wt}")
                self._safe_addstr(stdscr, 3 + i, 0, "|" + line.ljust(width - 2)[:width - 2] + "|")
            except (TypeError, ValueError):
                row_id = row.get("id", "?")
                self._safe_addstr(stdscr, 3 + i, 0, f"|{row_id}: <unrenderable>".ljust(width - 2)[:width - 2] + "|")

        remaining = len(self.rows) - (start + visible)
        if remaining > 0:
            self._safe_addstr(stdscr, 3 + visible, 2,
                              f"...{remaining} more below -- down-arrow to scroll")

        self._footer(stdscr, height, width,
                     "[up/dn] scroll  |  [Enter] resume  |  [d] delete  |  [v] view state  |  [Esc] back  |  [q] quit")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        try:
            import curses
            up_keys = (curses.KEY_UP, ord("k"))
            down_keys = (curses.KEY_DOWN, ord("j"))
            enter_keys = (curses.KEY_ENTER, 10, 13)
        except ImportError:
            up_keys = (ord("k"),)
            down_keys = (ord("j"),)
            enter_keys = (10, 13)

        if self._error:
            if key == ord("r"):
                self._load()
                return NO_TRANSITION
            if key in (ord("q"), 27):
                return ScreenTransition(TransitionKind.POP)
            return NO_TRANSITION

        if not self.rows:
            if key == ord("p"):
                return ScreenTransition(TransitionKind.PUSH, _PUSH_PIPELINE_SELECT)
            if key in (ord("q"), ord("s"), 27):
                return ScreenTransition(TransitionKind.POP)
            return NO_TRANSITION

        if key in up_keys:
            self.cursor = max(0, self.cursor - 1)
            return NO_TRANSITION
        if key in down_keys:
            self.cursor = min(len(self.rows) - 1, self.cursor + 1)
            return NO_TRANSITION
        if key in enter_keys and self.rows:
            row = self.rows[self.cursor]
            decision = LaunchDecision(
                kind="exit_with_launch",
                resume_id=row.get("id"),
            )
            return ScreenTransition(TransitionKind.EXIT_WITH_LAUNCH, decision)
        if key == ord("d") and self.rows:
            def _reload_cb():
                self._load()
            return ScreenTransition(
                TransitionKind.PUSH,
                DeleteConfirm(row=self.rows[self.cursor], main_root=self.main_root,
                              on_delete_success=_reload_cb),
            )
        if key == ord("v") and self.rows:
            return ScreenTransition(
                TransitionKind.PUSH,
                ViewStateOutput(row=self.rows[self.cursor]),
            )
        if key in (ord("q"), 27):
            return ScreenTransition(TransitionKind.POP)
        return NO_TRANSITION


# ---------------------------------------------------------------------------
# DeleteConfirm (modal child of WorktreeManage)
# ---------------------------------------------------------------------------


class DeleteConfirm(Screen):
    """Two-step delete confirm for a worktree row."""

    def __init__(self, row: dict, main_root: pathlib.Path, on_delete_success=None) -> None:
        self.row = row
        self.main_root = main_root
        self._result_msg = ""
        self.on_delete_success = on_delete_success

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Delete Worktree -- Confirm")
        self._separator(stdscr, 2, width)
        display_name = self.row.get("name") or self.row.get("id") or "?"
        self._safe_addstr(stdscr, 4, 2, f"Delete worktree {display_name}?")
        self._safe_addstr(stdscr, 5, 2, "This cannot be undone.")
        if self._result_msg:
            self._safe_addstr(stdscr, 7, 2, self._result_msg[:width - 4])
        self._footer(stdscr, height, width, "[y] confirm delete  |  [n] cancel  |  [Esc] back")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if key == ord("y"):
            try:
                import sys as _sys
                _sys.path.insert(0, str(pathlib.Path(__file__).parent))
                import session_registry  # type: ignore[import]
                session_registry.delete_record(self.main_root, self.row["id"])
            except Exception as exc:  # noqa: BLE001
                self._result_msg = f"Delete failed: {exc}"
                return NO_TRANSITION
            if self.on_delete_success is not None:
                self.on_delete_success()
            return ScreenTransition(TransitionKind.POP)
        if key in (ord("n"), 27, ord("q")):
            return ScreenTransition(TransitionKind.POP)
        return NO_TRANSITION


# ---------------------------------------------------------------------------
# ViewStateOutput (modal child of WorktreeManage)
# ---------------------------------------------------------------------------


class ViewStateOutput(Screen):
    """Show state details for a worktree row (read-only)."""

    def __init__(self, row: dict) -> None:
        self.row = row

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Worktree State")
        self._separator(stdscr, 2, width)
        lines = [f"{k}: {v}" for k, v in sorted(self.row.items())]
        visible = max(1, height - 6)
        for i, line in enumerate(lines[:visible]):
            self._safe_addstr(stdscr, 3 + i, 2, line[: width - 4])
        self._footer(stdscr, height, width, "[Esc] back  |  [q] quit")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if key in (27, ord("q")):
            return ScreenTransition(TransitionKind.POP)
        return NO_TRANSITION


# ---------------------------------------------------------------------------
# GitPreLaunch
# ---------------------------------------------------------------------------


class GitPreLaunch(Screen):
    """Uncommitted changes on main — c/a/s/q (H2 gate)."""

    def __init__(
        self,
        main_root: pathlib.Path,
        active_pipelines: set,
        accept_dirty: bool = False,
        worktree_session_id: str = "",
    ) -> None:
        self.main_root = main_root
        self.active_pipelines = active_pipelines
        self.accept_dirty = accept_dirty
        self.worktree_session_id = worktree_session_id
        self.is_dirty = False
        self.dirty_paths: list = []
        self._error: Optional[str] = None
        self._auto_skip = False
        self.cursor = 0
        self._foreign_paths: list = []
        self._this_session_paths: list = []
        self._foreign_count: int = 0
        self._foreign_session_ids: list = []
        # R2 G1 — concurrent-lock retry tracking. Both reset to (0, None) at:
        #   (a) successful lock acquisition (push proceeds → screen exits anyway)
        #   (b) any keypress-triggered retry where time.monotonic() - first_ts > 10.0
        #       (window expired; treat as fresh attempt)
        # NOT reset on `c` or `n` (those exit the screen; instance discarded).
        self._push_lock_retry_count: int = 0
        self._push_lock_first_failure_ts: Optional[float] = None

    def on_enter(self) -> None:
        try:
            self.is_dirty, self.dirty_paths = _main_git_is_dirty(self.main_root)
            if not self.is_dirty:
                self._auto_skip = True
                return
            if not self.accept_dirty:
                # Pre-compute foreign-session classification for the H2 gate prompt.
                since_ts = _h2_compute_since_ts(self.main_root)
                cl_path = (
                    self.main_root / ".claude" / "knowledge-log" / ".change-log.jsonl"
                )
                # dirty_paths from _main_git_is_dirty is in porcelain format ("XY path").
                raw_paths = [
                    ln[3:] if len(ln) > 3 else ln for ln in self.dirty_paths
                ]
                this_session, foreign = _filter_foreign_session_files(
                    change_log_path=cl_path,
                    current_session_id=self.worktree_session_id,
                    since_ts=since_ts,
                    dirty_paths=raw_paths,
                )
                self._this_session_paths = this_session
                self._foreign_paths = foreign
                self._foreign_count = len(foreign)
                # Collect distinct foreign session IDs for the UX prompt line.
                if foreign:
                    raw_cl = _h2_read_recent_writes(cl_path, since_ts)
                    by_file: dict = {}
                    for rec in raw_cl:
                        f = rec.get("file")
                        if f and f not in EXCLUDE_PATHS:
                            prev = by_file.get(f)
                            if prev is None or (rec.get("ts") or "") > (prev.get("ts") or ""):
                                by_file[f] = rec
                    fids: set = set()
                    for fp in foreign:
                        rec = by_file.get(fp)
                        sid = (rec.get("session_id") or "") if rec else ""
                        fids.add(sid if sid else "<unknown>")
                    self._foreign_session_ids = sorted(fids)
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            # treat as clean — auto-proceed on git failure per design
            self._auto_skip = True
            print(
                f"[claude-session] git status failed: {exc}; skipping main-dirty check",
                file=sys.stderr,
            )

    def render(self, stdscr) -> None:
        if self._auto_skip:
            return
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)

        if self._error:
            self._header(stdscr, width, "Git Pre-Launch", "[!] error")
            self._separator(stdscr, 2, width)
            self._safe_addstr(stdscr, 4, 2, "git status failed:")
            self._safe_addstr(stdscr, 5, 4, self._error[:width - 6])
            self._safe_addstr(stdscr, 7, 2, "Press c to refuse and exit, or q to abort.")
            self._footer(stdscr, height, width, "[c] refuse and exit  |  [q] abort")
            stdscr.refresh()
            return

        if self._foreign_count > 0:
            # H2 foreign-session gate prompt.
            self._header(stdscr, width, "Git Pre-Launch -- Foreign-Session Writes Detected")
            self._separator(stdscr, 2, width)
            self._safe_addstr(stdscr, 3, 2, "Pre-launch commit: foreign-session writes detected.")
            sid_display = self.worktree_session_id[:20] if self.worktree_session_id else "(unknown)"
            self._safe_addstr(stdscr, 4, 4, f"This worktree: {sid_display}")
            # Show foreign session id(s) + count per locked UX: "<id> (<count> files)".
            if len(self._foreign_session_ids) == 1:
                self._safe_addstr(
                    stdscr, 5, 4,
                    f"Foreign sessions: {self._foreign_session_ids[0]} ({self._foreign_count} files)",
                )
            else:
                fids_str = ", ".join(self._foreign_session_ids)
                self._safe_addstr(
                    stdscr, 5, 4,
                    f"Foreign sessions: {fids_str} ({self._foreign_count} files)",
                )
            self._safe_addstr(stdscr, 7, 2, "Options:")
            self._safe_addstr(stdscr, 8, 4, "[c] commit only this session's writes (recommended)")
            self._safe_addstr(stdscr, 9, 4, "[a] accept dirty -- commit everything (Frankenstein)")
            self._safe_addstr(stdscr, 10, 4, "[s] show diff of foreign-session files")
            self._safe_addstr(stdscr, 11, 4, "[q] quit launch (resolve manually, then re-run)")
            self._footer(stdscr, height, width, "Choice [c/a/s/q]:")
        else:
            # No foreign files detected — show simple commit prompt.
            self._header(stdscr, width, "Git Pre-Launch -- Uncommitted Changes on main")
            self._separator(stdscr, 2, width)

            visible = max(1, height - 8)
            start = max(0, self.cursor - visible + 1)
            for i, path in enumerate(self.dirty_paths[start: start + visible]):
                self._safe_addstr(stdscr, 3 + i, 2, path[:width - 4])

            remaining = len(self.dirty_paths) - (start + visible)
            if remaining > 0:
                self._safe_addstr(
                    stdscr, 3 + visible, 2,
                    f"...{remaining} more changed files -- down-arrow to scroll",
                )

            count_row = height - 5
            self._safe_addstr(stdscr, count_row, 2,
                              f"{len(self.dirty_paths)} paths with uncommitted changes.")
            self._safe_addstr(stdscr, count_row + 1, 2,
                              "To launch, commit AND push these to origin/<branch>.")
            self._safe_addstr(stdscr, count_row + 2, 2,
                              "Refuse with c if you want to resolve manually.")
            self._footer(stdscr, height, width,
                         "[c] commit & push  |  [q] refuse - resolve yourself  |  [n] abort launch")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if self._auto_skip:
            # Emit result immediately; no keypress needed.
            decision = LaunchDecision(
                kind="exit_with_launch",
                active_pipelines=self.active_pipelines,
            )
            return ScreenTransition(TransitionKind.EXIT_WITH_LAUNCH, decision)

        try:
            import curses as _curses_mod
            up_keys = (_curses_mod.KEY_UP,)
            down_keys = (_curses_mod.KEY_DOWN,)
        except ImportError:
            up_keys = ()
            down_keys = ()

        if key in up_keys:
            self.cursor = max(0, self.cursor - 1)
            return NO_TRANSITION
        if key in down_keys:
            self.cursor = min(max(0, len(self.dirty_paths) - 1), self.cursor + 1)
            return NO_TRANSITION

        _LOCK_HELD_MSG = "Another claude-session TUI is mid-push. Wait or resolve manually."

        # Default-on-Enter = 'c' (commit this session's writes).
        is_enter = key in (ord("\n"), ord("\r"), 10, 13)
        effective_c = key == ord("c") or is_enter

        if effective_c or key == ord("a"):
            # 'a' (accept dirty) requires typing the literal word "accept" to prevent
            # muscle-memory bypass. Re-prompt if user types anything other than "accept".
            if key == ord("a"):
                try:
                    import curses as _curses_a
                    # Suspend curses briefly to get string input.
                    with _curses_suspended(stdscr):
                        typed = input("Type 'accept' to commit everything (Frankenstein): ").strip()
                except (ImportError, EOFError):
                    typed = ""
                if typed != "accept":
                    self._error = "[!] confirmation failed; choose c/a/s/q again"
                    return NO_TRANSITION
                # User confirmed "accept" — commit everything.
                use_accept_dirty = True
            else:
                use_accept_dirty = self.accept_dirty

            # G3 mitigation: suspend curses for commit+push duration.
            print("[claude-session] Committing and pushing to origin "
                  "(pre-push hooks may take up to 60s)...", file=sys.stderr)
            sys.stderr.flush()
            try:
                with _curses_suspended(stdscr):
                    success, msg, conflict_path = _run_git_commit_and_push(
                        self.main_root,
                        accept_dirty=use_accept_dirty,
                        worktree_session_id=self.worktree_session_id,
                    )
            except KeyboardInterrupt:
                success, msg, conflict_path = (False, "push aborted by user (Ctrl-C)", None)

            # R2 G1 — handle concurrent-lock-held refusal with retry-counter.
            if not success and msg == _LOCK_HELD_MSG:
                now = time.monotonic()
                if self._push_lock_first_failure_ts is not None \
                        and (now - self._push_lock_first_failure_ts) > 10.0:
                    self._push_lock_retry_count = 0
                    self._push_lock_first_failure_ts = None
                if self._push_lock_first_failure_ts is None:
                    self._push_lock_first_failure_ts = now
                self._push_lock_retry_count += 1
                if self._push_lock_retry_count >= 3:
                    decision = LaunchDecision(
                        kind="exit_refused_dirty_state",
                        active_pipelines=self.active_pipelines,
                        git_commit_performed=False,
                        push_outcome_message=(
                            f"Refused: 3 lock-acquisition failures within 10s. "
                            f"Another claude-session TUI is holding the push lock. "
                            f"Resolve manually before re-launching."
                        ),
                    )
                    return ScreenTransition(TransitionKind.EXIT_ABORT, decision)
                self._error = (
                    f"{_LOCK_HELD_MSG} "
                    f"(retry {self._push_lock_retry_count}/3 within 10s window)"
                )
                return NO_TRANSITION

            if success:
                decision = LaunchDecision(
                    kind="exit_with_launch",
                    active_pipelines=self.active_pipelines,
                    git_commit_performed=True,
                    push_outcome_message=msg,
                )
                return ScreenTransition(TransitionKind.EXIT_WITH_LAUNCH, decision)
            # Failure path.
            self._error = msg + (f"\n  See: {conflict_path}" if conflict_path else "")
            decision = LaunchDecision(
                kind="exit_refused_dirty_state",
                active_pipelines=self.active_pipelines,
                git_commit_performed=False,
                push_outcome_message=msg,
                push_conflict_path=conflict_path,
            )
            return ScreenTransition(TransitionKind.EXIT_ABORT, decision)
            # NOTE: TransitionKind.EXIT_ABORT carries decision.kind='exit_refused_dirty_state'.

        if key == ord("s"):
            # Show diff of foreign-session files, then re-render.
            foreign_paths = self._foreign_paths or []
            if foreign_paths:
                try:
                    with _curses_suspended(stdscr):
                        subprocess.run(
                            ["git", "diff", "HEAD", "--"] + foreign_paths,
                            cwd=str(self.main_root),
                        )
                        input("\nPress Enter to continue...")
                except Exception:  # noqa: BLE001
                    pass
            return NO_TRANSITION

        if key == ord("q"):
            return ScreenTransition(
                TransitionKind.EXIT_ABORT,
                LaunchDecision(kind="exit_abort"),
            )

        # 'n' preserved for backward compat (maps to quit).
        if key == ord("n"):
            return ScreenTransition(
                TransitionKind.EXIT_ABORT,
                LaunchDecision(kind="exit_abort"),
            )

        return NO_TRANSITION


# ---------------------------------------------------------------------------
# Schema loader (Subtask 15 schema consumer)
# ---------------------------------------------------------------------------


def load_bootstrap_config_schema(main_root: pathlib.Path) -> dict:
    """Return the parsed schema as a dict whose keys are bootstrap-config key names.

    Each value is a 5-field spec: description, type, enum_values|None, default,
    pipeline_owner.  Returns the 'properties' sub-dict directly.
    """
    schema_path = main_root / ".claude" / "bootstrap-config.schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)["properties"]


_CYCLING_KEYS = frozenset({
    "threshold", "unattended", "sentinel_ttl", "max_episodes",
    "warning_floor_tokens", "work_room_floor_tokens",
    "context_window_tokens", "system_prompt_overhead_tokens",
    "bg_bash_keepalive_interval",
})


def load_cycling_schema() -> dict:
    """Inline-defined cycling schema for session-cycling.json (9 keys).

    Values MUST track the launcher's _CONFIG_DEFAULTS / _CONFIG_TYPES
    (find via grep anchors `_CONFIG_DEFAULTS = {` / `_CONFIG_TYPES = {`
    in bin/claude-session — count=1 each).

    TYPE-STRING TRANSLATION BOUNDARY: launcher's _CONFIG_TYPES uses
    'int'/'bool'/'str'. TUI validator (_si_validate_value) has branches
    for 'int'/'bool'/'string'/'enum'/'string-list' — NO 'str' branch.
    Today every cycling key is int or bool, so no translation is needed;
    the constraint is asserted below. When a 'str'-typed cycling key is
    added in bin/claude-session, this function MUST translate 'str' ->
    'string' before emission, or the validator will silently accept all
    user input (no type-check branch matches).
    """
    owner = "cycling"
    schema = {
        "threshold": {"description": "Token count that triggers a cycle.",
                      "type": "int", "default": 190000, "pipeline_owner": owner},
        "unattended": {"description": "Skip interactive prompts during cycling.",
                       "type": "bool", "default": True, "pipeline_owner": owner},
        "sentinel_ttl": {"description": "Seconds before sentinel expires.",
                         "type": "int", "default": 180, "pipeline_owner": owner},
        "max_episodes": {"description": "Max episodes per session.",
                         "type": "int", "default": 20, "pipeline_owner": owner},
        "warning_floor_tokens": {"description": "Low-token warning threshold.",
                                 "type": "int", "default": 90000, "pipeline_owner": owner},
        "work_room_floor_tokens": {"description": "Minimum work room before forced cycle.",
                                   "type": "int", "default": 60000, "pipeline_owner": owner},
        "context_window_tokens": {"description": "Model context window size.",
                                  "type": "int", "default": 200000, "pipeline_owner": owner},
        "system_prompt_overhead_tokens": {"description": "Tokens reserved for system prompt.",
                                          "type": "int", "default": 30000, "pipeline_owner": owner},
        "bg_bash_keepalive_interval": {"description": "Seconds between keepalive pings.",
                                       "type": "int", "default": 60, "pipeline_owner": owner},
    }
    # Guard against future 'str'/'string-list'/'enum' additions that
    # would require explicit translation — see docstring.
    assert all(v["type"] in ("int", "bool") for v in schema.values()), (
        "load_cycling_schema: non-int/bool type detected — add a translation "
        "step before the validator silently accepts all input."
    )
    return schema


# ---------------------------------------------------------------------------
# SettingsInspector (Subtask 16)
# ---------------------------------------------------------------------------

# Pipeline-owner display order for grouped layout.
_OWNER_ORDER = ["ux", "asset", "hygiene", "bootstrap", "universal", "cycling"]
_OWNER_LABEL = {
    "ux": "UX",
    "asset": "Asset",
    "hygiene": "Hygiene",
    "bootstrap": "Bootstrap",
    "universal": "Universal",
    "cycling": "Cycling",
}


def _si_save_bootstrap_config(worktree_root: pathlib.Path, values: dict) -> None:
    """Atomically write bootstrap values back to .claude/bootstrap-config.json.

    Merges flat dotted keys (e.g. 'pipelines.registry') into the nested JSON
    structure, then writes via tmp + os.replace.
    """
    config_path = worktree_root / ".claude" / "bootstrap-config.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {}
    # Apply flat dotted keys back to nested structure.
    for key, val in values.items():
        parts = key.split(".")
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = val
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=config_path.parent,
        prefix=".bootstrap-config.tmp.",
        suffix=".json",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _si_save_cycling_config(worktree_root: pathlib.Path, values: dict) -> None:
    """Atomically write cycling values back to .claude/session-cycling.json.

    Cycling keys are flat top-level (no dots); reads existing file to
    preserve non-edited keys, then writes via tmp + os.replace.
    """
    config_path = worktree_root / ".claude" / "session-cycling.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {}
    # Cycling keys are flat — no dotted-path traversal needed.
    data.update(values)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=config_path.parent,
        prefix=".session-cycling.tmp.",
        suffix=".json",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _si_save_config(worktree_root: pathlib.Path, values: dict) -> None:
    """Dispatch save: cycling keys -> session-cycling.json; rest -> bootstrap-config.json.

    Cycling saved FIRST (smaller file, fewer keys, lower failure probability).
    See sketch v2 Assumption A7 for partial-save accepted-gap rationale.
    """
    bootstrap_values = {k: v for k, v in values.items() if k not in _CYCLING_KEYS}
    cycling_values = {k: v for k, v in values.items() if k in _CYCLING_KEYS}
    # Save cycling FIRST (smaller file, fewer keys, lower failure probability).
    # See Assumption A7 for partial-failure accepted-gap rationale.
    if cycling_values:
        _si_save_cycling_config(worktree_root, cycling_values)
    if bootstrap_values:
        _si_save_bootstrap_config(worktree_root, bootstrap_values)


def _si_read_cycling_current_value(worktree_root: pathlib.Path, key: str):
    """Read the current value of a flat cycling key from session-cycling.json."""
    config_path = worktree_root / ".claude" / "session-cycling.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data.get(key)


def _si_read_current_value(worktree_root: pathlib.Path, key: str):
    """Read the current value of a key from bootstrap-config.json or session-cycling.json."""
    if key in _CYCLING_KEYS:
        return _si_read_cycling_current_value(worktree_root, key)
    config_path = worktree_root / ".claude" / "bootstrap-config.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _si_validate_value(spec: dict, value) -> Optional[str]:
    """Return an error string if value fails validation, else None."""
    field_type = spec["type"]
    if field_type == "bool":
        if not isinstance(value, bool):
            return f"Expected bool, got {type(value).__name__}"
    elif field_type == "enum":
        allowed = spec.get("enum_values", [])
        if value not in allowed:
            return f"Must be one of: {', '.join(allowed)}"
    elif field_type == "string":
        if not isinstance(value, str):
            return f"Expected string, got {type(value).__name__}"
    elif field_type == "string-list":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return "Expected list of strings"
        allowed = spec.get("enum_values")
        if allowed:
            bad = [x for x in value if x not in allowed]
            if bad:
                return f"Invalid list members: {bad}. Allowed: {allowed}"
    elif field_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"Expected integer, got {type(value).__name__}"
    return None


@contextlib.contextmanager
def _curses_suspended(stdscr):
    """Temporarily suspend curses so that external-input calls (editor or input())
    get a normal cooked terminal.  Resumes curses and forces a full redraw on exit.

    Invariant: stdscr must be the main curses window from curses.wrapper().  Pass
    None when not running under curses (line-mode, tests) — yields immediately with
    no curses calls.
    """
    if stdscr is None:
        yield
        return
    import curses
    curses.def_prog_mode()   # snapshot current curses terminal state
    curses.endwin()          # give terminal back to cooked mode
    sys.stdout.flush()
    try:
        yield
    finally:
        # Restore curses; touchwin() marks the whole window dirty so doupdate()
        # redraws every character rather than relying on diff tracking.
        stdscr.touchwin()
        stdscr.refresh()


class SettingsInspector(Screen):
    """Full bootstrap-config inspector screen (Subtask 16).

    Constructor signature is frozen: SettingsInspector(main_root: Path).
    """

    # States: default | empty | error | loading | over_populated | a11y
    # 'loading' is a transient state set in on_enter before schema read completes.

    def __init__(self, main_root: pathlib.Path) -> None:
        self.main_root = main_root
        # Worktree root = cwd; config is written to worktree-local path.
        self._worktree_root = pathlib.Path.cwd()
        self._schema: dict = {}
        self._error: Optional[str] = None
        self._cursor = 0          # flat index into _flat_keys
        self._flat_keys: List[str] = []  # ordered list: group header then keys
        self._values: dict = {}   # key -> current value (live edits)
        self._field_error: Optional[str] = None  # per-field validation error
        self._save_status: Optional[str] = None  # "saved" | "error: ..."
        self._viewport_top = 0    # scrolling
        self._is_header: List[bool] = []  # parallel to _flat_keys: True = group header

    def on_enter(self) -> None:
        self._error = None
        self._field_error = None
        self._save_status = None
        try:
            bootstrap_schema = load_bootstrap_config_schema(self.main_root)
            cycling_schema = load_cycling_schema()
            self._schema = {**bootstrap_schema, **cycling_schema}
        except FileNotFoundError as exc:
            self._error = f"Schema file not found: {exc.filename}"
            return
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            self._error = f"Schema load error: {exc}"
            return
        # Build flat key list grouped by owner.
        self._flat_keys = []
        self._is_header = []
        for owner in _OWNER_ORDER:
            group = [k for k, v in self._schema.items() if v.get("pipeline_owner") == owner]
            if not group:
                continue
            self._flat_keys.append(f"__header__{owner}")
            self._is_header.append(True)
            for k in group:
                self._flat_keys.append(k)
                self._is_header.append(False)
        # Load current values from config.
        for key in self._schema:
            val = _si_read_current_value(self._worktree_root, key)
            if val is None:
                val = self._schema[key].get("default")
            self._values[key] = val
        # Place cursor on first non-header.
        self._cursor = 0
        for i, header in enumerate(self._is_header):
            if not header:
                self._cursor = i
                break

    def _current_key(self) -> Optional[str]:
        if not self._flat_keys or self._cursor >= len(self._flat_keys):
            return None
        if self._is_header[self._cursor]:
            return None
        return self._flat_keys[self._cursor]

    def _move_cursor(self, delta: int) -> None:
        n = len(self._flat_keys)
        if n == 0:
            return
        new = self._cursor + delta
        # Skip headers.
        while 0 <= new < n and self._is_header[new]:
            new += delta
        if 0 <= new < n:
            self._cursor = new

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Settings -- bootstrap-config.json")
        self._separator(stdscr, 2, width)

        if self._error:
            self._safe_addstr(stdscr, 4, 2, f"[!] {self._error}")
            self._safe_addstr(stdscr, 6, 2, "Press Esc to go back.")
            self._footer(stdscr, height, width, "[Esc] back  |  [q] quit")
            stdscr.refresh()
            return

        if not self._flat_keys:
            self._safe_addstr(stdscr, 4, 2, "No schema properties defined.")
            self._footer(stdscr, height, width, "[Esc] back  |  [q] quit")
            stdscr.refresh()
            return

        # Body area: rows 3..(body_bottom) for content.
        # Description: up to DESC_MAX_LINES rows above footer.
        # Footer: height-2 (drawn by _footer).
        DESC_MAX_LINES = 1 if height < 20 else (2 if height < 26 else 3)
        body_top = 3
        body_bottom = height - 4 - (DESC_MAX_LINES - 1)
        desc_row_start = height - 3 - (DESC_MAX_LINES - 1)
        viewport_height = body_bottom - body_top

        # Adjust viewport so cursor stays visible.
        if self._cursor < self._viewport_top:
            self._viewport_top = self._cursor
        if self._cursor >= self._viewport_top + viewport_height:
            self._viewport_top = self._cursor - viewport_height + 1

        visible = self._flat_keys[self._viewport_top: self._viewport_top + viewport_height]
        visible_headers = self._is_header[self._viewport_top: self._viewport_top + viewport_height]

        for i, (item, is_hdr) in enumerate(zip(visible, visible_headers)):
            row = body_top + i
            abs_idx = self._viewport_top + i
            cursor_mark = "[>]" if abs_idx == self._cursor else "   "
            if is_hdr:
                owner = item.replace("__header__", "")
                label = _OWNER_LABEL.get(owner, owner)
                line = f"  --- {label} ---"
                self._safe_addstr(stdscr, row, 0, "|" + line.ljust(width - 2)[:width - 2] + "|")
            else:
                spec = self._schema[item]
                val = self._values.get(item, spec.get("default"))
                val_str = json.dumps(val) if not isinstance(val, str) else val
                interior = max(0, width - 2)
                # 1 + 3 + 1 + 30 + 1 = 36 chars before value; leave 1 for ellipsis guard
                val_avail = max(0, interior - 37)
                val_short = val_str if len(val_str) <= val_avail else val_str[:max(0, val_avail - 1)] + "…"

                line = f" {cursor_mark} {item:<30} {val_short}"
                self._safe_addstr(stdscr, row, 0, "|" + line.ljust(interior)[:interior] + "|")

        # Over-populated indicator.
        if len(self._flat_keys) > viewport_height:
            shown_end = self._viewport_top + viewport_height
            remaining = len(self._flat_keys) - shown_end
            if remaining > 0:
                more_str = f"...{remaining} more below -- down-arrow to scroll"
                self._safe_addstr(stdscr, body_bottom, 2, more_str[:width - 4])

        # Field error / save status: replaces description entirely.
        cur_key = self._current_key()
        if self._field_error:
            self._safe_addstr(stdscr, desc_row_start, 1, f"[!] {self._field_error}"[:width - 2])
        elif self._save_status:
            self._safe_addstr(stdscr, desc_row_start, 1, self._save_status[:width - 2])
        elif cur_key and cur_key in self._schema:
            desc = self._schema[cur_key].get("description", "")
            wrapped = textwrap.wrap(desc, width=max(1, width - 3)) if desc else []
            for _di, _dline in enumerate(wrapped[:DESC_MAX_LINES]):
                self._safe_addstr(stdscr, desc_row_start + _di, 1, _dline)

        hint = "[up/dn] move  |  [Enter/Space] edit  |  [S/Ctrl-S] save  |  [Esc] back"
        self._footer(stdscr, height, width, hint)
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if key in (27, ord("q")):  # Esc or q — back to shell
            return ScreenTransition(TransitionKind.POP)

        if self._error:
            return NO_TRANSITION

        if key in (ord("j"), 258):  # j or down-arrow
            self._move_cursor(1)
            self._field_error = None
            return NO_TRANSITION
        if key in (ord("k"), 259):  # k or up-arrow
            self._move_cursor(-1)
            self._field_error = None
            return NO_TRANSITION

        if key in (ord("S"), 19):  # uppercase S or Ctrl-S (19)
            self._do_save()
            return NO_TRANSITION

        if key in (ord(" "), 10, 13):  # Space or Enter — edit current field
            self._do_edit(stdscr=stdscr)
            return NO_TRANSITION

        return NO_TRANSITION

    def _do_edit(self, stdscr=None) -> None:
        cur_key = self._current_key()
        if not cur_key:
            return
        spec = self._schema[cur_key]
        field_type = spec["type"]
        self._field_error = None

        if field_type == "bool":
            # Toggle in-memory.
            current = self._values.get(cur_key)
            self._values[cur_key] = not current if isinstance(current, bool) else True

        elif field_type == "enum":
            allowed = spec.get("enum_values", [])
            current = self._values.get(cur_key, allowed[0] if allowed else "")
            try:
                idx = allowed.index(current)
            except ValueError:
                idx = -1
            self._values[cur_key] = allowed[(idx + 1) % len(allowed)] if allowed else current

        elif field_type in ("string", "string-list", "int"):
            self._edit_via_editor(cur_key, spec, stdscr=stdscr)

    def _edit_via_editor(self, key: str, spec: dict, stdscr=None) -> None:
        """Open $EDITOR or fall back to inline readline prompt."""
        field_type = spec["type"]
        current = self._values.get(key, spec.get("default"))

        editor = os.environ.get("EDITOR", "")
        if editor and field_type in ("string-list",):
            # JSON-lines edit: one item per line.
            lines = current if isinstance(current, list) else [str(current)]
            content = "\n".join(json.dumps(x) for x in lines) + "\n"
            self._open_editor_tmp(editor, key, content, field_type, stdscr=stdscr)
        elif editor and field_type == "string":
            self._open_editor_tmp(editor, key, str(current) + "\n", field_type, stdscr=stdscr)
        elif editor and field_type == "int":
            self._open_editor_tmp(editor, key, str(current) + "\n", field_type, stdscr=stdscr)
        else:
            # Inline fallback — input() needs a cooked terminal; suspend curses first.
            self._inline_edit(key, spec, current, stdscr=stdscr)

    def _open_editor_tmp(self, editor: str, key: str, content: str, field_type: str, stdscr=None) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="caa-si-")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            with _curses_suspended(stdscr):
                subprocess.call([editor, tmp_path])
            with open(tmp_path, encoding="utf-8") as fh:
                new_text = fh.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        if field_type == "string-list":
            items = []
            for raw in new_text.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    items.append(json.loads(raw))
                except json.JSONDecodeError:
                    items.append(raw)
            new_val = items
        elif field_type == "int":
            try:
                new_val = int(new_text.strip())
            except ValueError:
                self._field_error = "Invalid integer"
                return
        else:
            new_val = new_text.rstrip("\n")

        err = _si_validate_value(self._schema[key], new_val)
        if err:
            self._field_error = err
            return
        self._values[key] = new_val

    def _inline_edit(self, key: str, spec: dict, current, stdscr=None) -> None:
        """Minimal inline editor: prints prompt, reads one line from stdin.

        Suspends curses before calling input() so the terminal is in cooked
        mode and keystrokes echo correctly.
        """
        field_type = spec["type"]
        try:
            with _curses_suspended(stdscr):
                raw = input(f"New value for {key} [{current}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not raw:
            return
        if field_type == "int":
            try:
                new_val = int(raw)
            except ValueError:
                self._field_error = "Expected integer"
                return
        elif field_type == "string-list":
            try:
                new_val = json.loads(raw)
                if not isinstance(new_val, list):
                    new_val = [raw]
            except json.JSONDecodeError:
                new_val = [x.strip() for x in raw.split(",")]
        else:
            new_val = raw

        err = _si_validate_value(spec, new_val)
        if err:
            self._field_error = err
            return
        self._values[key] = new_val

    def _do_save(self) -> None:
        # Validate all current values before saving.
        for key, spec in self._schema.items():
            val = self._values.get(key)
            err = _si_validate_value(spec, val)
            if err:
                self._field_error = f"{key}: {err}"
                self._save_status = None
                return
        try:
            _si_save_config(self._worktree_root, self._values)
            self._save_status = "Saved."
            self._field_error = None
        except OSError as exc:
            # Recovering: save failed; values in memory unchanged, user can retry.
            self._save_status = f"Save error: {exc}"
            self._field_error = None


# ---------------------------------------------------------------------------
# IssueQueue and sub-screens (D4 / ISSUE-QUEUE Subtask 4)
# ---------------------------------------------------------------------------


def _fmt_relative_time(iso_str: str) -> str:
    """Format ISO-8601 UTC timestamp as a short relative-time string.

    Returns values like '2m', '5h', '3d', '2w', '1mo', '1y'.
    Falls back to '?' on parse failure.
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return '0m'
        if secs < 3600:
            return f'{max(1, secs // 60)}m'
        if secs < 86400:
            return f'{secs // 3600}h'
        days = secs // 86400
        if days < 14:
            return f'{days}d'
        if days < 60:
            return f'{days // 7}w'
        if days < 365:
            return f'{days // 30}mo'
        return f'{days // 365}y'
    except Exception:  # noqa: BLE001
        return '?'


def _sort_issues(records: list) -> list:
    """Sort by severity desc (high→med→low) then created_at desc.

    Implementation: two-pass stable sort. Pass 1 sorts by created_at descending
    (newest first within ties); Pass 2 then sorts by severity rank ascending
    (high=0 first), preserving the within-severity created_at ordering from
    Pass 1 because Python's `sorted` is stable.
    """
    _sev_rank = {'high': 0, 'med': 1, 'low': 2}
    by_created_desc = sorted(
        records,
        key=lambda r: r.get('created_at', ''),
        reverse=True,
    )
    return sorted(
        by_created_desc,
        key=lambda r: _sev_rank.get(r.get('severity', 'low'), 2),
    )


_PUSH_ISSUE_QUEUE = object()  # sentinel resolved in _resolve_screen


class IssueDetail(Screen):
    """Read-only modal showing all fields of one issue record."""

    def __init__(self, row: dict) -> None:
        self.row = row

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        issue_id = self.row.get('id', '?')
        self._header(stdscr, width, f"Issue Detail — {issue_id}")
        self._separator(stdscr, 2, width)

        fields = [
            ('id',               self.row.get('id', '')),
            ('title',            self.row.get('title', '')),
            ('severity',         self.row.get('severity', '')),
            ('status',           self.row.get('status', '')),
            ('created_at',       self.row.get('created_at', '')),
            ('updated_at',       self.row.get('updated_at', '')),
            ('origin_agent',     self.row.get('origin_agent', '')),
            ('summary',          self.row.get('summary', '')),
            ('suggested_approach', self.row.get('suggested_approach', '')),
            ('notes',            self.row.get('notes', '')),
            ('closure_reason',   self.row.get('closure_reason', '')),
            ('resolved_by',      self.row.get('resolved_by', '')),
            ('tags',             ', '.join(self.row.get('tags', []) or [])),
        ]

        visible = max(1, height - 6)
        for i, (label, value) in enumerate(fields[:visible]):
            val_str = str(value) if value else ''
            line = f"  {label:<20} {val_str}"
            self._safe_addstr(stdscr, 3 + i, 0, "|" + line.ljust(width - 2)[:width - 2] + "|")

        self._footer(stdscr, height, width, "[Esc] back")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if key in (27, ord("q")):
            return ScreenTransition(TransitionKind.POP)
        return NO_TRANSITION


class IssueQueue(Screen):
    """List open/triaged/in-progress issues; per-row actions: Enter/r/w/e/L/F5/Esc."""

    _DEFAULT_FILTER = {'status': ['open', 'triaged', 'in-progress']}
    _TOP_N = 50
    _RESOLVED_BY = 'tui-operator'  # OQ-1: hard-coded literal per ux-sketch recommendation

    def __init__(self, main_root: pathlib.Path) -> None:
        self.main_root = main_root
        self.rows: list = []
        self.cursor = 0
        self._state = 'loading'  # loading | default | empty | error | over_populated
        self._error: Optional[str] = None
        self._total_count = 0  # total matching records (before top-N truncation)
        self._mtime_cached: Optional[float] = None
        self._mtime_changed = False
        self._status_msg: Optional[str] = None  # transient action feedback

    def on_enter(self) -> None:
        self._load()

    def _load(self) -> None:
        """Snapshot issues.jsonl under LOCK_SH; cache mtime; update state."""
        self._state = 'loading'
        self._mtime_changed = False
        self._status_msg = None
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            import issue_registry  # type: ignore[import]
        except ImportError as exc:
            self._state = 'error'
            self._error = f'import error: {exc}'
            self.rows = []
            return

        # Cache mtime before reading (use lock file path as the sentinel file to stat)
        try:
            import issue_registry as _ir
            from issue_registry import _ISSUE_REL_PATH
            issue_path = self.main_root / _ISSUE_REL_PATH
            try:
                self._mtime_cached = issue_path.stat().st_mtime
            except OSError:
                self._mtime_cached = None
        except Exception:  # noqa: BLE001
            self._mtime_cached = None

        try:
            import issue_registry as _ir
            records = _ir.query(filt=self._DEFAULT_FILTER, main_root=self.main_root)
        except Exception as exc:  # noqa: BLE001
            self._state = 'error'
            self._error = str(exc)[:120]
            self.rows = []
            return

        sorted_records = _sort_issues(records)
        self._total_count = len(sorted_records)
        self.rows = sorted_records[:self._TOP_N]
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))

        if self._total_count == 0:
            self._state = 'empty'
        elif self._total_count > self._TOP_N:
            self._state = 'over_populated'
        else:
            self._state = 'default'
        self._error = None

    def _check_mtime(self) -> None:
        """Update _mtime_changed if file has been modified since last load."""
        if self._mtime_cached is None:
            return
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            import issue_registry as _ir
            from issue_registry import _ISSUE_REL_PATH
            current_mtime = (self.main_root / _ISSUE_REL_PATH).stat().st_mtime
            if current_mtime > self._mtime_cached:
                self._mtime_changed = True
        except OSError:
            pass  # file may not exist yet

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Issue Queue", f"{self._total_count} issues")
        self._separator(stdscr, 2, width)

        if self._state == 'loading':
            self._safe_addstr(stdscr, 4, 2, "Reading issues.jsonl…")
            self._footer(stdscr, height, width, "[Esc] back")
            stdscr.refresh()
            return

        if self._state == 'error':
            self._safe_addstr(stdscr, 4, 2, "Could not read issue queue:")
            self._safe_addstr(stdscr, 5, 4, (self._error or '')[:width - 6])
            self._safe_addstr(stdscr, 7, 2, "Press [r] to retry, [Esc] to go back.")
            self._footer(stdscr, height, width, "[r] retry  |  [Esc] back")
            stdscr.refresh()
            return

        if self._state == 'empty':
            self._safe_addstr(
                stdscr, 4, 2,
                "No open issues. The queue is populated by agents via the issues(...) MCP tool,"
                " including the knowledge-hygiene pipeline."
            )
            self._footer(stdscr, height, width, "[F5] reload  |  [Esc] back")
            stdscr.refresh()
            return

        # default / over_populated: render table
        # Column widths: id=12, severity=4, status=11, created=9, origin=16, title=remainder
        _id_w = 12
        _sev_w = 4
        _stat_w = 11
        _cr_w = 9
        _orig_w = 16
        _fixed = _id_w + _sev_w + _stat_w + _cr_w + _orig_w + 6  # 6 separators
        interior = max(0, width - 2)
        _title_w = max(4, interior - _fixed - 1)

        # Column header
        hdr = (f" {'ID':<{_id_w}}  {'SEV':<{_sev_w}}  {'STATUS':<{_stat_w}}"
               f"  {'AGE':>{_cr_w}}  {'ORIGIN':<{_orig_w}}  TITLE")
        self._safe_addstr(stdscr, 3, 0, "|" + hdr.ljust(interior)[:interior] + "|")

        visible = max(1, height - 7)  # -2 box, -1 header, -1 sep, -1 footer(-2), -1 status_msg
        start = max(0, self.cursor - visible + 1)

        for i, row in enumerate(self.rows[start: start + visible]):
            abs_i = start + i
            sel = "[>]" if abs_i == self.cursor else "   "
            rid = (row.get('id') or '')[:_id_w]
            sev = (row.get('severity') or '')[:_sev_w]
            stat = (row.get('status') or '')[:_stat_w]
            age = _fmt_relative_time(row.get('created_at', ''))
            orig = (row.get('origin_agent') or '')[:_orig_w]
            title_full = row.get('title') or ''
            title = title_full if len(title_full) <= _title_w else title_full[:max(0, _title_w - 1)] + '…'

            line = (f"{sel} {rid:<{_id_w}}  {sev:<{_sev_w}}  {stat:<{_stat_w}}"
                    f"  {age:>{_cr_w}}  {orig:<{_orig_w}}  {title}")
            self._safe_addstr(stdscr, 4 + i, 0, "|" + line.ljust(interior)[:interior] + "|")

        # Over-populated hint
        if self._state == 'over_populated':
            n_more = self._total_count - self._TOP_N
            hint = f"…{n_more} more — narrow filter or resolve issues to prune list"
            self._safe_addstr(stdscr, 4 + min(visible, len(self.rows)), 2, hint[:width - 4])

        # Status message (action feedback)
        if self._status_msg:
            self._safe_addstr(stdscr, height - 3, 2, self._status_msg[:width - 4])

        # Mtime-changed overlay footer (appended to main footer)
        footer = "[up/dn] scroll  |  [Enter] detail  |  [r] resolve  |  [w] wont-fix  |  [e] notes  |  [L] focus  |  [F5] reload  |  [Esc] back"
        if self._mtime_changed:
            footer = "(file changed — press F5 to reload)  |  " + footer
        self._footer(stdscr, height, width, footer)
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        try:
            import curses as _curses
            up_keys = (_curses.KEY_UP, ord("k"))
            down_keys = (_curses.KEY_DOWN, ord("j"))
            enter_keys = (_curses.KEY_ENTER, 10, 13)
            f5_key = _curses.KEY_F5
        except ImportError:
            up_keys = (ord("k"),)
            down_keys = (ord("j"),)
            enter_keys = (10, 13)
            f5_key = None

        # Check mtime on every keypress before dispatch
        self._check_mtime()

        # Error state: only retry and back
        if self._state == 'error':
            if key == ord('r'):
                self._load()
                return NO_TRANSITION
            if key in (27, ord('q')):
                return ScreenTransition(TransitionKind.POP)
            return NO_TRANSITION

        # Empty / loading state: only F5 and back
        if self._state in ('empty', 'loading'):
            if key == f5_key or (f5_key is None and key == ord('r')):
                self._load()
                return NO_TRANSITION
            if key in (27, ord('q')):
                return ScreenTransition(TransitionKind.POP)
            return NO_TRANSITION

        # default / over_populated
        if key in up_keys:
            self.cursor = max(0, self.cursor - 1)
            return NO_TRANSITION
        if key in down_keys:
            self.cursor = min(len(self.rows) - 1, self.cursor + 1)
            return NO_TRANSITION

        if key in (27, ord('q')):
            return ScreenTransition(TransitionKind.POP)

        if f5_key is not None and key == f5_key:
            self._load()
            return NO_TRANSITION

        if not self.rows:
            return NO_TRANSITION

        row = self.rows[self.cursor]

        if key in enter_keys:
            return ScreenTransition(TransitionKind.PUSH, IssueDetail(row=row))

        if key == ord('r'):
            return ScreenTransition(
                TransitionKind.PUSH,
                _IssueResolveAction(row=row, main_root=self.main_root,
                                    mode='resolve', on_success=self._load),
            )

        if key == ord('w'):
            return ScreenTransition(
                TransitionKind.PUSH,
                _IssueResolveAction(row=row, main_root=self.main_root,
                                    mode='wontfix', on_success=self._load),
            )

        if key == ord('e'):
            self._do_edit_notes(row, stdscr=stdscr)
            return NO_TRANSITION

        if key == ord('L'):  # uppercase L — LaunchFocusedSessionAction
            return ScreenTransition(
                TransitionKind.PUSH,
                _IssueLaunchAction(row=row),
            )

        return NO_TRANSITION

    def _do_edit_notes(self, row: dict, stdscr=None) -> None:
        """Open $EDITOR on a tempfile pre-filled with current notes; update on save."""
        editor = os.environ.get('EDITOR', '')
        current_notes = row.get('notes') or ''
        issue_id = row.get('id', '')

        if not editor:
            # Inline fallback
            try:
                with _curses_suspended(stdscr):
                    new_notes = input(f"Notes for {issue_id} (blank to clear): ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        else:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='caa-issue-notes-')
            try:
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as fh:
                    fh.write(current_notes)
                with _curses_suspended(stdscr):
                    subprocess.call([editor, tmp_path])
                with open(tmp_path, encoding='utf-8') as fh:
                    new_notes = fh.read().rstrip('\n')
            except OSError as exc:
                self._status_msg = f'Editor error: {exc}'
                return
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        try:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            import issue_registry as _ir
            _ir.update(issue_id, main_root=self.main_root, notes=new_notes)
            self._load()
        except Exception as exc:  # noqa: BLE001
            self._status_msg = f'Update failed: {exc}'


class _IssueResolveAction(Screen):
    """Modal prompt for resolve ('r') or wont-fix ('w') actions."""

    def __init__(self, row: dict, main_root: pathlib.Path, mode: str, on_success=None) -> None:
        self.row = row
        self.main_root = main_root
        self.mode = mode  # 'resolve' | 'wontfix'
        self.on_success = on_success
        self._result_msg = ''

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        issue_id = self.row.get('id', '?')
        title = self.row.get('title', '')
        if self.mode == 'resolve':
            self._header(stdscr, width, f"Resolve Issue {issue_id}")
        else:
            self._header(stdscr, width, f"Wont-Fix Issue {issue_id}")
        self._separator(stdscr, 2, width)
        self._safe_addstr(stdscr, 4, 2, f"Title: {title[:width - 10]}")
        self._safe_addstr(stdscr, 6, 2, "Enter closure reason (required; empty cancels):")
        if self._result_msg:
            self._safe_addstr(stdscr, 8, 2, self._result_msg[:width - 4])
        self._footer(stdscr, height, width, "[Enter] confirm  |  [Esc] cancel")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        try:
            import curses as _curses
            enter_keys = (_curses.KEY_ENTER, 10, 13)
        except ImportError:
            enter_keys = (10, 13)

        if key in (27, ord('q')):
            return ScreenTransition(TransitionKind.POP)

        if key in enter_keys:
            try:
                with _curses_suspended(stdscr):
                    raw = input("Closure reason: ").strip()
            except (EOFError, KeyboardInterrupt):
                return ScreenTransition(TransitionKind.POP)

            if not raw:
                self._result_msg = "[!] Closure reason is required. Press Esc to cancel."
                return NO_TRANSITION

            issue_id = self.row.get('id', '')
            try:
                sys.path.insert(0, str(pathlib.Path(__file__).parent))
                import issue_registry as _ir
                if self.mode == 'resolve':
                    result = _ir.resolve(
                        issue_id, closure_reason=raw,
                        resolved_by=IssueQueue._RESOLVED_BY,
                        main_root=self.main_root,
                    )
                else:
                    result = _ir.update(
                        issue_id, main_root=self.main_root,
                        status='wont-fix', closure_reason=raw,
                    )
                if isinstance(result, dict) and result.get('error'):
                    self._result_msg = f"[!] {result.get('message', result['error'])}"
                    return NO_TRANSITION
            except Exception as exc:  # noqa: BLE001
                self._result_msg = f"[!] Error: {exc}"
                return NO_TRANSITION

            if self.on_success is not None:
                self.on_success()
            return ScreenTransition(TransitionKind.POP)

        return NO_TRANSITION


class _IssueLaunchAction(Screen):
    """Modal confirm for launching a focused session (`claude-session --issue <id>`)."""

    def __init__(self, row: dict) -> None:
        self.row = row
        self._result_msg = ''

    def render(self, stdscr) -> None:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        self._draw_box(stdscr, height, width)
        self._header(stdscr, width, "Launch Focused Session")
        self._separator(stdscr, 2, width)
        issue_id = self.row.get('id', '?')
        title = self.row.get('title', '')
        self._safe_addstr(stdscr, 4, 2, f"Launch focused session for issue {issue_id}?")
        self._safe_addstr(stdscr, 5, 4, f"Title: {title[:width - 12]}")
        self._safe_addstr(stdscr, 7, 2, "Press [y] to confirm or [n] / [Esc] to cancel.")
        if self._result_msg:
            self._safe_addstr(stdscr, 9, 2, self._result_msg[:width - 4])
        self._footer(stdscr, height, width, "[y] confirm  |  [n] cancel  |  [Esc] back")
        stdscr.refresh()

    def handle_key(self, key: int, stdscr=None) -> ScreenTransition:
        if key in (ord('n'), 27, ord('q')):
            return ScreenTransition(TransitionKind.POP)

        if key == ord('y'):
            issue_id = self.row.get('id', '')
            # Find claude-session binary relative to this file
            bin_dir = pathlib.Path(__file__).parent
            cs_bin = bin_dir / 'claude-session'
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(cs_bin), '--issue', issue_id],
                    start_new_session=True,  # detach from parent TUI process group
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._result_msg = f"Spawned focused session pid={proc.pid} — use your terminal multiplexer to view."
            except OSError as exc:
                self._result_msg = f"[!] Spawn failed: {exc}"
            return NO_TRANSITION  # stay in modal so operator can read the pid message

        return NO_TRANSITION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_use_curses() -> bool:
    """True when curses is importable and stdin/stdout are TTYs with a real TERM."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    try:
        import curses  # noqa: F401
        return True
    except ImportError:
        return False


def _main_git_is_dirty(main_root: pathlib.Path) -> tuple:
    """Return (is_dirty, list_of_changed_paths) for the main repo.

    Shells out to 'git status --porcelain'.  Returns (False, []) on timeout or
    subprocess failure (callers treat failure as clean per design-sketch note).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(main_root),
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        return bool(lines), lines
    except (subprocess.TimeoutExpired, OSError):
        return False, []


COMMIT_MSG = "[claude-session] TUI pre-launch commit"
# Co-pointer: COMMIT_MSG is coupled to the --grep pattern in _h2_compute_since_ts.
# If this string is renamed, update the git log --grep pattern in lockstep.


# ---------------------------------------------------------------------------
# H2: session-attributed staging filter (Frankenstein-commit closure)
# ---------------------------------------------------------------------------

# Paths whose own writes are the attribution source for the gate.
# Including these in the candidate set is incoherent: every entry in
# .change-log.jsonl was written by some prior session, so the file itself
# ALWAYS appears foreign, inflating foreign_count by 1 on every TUI fire.
# Excluded from BOTH dirty-paths universe AND change-log iteration.
EXCLUDE_PATHS: frozenset = frozenset({
    ".claude/knowledge-log/.change-log.jsonl",
})


def _h2_parse_iso(ts_str: str):
    """Parse ISO-8601 string to datetime (UTC-aware). Returns None on failure."""
    try:
        # Handle 'Z' suffix and numeric offsets.
        s = ts_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


def _h2_read_recent_writes(
    change_log_path: pathlib.Path,
    since_ts_iso: str,
) -> list:
    """Return change-log entries with ts >= since_ts_iso.

    Pure-Python JSONL tail read. NO MCP dependency (TUI fires before any
    Claude session is up). Schema fields per knowledge.ts:buildChangeLogEntry:
    {ts, session_id, file, section, operation, status, actor, ...}.
    """
    if not change_log_path.exists():
        return []
    entries = []
    since_dt = _h2_parse_iso(since_ts_iso) if since_ts_iso else None
    try:
        with open(change_log_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # skip malformed lines (best-effort read)
                if since_dt is not None:
                    ts_str = rec.get("ts") or ""
                    if ts_str:
                        entry_dt = _h2_parse_iso(ts_str)
                        if entry_dt is not None and entry_dt < since_dt:
                            continue
                entries.append(rec)
    except OSError:
        return []  # unreadable → safe-default (no attribution data)
    return entries


def _h2_compute_since_ts(main_root: pathlib.Path) -> str:
    """B3 with B1 fallback. B3: last TUI pre-launch commit. B1: HEAD commit.

    Co-pointer: the --grep pattern below is coupled to COMMIT_MSG constant above.
    If COMMIT_MSG is renamed, this pattern MUST change in lockstep.
    """
    # B3: last successful TUI pre-launch commit.
    p = subprocess.run(
        ["git", "log", "-1", "--format=%cI",
         "--grep=^\\[claude-session\\] TUI pre-launch commit$"],
        capture_output=True, text=True, cwd=str(main_root), timeout=10,
    )
    ts = p.stdout.strip()
    if ts:
        return ts
    # B1 fallback: last commit on HEAD.
    p2 = subprocess.run(
        ["git", "log", "-1", "--format=%cI"],
        capture_output=True, text=True, cwd=str(main_root), timeout=10,
    )
    return p2.stdout.strip()  # may be empty (fresh repo) → reader treats as no filter


def _filter_foreign_session_files(
    change_log_path: pathlib.Path,
    current_session_id: str,
    since_ts: str,
    dirty_paths: list,
) -> "tuple[list[str], list[str]]":
    """Classify dirty_paths into (this_session_paths, foreign_session_paths).

    Hybrid policy (Option C):
      - Symlinked subtrees (.claude/knowledge/, .claude/knowledge-log/,
        .claude/mcp/): positive attribution via change-log. Stage ONLY paths
        whose latest change-log entry in the since window has
        session_id == current_session_id OR is a real-orphan (D-stage class).
      - Worktree-local subtrees (everything else): stage unconditionally.

    EXCLUDE_PATHS are stripped from both dirty_paths and change-log iteration.

    Entry-level filters (before bucketing):
      - D-skip: session_id starting with 'test-' → skip path
      - D-skip: operation == 'sidecar-unreadable' → skip path
      - D-skip: file in EXCLUDE_PATHS → skip path

    Returns (this_session_paths, foreign_session_paths).
    foreign_session_paths includes orphan/unknown-session paths in symlinked subtrees.
    """
    # Strip EXCLUDE_PATHS from candidate universe.
    dirty_paths = [p for p in dirty_paths if p not in EXCLUDE_PATHS]

    raw_entries = _h2_read_recent_writes(change_log_path, since_ts)

    # Entry-level filter: drop D-skip class before bucketing.
    entries = []
    skip_paths: set = set()
    for rec in raw_entries:
        sid = rec.get("session_id") or ""
        op = rec.get("operation") or ""
        f = rec.get("file") or ""
        if sid.startswith("test-"):
            if f:
                skip_paths.add(f)
            continue
        if op == "sidecar-unreadable":
            if f:
                skip_paths.add(f)
            continue
        if f in EXCLUDE_PATHS:
            skip_paths.add(f)
            continue
        entries.append(rec)

    # Bucket entries by file (latest entry wins per file).
    by_file: dict = {}
    for rec in entries:
        f = rec.get("file")
        if not f:
            continue
        prev = by_file.get(f)
        if prev is None or (rec.get("ts") or "") > (prev.get("ts") or ""):
            by_file[f] = rec

    SYMLINKED_PREFIXES = (
        ".claude/knowledge/", ".claude/knowledge-log/", ".claude/mcp/",
    )

    this_session: list = []
    foreign: list = []
    for path in dirty_paths:
        if path in skip_paths:
            continue
        is_symlinked = any(path.startswith(pfx) for pfx in SYMLINKED_PREFIXES)
        if not is_symlinked:
            # Worktree-local: stage unconditionally as this-session.
            this_session.append(path)
            continue
        # Symlinked subtree: positive attribution.
        rec = by_file.get(path)
        if rec is None:
            # No change-log entry within window → orphan (D-stage real).
            # Treat as foreign so the gate can surface it to the user.
            foreign.append(path)
            continue
        sid = rec.get("session_id") or ""
        if sid == current_session_id:
            this_session.append(path)
        else:
            # Foreign session (including null-sid legacy entries and real orphans).
            foreign.append(path)

    return this_session, foreign


def _h2_build_staging_list(
    main_root: pathlib.Path,
    worktree_session_id: str,
    *,
    accept_dirty: bool,
) -> "tuple[int, list[str], list[str]]":
    """Build the staged-path list using H2 hybrid attribution.

    Returns (foreign_count, paths_to_stage, orphan_source_session_ids).
    When accept_dirty=True, stages all paths (legacy behavior).
    """
    # Get dirty-path universe.
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--name-only"],
        capture_output=True, text=True, cwd=str(main_root), timeout=10,
    )
    dirty_paths = [p for p in diff.stdout.splitlines() if p.strip()]
    untracked = subprocess.run(
        ["git", "ls-files", "-o", "--exclude-standard"],
        capture_output=True, text=True, cwd=str(main_root), timeout=10,
    )
    dirty_paths += [p for p in untracked.stdout.splitlines() if p.strip()]
    dirty_paths = [p for p in dirty_paths if p not in EXCLUDE_PATHS]

    if accept_dirty:
        return len(dirty_paths), dirty_paths, []

    since_ts = _h2_compute_since_ts(main_root)
    cl_path = main_root / ".claude" / "knowledge-log" / ".change-log.jsonl"

    this_session, foreign = _filter_foreign_session_files(
        change_log_path=cl_path,
        current_session_id=worktree_session_id,
        since_ts=since_ts,
        dirty_paths=dirty_paths,
    )

    foreign_count = len(foreign)
    # Collect session IDs from foreign entries for the orphan-sources list.
    raw_entries = _h2_read_recent_writes(cl_path, since_ts)
    by_file: dict = {}
    for rec in raw_entries:
        f = rec.get("file")
        if f:
            prev = by_file.get(f)
            if prev is None or (rec.get("ts") or "") > (prev.get("ts") or ""):
                by_file[f] = rec
    orphan_sources: set = set()
    for path in foreign:
        rec = by_file.get(path)
        if rec is not None:
            sid = rec.get("session_id") or "<null-sid>"
            orphan_sources.add(sid)
        else:
            orphan_sources.add("<unknown-pre-boundary>")

    # Default: commit only this session's paths.
    paths_to_stage = list(this_session)
    return foreign_count, paths_to_stage, sorted(orphan_sources)


def _extract_pushed_sha(porcelain_stdout: str) -> Optional[str]:
    """Parse `git push --porcelain` output and return the new (pushed) SHA.

    Per git-push(1) porcelain format, non-comment ref-update lines have the form:
      <flag>TAB<from>:<to>TAB<summary>
    where <summary> is `<old_sha>..<new_sha>` for fast-forward or
    `<old_sha>...<new_sha>` for forced push.  We extract the new SHA (right side).
    Returns None on any parse failure; caller substitutes '<unknown>'.
    """
    sha_re = re.compile(r"[0-9a-f]{40}\.\.\.?([0-9a-f]{40})")
    for line in porcelain_stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in ("Done", "Everything up-to-date"):
            continue
        m = sha_re.search(stripped)
        if m:
            return m.group(1)
    return None


def _write_tui_push_conflict(
    main_root: pathlib.Path,
    *,
    branch: str,
    push_cmd,
    stderr: str,
    returncode: int,
) -> pathlib.Path:
    """Write a push-conflict diagnostic file to <main_root>/.agent_context/.

    Returns the path to the written file.  Per invariant 11 of
    docs/path-c-invariants.md, this file MUST be written BEFORE
    the error is escalated to the caller; the file path is the load-bearing signal.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S") + secrets.token_hex(2)
    filename = f"tui-push-conflict-{ts}.md"
    agent_context_dir = main_root / ".agent_context"
    agent_context_dir.mkdir(parents=True, exist_ok=True)
    conflict_path = agent_context_dir / filename

    # Capture HEAD and remote SHAs AFTER failed push for diagnostic fidelity.
    def _rev_parse(ref: str) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", ref],
                capture_output=True, text=True, cwd=str(main_root), timeout=5,
            )
            return r.stdout.strip() or "<unknown>"
        except Exception:  # noqa: BLE001
            return "<unknown>"

    head_sha = _rev_parse("HEAD")
    remote_sha = _rev_parse(f"origin/{branch}")
    push_cmd_str = " ".join(str(c) for c in push_cmd) if push_cmd else "<unknown>"

    content = (
        f"# TUI Pre-Launch Push Conflict\n"
        f"- **Timestamp**: {datetime.now(timezone.utc).isoformat()}\n"
        f"- **Push command**: {push_cmd_str}\n"
        f"- **Returncode**: {returncode}\n"
        f"- **Stderr (full)**:\n  {stderr}\n"
        f"- **HEAD SHA**: {head_sha}\n"
        f"- **Remote branch HEAD SHA**: {remote_sha}\n"
        f"- **Resolution commands**:\n"
        f"  git fetch origin {branch} && git rebase origin/{branch} && "
        f"git push origin HEAD:{branch}\n"
        f"- **Note**: Automated rebase NOT performed at TUI time. "
        f"Resolve manually before re-launching.\n"
        f"# TODO: consider adding .agent_context/tui-push-conflict-*.md to .gitignore "
        f"as a follow-on if these files accumulate noise.\n"
    )
    conflict_path.write_text(content, encoding="utf-8")
    return conflict_path


def _run_git_commit_and_push(
    main_root: pathlib.Path,
    *,
    accept_dirty: bool = False,
    worktree_session_id: str = "",
) -> "tuple[bool, str, Optional[pathlib.Path]]":
    """Run git add (targeted), git commit, git push.

    H2: replaces blanket `git add -A` with session-attributed staging.
    When accept_dirty=True, stages all dirty paths (legacy behavior).

    Returns (success, message, conflict_file_path):
      On success: (True, "pushed <sha12> to origin/<branch>", None)
      On commit failure: (False, "<stderr>", None)
      On push failure: (False, "<push_stderr>", <path-to-conflict-file>)
    """
    lock_path = main_root / ".agent_context" / "worktrees" / ".tui-push.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(str(lock_path), "a")  # O_CREAT | O_RDWR via 'a' mode  # noqa: WPS515
    try:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_fh.close()
            return (False, "Another claude-session TUI is mid-push. Wait or resolve manually.", None)

        # Step 1a: build session-attributed path list (H2 gate).
        foreign_count, paths_to_stage, _orphan_sources = _h2_build_staging_list(
            main_root, worktree_session_id, accept_dirty=accept_dirty,
        )
        if accept_dirty:
            all_count = len(paths_to_stage)
            print(
                f"[claude-session] --accept-dirty: skipping foreign-session check; "
                f"committing {all_count} files ({foreign_count} from foreign sessions).",
                file=sys.stderr,
            )
        elif foreign_count > 0:
            print(
                f"[claude-session] H2: staging {len(paths_to_stage)} files "
                f"(skipping {foreign_count} foreign-session files); "
                f"orphan sources: {_orphan_sources}",
                file=sys.stderr,
            )

        if not paths_to_stage:
            return (True, "no paths to stage; working tree clean", None)

        # Step 1b: git add -- <paths> (NEVER `git add -A`).
        add_result = subprocess.run(
            ["git", "add", "--"] + paths_to_stage,
            capture_output=True, text=True, cwd=str(main_root), timeout=30,
        )
        if add_result.returncode != 0:
            return (False, "git add failed: " + add_result.stderr, None)

        # Step 2: git commit with Session-Id: trailer.
        commit_cmd = ["git", "commit", "-m", COMMIT_MSG]
        if worktree_session_id:
            commit_cmd += ["--trailer", f"Session-Id: {worktree_session_id}"]
        commit_result = subprocess.run(
            commit_cmd,
            capture_output=True, text=True, cwd=str(main_root), timeout=30,
        )
        if commit_result.returncode != 0:
            return (False, "git commit failed: " + commit_result.stderr, None)

        # Step 3: resolve upstream branch (invariant 11 exact form — ${BRANCH:-master}).
        branch_proc = subprocess.run(
            ["bash", "-c",
             "BRANCH=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null"
             " | sed 's|^origin/||'); echo ${BRANCH:-master}"],
            capture_output=True, text=True, cwd=str(main_root), timeout=10,
        )
        branch = branch_proc.stdout.strip() or "master"

        # Step 4: git push --porcelain origin HEAD:<branch>
        push_proc = subprocess.run(
            ["git", "push", "--porcelain", "origin", f"HEAD:{branch}"],
            capture_output=True, text=True, cwd=str(main_root),
            timeout=int(os.environ.get("CLAUDE_SESSION_TUI_PUSH_TIMEOUT", "60")),
        )

        if push_proc.returncode == 0:
            pushed_sha = _extract_pushed_sha(push_proc.stdout) or "<unknown>"
            return (True, f"pushed {pushed_sha[:12]} to origin/{branch}", None)

        # Push failure → write diagnostic file then return refused.
        conflict_path = _write_tui_push_conflict(
            main_root,
            branch=branch,
            push_cmd=push_proc.args,
            stderr=push_proc.stderr or "(no stderr)",
            returncode=push_proc.returncode,
        )
        # Truncate push stderr at 4KB for PIPE_BUF safety (invariant 8);
        # full stderr is preserved verbatim in the conflict file.
        stderr_msg = (push_proc.stderr or "git push failed")[:4096]
        return (False, stderr_msg, conflict_path)

    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        lock_fh.close()


def _save_defaults_to_bootstrap_config(
    main_root: pathlib.Path,
    defaults_dict: dict,
) -> None:
    """Atomically overwrite pipelines.defaults in .claude/bootstrap-config.json.

    Preserves all other keys.  Atomic: write to tmp + os.replace (G-4 pattern).
    """
    config_path = main_root / ".claude" / "bootstrap-config.json"
    with open(config_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("pipelines", {})["defaults"] = defaults_dict
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=config_path.parent,
        prefix=".bootstrap-config.tmp.",
        suffix=".json",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _build_equivalent_cli(decision: LaunchDecision, defaults: dict) -> str:
    """Build the stderr-echo equivalent CLI string (Decision 5)."""
    parts = ["claude-session", "--raw"]
    if decision.resume_id:
        parts += ["--resume", decision.resume_id]
    elif decision.active_pipelines is not None:
        if decision.active_pipelines:
            parts += ["--pipelines", ",".join(sorted(decision.active_pipelines))]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Line-mode fallback
# ---------------------------------------------------------------------------


def _run_line_mode_fallback(
    main_root: pathlib.Path,
    pipeline_registry: set,
    pipeline_defaults: dict,
    pipeline_summaries: dict,
    accept_dirty: bool = False,
    worktree_session_id: str = "",
) -> LaunchDecision:
    """Minimal numbered-menu fallback when curses is unavailable."""
    print("[claude-session] TUI: curses unavailable; falling back to line-mode prompts",
          file=sys.stderr)

    registry = sorted(pipeline_registry)
    active: set = {k for k, v in pipeline_defaults.items() if v}

    while True:
        print("\nclaude-session -- interactive launcher")
        print("  l. Launch    -- use default pipelines")
        print("  1. Pipelines -- select & launch")
        print("  2. Worktrees -- manage existing sessions")
        print("  3. Settings  -- not yet implemented")
        print("  q. Quit")
        choice = input("> ").strip().lower()
        if choice == "q":
            return LaunchDecision(kind="exit_abort")
        if choice == "l":
            return LaunchDecision(kind="exit_with_launch", active_pipelines=active)
        if choice == "1":
            decision = _line_mode_pipeline_select(
                main_root, registry, active, pipeline_summaries,
                accept_dirty=accept_dirty, worktree_session_id=worktree_session_id,
            )
            if decision is not None:
                return decision
        elif choice == "2":
            decision = _line_mode_worktree_manage(main_root)
            if decision is not None:
                return decision
        elif choice == "3":
            print("Settings inspector not yet implemented. Press Enter to continue.")
            input()


def _line_mode_pipeline_select(
    main_root: pathlib.Path,
    registry: list,
    active: set,
    summaries: dict,
    accept_dirty: bool = False,
    worktree_session_id: str = "",
) -> Optional[LaunchDecision]:
    if not registry:
        print("No pipelines registered. Run 'caa-setup pipelines' or use --raw.")
        return None
    while True:
        print("\nPipelines -- Select & Launch")
        for i, name in enumerate(registry, 1):
            tog = "[x]" if name in active else "[ ]"
            summary = summaries.get(name, "")
            print(f"  {i}. {tog} {name:<12} {summary}")
        print("Current selection:", ", ".join(sorted(active)) or "(none)")
        print("Commands: <number> toggle, l launch, S save-defaults, q back")
        cmd = input("> ").strip()
        if cmd.lower() == "q":
            return None
        if cmd.lower() == "l":
            is_dirty, paths = _main_git_is_dirty(main_root)
            if is_dirty:
                return _line_mode_git_pre_launch(
                    main_root, set(active), paths,
                    accept_dirty=accept_dirty, worktree_session_id=worktree_session_id,
                )
            return LaunchDecision(kind="exit_with_launch", active_pipelines=set(active))
        if cmd == "S":
            try:
                _save_defaults_to_bootstrap_config(
                    main_root, {n: (n in active) for n in registry}
                )
                print("Defaults saved.")
            except OSError as exc:
                print(f"Save failed: {exc}")
            continue
        try:
            idx = int(cmd) - 1
            if 0 <= idx < len(registry):
                name = registry[idx]
                if name in active:
                    active.discard(name)
                else:
                    active.add(name)
        except ValueError:
            pass
    return None  # unreachable


def _line_mode_worktree_manage(main_root: pathlib.Path) -> Optional[LaunchDecision]:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import session_registry  # type: ignore[import]
        data = session_registry.read_registry(main_root)
        rows = sorted(data.values(), key=lambda r: r.get("last_touched", ""), reverse=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read registry: {exc}")
        return None
    if not rows:
        print("No active worktrees.")
        return None
    print("\nWorktrees -- Manage")
    for i, row in enumerate(rows, 1):
        display_name = row.get("name") or row.get("id") or "?"
        status = row.get("status") or "?"
        print(f"  {i}. {display_name:<22}  {status}")
    print("Commands: <number> resume, q back")
    cmd = input("> ").strip()
    if cmd.lower() == "q":
        return None
    try:
        idx = int(cmd) - 1
        if 0 <= idx < len(rows):
            return LaunchDecision(kind="exit_with_launch", resume_id=rows[idx]["id"])
    except ValueError:
        pass
    return None


def _line_mode_git_pre_launch(
    main_root: pathlib.Path,
    active_pipelines: set,
    dirty_paths: list,
    accept_dirty: bool = False,
    worktree_session_id: str = "",
) -> LaunchDecision:
    # Compute foreign-session classification for H2 gate.
    since_ts = _h2_compute_since_ts(main_root)
    cl_path = main_root / ".claude" / "knowledge-log" / ".change-log.jsonl"
    # dirty_paths here is porcelain format ("XY path"); extract the path part.
    raw_paths = [ln[3:] if len(ln) > 3 else ln for ln in dirty_paths]
    this_session_paths, foreign_paths = _filter_foreign_session_files(
        change_log_path=cl_path,
        current_session_id=worktree_session_id,
        since_ts=since_ts,
        dirty_paths=raw_paths,
    )
    foreign_count = len(foreign_paths)
    # Collect distinct foreign session IDs for the UX prompt line.
    foreign_session_ids: list = []
    if foreign_paths:
        raw_cl = _h2_read_recent_writes(cl_path, since_ts)
        by_file_lm: dict = {}
        for rec in raw_cl:
            f = rec.get("file")
            if f and f not in EXCLUDE_PATHS:
                prev = by_file_lm.get(f)
                if prev is None or (rec.get("ts") or "") > (prev.get("ts") or ""):
                    by_file_lm[f] = rec
        fids: set = set()
        for fp in foreign_paths:
            rec = by_file_lm.get(fp)
            sid = (rec.get("session_id") or "") if rec else ""
            fids.add(sid if sid else "<unknown>")
        foreign_session_ids = sorted(fids)

    print(f"\n{len(dirty_paths)} uncommitted changes on main:")
    for p in dirty_paths[:20]:
        print(f"  {p}")
    if len(dirty_paths) > 20:
        print(f"  ...{len(dirty_paths) - 20} more")

    if accept_dirty:
        # --accept-dirty: skip gate, stage everything.
        success, msg, conflict_path = _run_git_commit_and_push(
            main_root, accept_dirty=True, worktree_session_id=worktree_session_id,
        )
        print(f"[claude-session] {msg}", file=sys.stderr)
        if success:
            return LaunchDecision(
                kind="exit_with_launch",
                active_pipelines=active_pipelines,
                git_commit_performed=True,
                push_outcome_message=msg,
            )
        if conflict_path:
            print(f"[claude-session] Push diagnostic: {conflict_path}", file=sys.stderr)
        return LaunchDecision(
            kind="exit_refused_dirty_state",
            active_pipelines=active_pipelines,
            push_outcome_message=msg,
            push_conflict_path=conflict_path,
        )

    # H2 gate: show foreign-session info if present.
    if foreign_count > 0:
        print("\nPre-launch commit: foreign-session writes detected.")
        sid_display = worktree_session_id[:20] if worktree_session_id else "(unknown)"
        print(f"  This worktree: {sid_display}")
        if len(foreign_session_ids) == 1:
            print(f"  Foreign sessions: {foreign_session_ids[0]} ({foreign_count} files)")
        else:
            fids_str = ", ".join(foreign_session_ids)
            print(f"  Foreign sessions: {fids_str} ({foreign_count} files)")
        print("")
        print("Options:")
        print("  [c] commit only this session's writes (recommended)")
        print("  [a] accept dirty -- commit everything (Frankenstein)")
        print("  [s] show diff of foreign-session files")
        print("  [q] quit launch (resolve manually, then re-run)")
        while True:
            cmd = input("Choice [c/a/s/q]: ").strip().lower()
            if cmd == "" or cmd == "c":
                # Default-on-Enter = c.
                success, msg, conflict_path = _run_git_commit_and_push(
                    main_root, accept_dirty=False, worktree_session_id=worktree_session_id,
                )
                print(f"[claude-session] {msg}", file=sys.stderr)
                if success:
                    return LaunchDecision(
                        kind="exit_with_launch",
                        active_pipelines=active_pipelines,
                        git_commit_performed=True,
                        push_outcome_message=msg,
                    )
                if conflict_path:
                    print(f"[claude-session] Push diagnostic: {conflict_path}", file=sys.stderr)
                return LaunchDecision(
                    kind="exit_refused_dirty_state",
                    active_pipelines=active_pipelines,
                    push_outcome_message=msg,
                    push_conflict_path=conflict_path,
                )
            if cmd == "a":
                typed = input("Type 'accept' to commit everything (Frankenstein): ").strip()
                if typed != "accept":
                    print("[!] confirmation failed; choose c/a/s/q again")
                    continue
                success, msg, conflict_path = _run_git_commit_and_push(
                    main_root, accept_dirty=True, worktree_session_id=worktree_session_id,
                )
                print(f"[claude-session] {msg}", file=sys.stderr)
                if success:
                    return LaunchDecision(
                        kind="exit_with_launch",
                        active_pipelines=active_pipelines,
                        git_commit_performed=True,
                        push_outcome_message=msg,
                    )
                if conflict_path:
                    print(f"[claude-session] Push diagnostic: {conflict_path}", file=sys.stderr)
                return LaunchDecision(
                    kind="exit_refused_dirty_state",
                    active_pipelines=active_pipelines,
                    push_outcome_message=msg,
                    push_conflict_path=conflict_path,
                )
            if cmd == "s":
                if foreign_paths:
                    subprocess.run(
                        ["git", "diff", "HEAD", "--"] + foreign_paths,
                        cwd=str(main_root),
                    )
                continue
            if cmd == "q":
                return LaunchDecision(kind="exit_abort")
            # Unknown choice → re-prompt.
            print("[!] Unknown choice; choose c/a/s/q")

    # No foreign writes: simple commit prompt.
    print("Choose: c (commit & push), q (refuse - exit), n (abort)")
    cmd = input("> ").strip().lower()
    if cmd == "c" or cmd == "":
        success, msg, conflict_path = _run_git_commit_and_push(
            main_root, accept_dirty=False, worktree_session_id=worktree_session_id,
        )
        print(f"[claude-session] {msg}", file=sys.stderr)
        if success:
            return LaunchDecision(
                kind="exit_with_launch",
                active_pipelines=active_pipelines,
                git_commit_performed=True,
                push_outcome_message=msg,
            )
        if conflict_path:
            print(f"[claude-session] Push diagnostic: {conflict_path}", file=sys.stderr)
        return LaunchDecision(
            kind="exit_refused_dirty_state",
            active_pipelines=active_pipelines,
            push_outcome_message=msg,
            push_conflict_path=conflict_path,
        )
    if cmd == "q":
        print("[claude-session] Launch aborted. Resolve uncommitted changes on main yourself.",
              file=sys.stderr)
        return LaunchDecision(kind="exit_refused_dirty_state", active_pipelines=active_pipelines)
    if cmd == "n":
        return LaunchDecision(kind="exit_abort")
    print("[claude-session] Unknown choice; refusing launch.", file=sys.stderr)
    return LaunchDecision(kind="exit_refused_dirty_state", active_pipelines=active_pipelines)


# ---------------------------------------------------------------------------
# Curses dispatcher
# ---------------------------------------------------------------------------


def _run_curses(
    main_root: pathlib.Path,
    pipeline_registry: set,
    pipeline_defaults: dict,
    pipeline_summaries: dict,
    accept_dirty: bool = False,
    worktree_session_id: str = "",
) -> LaunchDecision:
    """Run the curses event loop.  Returns LaunchDecision on any exit path."""
    import curses

    # Determine registry-empty hint for Shell
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import session_registry as _sr  # type: ignore[import]
        _reg = _sr.read_registry(main_root)
        registry_empty = len(_reg) == 0
    except Exception:  # noqa: BLE001
        registry_empty = False

    pipeline_select_factory = lambda: PipelineSelect(  # noqa: E731
        main_root, pipeline_registry, pipeline_defaults, pipeline_summaries,
        accept_dirty=accept_dirty, worktree_session_id=worktree_session_id,
    )
    worktree_manage_factory = lambda: WorktreeManage(main_root)  # noqa: E731
    settings_factory = lambda: SettingsInspector(main_root)  # noqa: E731

    # Sentinel: GitPreLaunch resolved to clean-skip; REPLACE handler exits directly.
    _CLEAN_LAUNCH = object()

    def _resolve_screen(s):
        """Expand sentinel objects to real Screen instances.

        For _GitPreLaunchPlaceholder: if main is clean, returns _CLEAN_LAUNCH
        sentinel so the REPLACE handler exits with launch without a keypress.
        """
        if s is _PUSH_PIPELINE_SELECT:
            return pipeline_select_factory()
        if s is _PUSH_WORKTREE_MANAGE:
            return worktree_manage_factory()
        if s is _PUSH_SETTINGS:
            return settings_factory()
        if s is _PUSH_ISSUE_QUEUE:
            return IssueQueue(main_root)
        if isinstance(s, _GitPreLaunchPlaceholder):
            is_dirty, _ = _main_git_is_dirty(s.main_root)
            if not is_dirty:
                # Clean repo: skip GitPreLaunch screen entirely; no keypress needed.
                return _CLEAN_LAUNCH, LaunchDecision(
                    kind="exit_with_launch",
                    active_pipelines=s.active,
                )
            return GitPreLaunch(
                s.main_root, s.active,
                accept_dirty=s.accept_dirty,
                worktree_session_id=s.worktree_session_id,
            )
        return s

    def _main(stdscr):
        curses.cbreak()
        curses.noecho()
        stdscr.keypad(True)
        # 1-second getch timeout: enables mtime polling for IssueQueue without
        # a separate thread. getch returns -1 on timeout (no keypress).
        stdscr.timeout(1000)

        landing = Shell(
            main_root,
            registry_empty=registry_empty,
            pipeline_defaults=pipeline_defaults,
            accept_dirty=accept_dirty,
            worktree_session_id=worktree_session_id,
        )
        stack: list = [landing]

        while stack:
            top = stack[-1]
            top.render(stdscr)
            key = stdscr.getch()

            # Timeout tick: no keypress in 1s — check for mtime changes.
            if key == -1:
                if isinstance(top, IssueQueue):
                    top._check_mtime()
                continue

            transition = top.handle_key(key, stdscr=stdscr)

            if transition.kind == TransitionKind.NONE:
                continue
            if transition.kind == TransitionKind.PUSH:
                resolved = _resolve_screen(transition.payload)
                if isinstance(resolved, tuple) and resolved[0] is _CLEAN_LAUNCH:
                    return resolved[1]
                screen = resolved
                screen.on_enter()
                stack.append(screen)
                continue
            if transition.kind == TransitionKind.POP:
                stack.pop()
                continue
            if transition.kind == TransitionKind.REPLACE:
                resolved = _resolve_screen(transition.payload)
                # Clean-launch sentinel: skip GitPreLaunch, exit immediately.
                if isinstance(resolved, tuple) and resolved[0] is _CLEAN_LAUNCH:
                    return resolved[1]
                screen = resolved
                screen.on_enter()
                stack[-1] = screen
                continue
            if transition.kind == TransitionKind.EXIT_WITH_LAUNCH:
                payload = transition.payload
                if isinstance(payload, LaunchDecision):
                    return payload
                # WorktreeManage returns a LaunchDecision directly in payload
                return LaunchDecision(kind="exit_with_launch")
            if transition.kind == TransitionKind.EXIT_ABORT:
                # NOTE: TransitionKind.EXIT_ABORT covers BOTH user-abort and
                # refused-dirty-state. Discriminate via LaunchDecision.kind ==
                # 'exit_refused_dirty_state' before adding per-TransitionKind logic.
                # See WR-PHASE2-PREVENT axis 2.
                payload = transition.payload
                if isinstance(payload, LaunchDecision):
                    return payload
                return LaunchDecision(kind="exit_abort")

        # Stack exhausted without explicit exit → abort
        return LaunchDecision(kind="exit_abort")

    result = curses.wrapper(_main)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_tui(
    main_root: pathlib.Path,
    pipeline_registry: set,
    pipeline_defaults: dict,
    pipeline_summaries: dict,
    accept_dirty: bool = False,
    worktree_session_id: str = "",
) -> LaunchDecision:
    """Launch the interactive TUI and return the user's launch decision.

    Args:
        main_root: Path to the main repo root (not a worktree).
        pipeline_registry: set of pipeline names from bootstrap-config.json.
        pipeline_defaults: dict[name, bool] default-enabled state.
        pipeline_summaries: dict[name, str] human-readable summary per pipeline.
        accept_dirty: If True, bypass the H2 foreign-session confirmation gate.
        worktree_session_id: Pre-generated session ID for this launch (used in
            Session-Id: trailer and H2 attribution). Empty string = unknown.

    Returns:
        LaunchDecision with kind 'exit_with_launch', 'exit_abort', or
        'exit_refused_dirty_state'.
        Caller (bin/claude-session) translates into session-spawn variables.
    """
    if not _can_use_curses():
        decision = _run_line_mode_fallback(
            main_root, pipeline_registry, pipeline_defaults, pipeline_summaries,
            accept_dirty=accept_dirty, worktree_session_id=worktree_session_id,
        )
    else:
        decision = _run_curses(
            main_root, pipeline_registry, pipeline_defaults, pipeline_summaries,
            accept_dirty=accept_dirty, worktree_session_id=worktree_session_id,
        )

    decision.equivalent_cli = _build_equivalent_cli(decision, pipeline_defaults)

    if decision.kind == "exit_with_launch":
        print(
            f"[claude-session] TUI choice -> equivalent CLI: {decision.equivalent_cli}",
            file=sys.stderr,
        )
        if decision.push_outcome_message:
            print(f"[claude-session] {decision.push_outcome_message}", file=sys.stderr)
    elif decision.kind == "exit_refused_dirty_state":
        print("[claude-session] Launch refused: dirty main + push not completed.",
              file=sys.stderr)
        if decision.push_outcome_message:
            print(f"[claude-session]   {decision.push_outcome_message}", file=sys.stderr)
        if decision.push_conflict_path:
            print(f"[claude-session]   Diagnostic: {decision.push_conflict_path}",
                  file=sys.stderr)

    return decision
