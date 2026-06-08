"""caa_active_children.py — Build the ACTIVE L2 CHILDREN runbook block.

Internal-private helper used by both launchers (bin/claude-session and
bin/caa/launcher.py) to inject active-child re-attach instructions into
cycle-resume runbooks. Not part of the public API.
"""

import json
import os


def _build_active_children_block(session_dir: str) -> str:
    """Return the ACTIVE L2 CHILDREN block string for a cycle-resume runbook.

    Reads {session_dir}/children-registry.json and formats one entry per row
    where status == "active", sorted by child_id for determinism.

    Returns "" (empty string) on: absent file, malformed JSON, empty registry,
    zero active rows, or any other read failure. Never raises.
    """
    registry_path = os.path.join(session_dir, 'children-registry.json')
    try:
        with open(registry_path, encoding='utf-8') as _f:
            registry = json.load(_f)
        if not isinstance(registry, dict):
            return ''
    except Exception:
        return ''

    active_rows = [
        (child_id, row)
        for child_id, row in registry.items()
        if isinstance(row, dict) and row.get('status') == 'active'
    ]
    if not active_rows:
        return ''

    active_rows.sort(key=lambda x: x[0])

    lines = [
        '\n--- ACTIVE L2 CHILDREN ---',
        'The following L2 children were spawned by the previous episode and may still be running.'
        ' Re-establish Monitors on each before consuming new work:',
    ]
    for child_id, row in active_rows:
        child_session_dir = row.get('child_session_dir', '')
        task_title = row.get('task_title') or '(untitled)'
        lines.append(f'- child_id: {child_id}')
        lines.append(f'  child_session_dir: {child_session_dir}')
        lines.append(f'  task_title: {task_title}')
        lines.append(
            f'  Monitor terminal events: bin/suborch-status-pull.py'
            f' --child-session-dir {child_session_dir}'
            f' --filter completed,failed,attention-required --follow'
        )
        lines.append(
            f'  Monitor parent-messages: tail -F {session_dir}/parent-messages/{child_id}/parent-messages.jsonl'
        )
    lines.append('')
    return '\n'.join(lines) + '\n'
