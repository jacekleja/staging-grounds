"""family_arm_codex.py — Codex arm implementing the FamilyArm Protocol.

Owns the codex-specific spawn surface:
  - build_argv: assembles `codex exec` argv with -c model_instructions_file=<path>
    for system-priority orchestrator-prompt delivery.
  - prepare_env: asserts valid auth is present — ChatGPT-OAuth (auth_mode=chatgpt +
    non-expired token or refresh_token) OR OPENAI_API_KEY env var.
  - pre_spawn_hook: idempotently invokes bin/install-codex-hooks.sh to deploy hook
    shims and register them in ~/.codex/config.toml; also invokes check-codex-auth.sh.
  - configure_token_watcher: returns rollout_jsonl_token_count_events config so the
    shared watcher polls ~/.codex/sessions/... rollout.jsonl for token_count events.

LOAD-BEARING constraint per constraints/cross-family-codex-stdin-role-user.md:
  Stdin assigns role=user on codex. The orchestrator-prompt MUST be delivered via
  -c model_instructions_file=<path>. NEVER inline via stdin or prompt-argument.

Eager-context composition: CLAUDE.md + .claude/agents/*.md frontmatter descriptions
  + .claude/skills/*/SKILL.md frontmatter descriptions are assembled into the
  orchestrator-prompt at render time by orchestrator_prompt_render.py. This arm
  uses `prerendered_in_orchestrator_prompt` agent_registry_mode — the orchestrator
  prompt already contains the roster when this arm's build_argv fires.
"""

import base64
import json
import os
import pathlib
import subprocess
import sys
import time

from caa.family_descriptor import FamilyDescriptor
from caa.launcher import CliOptions, TokenMeasurementConfig

# Version stamp for shim-drift detection.  Compared against
# ~/.codex/hooks/.caa-shim-version; mismatch triggers re-install.
_EXPECTED_SHIM_VERSION = "1.0.2"  # 1.0.1→1.0.2: covers skill-agent-gate.py (D-10) + _dispatch_child_guard.py (A-4)

# Sentinel file written by install-codex-hooks.sh.
_SHIM_VERSION_FILE = pathlib.Path.home() / ".codex" / "hooks" / ".caa-shim-version"

# Path to the install script relative to repo root (resolved at call time).
_BIN_DIR = pathlib.Path(__file__).parent.parent.resolve()
_INSTALL_HOOKS_SCRIPT = _BIN_DIR / "install-codex-hooks.sh"
_AUTH_CHECK_SCRIPT = _BIN_DIR / "check-codex-auth.sh"

# Per-capability availability for the Codex arm.
# skill.invoke and monitor.parent_messages are Claude-native constructs not
# present in the codex CLI; Topic 10 work is needed before they can be enabled.
_CODEX_CAPABILITY_MAP: dict = {
    "session.resume":          "available",
    # context-tools MCP server is registered in ~/.codex/config.toml (install-codex-hooks.sh)
    "smart_read.sidecars":     "available",
    # context-tools MCP tool; available via codex MCP config
    "events.jsonl.append":     "available",
    # file-system write; codex has file-write tools
    "skill.invoke":            "missing: Skill tool is Claude-native; not available in codex arm",
    "monitor.parent_messages": "missing: Monitor primitive is Claude-native; not available in codex arm",
    "env.caa_child_sidecar_dir": "available",
    # launcher-injected env var; available unconditionally
    "hook.posttooluse":        "available",
    # codex hook shims installed by install-codex-hooks.sh
}


class CodexFamilyArm:
    """Codex-family implementation of the FamilyArm Protocol."""

    def prepare_env(
        self,
        descriptor: FamilyDescriptor,
        base_env: dict,
        session_id: str,
        worktree_path: pathlib.Path,
    ) -> dict:
        """Assert valid auth is available; return env unchanged otherwise.

        Accepts either:
          Path A — ChatGPT-OAuth: ~/.codex/auth.json present with
                   auth_mode=chatgpt AND (non-expired access_token OR
                   refresh_token present). Does NOT require OPENAI_API_KEY.
                   Mirrors the detection logic in bin/check-codex-auth.sh.
          Path B — API key: OPENAI_API_KEY set in base_env or os.environ.

        Fail fast here rather than letting codex fail mid-session.
        """
        # Path A: ChatGPT-OAuth detection — mirrors check-codex-auth.sh logic.
        if _chatgpt_oauth_valid():
            return base_env

        # Path B: API-key auth.
        if base_env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            return base_env

        raise RuntimeError(
            "No valid Codex auth found. Neither ChatGPT-OAuth "
            "(~/.codex/auth.json with non-expired token or refresh_token) "
            "nor OPENAI_API_KEY env var is available. "
            "Options: (1) codex login for ChatGPT-OAuth, "
            "(2) export OPENAI_API_KEY. "
            "See docs/runbooks/codex-orchestrator-host.md § Auth setup."
        )

    def build_argv(
        self,
        descriptor: FamilyDescriptor,
        rendered_prompt_path: str,
        episode_prompt: str | None,
        mcp_config_path: pathlib.Path,
        cli_options: CliOptions,
    ) -> list[str]:
        """Assemble the full CLI argv for a codex exec episode.

        LOAD-BEARING: orchestrator-prompt delivered via -c model_instructions_file=
        NOT via stdin (which assigns role=user, a silent severity-loss bug).

        mcp_config_path is unused for codex — MCP registration lives in
        ~/.codex/config.toml, managed by install-codex-hooks.sh; there is no
        per-invocation --mcp-config flag on codex.
        """
        # Binary resolution: honor CODEX_BINARY; fallback to 'codex'.
        codex_bin = os.environ.get("CODEX_BINARY") or _expand_binary_template(
            descriptor.cli_command.binary
        )

        argv = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "-c", "approval_policy=never",
            "-c", "sandbox_mode=danger-full-access",
            # LOAD-BEARING: system-tier delivery (base instructions role).
            # constraint: cross-family-codex-stdin-role-user.md
            "-c", f"model_instructions_file={rendered_prompt_path}",
        ]

        # Episode 2+: attach the runbook as additional context via developer_instructions.
        # model_instructions_file takes the primary orchestrator-prompt; developer_instructions
        # carries the runbook (intermediate priority, distinct from user).
        if episode_prompt is not None:
            session_dir = str(pathlib.Path(rendered_prompt_path).parent)
            runbook_path = os.path.join(session_dir, "cycle-resume-runbook.md")
            if os.path.isfile(runbook_path):
                try:
                    runbook_content = pathlib.Path(runbook_path).read_text(encoding="utf-8")
                    # Truncate if very large to stay within codex -c value limits.
                    if len(runbook_content) > 8000:
                        runbook_content = runbook_content[:8000] + "\n...[truncated]"
                    argv += ["-c", f"developer_instructions={runbook_content}"]
                except OSError:
                    pass  # Missing runbook is non-fatal; episode proceeds without it.
            # The episode pointer prompt is passed as the stdin/positional argument
            # to codex exec. Note: this carries role=user which is correct for the
            # episode-continuation pointer prompt (it IS a user-facing directive).
            argv.append(episode_prompt)

        return argv

    def pre_spawn_hook(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> None:
        """Codex pre-spawn: idempotently install hook shims and verify auth.

        1. Detect shim drift by comparing ~/.codex/hooks/.caa-shim-version to
           _EXPECTED_SHIM_VERSION; re-run install-codex-hooks.sh when stale or absent.
        2. Run check-codex-auth.sh to verify OPENAI_API_KEY is set and a trivial
           codex exec round-trip succeeds; abort with sys.exit(1) on failure.

        Both scripts are idempotent — safe to call on every session start.
        """
        # ── Step 1: hook shim drift detection + idempotent re-install ─────────
        _ensure_hooks_installed()

        # ── Step 2: auth pre-launch check ─────────────────────────────────────
        _run_auth_check()

    def configure_token_watcher(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> TokenMeasurementConfig:
        """Return rollout_jsonl_token_count_events config for the shared watcher.

        The shared watcher thread polls the codex rollout JSONL for token_count
        events (codex-rs/core/src/rollout/recorder.rs schema). The path template
        uses ${HOME}/.codex/sessions/${CODEX_SESSION_ID}/rollout.jsonl per the
        design's worked-example codex.json.

        The watcher resolves ${HOME} at poll time using os.path.expandvars.
        CODEX_SESSION_ID is set by codex in the subprocess env and then propagated
        to the sentinel dir for the cycle-hook to read.
        """
        return TokenMeasurementConfig(
            mechanism=descriptor.token_measurement_mechanism.mechanism,
            source_path_template=descriptor.token_measurement_mechanism.source_path_template,
            polling_interval_ms=descriptor.token_measurement_mechanism.polling_interval_ms,
        )

    def probe_capabilities(self, required_capabilities: list) -> dict:
        """Return per-capability availability for the Codex arm.

        skill.invoke and monitor.parent_messages are Claude-native; they return
        "missing: ..." until Topic 10 landing enables parity.  Unknown tokens
        (should not occur after C12) return "missing: unrecognized".
        """
        return {
            cap: _CODEX_CAPABILITY_MAP.get(
                cap,
                f"missing: capability '{cap}' not recognized by codex arm probe",
            )
            for cap in required_capabilities
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _chatgpt_oauth_valid() -> bool:
    """Return True if ChatGPT-OAuth auth is present and usable.

    Mirrors bin/check-codex-auth.sh Path A logic:
      1. ~/.codex/auth.json must exist with auth_mode == "chatgpt".
      2. access_token JWT exp must be in the future, OR refresh_token must be
         present (allows token refresh at spawn time without pre-expired failure).

    Returns False (rather than raising) so callers fall through to API-key check.
    """
    auth_json_path = pathlib.Path(
        os.environ.get("CODEX_AUTH_JSON", str(pathlib.Path.home() / ".codex" / "auth.json"))
    )
    if not auth_json_path.exists():
        return False

    try:
        data = json.loads(auth_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if data.get("auth_mode") != "chatgpt":
        return False

    tokens = data.get("tokens") or {}

    # Non-expired access_token: decode JWT payload and check exp field.
    access_token = tokens.get("access_token") or ""
    if access_token:
        try:
            payload_b64 = access_token.split(".")[1]
            # Convert base64url → standard base64 and pad.
            payload_b64 = payload_b64.replace("-", "+").replace("_", "/")
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            payload = json.loads(base64.b64decode(payload_b64))
            if payload.get("exp", 0) > time.time():
                return True
        except Exception:  # noqa: BLE001 — malformed JWT is non-fatal; fall through
            pass

    # refresh_token present: codex can self-refresh at spawn time.
    if tokens.get("refresh_token"):
        return True

    return False


def _expand_binary_template(template: str) -> str:
    """Expand ${VAR:-default} style env templates (codex binary field)."""
    if template.startswith("${") and "}" in template:
        inner = template[2:template.index("}")]
        if ":-" in inner:
            var_name, default = inner.split(":-", 1)
            return os.environ.get(var_name) or default
        return os.environ.get(inner, "codex")
    return template


def _current_shim_version() -> str | None:
    """Read ~/.codex/hooks/.caa-shim-version; return None if absent or unreadable."""
    try:
        return _SHIM_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _ensure_hooks_installed() -> None:
    """Run install-codex-hooks.sh if shim version is absent or stale.

    install-codex-hooks.sh is idempotent; calling it when already current is safe
    but avoided for performance (the drift check is a single file read).
    """
    current = _current_shim_version()
    if current == _EXPECTED_SHIM_VERSION:
        return  # Already current; skip.

    if not _INSTALL_HOOKS_SCRIPT.exists():
        # Non-fatal: warn but don't abort. The hooks may have been installed
        # by a different mechanism (manual copy, CI bootstrap, etc.).
        print(
            f"[codex-arm] WARNING: install-codex-hooks.sh not found at "
            f"{_INSTALL_HOOKS_SCRIPT}. Hook shims may be missing or stale.",
            file=sys.stderr,
        )
        return

    if current is None:
        print("[codex-arm] Hook shims not installed; running install-codex-hooks.sh ...", file=sys.stderr)
    else:
        print(
            f"[codex-arm] Hook shim version drift detected "
            f"(installed={current!r}, expected={_EXPECTED_SHIM_VERSION!r}); "
            f"re-running install-codex-hooks.sh ...",
            file=sys.stderr,
        )

    try:
        result = subprocess.run(
            ["bash", str(_INSTALL_HOOKS_SCRIPT)],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout, file=sys.stderr)
    except subprocess.CalledProcessError as e:
        # Non-fatal: hooks may already be functional; log and continue.
        # The pre-spawn auth check will fail if the environment is broken.
        print(
            f"[codex-arm] WARNING: install-codex-hooks.sh exited {e.returncode}: "
            f"{e.stderr}",
            file=sys.stderr,
        )


def _run_auth_check() -> None:
    """Run check-codex-auth.sh; sys.exit(1) if auth verification fails.

    Aborts with a clear message rather than letting codex fail mid-session
    (which produces confusing output). The auth check is a fast round-trip
    and exits 0 on success, non-zero on failure.
    """
    if not _AUTH_CHECK_SCRIPT.exists():
        # check-codex-auth.sh may not yet exist (S17 creates it). Fall back
        # to the minimal inline check: assert OPENAI_API_KEY is present.
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print(
                "[codex-arm] FATAL: OPENAI_API_KEY is not set. "
                "Codex cannot authenticate. Aborting session. "
                "See docs/runbooks/codex-orchestrator-host.md § Auth setup.",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    try:
        result = subprocess.run(
            ["bash", str(_AUTH_CHECK_SCRIPT)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,  # Auth check should complete well within 30s.
        )
        if result.stdout:
            print(result.stdout, file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(
            f"[codex-arm] FATAL: check-codex-auth.sh failed (exit {e.returncode}). "
            f"Codex auth is not ready. Aborting session.\n{e.stderr}",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(
            "[codex-arm] FATAL: check-codex-auth.sh timed out (30s). "
            "Codex auth probe did not complete. Aborting session.",
            file=sys.stderr,
        )
        sys.exit(1)
