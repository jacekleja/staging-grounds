#!/usr/bin/env python3
"""
frame-clarify-cue.py — UserPromptSubmit hook that nudges the orchestrator toward
frame-clarification when a task statement looks unframed.

Pairs with the `## Frame-clarification` section in `.claude/orchestrator-prompt.md`:
the section carries the protocol; this hook is the cue mechanism. Cheap regex /
length heuristic, OR-combined for HIGH RECALL (better to nudge on a sufficient
task than to silently skip on an insufficient one) — the prompt-section itself
is the precision filter, not the hook.

On a hit, emits a one-shot `additionalContext` block via the Claude Code
UserPromptSubmit hook output schema. Banner literal `--- FRAME-CLARIFY CHECK ---`
is load-bearing — the orchestrator-prompt's section can reference it.

Silent-tracker discipline: always exits 0; never raises; emits nothing to
stdout unless a hit produced the additionalContext block.
"""

from __future__ import annotations

import json
import re
import sys


# ---------------------------------------------------------------------------
# Heuristic constants
# ---------------------------------------------------------------------------

# Hook is one-shot and operator-facing — the prompt-section IS the protocol.
BANNER_OPEN = "--- FRAME-CLARIFY CHECK ---"
BANNER_CLOSE = "--- END FRAME-CLARIFY CHECK ---"

# Pointer at the orchestrator-prompt section that carries the actual protocol.
PROMPT_SECTION_POINTER = ".claude/orchestrator-prompt.md § Frame-clarification"

# Length above which any prompt deserves a frame-clarify nudge regardless of
# shape (long task statements tend to carry compound unframed asks).
LENGTH_HIT_THRESHOLD = 300

# Minimum length below which the absence-of-scope predicate stays silent. Short
# prompts ("hi", "yes", "fix the typo") should never trip the nudge — only
# medium-or-longer prompts that ALSO lack scope language.
SCOPE_ABSENCE_MIN_LENGTH = 80

# Design-shape: matches the canonical unframed-task pattern
#   "design a system that ..." / "build an app that ..." /
#   "create the X that ..."  / "write a Y that ..."
# Case-insensitive; tolerates intervening words between the verb and the
# article (e.g., "design for me a system that ...").
DESIGN_SHAPE_RE = re.compile(
    r"\b(design|build|create|write)\b.{0,40}?\b(a|an|the)\s+\w+\s+that\b",
    re.IGNORECASE | re.DOTALL,
)

# Concrete-scope signals: when ANY of these appear, the operator named what
# they're operating on. Their presence defeats the absence-of-scope-language
# predicate (signal c) — but NOT signals (a) length or (b) design-shape.
#
#  - file path with extension: parser.py, vision.md, orchestrator-prompt.md
#  - inline code in backticks: `validator`, `D-user-intent-coverage`
#  - path fragment with slash: .claude/agents/foo.md, docs/vision.md
#  - CamelCase identifier suggesting a named symbol: ValidatorAgent, FrameClarify
SCOPE_PRESENT_RES = (
    re.compile(r"\b[\w./-]+\.(?:py|md|json|ts|js|tsx|jsx|sh|yaml|yml|toml)\b", re.IGNORECASE),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"\b[\w-]+/[\w./-]+\b"),
    re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+){1,}\b"),
)


# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------


def _has_scope_language(prompt: str) -> bool:
    """True if the prompt names something concrete the operator is operating on."""
    return any(rx.search(prompt) for rx in SCOPE_PRESENT_RES)


def _should_cue(prompt: str) -> tuple[bool, str]:
    """Apply the three OR-combined signals. Returns (hit, reason_token).

    Signals (HIGH-RECALL, LOW-PRECISION tuning per the design — the
    orchestrator-prompt section is the precision filter):

      (a) length         — prompt is longer than LENGTH_HIT_THRESHOLD chars
      (b) design-shape   — DESIGN_SHAPE_RE matches anywhere in the prompt
      (c) scope-absent   — prompt is at least SCOPE_ABSENCE_MIN_LENGTH chars
                           AND has no concrete-scope language

    Reason token is for telemetry / debuggability only; never reaches the
    orchestrator's view.
    """
    length = len(prompt)

    if length > LENGTH_HIT_THRESHOLD:
        return True, "length"

    if DESIGN_SHAPE_RE.search(prompt):
        return True, "design-shape"

    if length >= SCOPE_ABSENCE_MIN_LENGTH and not _has_scope_language(prompt):
        return True, "scope-absent"

    return False, ""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _emit_cue() -> None:
    """Print the additionalContext payload per the Claude Code hook output schema.

    Wire shape (matches what user-intent-capture's sibling hooks rely on):

        {
          "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "<banner + pointer text>"
          }
        }
    """
    body = (
        f"\n{BANNER_OPEN}\n"
        "The just-submitted prompt looks unframed (long, or carries a "
        "design-shape verb pattern, or lacks concrete scope).\n"
        f"Before dispatching any specialist, apply the framing-sufficiency "
        f"protocol in {PROMPT_SECTION_POINTER}.\n"
        "If framing is already sufficient, proceed normally — this is a cue, not a gate.\n"
        f"{BANNER_CLOSE}\n"
    )
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": body,
        }
    }
    print(json.dumps(payload))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        # Malformed or empty stdin — nothing to evaluate, silent exit.
        return

    raw_prompt = event.get("prompt", "")
    if not isinstance(raw_prompt, str):
        return
    prompt = raw_prompt.strip()
    if not prompt:
        return

    hit, _reason = _should_cue(prompt)
    if hit:
        _emit_cue()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-open: a UserPromptSubmit hook crashing must not interfere with the
        # session. Silent exit 0; the orchestrator simply does not see the cue.
        pass
    sys.exit(0)
