# Context-Aware Agent System

## Context Hygiene

Prefer the most context-efficient tool available.

- ALWAYS Prefer `mcp__context-tools__smart_*` tools over built-in Bash/Read/Grep/Glob. Smart tools truncate output and log full results to `.agent_context/logs/`.
- Use outline/symbol views before reading full files. Understand structure first.
- **File writes:** Prefer `smart_write` for rewriting existing files under `.claude/`. `Edit` and native `Write` both require a prior native `Read` when the target exists — `smart_read` does NOT satisfy this guard.

## Telemetry Attribution

Every `mcp__context-tools__*` call MUST include `agent_id`. Subagents pass their frontmatter `name:` field value; the main orchestrator session passes `agent_id: "main"`. Individual agent prompts do NOT restate this directive — it is inherited from CLAUDE.md.

## Knowledge Store

`.claude/knowledge/` - IMPORTANT - is the first place to orient on any subsystem and on the project — read before exploring code.
`.claude/knowledge/` is current-state truth; `docs/` is intent/rationale. Prefer knowledge for implementation, docs for rationale.
