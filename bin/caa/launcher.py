"""launcher.py — Shared multi-episode launch loop for CAA sessions.

Owns: render-targets loop (orchestrator-prompt + agent files), pipeline MCP
composition, env hygiene (CAA_WORKTREE_ROOT, CAA_CAMPAIGN_ID, EPISODE-strip),
pre-warm, the episode loop (cycle-checkpoint detection → next episode), JSONL
transcript watcher dispatch, keep-or-remove prompt, handoff check, and registry
update calls.

Family-agnostic — calls into the family arm (FamilyArm Protocol) for the
spawn step. The family arm assembles the CLI argv, configures the env, runs
pre-spawn hooks, and returns a TokenMeasurementConfig.

Public entry point:

    run_session(
        family_descriptor, session_dir, task_body, pipeline_flags,
        cli_options, worktree_path, main_root,
    ) -> int

S16: Claude arm only. S17: codex arm. S18: gemini arm.
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

# Ensure bin/ is on sys.path so imports of session_registry, orchestrator_prompt_render,
# planner_synth, pipeline_manifest, _campaign_resolve work from within the caa/ package.
_BIN_DIR = pathlib.Path(__file__).parent.parent
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from caa.family_descriptor import FamilyDescriptor  # noqa: E402
from caa_active_children import _build_active_children_block  # noqa: E402


# ── Dataclasses for typed inputs ──────────────────────────────────────────────

@dataclass
class PipelineFlags:
    """Resolved pipeline state passed from the CLI entry point to run_session."""

    active_pipelines: set
    pipeline_registry: set
    pipeline_manifests: dict
    registry_list: list  # preserves bootstrap-config insertion order (for planner_synth)
    fail_open: bool = False


@dataclass
class CliOptions:
    """Parsed CLI options forwarded from bin/caa-session."""

    model: str = "sonnet"
    effort: str = "default"
    poll_interval: float = 1.0
    max_episodes: int = 20
    unattended: bool = False
    keepalive_interval: int = 30
    threshold: int = 150000
    keep_worktree: bool = False
    remove_worktree: bool = False
    campaign: str | None = None
    child_sidecar_dir: str | None = None
    child_id: str | None = None
    smoke_only: bool = False
    no_mcp_gate: bool = False
    is_resume: bool = False


# ── TokenMeasurementConfig (returned by family arm) ───────────────────────────

@dataclass
class TokenMeasurementConfig:
    """Tells the shared watcher thread which token source to poll."""

    mechanism: str              # "transcript_jsonl_poll" | "rollout_jsonl_token_count_events" | "stream_json_token_events"
    source_path_template: str   # may contain ${SESSION_DIR}, ${HOME}, etc.
    polling_interval_ms: int = 1000


# ── FamilyArm Protocol ────────────────────────────────────────────────────────

class FamilyArm(Protocol):
    """Protocol each per-family arm module must implement."""

    def prepare_env(
        self,
        descriptor: FamilyDescriptor,
        base_env: dict,
        session_id: str,
        worktree_path: pathlib.Path,
    ) -> dict: ...

    def build_argv(
        self,
        descriptor: FamilyDescriptor,
        rendered_prompt_path: str,
        episode_prompt: str | None,
        mcp_config_path: pathlib.Path,
        cli_options: CliOptions,
    ) -> list[str]: ...

    def pre_spawn_hook(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> None: ...

    def configure_token_watcher(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> TokenMeasurementConfig: ...

    def probe_capabilities(
        self,
        required_capabilities: list,
    ) -> dict: ...


# ── Internal helpers (imported from claude-session via SourceFileLoader) ──────

def _load_claude_session_module():
    """Load bin/claude-session as a Python module (SourceFileLoader, no .py suffix)."""
    cs_path = str(_BIN_DIR / "claude-session")
    loader = importlib.machinery.SourceFileLoader("_caa_claude_session", cs_path)
    spec = importlib.util.spec_from_loader("_caa_claude_session", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _set_pdeathsig():
    """Linux-only preexec_fn: prctl(PR_SET_PDEATHSIG, SIGTERM) so the kernel signals
    the watchdog when the parent (this launcher) dies via SIGKILL.  Best-effort —
    silent no-op on any ctypes/libc failure.
    """
    # PR_SET_PDEATHSIG=1, SIGTERM=15 — first instance of this pattern in bin/; ctypes
    # unavailability on non-glibc systems degrades silently rather than breaking the spawn.
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, 15, 0, 0, 0)
    except Exception:
        pass


def _spawn_suborch_watchdog(session_dir, main_root):
    """Spawn bin/suborch-watchdog.py as a background subprocess targeting the L1 parent.

    Caller (caa-session._dispatch_*) is responsible for registering atexit cleanup
    and signal handlers — per the convention comment inside run_session
    ("Signal and atexit setup are owned by the calling claude-session / caa-session"),
    this helper does NOT install any.

    Gated on CAA_CHILD_SIDECAR_DIR — when absent, returns None silently (this is an
    L1 launcher, not an L2 child, so no watchdog is needed).

    Returns the Popen object on success, or None on env-var absence or spawn failure
    (failure is logged as WARNING; never raises).
    """
    sidecar_env = os.environ.get("CAA_CHILD_SIDECAR_DIR")
    if not sidecar_env:
        return None  # not an L2 child — gate closed
    try:
        # CAA_CHILD_SIDECAR_DIR = {parent_session_dir}/children/{child_id}/
        # → .parent.parent gives {parent_session_dir}
        parent_session_dir = pathlib.Path(sidecar_env).resolve().parent.parent
    except Exception as e:
        print(f"[caa-session] WARNING: suborch-watchdog sidecar resolution failed: {e}",
              file=sys.stderr)
        return None
    # Sidecar files live in session_dir (per run_session's "state_dir = session_dir" derivation).
    state_dir = pathlib.Path(session_dir)
    log_path = state_dir / "suborch-watchdog.log"
    argv = [sys.executable, str(main_root / "bin" / "suborch-watchdog.py"),
            "--session-dir", str(parent_session_dir)]
    try:
        log_fh = open(log_path, "a", buffering=1)  # line-buffered append; inherited by watchdog
        proc = subprocess.Popen(
            argv,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            preexec_fn=_set_pdeathsig if sys.platform.startswith("linux") else None,
            close_fds=True,
        )
        (state_dir / "suborch-watchdog.pid").write_text(str(proc.pid))
        return proc
    except Exception as e:
        print(f"[caa-session] WARNING: suborch-watchdog spawn failed: {e}", file=sys.stderr)
        return None


def _register_launcher_session_id(session_id: str, child_sidecar_dir: str | None) -> None:
    """Write launcher_session_id into the children-registry row for this L2 child.

    Called immediately after monitor.pid is written.  Gated on child_sidecar_dir
    (set from CAA_CHILD_SIDECAR_DIR) — when absent (L1 launcher), returns silently.

    register_child (bin/parent_messages_register_child.py) writes the initial row
    before the launcher starts; this post-hoc enrichment adds the launcher's own
    session_id so bin/suborch-reap.py:step3_teardown_monitor can locate monitor.pid
    at teardown time via the L2 dispatch path.

    Uses the same flock-on-.children-registry.lock + atomic-rename discipline as
    parent_messages_register_child._atomic_write_registry to guard against races
    with other registry writers.

    Best-effort: all exceptions are caught and logged as WARNING; never raises.
    """
    if not child_sidecar_dir:
        return  # L1 launcher — not an L2 child; gate closed
    try:
        sidecar_path = pathlib.Path(child_sidecar_dir).resolve()
        # sidecar_path = {parent_session_dir}/children/{child_id}
        child_id = sidecar_path.name
        parent_session_dir = sidecar_path.parent.parent
    except Exception as e:
        print(
            f"[caa-session] WARNING: launcher_session_id update: path resolution failed: {e}",
            file=sys.stderr,
        )
        return
    registry_path = parent_session_dir / "children-registry.json"
    lock_path = parent_session_dir / ".children-registry.lock"
    try:
        lock_fd = open(str(lock_path), "a")  # noqa: SIM115 — explicit close in finally
    except OSError as e:
        print(
            f"[caa-session] WARNING: launcher_session_id update: lock open failed: {e}",
            file=sys.stderr,
        )
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            registry = {}
        except (json.JSONDecodeError, OSError) as e:
            print(
                f"[caa-session] WARNING: launcher_session_id update: registry read failed: {e}",
                file=sys.stderr,
            )
            return
        if child_id not in registry:
            print(
                f"[caa-session] WARNING: launcher_session_id update: row not found for "
                f"{child_id!r} in {registry_path}; skipping.",
                file=sys.stderr,
            )
            return
        registry[child_id]["launcher_session_id"] = session_id
        try:
            tmp_path = registry_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
            os.rename(str(tmp_path), str(registry_path))
        except OSError as e:
            print(
                f"[caa-session] WARNING: launcher_session_id update: registry write failed: {e}",
                file=sys.stderr,
            )
    except Exception as e:
        print(
            f"[caa-session] WARNING: launcher_session_id update: unexpected error: {e}",
            file=sys.stderr,
        )
        return
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


# ── Public entry point ────────────────────────────────────────────────────────

def run_session(
    family_descriptor: FamilyDescriptor,
    session_dir: str,
    task_body: str,
    pipeline_flags: PipelineFlags,
    cli_options: CliOptions,
    worktree_path: pathlib.Path,
    main_root: pathlib.Path,
    codex_raw_dir: str | None = None,
) -> int:
    """Run the multi-episode launch loop for one session under the named family.

    Returns the final exit code after keep-or-remove + handoff handling.

    Side effects (per design):
      - Writes rendered prompt to <session_dir>/orchestrator-prompt.rendered.md.
      - Writes worktree-local MCP delta to <worktree>/.mcp.json (claude) or invokes
        family install script (codex/gemini).
      - Writes runbook to <session_dir>/cycle-resume-runbook.md for episode 2+.
      - Writes registry updates via session_registry.update_record.

    codex_raw_dir: when set AND family=codex, each episode's subprocess stdout is
      captured to {codex_raw_dir}/codex-raw-ep{N}.jsonl (--json is injected into the
      codex argv automatically).  Caller (_dispatch_codex) reads these after return for
      axis-11 Branch A transcript coercion.
    """
    # Resolve the family arm module (dispatch by family name).
    arm = _get_family_arm(family_descriptor.family)

    # Import shared helpers from the ecosystem (session_registry, _campaign_resolve,
    # orchestrator_prompt_render, planner_synth). These are already factored modules
    # per the S16 integration-points list.
    import session_registry
    import orchestrator_prompt_render
    import planner_synth as _planner_synth
    from _campaign_resolve import _resolve_campaign_id_for_episode  # noqa: F401

    # Derive session_id from session_dir (format: .../sessions/<session_id>)
    session_id = os.path.basename(session_dir)

    # ── State directory ───────────────────────────────────────────────────────
    state_dir = session_dir
    os.makedirs(os.path.join(state_dir, "findings"), exist_ok=True)

    # ── Sessions root (for stale-session cleanup calls) ───────────────────────
    sessions_root = str(pathlib.Path(state_dir).parent)

    # ── Pipeline variables ────────────────────────────────────────────────────
    active_pipelines = pipeline_flags.active_pipelines
    pipeline_registry = pipeline_flags.pipeline_registry
    pipeline_manifests = pipeline_flags.pipeline_manifests
    registry_list = pipeline_flags.registry_list
    fail_open = pipeline_flags.fail_open

    # ── Config from cli_options ───────────────────────────────────────────────
    model = cli_options.model
    effort = cli_options.effort
    poll_interval = cli_options.poll_interval
    max_episodes = cli_options.max_episodes
    unattended = cli_options.unattended
    keepalive_interval = cli_options.keepalive_interval
    keep_worktree_flag = cli_options.keep_worktree
    remove_worktree_flag = cli_options.remove_worktree
    cli_campaign = cli_options.campaign
    child_sidecar_dir = cli_options.child_sidecar_dir
    child_id = cli_options.child_id

    initial_prompt = task_body

    # ── pipelines.json side-channel ───────────────────────────────────────────
    pipelines_path = os.path.join(state_dir, "pipelines.json")
    pipelines_payload = {
        "active_pipelines": sorted(active_pipelines),
        "registry": list(registry_list),
    }
    try:
        _tmp = pipelines_path + ".tmp"
        with open(_tmp, "w") as _f:
            json.dump(pipelines_payload, _f)
        os.replace(_tmp, pipelines_path)
    except (IOError, OSError) as _e:
        print(f"[caa-session] WARNING: pipelines.json write failed: {_e}", file=sys.stderr)

    # ── monitor.pid ───────────────────────────────────────────────────────────
    monitor_pid_path = os.path.join(state_dir, "monitor.pid")
    try:
        with open(monitor_pid_path, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"[caa-session] WARNING: monitor.pid write failed: {e}", file=sys.stderr)

    # ── L2: register launcher_session_id in children-registry row ────────────
    # Enables bin/suborch-reap.py:step3_teardown_monitor to locate monitor.pid
    # at the launcher's session dir path rather than the sidecar placeholder.
    # Gated on child_sidecar_dir — no-op for L1 launchers.
    _register_launcher_session_id(session_id, child_sidecar_dir)

    # ── Load claude-session internals (helpers used below) ────────────────────
    # We load the module to reuse _file_watcher, _log, _get_checkpoint_files,
    # _get_most_recent_checkpoint, _check_worktree_staleness, _prompt_keep_or_remove,
    # _check_next_task_handoff, _HandoffOutcome, _archive_and_delete_sidecar,
    # _confirm_relaunch, cleanup, _remove_worktree.
    # This avoids duplicating ~1000 LOC of supporting infrastructure in S16.
    # S17/S18 can refactor further if needed.
    cs = _load_claude_session_module()

    def _log(msg: str) -> None:
        cs._log(state_dir, msg)

    # ── Signal and atexit setup are owned by the calling claude-session / caa-session.
    # run_session does NOT register signals — caller owns the process lifecycle.

    # ── Inactive manifests (for worktree staleness check) ─────────────────────
    inactive_manifests = {
        k: v for k, v in pipeline_manifests.items() if k not in active_pipelines
    }

    # ── Episode loop ──────────────────────────────────────────────────────────
    episode = 0
    _interrupted = False
    _crashed = False

    try:
        while episode < max_episodes:
            episode += 1

            if episode > 5:
                print(f"[caa-session] WARNING: High episode count ({episode})", file=sys.stderr)
                _log(f"WARNING: High episode count ({episode})")

            near_cap_warning = ""
            if episode >= max_episodes - 2:
                remaining = max_episodes - episode
                near_cap_warning = (
                    f"WARNING: Episode {episode} of {max_episodes} -- "
                    f"{remaining} episode(s) remaining before hard cap. "
                    f"Consider wrapping up remaining work or raising max_episodes in .claude/session-cycling.json."
                )
                print(f"[caa-session] {near_cap_warning}", file=sys.stderr)
                _log(near_cap_warning)

            # Prepare state (C10)
            cycle_state_path = os.path.join(state_dir, "cycle.state")
            try:
                with open(cycle_state_path, "w") as f:
                    json.dump({"episode": episode, "pid": None}, f)
                    f.write("\n")
            except Exception as e:
                _log(f"WARNING: cycle.state write failed: {e}")
                print(f"[caa-session] WARNING: cycle.state write failed: {e}", file=sys.stderr)

            # Delete stale sentinels (C10)
            for stale_file in (
                "cycle-pending", "cycle-suppress", "cycling-active", "loop-active",
                "loop-terminated", "transcript.path", "claude-code-uuid",
                "fd-scan-logged-episode", "audit-proactive-active",
                "audit-counter-campaign-suspend",
            ):
                stale_path = os.path.join(state_dir, stale_file)
                try:
                    os.unlink(stale_path)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass

            # ── Render compose-time-layered prompt/agent files ─────────────────
            _rendered_op_path = os.path.join(state_dir, "orchestrator-prompt.rendered.md")
            prompt_file_arg = str(
                worktree_path / ".claude" / "orchestrator-prompt.md"
            )  # fallback

            _render_targets = [
                ("orchestrator-prompt",
                 str(worktree_path / ".claude" / "orchestrator-prompt.md"),
                 _rendered_op_path),
                ("planner-agent",
                 str(worktree_path / ".claude" / "agents" / "planner.md"),
                 str(worktree_path / ".claude" / "agents" / "planner.md")),
                ("solution-designer-agent",
                 str(worktree_path / ".claude" / "agents" / "solution-designer.md"),
                 str(worktree_path / ".claude" / "agents" / "solution-designer.md")),
                ("implementer-agent",
                 str(worktree_path / ".claude" / "agents" / "implementer.md"),
                 str(worktree_path / ".claude" / "agents" / "implementer.md")),
                ("validator-agent",
                 str(worktree_path / ".claude" / "agents" / "validator.md"),
                 str(worktree_path / ".claude" / "agents" / "validator.md")),
                ("pre-flight-gate-agent",
                 str(worktree_path / ".claude" / "agents" / "pre-flight-gate.md"),
                 str(worktree_path / ".claude" / "agents" / "pre-flight-gate.md")),
                # 7th target: dispatch-l2/SKILL.md rendered in-place per axis-7 (CL-3)
                # so the child session sees only its scope flag + family addenda.
                ("dispatch-l2-skill",
                 str(worktree_path / ".claude" / "skills" / "dispatch-l2" / "SKILL.md"),
                 str(worktree_path / ".claude" / "skills" / "dispatch-l2" / "SKILL.md")),
            ]

            # --- R7: Explicit Entry-0 orchestrator-prompt handler ---
            # Entry 0 writes to a sidecar under state_dir (NOT in-place) and
            # uses no baseline cache; it keeps the existing file-to-file call shape.
            try:
                from orchestrator_prompt_render import render as _opr_render
                _opr_render(
                    template_path=_render_targets[0][1],
                    active_flags=active_pipelines,
                    registry=pipeline_registry,
                    output_path=_render_targets[0][2],
                )
                prompt_file_arg = _render_targets[0][2]
            except Exception as e:
                print(
                    f"[caa-session] WARNING: render failed for {_render_targets[0][0]} ({e}); "
                    f"leaving {_render_targets[0][1]} as raw",
                    file=sys.stderr,
                )
                _log(f"render failed for {_render_targets[0][0]}: {e}")

            # --- R7: entries 1..6 in-place render loop (HEAD-anchored + baseline provenance) ---
            # Entry 0 (orchestrator-prompt sidecar) is handled by the block above.
            from orchestrator_prompt_render import render_string as _render_string
            from render_divergence_check import is_clean_render as _is_clean_render
            _BASELINE_ROOT = worktree_path / ".agent_context" / "render-baselines"

            for _label, _src, _dst in _render_targets[1:]:
                _rel_path = str(pathlib.Path(_src).relative_to(worktree_path))
                _baseline_path = _BASELINE_ROOT / (_rel_path + ".json")
                try:
                    # --- Step 1: read TEMPLATE from HEAD blob (decouple from working-tree) ---
                    try:
                        _template_bytes = subprocess.check_output(
                            ['git', '-C', str(worktree_path), 'show', f'HEAD:{_rel_path}'],
                            stderr=subprocess.PIPE,
                        )
                        _template_text = _template_bytes.decode('utf-8')
                    except subprocess.CalledProcessError:
                        # New render target not yet in HEAD. Do NOT fall back to working-tree.
                        _log(
                            f"render: HEAD lookup missed for {_rel_path}; skipping render "
                            f"(preserves uncommitted working-tree)."
                        )
                        continue

                    # --- Step 2: read working-tree (content + stat) ---
                    # If the file is ABSENT from the working tree (e.g. author intentionally
                    # deleted it as a pure-deletion edit, F10), working_bytes=b"" and
                    # working_stat=None. The predicate Case 1 (W == H byte-for-byte) will
                    # be FALSE because HEAD has non-empty content; Case 2 short-circuits on
                    # working_stat is None → FALSE. The preservation branch fires. The
                    # launcher does NOT resurrect the deleted file by rendering. HK-3
                    # downstream stages the deletion as `D <path>` via `git add -f`.
                    try:
                        with open(_src, 'rb') as _fh:
                            _working_bytes = _fh.read()
                        _working_stat = os.stat(_src)
                    except FileNotFoundError:
                        _working_bytes = b""
                        _working_stat = None

                    # --- Step 3: read baseline JSON if present ---
                    _baseline = None
                    if _baseline_path.exists():
                        try:
                            with open(_baseline_path, 'r', encoding='utf-8') as _fh:
                                _baseline = json.load(_fh)
                        except (json.JSONDecodeError, OSError) as _e:
                            _log(f"render: baseline {_baseline_path} unreadable ({_e}); treating as missing")
                            _baseline = None

                    # --- Step 4: R6 smart-render-skip predicate (unguarded invocation) ---
                    # R6 FIX (Finding #1): no `working_text is not None` guard. The predicate
                    # itself correctly resolves the absent-file case to FALSE (Case 1 fails
                    # because working_bytes=b"" != head_bytes; Case 2 fails on stat=None).
                    # Any author edit that diverges from launcher provenance — including
                    # the pure-deletion case (F10) — triggers PRESERVE.
                    if not _is_clean_render(
                        _working_bytes, _working_stat, _template_text, _baseline
                    ):
                        _log(
                            f"render: smart-render-skip — {_rel_path} diverges from HEAD AND "
                            f"is not launcher-provenance-clean; preserving working-tree. "
                            f"Stage via HK-3 (`git add -f {_rel_path}`) before cycle close."
                        )
                        continue

                    # --- Step 5: working-tree is clean (Case 1 or Case 2); safe write ---
                    _expected_rendered = _render_string(
                        template_text=_template_text,
                        active_flags=active_pipelines,
                        registry=pipeline_registry,
                    )
                    _expected_bytes = _expected_rendered.encode('utf-8')
                    with open(_dst, 'wb') as _fh:
                        _fh.write(_expected_bytes)
                    # Re-stat AFTER write to capture the new mtime_ns and size.
                    _post_stat = os.stat(_dst)
                    _baseline_path.parent.mkdir(parents=True, exist_ok=True)
                    _new_baseline = {
                        "rel_path": _rel_path,
                        "canonical_sha256": hashlib.sha256(_expected_bytes).hexdigest(),
                        "canonical_mtime_ns": _post_stat.st_mtime_ns,
                        "canonical_size": _post_stat.st_size,
                        "active_flags": sorted(active_pipelines),
                        "registry_snapshot": sorted(pipeline_registry),
                        "head_template_sha256": hashlib.sha256(
                            _template_text.encode('utf-8')
                        ).hexdigest(),
                        "written_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "writer_session_id": session_id,
                        "schema_version": 1,
                    }
                    # Write baseline AFTER canonical via temp-file + atomic rename. Fail-safe:
                    # if baseline write fails, next run sees stale baseline (mtime mismatch)
                    # → preserves → no destruction.
                    _tmp_baseline = _baseline_path.with_suffix(_baseline_path.suffix + ".tmp")
                    with open(_tmp_baseline, 'w', encoding='utf-8') as _fh:
                        json.dump(_new_baseline, _fh, indent=2, sort_keys=True)
                    os.replace(_tmp_baseline, _baseline_path)  # atomic on POSIX

                except Exception as e:
                    print(
                        f"[caa-session] WARNING: render failed for {_label} ({e}); "
                        f"leaving {_src} as raw",
                        file=sys.stderr,
                    )
                    _log(f"render failed for {_label}: {e}")

            # ── Protect in-place render targets from git add -A staging ─────────
            # Mirrors pipeline_prune's skip-worktree protection on pruned paths.
            # Entry 0 (_render_targets[0]) renders to state_dir sidecar and must
            # NOT receive the bit; entries 1-6 render in-place and need it.
            from pipeline_prune import _set_skip_worktree_bits  # noqa: E402
            _inplace_render_rel = [
                str(pathlib.Path(_dst).relative_to(worktree_path))
                for _, _src, _dst in _render_targets[1:]
            ]
            _set_skip_worktree_bits(worktree_path, _inplace_render_rel, _log)

            # ── Compose and append Active Pipelines section to planner.md ──────
            _worktree_planner_path = worktree_path / ".claude" / "agents" / "planner.md"
            _ordered_active = [n for n in registry_list if n in active_pipelines]
            try:
                _n_bytes, _k_bytes = _planner_synth.compose_and_append_planner(
                    active_pipelines=_ordered_active,
                    manifests=pipeline_manifests,
                    worktree_planner_path=_worktree_planner_path,
                    fail_open=fail_open,
                    logger=lambda m: _log(m),
                )
                _log(f"[caa-session] composed worktree planner.md size: {_n_bytes} bytes (append size: {_k_bytes} bytes)")
            except _planner_synth.PlannerSynthError as e:
                if fail_open:
                    print(f"[caa-session] WARNING: planner synthesis degraded (fail_open=true): {e}", file=sys.stderr)
                    _log(f"planner synthesis degraded (fail_open=true): {e}")
                else:
                    print(f"[caa-session] ERROR: planner synthesis failed: {e}", file=sys.stderr)
                    _log(f"planner synthesis failed: {e}")
                    return 2

            # ── Pipeline MCP composition (claude: worktree-local .mcp.json) ────
            # The family arm's build_argv() receives _mcp_config_path.
            # For claude: compose pipeline mcp_servers into worktree .mcp.json.
            # For codex/gemini: family arm handles MCP registration via install scripts.
            _pipeline_mcp: dict = {}
            for _pm in pipeline_manifests.values():
                if _pm.name in active_pipelines:
                    for _srv in _pm.mcp_servers:
                        _srv_path = main_root / ".claude" / "mcp" / _srv
                        _pipeline_mcp[_srv] = {
                            "command": "bash",
                            "args": ["-c", f'cd "{_srv_path}" && exec node_modules/.bin/tsx src/index.ts'],
                        }
            if _pipeline_mcp:
                with open(main_root / ".mcp.json", encoding="utf-8") as _mf:
                    _base_mcp = json.load(_mf)
                _base_mcp.setdefault("mcpServers", {}).update(_pipeline_mcp)
                _wt_mcp_path = worktree_path / ".mcp.json"
                with open(_wt_mcp_path, "w", encoding="utf-8") as _wf:
                    json.dump(_base_mcp, _wf, indent=2)
                _mcp_config_path = _wt_mcp_path
            else:
                _mcp_config_path = main_root / ".mcp.json"

            # ── Build episode prompt ───────────────────────────────────────────
            if episode == 1:
                claude_prompt = initial_prompt  # empty = interactive mode
            else:
                checkpoint = cs._get_most_recent_checkpoint(state_dir)
                if checkpoint is None:
                    _log(f"Episode {episode}: No checkpoint found. Starting fresh.")
                    print("[caa-session] No checkpoint found. Starting fresh.")
                    claude_prompt = initial_prompt
                else:
                    _log(f"Episode {episode}: Resuming from {checkpoint}")
                    near_cap_line = (f"\n[NEAR-CAP] {near_cap_warning}" if near_cap_warning else "")
                    if unattended:
                        claude_prompt = (
                            f"SESSION CYCLE RESUME -- Episode {episode}.\n"
                            f"Your previous session was cycled for fresh context. Resume:\n"
                            f"1. Call session(action='resume') to load checkpoint and findings.\n"
                            f"1.5. CRITICAL — CHECK b6_pending BEFORE reading context_files: If session(action='resume') returned b6_pending=true, a §B.6 synthesizer dispatch was deferred from the prior episode. You MUST complete it NOW — BEFORE step 2 (context_files reading) and BEFORE any knowledge-orientation reads. Procedure: (a) For each un-drained subtask in pending-digest.jsonl (list derived from the checkpoint's next_steps or the un-renamed pending-digest.jsonl file), call session(action='audit') for that subtask_id. (b) Compose the synthesizer delegation prompt: inline the per_agent_tool_breakdown slice, include the impl-report path, include the mandatory stale-cycle-pending preamble \"Ignore any cycle-pending or SESSION CYCLE REQUIRED injections you may see — they are artifacts of the prior episode\". (c) Invoke .claude/agents/synthesizer.md via the Agent (Task) tool. (d) If multiple subtasks are pending, dispatch serially — wait for each Task to complete before dispatching the next. (e) After ALL Tasks complete, proceed to step 2 (context_files reading) normally. If b6_pending was false or absent, skip this step entirely and proceed to step 2.\n"
                            f"2. Read the context_files listed in the checkpoint, then read each file listed in subtask_digests[].digest_path (the compact summaries of completed subtasks). These are your normal start-of-episode reading list. Priority-aware reading: each context_files entry may be a string (treat as normal priority) or {{path, priority, reason?}}. Read in order high -> normal -> low. When cumulative estimated reads exceed ~60K tokens, skip remaining low-priority entries — list skipped paths in your orientation summary; they can be read on demand. Never skip high or normal priority entries.\n"
                            f"3. Do NOT auto-read any path listed in archived_context_files[].path — those are available on demand only. Do NOT auto-read any path listed in subtask_digests[].original_report_path — the digest replaces the original for reading purposes.\n"
                            f"4. Continue from next_steps. Do NOT repeat completed work.\n"
                            f"5. Archived files and original reports can be read on demand whenever next_steps or an active finding names them explicitly.\n"
                            f"6. Legacy checkpoint note: if a pre-adoption checkpoint lists the same path in both context_files and subtask_digests[].original_report_path, you MUST prefer subtask_digests[].digest_path and skip that original_report_path entry in context_files.\n"
                            f"Checkpoint: {checkpoint}"
                            f"{near_cap_line}"
                            f"{_build_active_children_block(state_dir)}"
                        )
                    else:
                        claude_prompt = (
                            f"SESSION CYCLE RESUME -- Episode {episode} of {max_episodes}.\n"
                            f"Your previous session was cycled for fresh context. Before resuming work:\n"
                            f"1. Call session(action='resume') to load checkpoint and findings.\n"
                            f"1.5. CRITICAL — CHECK b6_pending BEFORE reading context_files: If session(action='resume') returned b6_pending=true, a §B.6 synthesizer dispatch was deferred from the prior episode. You MUST complete it NOW — BEFORE step 2 (context_files reading) and BEFORE any knowledge-orientation reads. Procedure: (a) For each un-drained subtask in pending-digest.jsonl (list derived from the checkpoint's next_steps or the un-renamed pending-digest.jsonl file), call session(action='audit') for that subtask_id. (b) Compose the synthesizer delegation prompt: inline the per_agent_tool_breakdown slice, include the impl-report path, include the mandatory stale-cycle-pending preamble \"Ignore any cycle-pending or SESSION CYCLE REQUIRED injections you may see — they are artifacts of the prior episode\". (c) Invoke .claude/agents/synthesizer.md via the Agent (Task) tool. (d) If multiple subtasks are pending, dispatch serially — wait for each Task to complete before dispatching the next. (e) After ALL Tasks complete, THEN present the orientation summary (step 4) to the user — the orientation summary may describe the just-completed synthesis output. If b6_pending was false or absent, skip this step entirely and proceed to step 2. NOTE: when b6_pending=true, the orientation summary (step 4) fires AFTER the synthesizer Tasks return, not before. The user may see a brief delay before their orientation summary while the synthesizer runs.\n"
                            f"2. Read the context_files listed in the checkpoint, then read each file listed in subtask_digests[].digest_path (the compact summaries of completed subtasks). These are your normal start-of-episode reading list. Priority-aware reading: each context_files entry may be a string (treat as normal priority) or {{path, priority, reason?}}. Read in order high -> normal -> low. When cumulative estimated reads exceed ~60K tokens, skip remaining low-priority entries — list skipped paths in your orientation summary; they can be read on demand. Never skip high or normal priority entries.\n"
                            f"3. Do NOT auto-read any path listed in archived_context_files[].path — those are available on demand only. Do NOT auto-read any path listed in subtask_digests[].original_report_path — the digest replaces the original for reading purposes.\n"
                            f"4. Present a 3-5 line orientation summary to the user containing: "
                            f"current task name, last completed step (from checkpoint.progress), "
                            f"proposed next step (from checkpoint.next_steps), count of open completion criteria, "
                            f"and visibility hints: e.g., \"N archived files available on demand\" (count of archived_context_files entries) and \"M subtask digests loaded in place of originals\" (count of subtask_digests entries). Omit a hint if its count is zero.\n"
                            f"5. Archived files and original reports can be read on demand whenever next_steps or an active finding names them explicitly.\n"
                            f"6. Legacy checkpoint note: if a pre-adoption checkpoint lists the same path in both context_files and subtask_digests[].original_report_path, you MUST prefer subtask_digests[].digest_path and skip that original_report_path entry in context_files.\n"
                            f"7. STOP your turn here. Emit only the orientation summary described above, "
                            f"then end your turn. Do not call any tool. Do not answer on the user's behalf. "
                            f"Wait for the user's explicit confirmation (delivered in their next message) "
                            f"before continuing.\n"
                            f"8. After user confirmation, proceed. Do NOT repeat completed work.\n"
                            f"Checkpoint: {checkpoint}"
                            f"{near_cap_line}"
                            f"{_build_active_children_block(state_dir)}"
                        )

            # ── Write cycle-resume-runbook (CHANNEL CHANGE: system prompt file) ─
            # Guard: only when claude_prompt is truthy (Episode ≥ 2 with valid checkpoint).
            episode_prompt_arg: str | None = None
            if claude_prompt:
                runbook_path = os.path.join(state_dir, "cycle-resume-runbook.md")
                try:
                    tmp_path = runbook_path + ".tmp"
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        f.write(claude_prompt)
                    os.replace(tmp_path, runbook_path)
                    _log(f"Episode {episode}: cycle-resume-runbook written ({len(claude_prompt)} bytes)")
                except Exception as e:
                    _log(f"Episode {episode}: cycle-resume-runbook write FAILED: {e}")
                if episode >= 2:
                    episode_prompt_arg = (
                        "Resume your session: your cycle-resume runbook is in your system prompt — "
                        "read the SESSION CYCLE RESUME block and execute its instructions now."
                    )
                else:
                    episode_prompt_arg = (
                        "Your task is appended to the bottom of your system prompt — "
                        "read it and execute the work now."
                    )
            elif child_sidecar_dir is not None:
                # D5 fix: L2 episode-1 — no cycle-resume runbook yet; load the dispatch prompt
                # written by the parent into the child sidecar dir.
                episode_prompt_arg = _resolve_l2_episode1_prompt(
                    child_sidecar_dir, episode, _log,
                )

            # ── Pre-spawn hook (JSONL snapshot for claude; install scripts for codex/gemini) ──
            arm.pre_spawn_hook(family_descriptor, state_dir, worktree_path)

            # ── Snapshot existing checkpoints before launch ────────────────────
            checkpoints_before = set(cs._get_checkpoint_files(state_dir))

            # ── Prepare environment ───────────────────────────────────────────
            base_env = os.environ.copy()
            base_env.pop("CLAUDE_SESSION_ID", None)
            base_env["CLAUDE_SESSION_ID"] = session_id
            base_env.pop("CLAUDE_HOOK_ORCHESTRATOR_DEPTH", None)
            base_env["CLAUDE_HOOK_ORCHESTRATOR_DEPTH"] = "1"
            base_env.pop("CLAUDE_SESSION_DEPTH", None)
            _depth = "0"
            if os.environ.get("CAA_CHILD_SIDECAR_DIR"):
                try:
                    # depth-only — pipelines are read upstream in bin/caa-session pre-activation;
                    # see bin/caa-session _dispatch_gemini (gemini arm) and _dispatch_codex (codex arm)
                    with open(os.path.join(os.environ["CAA_CHILD_SIDECAR_DIR"], "child-profile.json")) as _f:
                        _depth = str(int(json.load(_f).get("depth", 0)))
                except Exception:
                    _depth = "0"
            base_env["CLAUDE_SESSION_DEPTH"] = _depth
            base_env.pop("CAA_WORKTREE_ROOT", None)
            base_env["CAA_WORKTREE_ROOT"] = str(worktree_path)
            base_env.pop("CAA_CAMPAIGN_ID", None)
            base_env["CAA_CAMPAIGN_ID"] = _resolve_campaign_id_for_episode(
                cli_campaign, state_dir, cycle_state_path,
            ) or ""
            base_env.pop("CAA_CHILD_SIDECAR_DIR", None)
            base_env.pop("CAA_CHILD_SESSION_DIR", None)
            if child_sidecar_dir is not None:
                base_env["CAA_CHILD_SIDECAR_DIR"] = child_sidecar_dir
                base_env["CAA_CHILD_SESSION_DIR"] = state_dir
            base_env.pop("EPISODE", None)
            # Harness-replacement env gates
            base_env.setdefault("CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_TERMINAL_TITLE", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_POLICY_SKILLS", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY", "1")
            base_env.setdefault("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")

            # Family arm layers family-specific env additions on top of base.
            env = arm.prepare_env(family_descriptor, base_env, session_id, worktree_path)

            # ── Pre-warm page cache for .claude/ tree ──────────────────────────
            _wt_str = str(worktree_path)
            _skills_dirs = {
                os.path.join(_wt_str, ".claude", "skills"),
                os.path.expanduser("~/.claude/skills"),
            }
            _prewarm_dirs = (
                os.path.join(_wt_str, ".claude", "agents"),
                os.path.join(_wt_str, ".claude", "commands"),
                os.path.join(_wt_str, ".claude", "skills"),
                os.path.join(_wt_str, ".claude", "rules"),
                os.path.join(_wt_str, ".claude", "plugins"),
                os.path.join(_wt_str, ".claude", "output-styles"),
                os.path.expanduser("~/.claude/agents"),
                os.path.expanduser("~/.claude/commands"),
                os.path.expanduser("~/.claude/rules"),
                os.path.expanduser("~/.claude/skills"),
                os.path.expanduser("~/.claude/plugins"),
                os.path.expanduser("~/.claude/output-styles"),
            )
            for d in _prewarm_dirs:
                if os.path.isdir(d):
                    all_files = d in _skills_dirs
                    for _root, _dirs, _files in os.walk(d):
                        for fname in _files:
                            if all_files or fname.endswith(".md"):
                                try:
                                    with open(os.path.join(_root, fname), "rb") as fh:
                                        fh.read()
                                except Exception:
                                    pass

            # ── Build CLI argv via the family arm ─────────────────────────────
            argv = arm.build_argv(
                family_descriptor,
                rendered_prompt_path=prompt_file_arg,
                episode_prompt=episode_prompt_arg,
                mcp_config_path=_mcp_config_path,
                cli_options=cli_options,
            )

            # ── Log pre-launch state ───────────────────────────────────────────
            cycle_state_ok = "OK" if os.path.isfile(cycle_state_path) else "MISSING"
            monitor_pid_ok = "OK" if os.path.isfile(monitor_pid_path) else "MISSING"
            _log(
                f"State check: cycle.state={cycle_state_ok} "
                f"monitor.pid={monitor_pid_ok}"
            )

            # ── Authoritative-transcript pre-Popen unlink (A1) ────────────────
            auth_path = os.path.join(state_dir, "authoritative-transcript.json")
            try:
                os.unlink(auth_path)
                _log("AUTH_TRANSCRIPT_PREPOPEN_UNLINK=removed")
            except FileNotFoundError:
                _log("AUTH_TRANSCRIPT_PREPOPEN_UNLINK=absent")
            except Exception as e:
                _log(f"AUTH_TRANSCRIPT_PREPOPEN_UNLINK=error: {e}")

            # ── Token watcher config from family arm ──────────────────────────
            token_cfg = arm.configure_token_watcher(family_descriptor, state_dir, worktree_path)

            # Snapshot JSONL files before launch (family arm pre_spawn_hook already ran;
            # for claude, pre_spawn_hook captured the snapshot — we re-read it here from
            # the module-level global set by the arm).
            slug = cs._claude_project_slug(worktree_path)
            jsonl_dir = os.path.expanduser(f"~/.claude/projects/{slug}")
            try:
                jsonl_snapshot = {
                    f for f in os.listdir(jsonl_dir)
                    if f.endswith(".jsonl") and f != "history.jsonl"
                }
            except FileNotFoundError:
                jsonl_snapshot = set()
            except Exception as e:
                _log(f"JSONL_SNAPSHOT_ERROR: {e}")
                jsonl_snapshot = set()
            _log(f"JSONL_SNAPSHOT count={len(jsonl_snapshot)}")

            claude_spawn_ts = int(time.time() * 1000) - 2000  # 2s skew margin

            # ── Spawn the CLI subprocess ───────────────────────────────────────
            # Non-claude families (codex, gemini) run non-interactively: close
            # stdin so they don't block waiting for terminal input. Claude
            # inherits stdin for interactive TUI / MCP stdio negotiation.
            _popen_stdin = (
                subprocess.DEVNULL
                if family_descriptor.family not in ("claude",)
                else None
            )

            # ── Codex raw transcript capture (axis-11 Branch A) ───────────────
            # When codex_raw_dir is set, redirect codex stdout to a per-episode
            # JSONL file so _dispatch_codex() can coerce it after run_session()
            # returns.  --json is injected into the argv to enable JSONL output.
            _codex_raw_fh = None
            if family_descriptor.family == "codex" and codex_raw_dir is not None:
                _raw_ep_path = os.path.join(codex_raw_dir, f"codex-raw-ep{episode}.jsonl")
                try:
                    _codex_raw_fh = open(_raw_ep_path, "wb")  # noqa: SIM115
                    # Inject --json after "exec" for JSONL streaming output; idempotent.
                    argv = list(argv)
                    if "--json" not in argv:
                        try:
                            argv.insert(argv.index("exec") + 1, "--json")
                        except ValueError:
                            pass  # "exec" not in argv; skip injection
                    _log(f"codex-raw-capture OPEN ep={episode} path={_raw_ep_path}")
                except OSError as _e:
                    _log(f"codex-raw-capture OPEN_FAILED ep={episode}: {_e}")
                    _codex_raw_fh = None

            # D6 debug: log argv shape immediately before Popen to disambiguate
            # build_argv defect vs claude CLI parsing defect. The L2 child's
            # claude exits within ~1s with "Input must be provided either through
            # stdin or as a prompt argument when using --print" despite
            # _resolve_l2_episode1_prompt returning the dispatch prompt and
            # family_arm_claude.build_argv appending it as the final positional.
            try:
                _argv_last4 = [str(a)[:80] for a in argv[-4:]]
            except Exception as _e:
                _argv_last4 = [f"<inspect-err: {_e}>"]
            _log(f"argv_inspect family={family_descriptor.family} len={len(argv)} last_4={_argv_last4}")

            proc = subprocess.Popen(argv, cwd=str(worktree_path), env=env,
                                    stdin=_popen_stdin, stdout=_codex_raw_fh)
            cs._claude_proc = proc  # expose for signal handler

            _log(f"Episode {episode}: {family_descriptor.family} started (pid {proc.pid})")

            # ── Start file-watcher as daemon thread ───────────────────────────
            watcher = threading.Thread(
                target=cs._file_watcher,
                args=(state_dir, proc.pid, poll_interval,
                      jsonl_snapshot, claude_spawn_ts, episode,
                      worktree_path),
                kwargs={"keepalive_interval": keepalive_interval,
                        "sessions_root": sessions_root},
                daemon=True,
                name=f"watcher-ep{episode}",
            )
            cs._watcher_thread = watcher
            watcher.start()
            _log(f"Episode {episode}: file-watcher thread started")

            # ── Block until CLI exits ─────────────────────────────────────────
            proc.wait()
            # Close codex raw capture file (if open) before watcher cleanup.
            if _codex_raw_fh is not None:
                try:
                    _codex_raw_fh.close()
                except OSError:
                    pass
                _codex_raw_fh = None
            cs._claude_proc = None

            if cs._interrupted:
                _interrupted = True
                break

            _log(f"Episode {episode}: {family_descriptor.family} exited")

            # ── Stop watcher thread ───────────────────────────────────────────
            watcher.join(timeout=2.0)
            cs._watcher_thread = None

            # Remove pidfile if watcher didn't clean it up
            pidfile = os.path.join(state_dir, ".claude.pid")
            try:
                os.unlink(pidfile)
            except FileNotFoundError:
                pass
            except Exception:
                pass

            # Mirror: remove authoritative-transcript.json if watcher didn't clean
            try:
                os.unlink(os.path.join(state_dir, "authoritative-transcript.json"))
            except FileNotFoundError:
                pass
            except Exception:
                pass

            # ── Check for NEW cycle-checkpoint ────────────────────────────────
            checkpoints_after = set(cs._get_checkpoint_files(state_dir))
            if checkpoints_after != checkpoints_before:
                print(f"[caa-session] Cycle complete. Starting episode {episode + 1}.")
                _log(
                    f"Episode {episode}: cycle-checkpoint found, "
                    f"cycling to episode {episode + 1}"
                )
                try:
                    import session_registry as _sr
                    _sr.update_record(
                        main_root, session_id,
                        last_episode=episode,
                        last_touched=_iso_now(),
                    )
                except Exception:
                    pass
                cs._check_worktree_staleness(worktree_path, main_root, state_dir)
                continue  # Next episode

            # No new checkpoint — session done
            print(f"[caa-session] Session exited after {episode} episode(s).")
            _log(f"Session exited after {episode} episode(s) (no cycle-checkpoint found)")
            try:
                import session_registry as _sr
                _sr.update_record(
                    main_root, session_id,
                    last_episode=episode,
                    last_touched=_iso_now(),
                )
            except Exception:
                pass
            break

    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        if exc.code != 0:
            _crashed = True
        raise
    except BaseException as e:
        _crashed = True
        _log(f"Unexpected error in episode loop: {e}")
        print(f"[caa-session] Unexpected error: {e}", file=sys.stderr)
        raise

    # ── Max episodes message ──────────────────────────────────────────────────
    if episode >= max_episodes and not _interrupted:
        print(f"[caa-session] Max episodes ({max_episodes}) reached.", file=sys.stderr)
        _log(f"Max episodes ({max_episodes}) reached")

    # ── next-task-handoff check ───────────────────────────────────────────────
    if not _interrupted and not _crashed:
        _handoff_result = cs._check_next_task_handoff(pathlib.Path(state_dir))
        if _handoff_result.outcome == cs._HandoffOutcome.PROCEED_RELAUNCH:
            _task_preview = (_handoff_result.parsed or {}).get("task", "")
            _user_confirmed = cs._confirm_relaunch(_task_preview)
            if not _user_confirmed:
                print("[caa-session] Auto-relaunch aborted by user. "
                      "Verbatim task preserved.", file=sys.stderr)
                cs._archive_and_delete_sidecar(state_dir, main_root)
            else:
                _archived_path = cs._archive_and_delete_sidecar(state_dir, main_root)
                cs.cleanup()
                cs._prompt_keep_or_remove(
                    keep_flag=False,
                    remove_flag=False,
                    episode=episode,
                    max_episodes=max_episodes,
                    force_remove=True,
                )
                try:
                    import session_registry as _sr
                    _sr.delete_record(main_root, session_id)
                except Exception:
                    pass
                cs._remove_worktree(worktree_path, keep_flag=False)
                _relaunch_argv = (
                    [sys.executable, sys.argv[0]]
                    + (_handoff_result.parsed or {}).get("invocation_args", [])
                    + [(_handoff_result.parsed or {}).get("task", "")]
                )
                _new_env = os.environ.copy()
                _new_env.pop("CAA_CAMPAIGN_ID", None)
                os.execvpe(sys.executable, _relaunch_argv, _new_env)

        elif _handoff_result.outcome in (
            cs._HandoffOutcome.PUSH_GATE_FAILED,
            cs._HandoffOutcome.SENTINEL_GATE_FAILED,
            cs._HandoffOutcome.PARSE_ERROR,
        ):
            cs._archive_and_delete_sidecar(state_dir, main_root)
            print(_handoff_result.advisory, file=sys.stderr)

    # ── Clean-exit keep-vs-remove prompt ─────────────────────────────────────
    if not _interrupted and not _crashed:
        keep, name = cs._prompt_keep_or_remove(
            keep_flag=keep_worktree_flag,
            remove_flag=remove_worktree_flag,
            episode=episode,
            max_episodes=max_episodes,
        )
        if keep:
            try:
                import session_registry as _sr
                _sr.update_record(
                    main_root, session_id,
                    status="clean-exit-kept",
                    name=name,
                    last_touched=_iso_now(),
                )
            except Exception:
                pass
            _wt_rel = worktree_path.relative_to(main_root) if worktree_path else "?"
            _short = session_id[:8]
            if name:
                print(f"[caa-session] Worktree kept at {_wt_rel}/ as "
                      f"'{name}' (resume via --resume {_short} or --resume {name}).")
            else:
                print(f"[caa-session] Worktree kept at {_wt_rel}/ "
                      f"(resume via --resume {_short}).")
        else:
            try:
                import session_registry as _sr
                _sr.delete_record(main_root, session_id)
            except Exception:
                pass
            cs._remove_worktree(worktree_path, keep_flag=False)
            print("[caa-session] Worktree removed.")

    cs.cleanup()
    return 0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_family_arm(family_name: str) -> FamilyArm:
    """Dispatch to the per-family arm module by name."""
    if family_name == "claude":
        from caa.family_arm_claude import ClaudeFamilyArm
        return ClaudeFamilyArm()
    if family_name == "codex":
        from caa.family_arm_codex import CodexFamilyArm
        return CodexFamilyArm()
    if family_name == "gemini":
        from caa.family_arm_gemini import GeminiFamilyArm
        return GeminiFamilyArm()
    raise ValueError(
        f"Unknown family: {family_name!r}. "
        f"Supported: claude (S16), codex (S17), gemini (S18)."
    )


def _resolve_l2_episode1_prompt(
    child_sidecar_dir: str,
    episode: int,
    log_fn: Any,
) -> str | None:
    """Read child-dispatch-prompt.md from the child sidecar dir for L2 episode-1.

    Returns the verbatim file content as the episode prompt, or None on failure
    (caller lets the spawn proceed and fail loudly with the original D5 error).
    """
    dispatch_prompt_path = os.path.join(child_sidecar_dir, "child-dispatch-prompt.md")
    try:
        with open(dispatch_prompt_path, encoding="utf-8") as f:
            prompt_body = f.read()
        log_fn(
            f"Episode {episode}: L2 dispatch prompt loaded from "
            f"{child_sidecar_dir}/child-dispatch-prompt.md ({len(prompt_body)} bytes)"
        )
        return prompt_body
    except FileNotFoundError:
        msg = (
            f"[caa-session] WARNING: L2 episode-{episode} child-dispatch-prompt.md "
            f"not found at {dispatch_prompt_path} — spawn will fail without a prompt"
        )
        log_fn(msg)
        print(msg, file=sys.stderr)
        return None
    except Exception as e:
        msg = (
            f"[caa-session] WARNING: L2 episode-{episode} child-dispatch-prompt.md "
            f"read error at {dispatch_prompt_path}: {e} — spawn will fail without a prompt"
        )
        log_fn(msg)
        print(msg, file=sys.stderr)
        return None
