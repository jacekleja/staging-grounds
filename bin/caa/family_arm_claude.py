"""family_arm_claude.py — Claude arm implementing the FamilyArm Protocol.

Owns Claude-specific spawn surface:
  - build_argv: assembles the claude CLI argv (same flags as pre-extraction
    claude-session, reproducing the behavior verbatim).
  - prepare_env: no Claude-specific additions beyond the shared CAA_* env block.
  - pre_spawn_hook: JSONL snapshot (authoritative-transcript unlink is handled
    by the shared launcher before this call).
  - configure_token_watcher: returns transcript_jsonl_poll config.

The Claude arm does NOT implement codex or gemini logic — those land at S17/S18.
"""

import os
import pathlib

from caa.family_descriptor import FamilyDescriptor
from caa.launcher import CliOptions, TokenMeasurementConfig


# All 7 known capability tokens are available in the Claude arm —
# Claude is the reference family; the L2 discipline body was authored
# against Claude's native tool surface (session.resume, Skill, Monitor, etc.).
_CLAUDE_AVAILABLE_CAPABILITIES: frozenset = frozenset({
    "session.resume",
    "smart_read.sidecars",
    "events.jsonl.append",
    "skill.invoke",
    "monitor.parent_messages",
    "env.caa_child_sidecar_dir",
    "hook.posttooluse",
})


class ClaudeFamilyArm:
    """Claude-family implementation of the FamilyArm Protocol."""

    def prepare_env(
        self,
        descriptor: FamilyDescriptor,
        base_env: dict,
        session_id: str,
        worktree_path: pathlib.Path,
    ) -> dict:
        """Return the env dict with Claude-specific additions (none beyond shared block)."""
        # Claude's auth is platform-managed OAuth — no API key injection needed.
        # The shared launcher already handled all CAA_* vars.
        return base_env

    def build_argv(
        self,
        descriptor: FamilyDescriptor,
        rendered_prompt_path: str,
        episode_prompt: str | None,
        mcp_config_path: pathlib.Path,
        cli_options: CliOptions,
    ) -> list[str]:
        """Assemble the full CLI argv for a Claude Code episode.

        Reproduces the pre-extraction claude-session claude_args construction
        verbatim (C11 invariant: stock flag set only).
        """
        # Binary resolution: honor CLAUDE_BINARY env var; fallback to 'claude'.
        claude_bin = os.environ.get("CLAUDE_BINARY") or descriptor.cli_command.binary
        # Expand ${CLAUDE_BINARY:-claude} style template: simple env expansion.
        if claude_bin.startswith("${") and "}" in claude_bin:
            # Parse ${VAR:-default} pattern.
            inner = claude_bin[2:claude_bin.index("}")]
            if ":-" in inner:
                var_name, default = inner.split(":-", 1)
                claude_bin = os.environ.get(var_name) or default
            else:
                claude_bin = os.environ.get(inner, "claude")

        argv = [
            claude_bin,
            "--model", cli_options.model,
        ]
        # D4 fix: claude CLI rejects --effort default (valid values: low|medium|high|xhigh|max).
        # The CliOptions.effort dataclass default is the literal string "default", a sentinel
        # meaning "use claude's CLI default" — i.e. omit the flag.
        if cli_options.effort and cli_options.effort != "default":
            argv.extend(["--effort", cli_options.effort])
        argv.extend([
            "--dangerously-skip-permissions",
            "--setting-sources", "project,local",
            "--mcp-config", str(mcp_config_path),
            "--strict-mcp-config",
            "--system-prompt-file", rendered_prompt_path,
            "--disallowedTools",
            "TaskCreate,TaskGet,TaskList,TaskOutput,TaskStop,TaskUpdate,"
            "CronCreate,CronDelete,CronList,EnterPlanMode,ExitPlanMode,"
            # D10 fix: Monitor primitive REMOVED from disallowedTools so that
            # L2 child sessions can establish the parent-messages.jsonl Monitor
            # per `.claude/skills/dispatch-l2/SKILL.md §K.1 step 3` and
            # `§K.2 — Receiving messages from parent`. The original wholesale
            # port (commit 43651850) included Monitor in the disallow without
            # documented rationale; SKILL.md §K.2 explicitly requires it for
            # non-abort parent→L2 messaging on the Claude family route.
            "EnterWorktree,ExitWorktree,NotebookEdit,PushNotification,"
            "RemoteTrigger,ScheduleWakeup,AskUserQuestion,Read,Write,Edit,Glob,Grep",
        ])

        # Episode 2+: runbook already written to cycle-resume-runbook.md by the
        # shared launcher; attach as a second system prompt file, then add the
        # pointer prompt as the positional argument.
        if episode_prompt is not None:
            # The runbook path is deterministic: <session_dir>/cycle-resume-runbook.md.
            # We don't receive session_dir here directly, but it's embedded in
            # rendered_prompt_path's parent (rendered_prompt_path is inside session_dir).
            session_dir = str(pathlib.Path(rendered_prompt_path).parent)
            runbook_path = os.path.join(session_dir, "cycle-resume-runbook.md")
            if os.path.isfile(runbook_path):
                argv = argv + ["--append-system-prompt-file", runbook_path]
            # D6 fix: claude CLI's --disallowedTools is declared variadic
            # (Commander.js `<tools...>` syntax) and greedily consumes
            # trailing positional args until another --<flag> or end-of-argv.
            # On episode 1 the runbook doesn't exist yet (it's written for
            # episode-2+ resume), so without a flag-terminator argv ends
            # `..., --disallowedTools <list>, <episode_prompt>` and the
            # prompt gets eaten as another tool name -- claude then reports
            # "Input must be provided either through stdin or as a prompt
            # argument when using --print" because zero positionals reach
            # parsing. The POSIX "--" flag-terminator stops the variadic
            # and leaves episode_prompt as the positional prompt arg.
            # Universal fix: works whether --append-system-prompt-file was
            # inserted (episode 2+) or not (episode 1).
            argv = argv + ["--", episode_prompt]

        return argv

    def pre_spawn_hook(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> None:
        """Claude pre-spawn: JSONL snapshot is handled by the shared launcher.

        No additional action needed for the Claude arm — the shared launcher
        captures the JSONL snapshot in the episode loop before calling this hook.
        The authoritative-transcript unlink is also in the shared launcher (A1).
        """
        # No-op for Claude: the shared launcher manages the JSONL snapshot and
        # auth-transcript unlink in the pre-Popen adjacency block.
        pass

    def configure_token_watcher(
        self,
        descriptor: FamilyDescriptor,
        session_dir: str,
        worktree_path: pathlib.Path,
    ) -> TokenMeasurementConfig:
        """Return transcript_jsonl_poll config — the Claude token measurement path."""
        return TokenMeasurementConfig(
            mechanism=descriptor.token_measurement_mechanism.mechanism,
            source_path_template=descriptor.token_measurement_mechanism.source_path_template,
            polling_interval_ms=descriptor.token_measurement_mechanism.polling_interval_ms,
        )

    def probe_capabilities(self, required_capabilities: list) -> dict:
        """Return per-capability availability for the Claude arm.

        Claude is the reference family; all 7 known capability tokens are
        available.  Unknown tokens (should not reach this probe after C12)
        return "missing: not recognized by claude arm probe".
        """
        return {
            cap: "available" if cap in _CLAUDE_AVAILABLE_CAPABILITIES
            else f"missing: capability '{cap}' not recognized by claude arm probe"
            for cap in required_capabilities
        }
