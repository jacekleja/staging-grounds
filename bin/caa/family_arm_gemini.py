"""family_arm_gemini.py — Gemini arm implementing the FamilyArm Protocol.

Owns gemini-specific spawn surface:
  - prepare_env: GEMINI_NONINTERACTIVE + telemetry-suppress env vars.
  - build_argv: assembles `gemini -p <prompt> --yolo --output-format stream-json`;
      orchestrator-prompt delivered via GEMINI.md workspace file (no --system-prompt
      flag exists — constraint at constraints/gemini-cli-no-system-prompt-flag.md).
  - pre_spawn_hook: (1) drift-detected idempotent install-gemini-hooks.sh run;
      (2) bin/check-gemini-auth.sh proactive OAuth refresh.
  - configure_token_watcher: returns stream_json_token_events config pointing
      the shared watcher at <session_dir>/gemini-stream.jsonl.

LOAD-BEARING constraints honored here:
  - NO --system-prompt flag: orchestrator-prompt copied to GEMINI.md before spawn.
  - Token threshold: 190K per constraints/gemini-orchestrator-host-tpm-exhaustion.md.
  - Shim version drift: ~/.gemini/hooks/.caa-shim-version compared to
    _gemini_hook_adapter.CAA_SHIM_VERSION; install script re-run on mismatch.
"""

import os
import pathlib
import subprocess
import sys

from caa.family_descriptor import FamilyDescriptor
from caa.launcher import CliOptions, TokenMeasurementConfig

# Version stamp from the adapter — import lazily to avoid hard-dep at module load
# (the adapter lives in .claude/hooks/, not on the normal Python path).
_EXPECTED_SHIM_VERSION: str | None = None

# Version-stamp file written by install-gemini-hooks.sh.
_SHIM_VERSION_STAMP = pathlib.Path.home() / ".gemini" / "hooks" / ".caa-shim-version"

# Per-capability availability for the Gemini arm.
# Most capabilities are deferred pending A-3 (MCP co-deployment to ~/.gemini/)
# and A-4 (Monitor-substitute + Skill-invoke parity).  Only env.caa_child_sidecar_dir
# is confirmed available (unconditional launcher injection).
# Update this map when A-3/A-4 land and wire the gemini arm for each capability.
_GEMINI_CAPABILITY_MAP: dict = {
    "session.resume":          "deferred per Topic 10",   # needs A-3 MCP co-deployment
    "smart_read.sidecars":     "deferred per Topic 10",   # needs A-3 MCP co-deployment
    "events.jsonl.append":     "deferred per Topic 10",   # needs A-3 MCP co-deployment
    "skill.invoke":            "deferred per Topic 10",   # needs A-4 Skill-invoke parity
    "monitor.parent_messages": "deferred per Topic 10",   # needs A-4 Monitor-substitute
    "env.caa_child_sidecar_dir": "available",             # launcher-injected; unconditional
    "hook.posttooluse":        "deferred per Topic 10",   # needs gemini hook-shim co-deploy
}


def _get_repo_root_from_module() -> pathlib.Path:
    """Derive repo root from this module's location: bin/caa/family_arm_gemini.py → ../../."""
    return pathlib.Path(__file__).resolve().parent.parent.parent


def _read_expected_shim_version(repo_root: pathlib.Path) -> str | None:
    """Read CAA_SHIM_VERSION from the adapter source in-repo."""
    adapter_path = repo_root / ".claude" / "hooks" / "_gemini_hook_adapter.py"
    if not adapter_path.exists():
        return None
    try:
        for line in adapter_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("CAA_SHIM_VERSION"):
                # e.g. CAA_SHIM_VERSION = "1.0.0"
                _, _, val = line.partition("=")
                return val.strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _installed_shim_version() -> str | None:
    """Read the version stamp written by install-gemini-hooks.sh."""
    try:
        return _SHIM_VERSION_STAMP.read_text(encoding="utf-8").strip()
    except Exception:
        return None


class GeminiFamilyArm:
    """Gemini-family implementation of the FamilyArm Protocol."""

    def prepare_env(
        self,
        descriptor: FamilyDescriptor,
        base_env: dict,
        session_id: str,
        worktree_path: pathlib.Path,
    ) -> dict:
        """Return env dict with gemini-specific additions layered on top of base.

        Sets GEMINI_NONINTERACTIVE=1 (suppress any TTY prompts gemini may emit)
        and CAA_CYCLE_THRESHOLD=190000 (if not already set) to enforce the
        TPM-exhaustion-mitigation threshold for gemini orchestrator-host duty.
        """
        env = dict(base_env)

        # Suppress interactive prompts — gemini may ask for confirmation in TTY mode.
        env.setdefault("GEMINI_NONINTERACTIVE", "1")

        # Telemetry suppression (best-effort; these may have no effect depending on
        # gemini CLI version, but are harmless).
        env.setdefault("GEMINI_TELEMETRY_OPTOUT", "1")

        # LOAD-BEARING: 190K cycling threshold for TPM-exhaustion mitigation.
        # Only set if the operator has not explicitly overridden it.
        # cycle-hook.py reads CAA_CYCLE_THRESHOLD from env.
        env.setdefault("CAA_CYCLE_THRESHOLD", "190000")

        return env

    def build_argv(
        self,
        descriptor: FamilyDescriptor,
        rendered_prompt_path: str,
        episode_prompt: str | None,
        mcp_config_path: pathlib.Path,
        cli_options: CliOptions,
    ) -> list[str]:
        """Assemble the full CLI argv for a gemini episode.

        System-prompt delivery: copies rendered_prompt_path → <worktree>/GEMINI.md
        before returning (side effect, not a flag). The GEMINI.md file is
        workspace-discovered by the gemini CLI at startup.

        Invocation form: gemini -p <prompt> --yolo --output-format stream-json

        The prompt argument is the episode_prompt for episode 2+ (cycle-resume
        instruction), or an empty string for episode 1 (the orchestrator-prompt
        is delivered via GEMINI.md, not as an inline arg).

        mcp_config_path is not wired as a CLI flag here — gemini reads MCP config
        from ~/.gemini/settings.json, managed by install-gemini-hooks.sh.
        """
        # Binary resolution: honor GEMINI_BINARY env var; fallback to descriptor binary.
        gemini_bin = os.environ.get("GEMINI_BINARY") or descriptor.cli_command.binary
        # Expand ${GEMINI_BINARY:-gemini} style template.
        if gemini_bin.startswith("${") and "}" in gemini_bin:
            inner = gemini_bin[2:gemini_bin.index("}")]
            if ":-" in inner:
                var_name, default = inner.split(":-", 1)
                gemini_bin = os.environ.get(var_name) or default
            else:
                gemini_bin = os.environ.get(inner, "gemini")

        # Determine the worktree path from rendered_prompt_path's parent.
        # rendered_prompt_path is <session_dir>/orchestrator-prompt.rendered.md.
        # GEMINI.md must live in the worktree CWD (gemini discovers it by workspace).
        session_dir = pathlib.Path(rendered_prompt_path).parent

        # Derive worktree: the launcher passes cwd=worktree_path to Popen.
        # We must copy to the worktree root so gemini discovers GEMINI.md on startup.
        # The worktree path is available through the session state directory sibling.
        # Strategy: read CAA_WORKTREE_ROOT from env (set by shared launcher's base_env
        # before arm.build_argv is called — see launcher.py:471-472).
        worktree_str = os.environ.get("CAA_WORKTREE_ROOT") or str(session_dir)
        gemini_md_path = pathlib.Path(worktree_str) / "GEMINI.md"

        # LOAD-BEARING: copy orchestrator-prompt to GEMINI.md (workspace-discovered
        # system-priority delivery — no --system-prompt flag exists for gemini CLI).
        # Attention-weight risk: GEMINI.md receives lower attention than a true
        # system-role message; cycling at 190K is the primary mitigation.
        try:
            rendered_content = pathlib.Path(rendered_prompt_path).read_text(encoding="utf-8")
            tmp_path = str(gemini_md_path) + ".tmp"
            pathlib.Path(tmp_path).write_text(rendered_content, encoding="utf-8")
            os.replace(tmp_path, str(gemini_md_path))
        except Exception as e:
            print(
                f"[family_arm_gemini] WARNING: failed to write GEMINI.md at "
                f"{gemini_md_path}: {e}",
                file=sys.stderr,
            )

        # Build argv: gemini -p <prompt> --yolo --output-format stream-json
        # Episode 1: episode_prompt carries "Resume your session..." (the launcher
        #   always sets this when the initial prompt is non-empty). Gemini cannot
        #   access cycle-resume-runbook.md via GEMINI.md so we inline the runbook
        #   content as the -p argument instead of the pointer string.
        # Episode 2+: episode_prompt is the cycle-resume pointer string — GEMINI.md
        #   is already set to the orchestrator-prompt so the model understands the
        #   SESSION CYCLE RESUME block reference (the runbook is a sibling file it
        #   cannot see). Inline the runbook content here too for episode 2+.
        prompt_arg = ""
        if episode_prompt is not None:
            # Try to read the cycle-resume-runbook (contains the actual task body).
            runbook_path = session_dir / "cycle-resume-runbook.md"
            if runbook_path.exists():
                try:
                    runbook_content = runbook_path.read_text(encoding="utf-8")
                    # Truncate to stay within gemini -p limits.
                    if len(runbook_content) > 8000:
                        runbook_content = runbook_content[:8000] + "\n...[truncated]"
                    prompt_arg = runbook_content
                except OSError:
                    prompt_arg = episode_prompt  # Fall back to pointer string.
            else:
                prompt_arg = episode_prompt  # No runbook; use pointer string.

        argv = [
            gemini_bin,
            "-p", prompt_arg,
            "--yolo",
            "--output-format", "stream-json",
        ]

        return argv

    def pre_spawn_hook(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> None:
        """Gemini pre-spawn: idempotent hook install + proactive OAuth refresh.

        Two steps, in order:
        1. Check shim version drift; re-run install-gemini-hooks.sh if stale.
        2. Run bin/check-gemini-auth.sh to proactively refresh OAuth tokens.
        """
        repo_root = _get_repo_root_from_module()

        # ── Step 1: Shim drift detection and idempotent install ──────────────
        expected = _read_expected_shim_version(repo_root)
        installed = _installed_shim_version()

        needs_install = (
            installed is None          # never installed
            or (expected is not None and installed != expected)  # version mismatch
        )

        install_script = repo_root / "bin" / "install-gemini-hooks.sh"
        if needs_install and install_script.exists():
            print(
                f"[family_arm_gemini] Shim drift detected "
                f"(installed={installed!r}, expected={expected!r}). "
                f"Running install-gemini-hooks.sh...",
                file=sys.stderr,
            )
            try:
                result = subprocess.run(
                    ["bash", str(install_script)],
                    cwd=str(repo_root),
                    capture_output=False,
                    timeout=60,
                )
                if result.returncode != 0:
                    print(
                        f"[family_arm_gemini] WARNING: install-gemini-hooks.sh "
                        f"exited {result.returncode}",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(
                    f"[family_arm_gemini] WARNING: install-gemini-hooks.sh failed: {e}",
                    file=sys.stderr,
                )
        elif not install_script.exists():
            print(
                f"[family_arm_gemini] WARNING: install-gemini-hooks.sh not found at "
                f"{install_script}",
                file=sys.stderr,
            )

        # ── Step 2: Proactive OAuth refresh (gemini auth lifecycle) ──────────
        # Gemini OAuth tokens expire under long-lived orchestrator sessions.
        # Proactive refresh before spawn reduces mid-session token-expiry failures.
        auth_check = repo_root / "bin" / "check-gemini-auth.sh"
        if auth_check.exists():
            try:
                result = subprocess.run(
                    ["bash", str(auth_check)],
                    cwd=str(repo_root),
                    capture_output=False,
                    timeout=30,
                )
                if result.returncode != 0:
                    # Auth check failure is fatal: a session without valid auth will
                    # immediately fail, so we abort here rather than waste worktree.
                    print(
                        f"[family_arm_gemini] ERROR: check-gemini-auth.sh failed "
                        f"(exit {result.returncode}). Aborting spawn.",
                        file=sys.stderr,
                    )
                    sys.exit(result.returncode)
            except subprocess.TimeoutExpired:
                print(
                    "[family_arm_gemini] WARNING: check-gemini-auth.sh timed out (30s). "
                    "Continuing (auth may be stale).",
                    file=sys.stderr,
                )
            except Exception as e:
                print(
                    f"[family_arm_gemini] WARNING: check-gemini-auth.sh failed: {e}. "
                    f"Continuing.",
                    file=sys.stderr,
                )
        else:
            print(
                f"[family_arm_gemini] WARNING: check-gemini-auth.sh not found at "
                f"{auth_check}. Skipping auth check.",
                file=sys.stderr,
            )

    def configure_token_watcher(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> TokenMeasurementConfig:
        """Return stream_json_token_events config pointing at gemini-stream.jsonl.

        The shared launcher tees gemini's --output-format stream-json stdout to
        <session_dir>/gemini-stream.jsonl. The watcher parses that file for
        incremental token events and the final stats record.

        The 190K threshold is enforced via CAA_CYCLE_THRESHOLD env var (set in
        prepare_env). cycle-hook.py reads that env var and fires cycle-pending.
        """
        return TokenMeasurementConfig(
            mechanism=descriptor.token_measurement_mechanism.mechanism,
            source_path_template=descriptor.token_measurement_mechanism.source_path_template,
            polling_interval_ms=descriptor.token_measurement_mechanism.polling_interval_ms,
        )

    def probe_capabilities(self, required_capabilities: list) -> dict:
        """Return per-capability availability for the Gemini arm.

        Most capabilities are deferred pending A-3/A-4 work; only
        env.caa_child_sidecar_dir is confirmed available.  Deferred status
        is treated as soft-pass by run_capability_probe() — no fail-fast.
        Unknown tokens also return "deferred per Topic 10" until the gemini
        capability surface is fully mapped post A-3/A-4.
        """
        from caa.capability_probe import DEFERRED_STATUS
        return {
            cap: _GEMINI_CAPABILITY_MAP.get(cap, DEFERRED_STATUS)
            for cap in required_capabilities
        }


# ── Stream-JSON token parsing (consumed by the shared watcher) ───────────────
#
# Design note (Unknown #2 from the S14 design doc):
#   The exact field names in gemini --output-format stream-json events were not
#   verified at design time. Based on Q-9 (gemini-self-knowledge-r2.md) the
#   output has: incremental token events mid-turn + a final "stats" object.
#   Standard Gemini API uses usageMetadata.{promptTokenCount, candidatesTokenCount,
#   totalTokenCount}. We parse multiple candidate shapes and sum defensively.
#
# This parser is called by the shared file-watcher when mechanism==stream_json_token_events.

def parse_stream_json_tokens(line: str) -> int:
    """Parse one line of gemini --output-format stream-json; return token delta.

    Returns 0 if the line is not a recognized token event (fail-open).

    Candidate schemas tried in order:
      1. {usageMetadata: {totalTokenCount: N}}         — Gemini API standard
      2. {usageMetadata: {promptTokenCount: A, candidatesTokenCount: B}}
      3. {stats: {totalTokenCount: N}}                 — CLI-level stats wrapper
      4. {stats: {input_tokens: A, output_tokens: B}}  — alternative naming
      5. {totalTokens: N}                              — flat variant observed empirically
    """
    import json
    try:
        obj = json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return 0

    if not isinstance(obj, dict):
        return 0

    # Shape 1 & 2: usageMetadata (Gemini REST API standard)
    usage = obj.get("usageMetadata")
    if isinstance(usage, dict):
        total = usage.get("totalTokenCount")
        if isinstance(total, (int, float)):
            return int(total)
        prompt = usage.get("promptTokenCount", 0) or 0
        cands = usage.get("candidatesTokenCount", 0) or 0
        if prompt or cands:
            return int(prompt) + int(cands)

    # Shape 3 & 4: stats wrapper (CLI-level)
    stats = obj.get("stats")
    if isinstance(stats, dict):
        total = stats.get("totalTokenCount")
        if isinstance(total, (int, float)):
            return int(total)
        inp = stats.get("input_tokens", 0) or 0
        out = stats.get("output_tokens", 0) or 0
        if inp or out:
            return int(inp) + int(out)

    # Shape 5: flat totalTokens
    total = obj.get("totalTokens")
    if isinstance(total, (int, float)):
        return int(total)

    return 0
