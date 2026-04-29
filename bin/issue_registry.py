#!/usr/bin/env python3
"""Issue queue registry — durable JSONL storage for addressable project issues.

Implements the storage layer defined in design-issue-queue.md §2 D1+D2+D11 and
the full API surface specified in plan-ISSUE-QUEUE-subtask-1.md.

Mirrors bin/orphan_registry.py for concurrency primitives:
- LOCK_EX for writes/state mutations; LOCK_SH for reads.
- Atomic-rewrite: read → mutate in memory → tempfile + os.replace (mirrors
  orphan_registry._atomic_rewrite at :181-204).
- JSONL append under LOCK_EX with pre-write dedupe scan inside the same lock
  window (mirrors orphan_registry.record_orphan at :283-292).

Storage:
- .agent_context/issues.jsonl (main repo, NOT worktree-local)
- .agent_context/issues.jsonl.lock (companion lock file)

PROJECT_ROOT detection mirrors orphan_registry._resolve_main_root but adds
PROJECT_ROOT env-var fast-path (Priority 0) per mcp-server/architecture.md
§ Project-root detection.
"""

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ISSUE_REL_PATH = pathlib.Path('.agent_context') / 'issues.jsonl'
_ISSUE_LOCK_REL_PATH = pathlib.Path('.agent_context') / 'issues.jsonl.lock'

# Closed enums — per ISSUE-QUEUE Subtask 1 sketch Decision 6
STATUS_VALUES = frozenset({'open', 'triaged', 'in-progress', 'resolved', 'wont-fix', 'duplicate'})
SEVERITY_VALUES = frozenset({'low', 'med', 'high'})

# Internal aliases that module consumers may also reference
_VALID_STATUS = STATUS_VALUES
_VALID_SEVERITY = SEVERITY_VALUES
_TERMINAL_STATUS = frozenset({'resolved', 'wont-fix', 'duplicate'})
_REQUIRED_CLOSURE_REASON_STATUSES = frozenset({'resolved', 'wont-fix', 'duplicate'})

_MAX_SUMMARY_BYTES = 4096


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IssueRegistryError(Exception):
    pass


class MalformedRecordError(IssueRegistryError):
    pass


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _issue_path(main_root: pathlib.Path) -> pathlib.Path:
    return main_root / '.agent_context' / 'issues.jsonl'


def _lock_path(main_root: pathlib.Path) -> pathlib.Path:
    return main_root / '.agent_context' / 'issues.jsonl.lock'


def _resolve_main_root() -> pathlib.Path:
    """Resolve the main repo root for storage.

    Priority 0: PROJECT_ROOT env var (if set and directory exists).
    Priority 1: git rev-parse --git-common-dir walk-up (mirrors
      orphan_registry._resolve_main_root at :77-90; auto-resolves to main
      even when invoked from a worktree — load-bearing for D1 main-shared
      storage without depending on symlink wiring).

    Raises IssueRegistryError if root cannot be determined.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 2
    """
    env = os.environ.get('PROJECT_ROOT')
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env).resolve()

    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--git-common-dir'],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except Exception as e:
        raise IssueRegistryError(f'issue_registry: cannot resolve main root: {e}') from e
    return pathlib.Path(out).resolve().parent


def _compute_id(created_at: str, dedupe_key: str) -> str:
    """Compute issue ID as iss_<first-12-hex-of-sha256(created_at + dedupe_key)>.

    48-bit space keeps birthday-collision probability below 1e-6 up to ~100k records.
    # per ISSUE-QUEUE Subtask 1 sketch Decision 5; entropy extended from 8→12 hex digits
    """
    digest = hashlib.sha256((created_at + dedupe_key).encode('utf-8')).hexdigest()
    return f'iss_{digest[:12]}'


@contextmanager
def _open_locked(main_root: pathlib.Path, exclusive: bool) -> Iterator[pathlib.Path]:
    """Hold LOCK_EX or LOCK_SH on the companion lock file.

    Mirrors orphan_registry._open_locked at :133-153.
    Lock is on the companion file (not the data file) so os.replace inode-swap
    on atomic-rewrite does not defeat the lock.
    Yields the issue data-file path (caller opens it as needed).
    """
    issue_p = _issue_path(main_root)
    lock_p = _lock_path(main_root)
    issue_p.parent.mkdir(parents=True, exist_ok=True)
    lk_fd = os.open(str(lock_p), os.O_CREAT | os.O_RDWR, 0o644)
    lk_fh = os.fdopen(lk_fd, 'r+')
    try:
        fcntl.flock(lk_fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield issue_p
    finally:
        fcntl.flock(lk_fh, fcntl.LOCK_UN)
        lk_fh.close()


def _read_records(issue_p: pathlib.Path) -> list:
    """Read all JSONL records from issue_p. Returns [] on ENOENT.

    Malformed lines are skipped with a stderr WARNING; parsing continues.
    Mirrors orphan_registry._read_records at :156-178.
    """
    if not issue_p.exists():
        return []
    records = []
    with open(str(issue_p), 'r', encoding='utf-8') as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(
                    f'issue_registry: skipping malformed line {lineno}: {str(e)[:80]}',
                    file=sys.stderr,
                )
                continue
            records.append(rec)
    return records


def _atomic_rewrite(issue_p: pathlib.Path, records: list) -> None:
    """Write records atomically via tempfile + os.replace + fsync.

    Mirrors orphan_registry._atomic_rewrite at :181-204.
    """
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f'.issues.jsonl.tmp.{os.getpid()}.',
            dir=str(issue_p.parent),
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(issue_p))
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _apply_filter(records: list, filt: Optional[dict]) -> list:
    """Apply filter dict to a list of records. Returns matching records.

    Supported filter keys: id (str), status (str or list), severity (str or list),
    tags (str or list, any-match), since (ISO-8601 str), dedupe_key (str),
    origin_agent (str).
    """
    if not filt:
        return records

    result = records

    id_filter = filt.get('id')
    if id_filter:
        result = [r for r in result if r.get('id') == id_filter]

    status_filter = filt.get('status')
    if status_filter:
        if isinstance(status_filter, str):
            status_filter = [status_filter]
        result = [r for r in result if r.get('status') in status_filter]

    severity_filter = filt.get('severity')
    if severity_filter:
        if isinstance(severity_filter, str):
            severity_filter = [severity_filter]
        result = [r for r in result if r.get('severity') in severity_filter]

    tags_filter = filt.get('tags')
    if tags_filter:
        if isinstance(tags_filter, str):
            tags_filter = [tags_filter]
        result = [r for r in result if any(t in r.get('tags', []) for t in tags_filter)]

    since_filter = filt.get('since')
    if since_filter:
        result = [r for r in result if r.get('created_at', '') >= since_filter]

    dedupe_key_filter = filt.get('dedupe_key')
    if dedupe_key_filter:
        result = [r for r in result if r.get('dedupe_key') == dedupe_key_filter]

    origin_agent_filter = filt.get('origin_agent')
    if origin_agent_filter:
        result = [r for r in result
                  if r.get('origin', {}).get('agent') == origin_agent_filter]

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def file_issue(
    title: str,
    summary: str,
    severity: str,
    dedupe_key: str,
    origin: dict,
    tags: Optional[list] = None,
    suggested_approach: Optional[str] = None,
    derived_from_finding_id: Optional[str] = None,
    related_artifacts: Optional[list] = None,
    main_root: Optional[pathlib.Path] = None,
) -> dict:
    """Append a new issue record to issues.jsonl under LOCK_EX.

    Returns {id, created: True, log_file} on success.
    Returns {id: None, error: 'dedupe-collision', existing_id} if an open record
    with the same dedupe_key already exists.
    Returns {id: None, error: 'invalid-severity'|'invalid-status', allowed: [...]}
    on validation failure.

    Pre-write dedupe scan executes inside the same LOCK_EX window as the append
    to prevent concurrent duplicate writes.

    # per ISSUE-QUEUE Subtask 1 sketch Decisions 3, 4, 5, 6
    """
    if severity not in _VALID_SEVERITY:
        return {'id': None, 'error': 'invalid-severity', 'allowed': sorted(_VALID_SEVERITY)}

    if not title or not title.strip():
        return {'id': None, 'error': 'invalid-title', 'message': 'title is required and must be non-empty'}

    if not summary or not summary.strip():
        return {'id': None, 'error': 'invalid-summary', 'message': 'summary is required and must be non-empty'}

    if not dedupe_key or not dedupe_key.strip():
        return {'id': None, 'error': 'invalid-dedupe-key', 'message': 'dedupe_key is required and must be non-empty'}

    if not origin or not isinstance(origin, dict):
        return {'id': None, 'error': 'invalid-origin', 'message': 'origin must be a non-empty dict'}

    if len(summary.encode('utf-8')) > _MAX_SUMMARY_BYTES:
        summary = summary.encode('utf-8')[:_MAX_SUMMARY_BYTES].decode('utf-8', errors='replace')

    if main_root is None:
        main_root = _resolve_main_root()

    created_at = _iso_now()
    issue_id = _compute_id(created_at, dedupe_key)

    record = {
        'id': issue_id,
        'title': title,
        'summary': summary,
        'severity': severity,
        'status': 'open',
        'tags': tags or [],
        'related_artifacts': related_artifacts or [],
        'suggested_approach': suggested_approach,
        'dedupe_key': dedupe_key,
        'origin': origin,
        'derived_from_finding_id': derived_from_finding_id,
        'created_at': created_at,
        'updated_at': created_at,
        'resolved_at': None,
        'resolved_by': None,
        'closure_reason': None,
        'reopen_reason': None,
    }

    log_file = str(_issue_path(main_root))

    with _open_locked(main_root, exclusive=True) as issue_p:
        # Pre-write dedupe scan inside the same LOCK_EX window (Decision 4).
        existing = _read_records(issue_p)
        collision = next(
            (r for r in existing
             if r.get('dedupe_key') == dedupe_key and r.get('status') not in _TERMINAL_STATUS),
            None,
        )
        if collision:
            return {'id': None, 'error': 'dedupe-collision', 'existing_id': collision['id']}

        with open(str(issue_p), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')

    return {'id': issue_id, 'created': True, 'log_file': log_file}


def get_open(
    filt: Optional[dict] = None,
    main_root: Optional[pathlib.Path] = None,
) -> list:
    """Return open issues, optionally filtered.

    Reads under LOCK_SH. Defaults to status=open only unless filter overrides.
    Filter keys: status, severity, tags, since, dedupe_key, origin_agent.

    # per ISSUE-QUEUE Subtask 1 sketch Decision (get_open spec in plan)
    """
    if main_root is None:
        main_root = _resolve_main_root()

    with _open_locked(main_root, exclusive=False) as issue_p:
        records = _read_records(issue_p)

    # Default to open-only unless caller overrides
    if not filt or 'status' not in filt:
        base_filt = dict(filt or {})
        base_filt.setdefault('status', 'open')
        return _apply_filter(records, base_filt)

    return _apply_filter(records, filt)


def query(
    filt: Optional[dict] = None,
    main_root: Optional[pathlib.Path] = None,
) -> list:
    """Return issues matching filter with full-status support.

    Same as get_open but does NOT default to status=open.
    Reads under LOCK_SH.

    # per ISSUE-QUEUE Subtask 1 sketch Decision (query spec in plan)
    """
    if main_root is None:
        main_root = _resolve_main_root()

    with _open_locked(main_root, exclusive=False) as issue_p:
        records = _read_records(issue_p)

    return _apply_filter(records, filt)


def resolve(
    issue_id: str,
    closure_reason: str,
    resolved_by: str,
    main_root: Optional[pathlib.Path] = None,
) -> dict:
    """Mark an issue resolved via atomic-rewrite.

    Sets status=resolved, resolved_at, resolved_by, closure_reason.
    Returns {id, status, resolved_at} on success.
    Returns {error: ...} on not-found or already-terminal.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 6
    """
    if not closure_reason or not closure_reason.strip():
        return {'error': 'closure-reason-required', 'message': 'closure_reason must be non-empty for resolve'}

    if not resolved_by or not resolved_by.strip():
        return {'error': 'resolved-by-required', 'message': 'resolved_by must be non-empty'}

    if main_root is None:
        main_root = _resolve_main_root()

    with _open_locked(main_root, exclusive=True) as issue_p:
        records = _read_records(issue_p)
        target = next((r for r in records if r.get('id') == issue_id), None)
        if target is None:
            return {'error': 'not-found', 'id': issue_id}

        if target.get('status') in _TERMINAL_STATUS:
            return {'error': 'already-terminal', 'id': issue_id, 'status': target.get('status')}

        now = _iso_now()
        target['status'] = 'resolved'
        target['resolved_at'] = now
        target['resolved_by'] = resolved_by
        target['closure_reason'] = closure_reason
        target['updated_at'] = now

        _atomic_rewrite(issue_p, records)

    return {'id': issue_id, 'status': 'resolved', 'resolved_at': now}


def update(
    issue_id: str,
    main_root: Optional[pathlib.Path] = None,
    **fields,
) -> dict:
    """Update mutable fields on an issue via atomic-rewrite.

    Rejects mutations to terminal records UNLESS the call includes
    status='open' AND reopen_reason=<non-empty> in the same call.

    Special handling for add_tag / remove_tag lists.
    Returns {id, updated_fields, status} on success.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 7
    """
    if main_root is None:
        main_root = _resolve_main_root()

    new_status = fields.get('status')
    reopen_reason = fields.get('reopen_reason')

    if new_status is not None and new_status not in _VALID_STATUS:
        return {'error': 'invalid-status', 'allowed': sorted(_VALID_STATUS)}

    new_severity = fields.get('severity')
    if new_severity is not None and new_severity not in _VALID_SEVERITY:
        return {'error': 'invalid-severity', 'allowed': sorted(_VALID_SEVERITY)}

    with _open_locked(main_root, exclusive=True) as issue_p:
        records = _read_records(issue_p)
        target = next((r for r in records if r.get('id') == issue_id), None)
        if target is None:
            return {'error': 'not-found', 'id': issue_id}

        # Terminal-record guard (Decision 7)
        if target.get('status') in _TERMINAL_STATUS:
            is_reopen = (new_status == 'open' and reopen_reason and reopen_reason.strip())
            if not is_reopen:
                return {
                    'error': 'terminal-record-mutation',
                    'id': issue_id,
                    'current_status': target.get('status'),
                    'message': "Reopen with status='open' and reopen_reason=<non-empty> in same call",
                }
            # Reopen path: clear terminal fields
            target['status'] = 'open'
            target['reopen_reason'] = reopen_reason
            target['resolved_at'] = None
            target['resolved_by'] = None
            target['closure_reason'] = None
            updated_fields = ['status', 'reopen_reason', 'resolved_at', 'resolved_by', 'closure_reason']
        else:
            updated_fields = []

            if new_status is not None:
                # Closing transitions require closure_reason
                if new_status in _REQUIRED_CLOSURE_REASON_STATUSES:
                    closure_reason = fields.get('closure_reason')
                    if not closure_reason or not closure_reason.strip():
                        return {
                            'error': 'closure-reason-required',
                            'message': f"closure_reason required when status={new_status!r}",
                        }
                    target['closure_reason'] = closure_reason
                    updated_fields.append('closure_reason')
                target['status'] = new_status
                updated_fields.append('status')

            if new_severity is not None:
                target['severity'] = new_severity
                updated_fields.append('severity')

            closure_reason_field = fields.get('closure_reason')
            if closure_reason_field is not None and 'closure_reason' not in updated_fields:
                target['closure_reason'] = closure_reason_field
                updated_fields.append('closure_reason')

            if reopen_reason is not None and 'reopen_reason' not in updated_fields:
                target['reopen_reason'] = reopen_reason
                updated_fields.append('reopen_reason')

            for key in ('title', 'summary', 'suggested_approach', 'notes'):
                if key in fields:
                    target[key] = fields[key]
                    updated_fields.append(key)

            add_tags = fields.get('add_tag') or fields.get('add_tags') or []
            if isinstance(add_tags, str):
                add_tags = [add_tags]
            remove_tags = fields.get('remove_tag') or fields.get('remove_tags') or []
            if isinstance(remove_tags, str):
                remove_tags = [remove_tags]
            if add_tags or remove_tags:
                current_tags = list(target.get('tags', []))
                for t in add_tags:
                    if t not in current_tags:
                        current_tags.append(t)
                for t in remove_tags:
                    if t in current_tags:
                        current_tags.remove(t)
                target['tags'] = current_tags
                if add_tags or remove_tags:
                    updated_fields.append('tags')

        target['updated_at'] = _iso_now()
        _atomic_rewrite(issue_p, records)

    return {'id': issue_id, 'updated_fields': updated_fields, 'status': target.get('status')}


def convert_pending_human_review(
    study_state_path: str,
    apply: bool = True,
    main_root: Optional[pathlib.Path] = None,
) -> dict:
    """Migrate pending_human_review entries from .study-state to issues.jsonl.

    Edge-case handlers (Decision 8):
    1. Backup already exists: overwrite + stderr log.
    2. pending_human_review key absent: no-op, no backup.
    3. pending_human_review is empty list []: treat same as absent (key is removed
       from study-state for idempotent re-run convergence).
    4. YAML parse fails: raise IssueRegistryError + caller exits 2.

    Ordering: parse YAML → write backup → iterate entries → file_issue per entry
    (dedupe collisions swallowed) → write .study-state back without
    pending_human_review key.

    When apply=False (dry-run), reports what WOULD migrate without mutation.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 8
    """
    if not _YAML_AVAILABLE:
        raise IssueRegistryError(
            'issue_registry: PyYAML is required for migration. Install it: pip install PyYAML'
        )

    study_path = pathlib.Path(study_state_path)
    if not study_path.exists():
        raise IssueRegistryError(
            f'issue_registry: study-state file not found: {study_state_path}'
        )

    # Case 4: parse YAML
    try:
        with open(str(study_path), 'r', encoding='utf-8') as fh:
            state = yaml.safe_load(fh)
    except Exception as e:
        raise IssueRegistryError(f'cannot parse {study_state_path}: {e}') from e

    if state is None:
        state = {}

    # Cases 2 and 3: key absent or empty list
    pending = state.get('pending_human_review')
    if pending is None:
        print('issue_migrate: no pending_human_review key found — nothing to migrate', file=sys.stderr)
        return {'migrated': 0, 'skipped_dedupe': 0, 'errors': [], 'backup_path': None, 'status': 'no-pending-review-key'}

    if not pending:  # empty list []
        print('issue_migrate: pending_human_review is empty — removing key and exiting', file=sys.stderr)
        if apply:
            del state['pending_human_review']
            with open(str(study_path), 'w', encoding='utf-8') as fh:
                yaml.safe_dump(state, fh, default_flow_style=False, allow_unicode=True)
        return {'migrated': 0, 'skipped_dedupe': 0, 'errors': [], 'backup_path': None, 'status': 'empty-pending-review'}

    # Case 1: write backup before mutation
    backup_path = study_path.parent / (study_path.name + '.pre-issue-migration')
    if not apply:
        # Dry-run: report candidates without mutation
        candidates = []
        for entry in pending:
            topic = entry.get('topic') or entry.get('file') or ''
            flagged_at = entry.get('flagged_at', '')
            candidates.append({
                'topic': topic,
                'dedupe_key': f'hygiene:{topic}:{flagged_at}',
                'entry': entry,
            })
        return {
            'migrated': 0,
            'skipped_dedupe': 0,
            'errors': [],
            'backup_path': str(backup_path),
            'status': 'dry-run',
            'would_migrate': candidates,
        }

    if backup_path.exists():
        print(f'issue_migrate: overwriting existing {backup_path.name} backup', file=sys.stderr)
    with open(str(backup_path), 'w', encoding='utf-8') as fh:
        yaml.safe_dump(state, fh, default_flow_style=False, allow_unicode=True)

    if main_root is None:
        main_root = _resolve_main_root()

    migrated = 0
    skipped_dedupe = 0
    errors = []

    for entry in pending:
        topic = entry.get('topic') or entry.get('file') or ''
        flagged_at = entry.get('flagged_at', '')
        run_id = entry.get('run_id') or entry.get('hygiene_run_id') or ''
        reason = entry.get('reason') or entry.get('error_message') or 'flagged for human review'

        dedupe_key = f'hygiene:{topic}:{flagged_at}'
        origin = {
            'agent': 'knowledge-triager',
            'hygiene_run_id': run_id,
            'filed_at_round': None,
            'session_id': None,
            'finding_id': None,
        }

        result = file_issue(
            title=f'Knowledge review: {topic}',
            summary=reason,
            severity='med',
            dedupe_key=dedupe_key,
            origin=origin,
            tags=['hygiene', 'knowledge-curation'],
            main_root=main_root,
        )

        if result.get('id') is not None:
            migrated += 1
        elif result.get('error') == 'dedupe-collision':
            skipped_dedupe += 1
        else:
            errors.append({'entry': entry, 'error': result})

    # ALWAYS remove pending_human_review key after successful migration (Decision 8 step 6)
    del state['pending_human_review']
    with open(str(study_path), 'w', encoding='utf-8') as fh:
        yaml.safe_dump(state, fh, default_flow_style=False, allow_unicode=True)

    return {
        'migrated': migrated,
        'skipped_dedupe': skipped_dedupe,
        'errors': errors,
        'backup_path': str(backup_path),
        'status': 'ok',
    }


# ---------------------------------------------------------------------------
# CLI implementation
# ---------------------------------------------------------------------------

def _cmd_file(args: argparse.Namespace, main_root: Optional[pathlib.Path] = None) -> int:
    """Handle 'file' subcommand.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 9
    """
    tags = [t.strip() for t in args.tags.split(',') if t.strip()] if args.tags else []
    related_artifacts = (
        [a.strip() for a in args.related_artifacts.split(',') if a.strip()]
        if args.related_artifacts else []
    )

    origin = {
        'agent': args.origin_agent,
        'session_id': args.origin_session_id,
        'finding_id': args.origin_finding_id,
        'hygiene_run_id': args.origin_hygiene_run_id,
        'filed_at_round': args.origin_filed_at_round,
    }

    result = file_issue(
        title=args.title,
        summary=args.summary,
        severity=args.severity,
        dedupe_key=args.dedupe_key,
        origin=origin,
        tags=tags,
        suggested_approach=args.suggested_approach,
        derived_from_finding_id=args.derived_from_finding_id,
        related_artifacts=related_artifacts,
        main_root=main_root,
    )
    print(json.dumps(result))

    if result.get('id') is not None:
        return 0
    # All error cases from file_issue (dedupe-collision, invalid-severity,
    # invalid-title, missing required field) are business-logic errors → exit 1.
    return 1


def _cmd_resolve(args: argparse.Namespace, main_root: Optional[pathlib.Path] = None) -> int:
    """Handle 'resolve' subcommand.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 9
    """
    result = resolve(
        issue_id=args.id,
        closure_reason=args.closure_reason,
        resolved_by=args.resolved_by,
        main_root=main_root,
    )
    print(json.dumps(result))
    return 0 if 'error' not in result else 1


def _cmd_update(args: argparse.Namespace, main_root: Optional[pathlib.Path] = None) -> int:
    """Handle 'update' subcommand.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 9
    """
    fields = {}
    if args.status:
        fields['status'] = args.status
    if args.severity:
        fields['severity'] = args.severity
    if args.reopen_reason:
        fields['reopen_reason'] = args.reopen_reason
    if args.closure_reason:
        fields['closure_reason'] = args.closure_reason
    if args.add_tag:
        fields['add_tag'] = args.add_tag
    if args.remove_tag:
        fields['remove_tag'] = args.remove_tag

    result = update(issue_id=args.id, main_root=main_root, **fields)
    print(json.dumps(result))

    error = result.get('error', '')
    if error in ('terminal-record-mutation', 'closure-reason-required', 'invalid-status', 'invalid-severity'):
        return 1
    return 0 if 'updated_fields' in result else 2


def _cmd_query(args: argparse.Namespace, main_root: Optional[pathlib.Path] = None) -> int:
    """Handle 'query' subcommand.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 9
    """
    filt: dict = {}

    if getattr(args, 'all', False):
        pass  # No status filter when --all is given
    elif args.status:
        filt['status'] = args.status
    else:
        filt['status'] = 'open'

    if args.severity:
        filt['severity'] = args.severity
    if args.tag:
        filt['tags'] = args.tag
    if args.since:
        filt['since'] = args.since
    if args.dedupe_key:
        filt['dedupe_key'] = args.dedupe_key
    if args.origin_agent:
        filt['origin_agent'] = args.origin_agent

    records = query(filt=filt if filt else None, main_root=main_root)
    limit = args.limit if args.limit and args.limit > 0 else 100
    truncated = len(records) > limit
    records = records[:limit]
    print(json.dumps({'records': records, 'count': len(records), 'truncated': truncated}))
    return 0


def _cmd_migrate(args: argparse.Namespace, main_root: Optional[pathlib.Path] = None) -> int:
    """Handle 'migrate' subcommand.

    # per ISSUE-QUEUE Subtask 1 sketch Decision 9 + Decision 11
    """
    study_state_path = args.study_state_path or '.claude/knowledge/.study-state'
    apply = getattr(args, 'apply', False)

    try:
        result = convert_pending_human_review(
            study_state_path=study_state_path,
            apply=apply,
            main_root=main_root,
        )
    except IssueRegistryError as e:
        print(json.dumps({'error': str(e)}))
        return 2
    except FileNotFoundError as e:
        print(json.dumps({'error': str(e)}))
        return 1

    print(json.dumps(result))
    return 0 if result.get('status') in ('ok', 'no-pending-review-key', 'empty-pending-review', 'dry-run') else 1


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog='issue_registry.py',
        description='Issue queue registry — file, resolve, update, query, migrate issues.',
    )
    subs = parser.add_subparsers(dest='subcommand', metavar='<subcommand>')
    subs.required = True

    # --- file ---
    p_file = subs.add_parser('file', help='File a new issue')
    p_file.add_argument('--title', required=True, help='Issue title')
    p_file.add_argument('--summary', required=True, help='Issue summary')
    p_file.add_argument('--severity', required=True, choices=sorted(_VALID_SEVERITY),
                        help='Severity: low|med|high')
    p_file.add_argument('--dedupe-key', required=True, dest='dedupe_key',
                        help='Deduplication key (natural key; free-form)')
    p_file.add_argument('--origin-agent', required=True, dest='origin_agent',
                        help='Agent filing this issue')
    p_file.add_argument('--tags', default='', help='Comma-separated tags')
    p_file.add_argument('--suggested-approach', dest='suggested_approach', default=None)
    p_file.add_argument('--origin-session-id', dest='origin_session_id', default=None)
    p_file.add_argument('--origin-finding-id', dest='origin_finding_id', default=None)
    p_file.add_argument('--origin-hygiene-run-id', dest='origin_hygiene_run_id', default=None)
    p_file.add_argument('--origin-filed-at-round', dest='origin_filed_at_round', default=None)
    p_file.add_argument('--derived-from-finding-id', dest='derived_from_finding_id', default=None)
    p_file.add_argument('--related-artifacts', dest='related_artifacts', default=None,
                        help='Comma-separated artifact paths')

    # --- resolve ---
    p_resolve = subs.add_parser('resolve', help='Resolve an issue')
    p_resolve.add_argument('--id', required=True, help='Issue ID (iss_xxxxxxxx)')
    p_resolve.add_argument('--closure-reason', required=True, dest='closure_reason',
                           help='Reason for resolution')
    p_resolve.add_argument('--resolved-by', required=True, dest='resolved_by',
                           help='Agent or human resolving the issue')

    # --- update ---
    p_update = subs.add_parser('update', help='Update issue fields')
    p_update.add_argument('--id', required=True, help='Issue ID')
    p_update.add_argument('--status', choices=sorted(_VALID_STATUS), default=None)
    p_update.add_argument('--severity', choices=sorted(_VALID_SEVERITY), default=None)
    p_update.add_argument('--reopen-reason', dest='reopen_reason', default=None)
    p_update.add_argument('--closure-reason', dest='closure_reason', default=None)
    p_update.add_argument('--add-tag', dest='add_tag', action='append', default=[],
                          metavar='TAG')
    p_update.add_argument('--remove-tag', dest='remove_tag', action='append', default=[],
                          metavar='TAG')

    # --- query ---
    p_query = subs.add_parser('query', help='Query issues')
    p_query.add_argument('--status', choices=sorted(_VALID_STATUS), default=None)
    p_query.add_argument('--all', action='store_true', default=False,
                         help='Return all statuses (overrides --status)')
    p_query.add_argument('--severity', choices=sorted(_VALID_SEVERITY), default=None)
    p_query.add_argument('--tag', action='append', default=[], metavar='TAG',
                         help='Filter by tag (any-match; repeatable)')
    p_query.add_argument('--since', default=None, help='ISO-8601 lower bound on created_at')
    p_query.add_argument('--dedupe-key', dest='dedupe_key', default=None)
    p_query.add_argument('--origin-agent', dest='origin_agent', default=None)
    p_query.add_argument('--limit', type=int, default=100)

    # --- migrate ---
    p_migrate = subs.add_parser('migrate', help='Migrate pending_human_review from .study-state')
    p_migrate.add_argument('--study-state-path', dest='study_state_path', default=None,
                           help='Path to .study-state YAML (default: .claude/knowledge/.study-state)')
    p_migrate.add_argument('--apply', action='store_true', default=False,
                           help='Apply migration (without --apply, dry-run only)')

    return parser


def main() -> int:
    """CLI entrypoint. All subcommands emit JSON to stdout; errors go to stderr.

    Exit codes: 0=success, 1=validation/business-logic, 2=internal/IO/parse.
    """
    parser = _build_parser()
    args = parser.parse_args()

    try:
        main_root = _resolve_main_root()
    except IssueRegistryError as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        return 2

    dispatch = {
        'file': _cmd_file,
        'resolve': _cmd_resolve,
        'update': _cmd_update,
        'query': _cmd_query,
        'migrate': _cmd_migrate,
    }
    handler = dispatch.get(args.subcommand)
    if handler is None:
        print(json.dumps({'error': f'unknown subcommand: {args.subcommand}'}), file=sys.stderr)
        return 1

    return handler(args, main_root)


if __name__ == '__main__':
    sys.exit(main())
