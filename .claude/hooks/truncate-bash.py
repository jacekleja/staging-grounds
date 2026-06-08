#!/usr/bin/env python3
# dispatch-child-safe: true
"""
PreToolUse hook for Bash: truncates output of commands likely to produce
large output. Safety net for when agents bypass smart_bash.

Returns JSON with updatedInput to rewrite the command, or empty to pass through.
"""

import json
import sys
import re

# Commands likely to produce large output, mapped to truncation strategies
LARGE_OUTPUT_COMMANDS = [
    # Build tools
    (r'\b(npm|yarn|pnpm)\s+(run\s+)?(build|compile|bundle)', 'tail', 30),
    (r'\b(tsc|webpack|vite|esbuild|rollup)\b', 'tail', 30),
    (r'\bmake\b(?!\s+-[nq])', 'tail', 30),
    (r'\bcargo\s+build\b', 'tail', 30),
    # Test runners
    (r'\b(pytest|python\s+-m\s+pytest)\b', 'tail', 50),
    (r'\b(npm|yarn|pnpm)\s+(run\s+)?test\b', 'tail', 50),
    (r'\b(vitest|jest|mocha)\b', 'tail', 50),
    # Package managers (install)
    (r'\b(npm|yarn|pnpm)\s+install\b', 'tail', 10),
    (r'\bpip\s+install\b', 'tail', 10),
    (r'\bcargo\s+(install|fetch)\b', 'tail', 10),
    (r'\bapt(-get)?\s+install\b', 'tail', 10),
    # Log dumps
    (r'\b(cat|less|more)\s+.*\.(log|out|err)\b', 'headtail', 20),
    (r'\bjournalctl\b', 'tail', 30),
    (r'\bdocker\s+logs\b', 'tail', 30),
    # Recursive listings
    (r'\bfind\s+', 'head', 50),
    (r'\bls\s+-[^\s]*R', 'head', 50),
    (r'\btree\b', 'head', 50),
]

MAX_OUTPUT_LINES = 200


def should_truncate(command: str) -> tuple[str, int] | None:
    """Check if a command is likely to produce large output.
    Returns (strategy, line_count) or None."""
    for pattern, strategy, lines in LARGE_OUTPUT_COMMANDS:
        if re.search(pattern, command):
            return (strategy, lines)
    return None


def rewrite_command(command: str, strategy: str, lines: int) -> str:
    """Rewrite a command to truncate its output."""
    # Don't double-wrap if already piped to head/tail
    if re.search(r'\|\s*(head|tail|grep|awk|sed|wc)\b', command):
        return command

    # Don't wrap if output is already redirected
    if re.search(r'[12]?\s*>(?!&)', command):
        return command

    if strategy == 'tail':
        return f'{{ {command}; }} 2>&1 | tail -n {lines}'
    elif strategy == 'head':
        return f'{{ {command}; }} 2>&1 | head -n {lines}'
    elif strategy == 'headtail':
        return (
            f'{{ {command}; }} 2>&1 | '
            f'{{ head -n {lines}; echo "\\n... [output truncated by hook] ..."; tail -n {lines}; }}'
        )

    return command


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = event.get('tool_name', '')
    if tool_name != 'Bash':
        return

    tool_input = event.get('tool_input', {})
    command = tool_input.get('command', '')

    if not command:
        return

    result = should_truncate(command)
    if result is None:
        return

    strategy, lines = result
    new_command = rewrite_command(command, strategy, lines)

    if new_command != command:
        output = {
            'decision': 'approve',
            'updatedInput': {
                'command': new_command,
            },
        }
        print(json.dumps(output))


if __name__ == '__main__':
    main()
