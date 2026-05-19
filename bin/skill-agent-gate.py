#!/usr/bin/env python3
"""PreToolUse/Skill hook: gate skill invocations by caller-allowlist.

Implements 3-way fail-direction posture per architect §1 Item 2 (a):
  - skill file missing                  → FAIL-OPEN  (platform handles)
  - caller-allowlist: field absent      → FAIL-OPEN  (ungated by design)
  - caller-allowlist: malformed YAML    → FAIL-CLOSED (sole-primary-defense)
  - caller-allowlist: present + miss    → BLOCK with reason
  - caller-allowlist: present + match   → pass
  - caller-allowlist: [] (empty list)   → BLOCK all agents (user-only marker)

Lookup order for skill files:
  1. .claude/skills/<name>/SKILL.md   (traditional skills)
  2. .claude/commands/<name>.md       (slash commands — fallback)

Payload shape — UPDATED 2026-05-07 (empirical capture, issue iss_e748eb17df51):

E-1's 5-fire sample was insufficient — it only captured compose-time and
main-session calls, missing the third payload shape: subagent-turn-body Skill
calls made from within a running subagent turn.

Three confirmed payload shapes:
  Shape 1 (main/orchestrator session):  subagent_type ABSENT, agent_type ABSENT,
    agent_id = session UUID → caller resolves to "main" (fallback).
  Shape 2 (compose-time subagent dispatch): subagent_type = role name (e.g.
    "researcher"), agent_type ABSENT → caller resolves via subagent_type.
  Shape 3 (subagent-turn-body Skill call, captured 2026-05-07): subagent_type
    ABSENT, agent_type = role name (e.g. "researcher"), agent_id = 17-char hex
    per-instance UUID → caller MUST resolve via agent_type; agent_id is NOT a
    role name and must not be used for allowlist matching.

Resolver chain: subagent_type → agent_type → agent_id → "main".
  agent_id is last-resort only (allows gate to emit a meaningful caller= token
  in error messages when all role-name fields are absent).

Evidence: .agent_context/sessions/1778154976-19047-d677afc6bc48/skill-agent-gate-debug.jsonl
  (captured 2026-05-07), diag-iss_e748eb17df51-axis-A.md, -axis-B.md,
  probe-iss_e748eb17df51-empirical-capture-rerun.md (same session_dir).
Constraint ref: .claude/knowledge/constraints/platform/pretooluse-skill-event-payload.md
"""
import json
import os
import re
import sys
from datetime import datetime, timezone


def _session_dir():
    """Mirror delegation-trace-hook.py:get_session_dir resolution.

    Project root derived from __file__ (2 parents up from bin/skill-agent-gate.py).
    Falls back to .agent_context/audit/ if CLAUDE_SESSION_ID unset.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sid = os.environ.get("CLAUDE_SESSION_ID", "")
    if sid:
        return os.path.join(project_root, ".agent_context", "sessions", sid)
    return os.path.join(project_root, ".agent_context", "audit")


def _log_jsonl(path, record):
    """Append one JSONL record. Fail-quiet on any IOError."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


# Match a `caller-allowlist:` line in YAML frontmatter and capture the value.
# Accepts inline list form: caller-allowlist: [a, b, c]
# Accepts block list form: caller-allowlist:\n  - a\n  - b
_ALLOWLIST_LINE_RE = re.compile(r"^caller-allowlist:[ \t]*(.*)$", re.MULTILINE)


class FrontmatterParseError(Exception):
    """Raised when caller-allowlist: line matches but value cannot be extracted."""


def parse_caller_allowlist(skill_path):
    """Read SKILL.md frontmatter; return list[str], or None if field absent.

    Returns None  -> field-absent (ungated path; FAIL-OPEN)
    Returns list  -> parsed allowlist
    Raises FrontmatterParseError -> matched line but unparseable (FAIL-CLOSED)
    """
    with open(skill_path) as f:
        content = f.read()
    # Limit to frontmatter block (between leading --- markers).
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        fm = content[4:end] if end != -1 else content
    else:
        fm = content
    m = _ALLOWLIST_LINE_RE.search(fm)
    if not m:
        return None  # field absent — ungated
    raw = m.group(1).strip()
    if raw.startswith("[") and raw.endswith("]"):
        # Inline list form: [a, b, c]
        items = [tok.strip().strip('"').strip("'") for tok in raw[1:-1].split(",")]
        items = [tok for tok in items if tok]
        # Empty inline list [] is an explicit user-only marker: block all agents.
        return items
    if raw == "":
        # Block list form: scan subsequent lines for '  - <name>' entries.
        block = []
        for line in fm[m.end():].splitlines():
            ls = line.strip()
            if ls.startswith("- "):
                block.append(ls[2:].strip().strip('"').strip("'"))
            elif ls == "" or ls.startswith("#"):
                continue
            else:
                break
        if not block:
            raise FrontmatterParseError("caller-allowlist: matched but no list items found")
        return block
    raise FrontmatterParseError(f"unrecognized caller-allowlist value shape: {raw!r}")


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)  # FAIL-OPEN on unparseable event

    session_dir = _session_dir()

    try:
        tool_input = event.get("tool_input", {}) or {}
        # E-1 RESOLVED: empirical field name is `tool_input.skill` (confirmed from
        # skill-agent-gate-debug.jsonl records 13-17, 5 real platform fires).
        skill_name = tool_input.get("skill", "")

        # Resolver chain (empirically grounded, 2026-05-07 capture, iss_e748eb17df51):
        #   subagent_type → agent_type → agent_id → "main"
        # Shape 2 (compose-time dispatch): subagent_type = role name.
        # Shape 3 (subagent-turn-body call): subagent_type ABSENT, agent_type = role name,
        #   agent_id = 17-char hex UUID (NOT a role name; last-resort for error messages only).
        # Shape 1 (main session): all three absent → resolves to "main".
        caller = event.get("subagent_type") or event.get("agent_type") or event.get("agent_id") or "main"

        if not skill_name:
            sys.exit(0)  # FAIL-OPEN: cannot determine skill name

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # CAA_SKILLS_ROOT overrides the skills lookup root (used by probe/test harnesses).
        skills_root = os.environ.get("CAA_SKILLS_ROOT") or os.path.join(project_root, ".claude", "skills")
        skill_path = os.path.join(skills_root, skill_name, "SKILL.md")
        if not os.path.exists(skill_path):
            # Fallback: slash-command at .claude/commands/<name>.md
            skill_path = os.path.join(project_root, ".claude", "commands", skill_name + ".md")
            if not os.path.exists(skill_path):
                sys.exit(0)  # FAIL-OPEN: neither skill nor command file found

        try:
            allowlist = parse_caller_allowlist(skill_path)
        except FrontmatterParseError as e:
            # FAIL-CLOSED: matched but unparseable.
            _log_jsonl(
                os.path.join(session_dir, "skill-agent-gate-errors.jsonl"),
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "kind": "malformed-allowlist",
                    "skill": skill_name,
                    "error": str(e),
                },
            )
            print(json.dumps({
                "decision": "block",
                "reason": f"skill-agent-gate: malformed caller-allowlist on {skill_name}; "
                          f"refusing to invoke under sole-primary-defense posture",
            }))
            sys.exit(0)

        if allowlist is None:
            sys.exit(0)  # FAIL-OPEN: field absent (ungated by design)

        # "main" and "orchestrator" are synonyms for the main session identity.
        # A skill author may write [orchestrator] to restrict to the main session;
        # the platform emits no subagent_type for main calls, so caller resolves to
        # "main". Treat membership in either token as a match (architect §1 Item 2a).
        effective_caller_tokens = {caller}
        if caller == "main":
            effective_caller_tokens.add("orchestrator")
        elif caller == "orchestrator":
            effective_caller_tokens.add("main")

        if effective_caller_tokens & set(allowlist):
            sys.exit(0)  # pass

        # In-allowlist miss: BLOCK.
        print(json.dumps({
            "decision": "block",
            "reason": f"skill {skill_name} restricted to {allowlist}; caller={caller}",
        }))
        sys.exit(0)

    except Exception as e:
        # Unhandled exception in hook body: FAIL-OPEN (preserve session liveness).
        _log_jsonl(
            os.path.join(session_dir, "skill-agent-gate-errors.jsonl"),
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "unhandled-exception",
                "error": repr(e),
            },
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
