#!/usr/bin/env python3
# dispatch-child-safe: true
"""PostToolUse hook: findings-shape issue warning.

Fires on every PostToolUse event for mcp__context-tools__findings.
If the findings call's tags include 'gap', 'coupling', or 'surprise' AND
the content field matches a remedial-shape regex, injects an additionalContext
advisory suggesting the caller file an issue instead.

Soft warn only — never blocks. Always exits 0.
"""
import json
import re
import sys

# Tags that suggest issue-shaped content
TRIGGER_TAGS = {"gap", "coupling", "surprise"}

# Regex matching remedial-shape content (case-insensitive)
REMEDIAL_RE = re.compile(
    r"should be fixed|needs fix|broken|TODO",
    re.IGNORECASE,
)

ADVISORY = (
    "This finding looks issue-shaped (remediable problem). "
    "Consider filing via issues(action='file', ...) instead "
    "-- see orchestrator-prompt §M."
)


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return

    tool_name = event.get("tool_name", "")
    if tool_name != "mcp__context-tools__findings":
        return

    tool_input = event.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return

    tags = tool_input.get("tags")
    content = tool_input.get("content", "")

    if not isinstance(tags, list) or not isinstance(content, str):
        return

    tag_set = set(tags)
    if tag_set.isdisjoint(TRIGGER_TAGS):
        return

    if not REMEDIAL_RE.search(content):
        return

    print(json.dumps({
        "continue": True,
        "additionalContext": ADVISORY,
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
