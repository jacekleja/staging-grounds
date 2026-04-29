#!/usr/bin/env python3
"""One-shot migration: pending_human_review → issues.jsonl.

Thin CLI wrapper around issue_registry.convert_pending_human_review().
Separate file for self-documenting filename — used by caa-setup upgrade
(setup/src/upgrade.ts:854 runMigrationUpgrade) and by operators running
the migration manually.

Design reference: design-issue-queue.md §2 D11 step 6.

# per ISSUE-QUEUE Subtask 1 sketch Decision 11
"""

import argparse
import json
import os
import sys

# Allow running as script from project root or from bin/
sys.path.insert(0, os.path.dirname(__file__))

import issue_registry


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='issue_migrate_pending_human_review.py',
        description='Migrate pending_human_review entries from .study-state to issues.jsonl.',
    )
    parser.add_argument(
        '--study-state-path',
        dest='study_state_path',
        default='.claude/knowledge/.study-state',
        help='Path to .study-state YAML (default: .claude/knowledge/.study-state)',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        default=False,
        help='Apply migration (without --apply, dry-run only — shows what WOULD migrate)',
    )
    args = parser.parse_args()

    if not args.apply:
        print('Issue migrate: DRY RUN — pass --apply to execute migration', file=sys.stderr)

    try:
        result = issue_registry.convert_pending_human_review(
            study_state_path=args.study_state_path,
            apply=args.apply,
        )
    except issue_registry.IssueRegistryError as e:
        print(json.dumps({'error': str(e)}))
        return 2

    print(json.dumps(result))

    status = result.get('status', '')
    if status in ('ok', 'no-pending-review-key', 'empty-pending-review', 'dry-run'):
        if not args.apply:
            migrated = len(result.get('would_migrate') or [])
        else:
            migrated = result.get('migrated', 0)
        skipped = result.get('skipped_dedupe', 0)
        backup = result.get('backup_path') or 'N/A'
        label = 'DRY RUN — would migrate' if not args.apply else 'Migrated'
        print(
            f'Issue migrate: {label}: {migrated} | Skipped (dedupe): {skipped} | Backup: {backup}',
            file=sys.stderr,
        )
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(main())
