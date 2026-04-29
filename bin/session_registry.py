"""session_registry.py — Registry read/write for per-session worktrees.

This is the ONLY module that mutates `.agent_context/worktrees/.registry.json`.
Callers: `bin/claude-session` (create, update, delete, sweep, list, resolve).

Format: JSON object mapping session_id (str) -> record (dict).

Locking discipline (CL-3.3):
  - All writes hold fcntl.LOCK_EX for the full read-modify-write cycle.
  - All reads hold fcntl.LOCK_SH.
  - NFS is NOT supported — flock is advisory on most NFS mounts (Fallback F1).

Supports Python 3.10+.
"""

import fcntl
import json
import os
import pathlib
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class RegistryError(Exception):
    """Base class for all registry errors."""


class MalformedRegistryError(RegistryError):
    """JSON parse failure, top-level not a dict, or schema validation failure.

    Callers should catch this and exit 3 with 'registry corrupt, run --gc to reconcile'.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class AmbiguousResolveError(RegistryError):
    """Multi-match in resolve().

    Callers should catch this and exit 2 with the list of candidate ids (CL-7.7).
    """

    def __init__(self, candidates: list[str]) -> None:
        self.candidates = candidates
        super().__init__(f"Ambiguous: {candidates}")


# ---------------------------------------------------------------------------
# Schema constants + validator
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "id",
    "name",
    "worktree_path",
    "branch",
    "created_at",
    "last_episode",
    "origin_sha",
    "origin_sha_offline",
    "status",
    "last_touched",
})

_STATUS_VALUES: frozenset[str] = frozenset({
    "active", "abandoned", "clean-exit-kept",
    "quarantining",  # transient: provisional record written; rename not yet complete
    "quarantined",   # final: worktree moved to .unmerged/; awaiting manual recovery
})

# Fields that can never be defaulted in repair-on-merge — their absence is corruption.
# origin_sha is also non-defaultable (no meaningful value), but it's not in this set
# because a missing origin_sha will be caught by _validate_record's required-fields check
# after repair; we raise there with a clearer message than the "non-defaultable" one.
_NEVER_DEFAULT_FIELDS: frozenset[str] = frozenset({"id", "worktree_path"})

# Optional fields that appear only on quarantining/quarantined records.
# For status='quarantining': unmerged_path is the INTENDED destination (pre-rename).
# For status='quarantined': unmerged_path is the ACTUAL post-rename path.
# Reconciliation logic uses this distinction to discriminate crash states.
_QUARANTINE_OPTIONAL_FIELDS: frozenset[str] = frozenset({
    "quarantine_timestamp",  # ISO-8601 string
    "quarantine_reason",     # human-readable reason string
    "unmerged_path",         # absolute path in .unmerged/; REQUIRED when status in quarantine set
    "head_sha",              # 40-char hex HEAD SHA at time of quarantine, or null
})


def _validate_record(record: dict) -> None:
    """Validate a record dict against the CL-3.2 schema.

    Raises MalformedRegistryError with a specific reason on any violation.
    """
    missing = _REQUIRED_FIELDS - record.keys()
    if missing:
        raise MalformedRegistryError(f"record missing required fields: {sorted(missing)}")

    if not isinstance(record["id"], str) or not record["id"]:
        raise MalformedRegistryError("'id' must be a non-empty string")

    if record["status"] not in _STATUS_VALUES:
        raise MalformedRegistryError(
            f"'status' must be one of {sorted(_STATUS_VALUES)}; got {record['status']!r}"
        )

    if not isinstance(record["last_episode"], int) or record["last_episode"] < 0:
        raise MalformedRegistryError(
            f"'last_episode' must be a non-negative int; got {record['last_episode']!r}"
        )

    if not isinstance(record["origin_sha_offline"], bool):
        raise MalformedRegistryError(
            f"'origin_sha_offline' must be bool; got {record['origin_sha_offline']!r}"
        )

    if not isinstance(record["worktree_path"], str) or not os.path.isabs(record["worktree_path"]):
        raise MalformedRegistryError(
            f"'worktree_path' must be an absolute path; got {record['worktree_path']!r}"
        )

    if record["name"] is not None and not isinstance(record["name"], str):
        raise MalformedRegistryError(
            f"'name' must be a string or null; got {type(record['name']).__name__}"
        )

    for ts_field in ("created_at", "last_touched"):
        val = record[ts_field]
        if not isinstance(val, str):
            raise MalformedRegistryError(f"'{ts_field}' must be a string; got {type(val).__name__}")
        try:
            datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MalformedRegistryError(
                f"'{ts_field}' is not a valid ISO-8601 timestamp: {val!r}"
            ) from exc

    # Quarantine-conditional validation: unmerged_path is required when status is
    # in the quarantine set; the other three optional fields are shape-checked on presence.
    if record["status"] in {"quarantining", "quarantined"}:
        if "unmerged_path" not in record:
            raise MalformedRegistryError(
                "quarantining/quarantined record missing 'unmerged_path'"
            )
        if not isinstance(record["unmerged_path"], str) or not os.path.isabs(record["unmerged_path"]):
            raise MalformedRegistryError(
                f"'unmerged_path' must be an absolute path; got {record['unmerged_path']!r}"
            )
        if "quarantine_timestamp" in record:
            val = record["quarantine_timestamp"]
            if not isinstance(val, str):
                raise MalformedRegistryError(
                    f"'quarantine_timestamp' must be a string; got {type(val).__name__}"
                )
            try:
                datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError as exc:
                raise MalformedRegistryError(
                    f"'quarantine_timestamp' is not a valid ISO-8601 timestamp: {val!r}"
                ) from exc
        if "quarantine_reason" in record:
            if not isinstance(record["quarantine_reason"], str):
                raise MalformedRegistryError(
                    f"'quarantine_reason' must be a string; got {type(record['quarantine_reason']).__name__}"
                )
        if "head_sha" in record:
            val = record["head_sha"]
            if val is not None and (not isinstance(val, str) or len(val) != 40):
                raise MalformedRegistryError(
                    f"'head_sha' must be a 40-char hex string or null; got {val!r}"
                )


# ---------------------------------------------------------------------------
# Path + lock helpers
# ---------------------------------------------------------------------------


def registry_path(main_root: pathlib.Path) -> pathlib.Path:
    """Return the registry file path for a given main repo root (CL-3.1).

    The file may not exist yet; callers use this for display and test fixtures.
    """
    return main_root / ".agent_context" / "worktrees" / ".registry.json"


def _iso_now() -> str:
    """Return the current UTC time as an ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lock_path(main_root: pathlib.Path) -> pathlib.Path:
    return registry_path(main_root).parent / ".registry.lock"


@contextmanager
def _open_locked(main_root: pathlib.Path, exclusive: bool):
    """Context manager: hold an fcntl flock on the stable lock file, then
    open and yield the registry data file for reading.

    The lock target is `.registry.lock` (a separate, never-replaced file).
    This avoids the os.replace inode-swap problem: if we locked .registry.json
    directly and then replaced it with os.replace, the next opener would get a
    new inode with no competing lock held.

    Yields a file object for .registry.json positioned at offset 0.
    Lock is released on context exit.
    """
    reg_path = registry_path(main_root)
    lk_path = _lock_path(main_root)
    # Ensure dir exists before os.open so O_CREAT can succeed.
    reg_path.parent.mkdir(parents=True, exist_ok=True)

    # Open (create if missing) the stable lock file.
    lk_fd = os.open(str(lk_path), os.O_CREAT | os.O_RDWR, 0o644)
    lk_fh = os.fdopen(lk_fd, "r+")
    try:
        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lk_fh, lock_type)
        # Open the data file after the lock is held (create if missing).
        data_fd = os.open(str(reg_path), os.O_CREAT | os.O_RDWR, 0o644)
        data_fh = os.fdopen(data_fd, "r+")
        try:
            data_fh.seek(0)
            yield data_fh
        finally:
            data_fh.close()
    finally:
        fcntl.flock(lk_fh, fcntl.LOCK_UN)
        lk_fh.close()


# ---------------------------------------------------------------------------
# Atomic write + stale-tempfile sweeper
# ---------------------------------------------------------------------------


def _atomic_write_json(path: pathlib.Path, data: dict) -> None:
    """Write data as JSON to path atomically via tempfile + os.replace + fsync."""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".registry.json.tmp.{os.getpid()}.",
            dir=path.parent,
        )
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        tmp_path = None  # os.replace succeeded; no cleanup needed
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _is_pid_alive(pid: int) -> bool:
    """Return True if the process with the given pid is still running."""
    try:
        os.kill(pid, 0)
        return True  # process exists (and we have permission to signal it)
    except ProcessLookupError:
        return False
    except OSError:
        # Permission denied — process exists but we can't signal it; treat as alive.
        return True


def _sweep_stale_tempfiles(directory: pathlib.Path, older_than_seconds: int = 60) -> None:
    """Remove stale tempfiles left by crashed writers.

    Parses the pid from filename prefix `.registry.json.tmp.<pid>.`; skips
    files whose pid is still alive. Sweeps only confirmed-dead or unparseable
    (legacy orphan) tempfiles that are older than older_than_seconds.
    """
    now = datetime.now(timezone.utc).timestamp()
    for entry in directory.glob(".registry.json.tmp.*"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age = now - mtime
        if age < older_than_seconds:
            continue
        # Parse pid from filename: .registry.json.tmp.<pid>.<suffix>
        parts = entry.name.split(".")
        # parts: ['', 'registry', 'json', 'tmp', '<pid>', '<suffix>']
        pid: Optional[int] = None
        if len(parts) >= 5:
            try:
                pid = int(parts[4])
            except ValueError:
                pid = None

        if pid is not None and _is_pid_alive(pid):
            # Writer's pid is still alive; do not sweep its tempfile.
            continue

        try:
            os.unlink(entry)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Internal JSON load helper
# ---------------------------------------------------------------------------


def _load_registry_from_fh(fh) -> dict:
    """Read and parse registry JSON from an open (locked) file handle.

    Returns {} on empty or missing content.
    Raises MalformedRegistryError if content is non-empty but invalid JSON or
    not a top-level dict.
    """
    raw = fh.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedRegistryError(f"JSON parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedRegistryError(
            f"registry must be a JSON object at top level; got {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_registry(main_root: pathlib.Path) -> dict[str, dict]:
    """Return the full registry as a dict keyed by session id (CL-3.3).

    Returns {} if the registry file is missing or empty.
    Does NOT validate individual record schemas (lazy read — keeps --list
    tolerant of legacy or manually-edited entries).
    Uses LOCK_SH.
    """
    with _open_locked(main_root, exclusive=False) as fh:
        return _load_registry_from_fh(fh)


def write_record(main_root: pathlib.Path, record: dict) -> None:
    """Insert or update a registry record keyed by record['id'] (CL-3.2, CL-3.4).

    Validates the record schema before writing. Raises MalformedRegistryError
    if validation fails. Atomic write under LOCK_EX.
    """
    _validate_record(record)
    path = registry_path(main_root)
    _sweep_stale_tempfiles(path.parent)
    with _open_locked(main_root, exclusive=True) as fh:
        data = _load_registry_from_fh(fh)
        data[record["id"]] = record
        _atomic_write_json(path, data)


def update_record(main_root: pathlib.Path, session_id: str, **fields) -> None:
    """Merge fields into an existing record (CL-3.2, CL-3.6).

    Fails silently if the record does not exist (non-crashing for callers that
    race with session cleanup).

    Applies repair-on-merge for legacy records missing newer schema fields:
    fills safe defaults for all optional fields before validation. Fields
    'id' and 'worktree_path' are NEVER defaulted — if absent, raises
    MalformedRegistryError (those absences indicate corruption). A missing
    'origin_sha' will fail _validate_record's required-fields check after
    repair, also raising MalformedRegistryError.

    Held under one LOCK_EX for the full read-modify-write cycle (CL-3.3,
    Decision #2 — avoids lost-update TOCTOU).
    """
    path = registry_path(main_root)
    _sweep_stale_tempfiles(path.parent)
    with _open_locked(main_root, exclusive=True) as fh:
        data = _load_registry_from_fh(fh)
        if session_id not in data:
            return  # fail softly
        merged = dict(data[session_id])
        merged.update(fields)

        # Repair-on-merge: fill missing optional fields with schema-safe defaults
        # before validation. This keeps update_record from crashing on legacy
        # records that predate a field addition.
        for never_field in _NEVER_DEFAULT_FIELDS:
            if never_field not in merged:
                raise MalformedRegistryError(
                    f"record {session_id!r} is missing non-defaultable field {never_field!r}; "
                    f"this indicates corruption — run --gc to reconcile"
                )
        if "status" not in merged:
            merged["status"] = "active"
        if "origin_sha_offline" not in merged:
            merged["origin_sha_offline"] = False
        if "last_episode" not in merged:
            merged["last_episode"] = 0
        now = _iso_now()
        if "created_at" not in merged:
            merged["created_at"] = now
        if "last_touched" not in merged:
            merged["last_touched"] = now
        if "name" not in merged:
            merged["name"] = None
        if "branch" not in merged:
            merged["branch"] = "detached"

        _validate_record(merged)
        data[session_id] = merged
        _atomic_write_json(path, data)


def delete_record(main_root: pathlib.Path, session_id: str) -> bool:
    """Remove a record from the registry (CL-3.7).

    Returns True if the record existed and was deleted, False if absent.
    Atomic write under LOCK_EX.
    """
    path = registry_path(main_root)
    _sweep_stale_tempfiles(path.parent)
    with _open_locked(main_root, exclusive=True) as fh:
        data = _load_registry_from_fh(fh)
        if session_id not in data:
            return False
        del data[session_id]
        _atomic_write_json(path, data)
        return True


def list_active(
    main_root: pathlib.Path,
    *,
    exclude_session_id: Optional[str] = None,
) -> list[dict]:
    """Return records with status='active', sorted by last_touched DESC (CL-4.1).

    Excludes the record with id == exclude_session_id when given (used by the
    concurrent-session warning to exclude the newly-started session itself).
    """
    registry = read_registry(main_root)
    active = [
        rec
        for rec in registry.values()
        if rec.get("status") == "active"
        and rec.get("id") != exclude_session_id
    ]
    active.sort(key=lambda r: r.get("last_touched", ""), reverse=True)
    return active


def resolve(main_root: pathlib.Path, id_or_name: str) -> Optional[dict]:
    """Resolve a record by id-exact, name-exact, or id-prefix (CL-7.7).

    Returns the matching record dict, or None if no match.
    Raises AmbiguousResolveError if multiple records match within a stage.

    Precedence: id-exact > name-exact > id-prefix. If a session's NAME equals
    another session's ID, the name-match wins (stage 2 short-circuits stage 3).
    Callers disambiguate by passing the full id if they want id-precedence.

    Three-stage cascade (stops at first stage with >=1 hit):
      Stage 1 — exact id match.
      Stage 2 — exact name match (case-sensitive).
      Stage 3 — id startswith prefix match; multi-match raises AmbiguousResolveError.
    """
    registry = read_registry(main_root)

    # Stage 1: exact id
    if id_or_name in registry:
        return registry[id_or_name]

    # Stage 2: exact name
    name_matches = [rec for rec in registry.values() if rec.get("name") == id_or_name]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise AmbiguousResolveError([rec["id"] for rec in name_matches])

    # Stage 3: id prefix
    prefix_matches = [rec for rec in registry.values() if rec["id"].startswith(id_or_name)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise AmbiguousResolveError([rec["id"] for rec in prefix_matches])

    return None


def sweep_and_mark_abandoned(
    main_root: pathlib.Path,
    is_pid_live: Callable[[dict], bool],
) -> list[str]:
    """Scan active records and flip dead-monitor sessions to 'abandoned' (CL-4.4, CL-3.7).

    is_pid_live receives the FULL record dict; the caller (launcher) extracts
    the relevant liveness signal (e.g. reads monitor.pid from disk). This keeps
    path construction logic in the launcher where it belongs.

    Note: the subtask spec typed this as Callable[[int], bool] (pid), but the
    critic-ratified override passes the full record dict so the caller can pick
    the field (pid, sock, future signals) without coupling the registry to the
    monitor.pid path layout. See impl report § Subtask divergences.

    Returns the list of session ids transitioned to 'abandoned'.
    Atomic write under LOCK_EX (CL-3.3).
    """
    path = registry_path(main_root)
    _sweep_stale_tempfiles(path.parent)
    with _open_locked(main_root, exclusive=True) as fh:
        data = _load_registry_from_fh(fh)
        transitioned: list[str] = []
        for session_id, record in data.items():
            if record.get("status") != "active":
                continue
            if not is_pid_live(record):
                record = dict(record)
                record["status"] = "abandoned"
                record["last_touched"] = _iso_now()
                data[session_id] = record
                transitioned.append(session_id)
        if transitioned:
            _atomic_write_json(path, data)
        return transitioned


def mark_quarantined(
    main_root: pathlib.Path,
    session_id: str,
    *,
    head_sha: Optional[str],
    unmerged_path: str,
    reason: str,
) -> bool:
    """Flip a record's status from 'quarantining' to 'quarantined' and stamp
    the post-rename unmerged_path. Returns True if the record existed and was
    updated; False if absent.

    Atomic write under LOCK_EX (CL-3.3). The provisional 'quarantining'
    record is created by write_record before the rename (§F of sketch
    WR-PHASE2-PREVENT-subtask-5); this helper is called AFTER the rename
    succeeds to flip status to 'quarantined'.

    Idempotent: if the record is already 'quarantined', updates
    unmerged_path and head_sha to the provided values and returns True.
    """
    path = registry_path(main_root)
    _sweep_stale_tempfiles(path.parent)
    with _open_locked(main_root, exclusive=True) as fh:
        data = _load_registry_from_fh(fh)
        if session_id not in data:
            return False
        record = dict(data[session_id])
        record["status"] = "quarantined"
        record["unmerged_path"] = unmerged_path
        record["head_sha"] = head_sha
        record["quarantine_reason"] = reason
        record["quarantine_timestamp"] = _iso_now()
        record["last_touched"] = _iso_now()
        _validate_record(record)
        data[session_id] = record
        _atomic_write_json(path, data)
        return True
