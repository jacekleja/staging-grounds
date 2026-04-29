#!/usr/bin/env python3
"""Cross-session orphan commit registry (axis-4 / Phase C of WR-PHASE2-PREVENT).

Durable JSONL registry of commits that were either quarantined by the
launcher's reachability gate (scope_label='worktree-quarantine') or
explicitly excluded from a scope-foreign push (scope_label='scope-foreign-push').

Storage: .agent_context/orphans.jsonl (worktree-local; NOT cross-worktree).
See .claude/knowledge/decisions/orphan-registry.md Decision C for the
worktree-local vs. main-shared tradeoff.

Concurrent-safety: LOCK_EX on companion lock file (orphans.jsonl.lock) for
writes and reconcile; LOCK_SH for reads. Per-record payload <4KB so OS-level
write(2) is PIPE_BUF-atomic as belt-and-braces; flock is the primary guard.
[verified: docs/path-c-invariants.md invariant 8]

TODO (Phase C.1 upgrade): adopt the Session-Id: git-trailer convention
so originating_session_id can be derived from the commit message
deterministically rather than passed as Optional[str]. See axis-4 §10 option (a).
"""

import fcntl
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORPHAN_REL_PATH = pathlib.Path('.agent_context') / 'orphans.jsonl'
_ORPHAN_LOCK_REL_PATH = pathlib.Path('.agent_context') / 'orphans.jsonl.lock'
_VALID_SCOPE_LABELS = frozenset({'worktree-quarantine', 'scope-foreign-push'})
_REQUIRED_RECORD_FIELDS = frozenset({
    'timestamp', 'originating_session_id', 'commit_sha',
    'scope_label', 'recovery_instructions', 'resolved',
})
_MAX_RECOVERY_INSTRUCTIONS_BYTES = 2048  # cap per axis-4 §10
_GIT_TIMEOUT = 3  # seconds for merge-base check


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OrphanRegistryError(Exception):
    pass


class MalformedRecordError(OrphanRegistryError):
    pass


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _orphan_path(main_root: pathlib.Path) -> pathlib.Path:
    return main_root / '.agent_context' / 'orphans.jsonl'


def _lock_path(main_root: pathlib.Path) -> pathlib.Path:
    return main_root / '.agent_context' / 'orphans.jsonl.lock'


def _resolve_main_root() -> pathlib.Path:
    """Walk up from cwd via git rev-parse --git-common-dir to find main root.

    Mirrors bin/claude-session:_resolve_project_root at lines 63-86.
    Raises OrphanRegistryError on git failure (e.g., not a git repo).
    """
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--git-common-dir'],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except Exception as e:
        raise OrphanRegistryError(f'orphan_registry: cannot resolve main root: {e}') from e
    return pathlib.Path(out).resolve().parent


def _detect_default_branch(main_root: pathlib.Path) -> str:
    """Return default remote branch name (e.g. 'master', 'main').

    Duplicated from bin/claude-session:_detect_default_branch at lines 135-160
    to keep orphan_registry.py standalone (importing from an extension-less
    file requires SourceFileLoader; coupling cost exceeds duplication cost).
    Falls back to 'master' on any failure.
    """
    try:
        result = subprocess.run(
            ['git', 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD'],
            cwd=str(main_root), capture_output=True, text=True, check=True, timeout=5,
        )
        ref = result.stdout.strip()
        if not ref:
            raise ValueError('empty ref')
        return ref.split('/', 1)[1] if '/' in ref else ref
    except Exception:
        return 'master'


def _is_ancestor(commit_sha: str, main_root: pathlib.Path) -> bool:
    """Return True if commit_sha is an ancestor of origin/<default_branch>.

    Uses git merge-base --is-ancestor with a conservative timeout.
    On timeout or error: returns False (treat as unreachable — write the record;
    reconcile() self-corrects on next resume).
    """
    branch = _detect_default_branch(main_root)
    try:
        result = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', commit_sha, f'origin/{branch}'],
            cwd=str(main_root), capture_output=True, timeout=_GIT_TIMEOUT,
        )
        return result.returncode == 0
    except Exception:
        # Conservative: treat as unreachable so the record is written.
        return False


@contextmanager
def _open_locked(main_root: pathlib.Path, exclusive: bool) -> Iterator[pathlib.Path]:
    """Hold LOCK_EX or LOCK_SH on the companion lock file.

    Mirrors session_registry._open_locked at lines 204-238.
    Yields the orphan data-file path (caller opens it as needed).
    Lock is on the COMPANION file (not the data file) so os.replace
    inode-swap on reconcile does not defeat the lock.
    """
    orphan_p = _orphan_path(main_root)
    lock_p = _lock_path(main_root)
    # Ensure parent exists so O_CREAT can succeed.
    orphan_p.parent.mkdir(parents=True, exist_ok=True)
    lk_fd = os.open(str(lock_p), os.O_CREAT | os.O_RDWR, 0o644)
    lk_fh = os.fdopen(lk_fd, 'r+')
    try:
        fcntl.flock(lk_fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield orphan_p
    finally:
        fcntl.flock(lk_fh, fcntl.LOCK_UN)
        lk_fh.close()


def _read_records(orphan_p: pathlib.Path) -> list[dict]:
    """Read all JSONL records from orphan_p. Returns [] on ENOENT.

    Malformed lines are skipped with a stderr WARNING; parsing continues.
    """
    if not orphan_p.exists():
        return []
    records = []
    with open(str(orphan_p), 'r', encoding='utf-8') as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(
                    f'orphan_registry: skipping malformed line {lineno}: {str(e)[:80]}',
                    file=sys.stderr,
                )
                continue
            records.append(rec)
    return records


def _atomic_rewrite(orphan_p: pathlib.Path, records: list[dict]) -> None:
    """Write records atomically via tempfile + os.replace + fsync.

    Mirrors session_registry._atomic_write_json pattern.
    """
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f'.orphans.jsonl.tmp.{os.getpid()}.',
            dir=str(orphan_p.parent),
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(orphan_p))
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_orphan(
    *,
    commit_sha: str,
    session_id: Optional[str],
    scope_label: str,
    recovery_instructions: str,
    main_root: Optional[pathlib.Path] = None,
) -> bool:
    """Append an orphan record if commit_sha is not already upstream-reachable.

    Returns True if a record was written, False if skipped (reachable or
    validation failure).

    Pre-conditions:
      - commit_sha is 40-char hex
      - scope_label is in _VALID_SCOPE_LABELS
      - recovery_instructions is non-empty (capped at _MAX_RECOVERY_INSTRUCTIONS_BYTES)

    Side effects:
      - Runs reconcile() before appending to flip any now-reachable records
        and avoid a duplicate write for a commit that became reachable.
      - Runs git merge-base --is-ancestor write-time guard (timeout 3s).
      - Holds LOCK_EX on companion lock file for the duration of the append.

    TODO (Phase C.1 upgrade): adopt the Session-Id: git-trailer convention
    so session_id can be derived from the commit message deterministically
    rather than passed as Optional[str]. See axis-4 §10 option (a).
    """
    # Input validation.
    if not commit_sha or len(commit_sha) != 40 or not all(c in '0123456789abcdefABCDEF' for c in commit_sha):
        print(
            f'orphan_registry: record_orphan rejected: commit_sha must be 40-char hex, got {commit_sha!r}',
            file=sys.stderr,
        )
        return False
    if scope_label not in _VALID_SCOPE_LABELS:
        print(
            f'orphan_registry: record_orphan rejected: scope_label {scope_label!r} not in {_VALID_SCOPE_LABELS}',
            file=sys.stderr,
        )
        return False
    if not recovery_instructions:
        print('orphan_registry: record_orphan rejected: recovery_instructions is empty', file=sys.stderr)
        return False
    # Cap recovery_instructions.
    if len(recovery_instructions.encode('utf-8')) > _MAX_RECOVERY_INSTRUCTIONS_BYTES:
        recovery_instructions = recovery_instructions.encode('utf-8')[:_MAX_RECOVERY_INSTRUCTIONS_BYTES].decode('utf-8', errors='replace')

    if main_root is None:
        main_root = _resolve_main_root()

    # Write-time ancestor guard: skip if already reachable.
    if _is_ancestor(commit_sha, main_root):
        return False

    # Pre-reconcile to flip any now-reachable orphans (idempotent).
    # TODO: index by SHA for large-file perf if file grows beyond 100 records.
    try:
        reconcile(main_root=main_root)
    except Exception as e:
        print(f'orphan_registry: reconcile pre-pass failed: {e}', file=sys.stderr)
        # Continue — do not block record_orphan on reconcile failure.

    record = {
        'timestamp': _iso_now(),
        'originating_session_id': session_id,
        'commit_sha': commit_sha,
        'scope_label': scope_label,
        'recovery_instructions': recovery_instructions,
        'resolved': False,
    }

    with _open_locked(main_root, exclusive=True) as orphan_p:
        # Check for duplicate (same SHA already in file — reconcile above may
        # have just set resolved=true; we still don't duplicate).
        existing = _read_records(orphan_p)
        if any(r.get('commit_sha') == commit_sha for r in existing):
            return False
        # Append the new record.
        with open(str(orphan_p), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')
        return True


def reconcile(main_root: Optional[pathlib.Path] = None) -> int:
    """Scan all unresolved records; mark resolved=True for any that are now
    upstream-reachable.

    Idempotent. Safe to call repeatedly. Returns count of newly-resolved
    records (0 on no-op). Holds LOCK_EX for the atomic rewrite.

    Implementation: read whole file, mutate in-memory, write atomically via
    tempfile + os.replace (mirrors session_registry._atomic_write_json).
    # TODO: index by SHA for large-file perf if file grows beyond 100 records.
    """
    if main_root is None:
        main_root = _resolve_main_root()

    with _open_locked(main_root, exclusive=True) as orphan_p:
        records = _read_records(orphan_p)
        if not records:
            return 0

        newly_resolved = 0
        for rec in records:
            if rec.get('resolved'):
                continue
            sha = rec.get('commit_sha', '')
            if not sha:
                continue
            if _is_ancestor(sha, main_root):
                rec['resolved'] = True
                rec['resolved_at'] = _iso_now()
                rec['resolved_by'] = 'auto-reconciler'
                newly_resolved += 1

        if newly_resolved:
            _atomic_rewrite(orphan_p, records)

        return newly_resolved


def get_unresolved(
    session_id: Optional[str] = None,
    main_root: Optional[pathlib.Path] = None,
) -> list[dict]:
    """Return list of unresolved records.

    When session_id is None, returns ALL unresolved records.
    When session_id is a str, returns only records whose
    originating_session_id matches. Records with originating_session_id=None
    are NOT returned by a string-filtered call (use session_id=None to surface
    those — the launcher hook calls both forms per plan §2).

    Holds LOCK_SH for read consistency. Tolerates ENOENT (returns []).
    Tolerates malformed JSONL lines (skipped with stderr WARNING).
    """
    if main_root is None:
        main_root = _resolve_main_root()

    with _open_locked(main_root, exclusive=False) as orphan_p:
        records = _read_records(orphan_p)

    unresolved = [r for r in records if not r.get('resolved')]
    if session_id is None:
        return unresolved
    return [r for r in unresolved if r.get('originating_session_id') == session_id]
