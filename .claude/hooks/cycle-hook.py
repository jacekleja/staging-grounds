#!/usr/bin/env python3
"""
Unified session cycling hook for v3.
Registered as both PostToolUse (matcher: "") and SubagentStop (matcher: "").
Counts tokens from transcript, creates/removes cycle-pending, injects warnings.
"""
import glob as _glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone


# Claude Code's transcript-dir slug rule: every character outside [A-Za-z0-9-]
# becomes '-' (no coalescing of consecutive hyphens). Verified by empirical
# ls ~/.claude/projects/ — both '.agent_context' and 'agent_context' are
# mapped with '.' AND '_' -> '-', yielding the double-hyphen worktree form.
# Mirror of bin/claude-session._claude_project_slug; kept local because hooks
# are standalone scripts and cannot import from bin/.
_SLUG_NON_ALNUM = re.compile(r'[^A-Za-z0-9-]')


# Staleness ceiling for Tier-3b heuristic picks. A transcript whose mtime
# is older than this (seconds) cannot plausibly belong to the current live
# session; reject rather than read its cached_read_input_tokens total.
# Prevents the CYCLE-COUNT-BUG failure where a stale unrelated transcript's
# 150K-300K cached-read totals get written to cycle-pending and trigger a
# spurious cycle on the first PostToolUse fire of a fresh worktree session.
# See .agent_context/sessions/1776714350-32230-fd014ef2358e/CYCLE-COUNT-BUG-diag-counter.md
# for the full failure chain.
_HEURISTIC_MAX_MTIME_DELTA_S = 3600

# Tail-window sizing for extract_total_tokens. The common case (the last
# assistant-usage record sits within the last 64 KB) is served by a single
# _TOKEN_TAIL_BYTES read. When a single oversized record (a large tool_result,
# >64 KB) pushes ALL assistant-usage records out of that window, the reverse
# scan finds nothing and the read escalates by doubling the window — capped at
# _TOKEN_TAIL_MAX_BYTES — until a qualifying record is found or the whole file
# has been read. Without escalation the function returned None on a healthy
# transcript, driving a spurious fail-closed cycle (see diagnosis
# diagnosis-measurement-subsystem-transcript-discovery.md).
_TOKEN_TAIL_BYTES = 65536
_TOKEN_TAIL_MAX_BYTES = 4 * 1024 * 1024


def _claude_project_slug(path):
    """Map filesystem path -> Claude Code's ~/.claude/projects/<slug> dir name."""
    return _SLUG_NON_ALNUM.sub('-', str(path).rstrip('/'))


def _infer_alt_cwd(session_dir, cwd):
    """Infer the alternate cwd for transcript discovery (REQ-6 recovery path).

    session_dir is always rooted at {main_project_root}/.agent_context/sessions/{session_id}.
    When cwd is the main project root, the alternate is the worktree path.
    When cwd is a worktree path (or any subdir of the main root), the alternate
    is the main project root.

    Returns the alternate cwd string, or None if it cannot be safely inferred
    (e.g. session_dir has unexpected structure, or the inferred worktree dir does
    not exist on disk).
    """
    try:
        parts = session_dir.split('/.agent_context/sessions/')
        if len(parts) != 2:
            return None
        project_root = parts[0]
        session_id_part = parts[1]
        cwd_norm = cwd.rstrip('/')
        if cwd_norm == project_root:
            # cwd is the main project root -> alt is the worktree path.
            # Guard with os.path.isdir: if the worktree was already cleaned up,
            # scanning its non-existent slug directory is harmless but wasteful.
            wt_path = os.path.join(project_root, '.agent_context', 'worktrees', session_id_part)
            return wt_path if os.path.isdir(wt_path) else None
        if cwd_norm.startswith(project_root + '/'):
            # cwd is the worktree path (or some other subdir) -> alt is the main root.
            return project_root
    except (AttributeError, ValueError):
        pass
    return None


def _log(session_dir, msg):
    """Append a timestamped line to cycle-hook.log in the session directory."""
    try:
        logfile = os.path.join(session_dir, "cycle-hook.log")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with open(logfile, 'a') as f:
            f.write(f"[{ts}] {msg}\n")
    except (IOError, OSError):
        pass  # Logging must never break the hook


# POSIX O_APPEND guarantees atomic writes ≤PIPE_BUF (4096 bytes); our log
# lines are <500 bytes, so concurrent writes from bin/claude-session._log
# and _log_both interleave at line boundaries — no locking needed.
def _log_both(session_dir, msg):
    """Mirror a line into both cycle-hook.log and monitor.log. Uses the
    bin/claude-session timestamp format (second precision) for visual
    alignment with wrapper-originated entries, prefixed [cycle-hook]."""
    _log(session_dir, msg)
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(session_dir, "monitor.log"), 'a') as f:
            f.write(f"[{ts}] [cycle-hook] {msg}\n")
    except (IOError, OSError):
        pass


# Module-level session_dir set by main() for use by _log in find_transcript
_active_session_dir = None


def extract_total_tokens(transcript_path):
    """Read last assistant message from transcript and sum its usage fields.

    Why this works: cache_read_input_tokens grows monotonically -- it represents
    the ENTIRE prior context served from cache each turn. So the last message's
    usage reflects the current total context window size:

      total_in = input_tokens + cache_read_input_tokens + cache_creation_input_tokens

    Also handles codex rollout JSONL format (rollout-*.jsonl files):
      {"type": "token_count", "total_tokens": N, ...}
    For codex, total_tokens is read directly from the last token_count event;
    this is the canonical token count reported by the codex-rs rollout recorder.

    Reads a growing tail window (starting at _TOKEN_TAIL_BYTES) to stay fast on
    large transcripts. The common case returns after a single 64 KB read; the
    window only escalates (doubling, capped at _TOKEN_TAIL_MAX_BYTES) when a
    single oversized record has pushed every assistant-usage record out of the
    last 64 KB — otherwise the function would return None on a healthy file and
    drive a spurious fail-closed cycle.
    Returns None if no qualifying message found.
    MUST skip synthetic messages (total_in == 0).

    Claude Code transcript format nests usage inside a 'message' wrapper:
      {"type": "assistant", "message": {"role": "assistant", "usage": {...}}, ...}
    We handle both nested (real) and flat (legacy/test) formats for robustness.
    """
    try:
        with open(transcript_path, 'rb') as f:
            f.seek(0, 2)
            file_size = f.tell()

            window = _TOKEN_TAIL_BYTES
            while True:
                read_from = max(0, file_size - window)
                f.seek(read_from)
                tail = f.read().decode('utf-8', errors='replace')

                # Parse JSONL lines in reverse to find last qualifying record.
                # When read_from > 0 the first line may be a partial fragment;
                # json.loads raises and we skip it (the try/except below).
                result = _scan_tail_for_tokens(tail)
                if result is not None:
                    return result

                # No qualifying record in this window. Escalate only if the
                # window has not yet covered the whole file and is below the cap.
                if read_from == 0 or window >= _TOKEN_TAIL_MAX_BYTES:
                    return None
                window = min(window * 2, file_size, _TOKEN_TAIL_MAX_BYTES)
    except (IOError, OSError):
        return None


def _scan_tail_for_tokens(tail):
    """Reverse-scan a decoded tail window for the last qualifying token record.

    Returns the token total (int) or None if no qualifying record is present.
    Handles both Claude Code usage formats (nested + flat) and both codex
    token_count formats (wrapped event_msg + pre-0.131.0 flat).
    """
    lines = [l for l in tail.splitlines() if l.strip()]
    for line in reversed(lines):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Codex 0.131.0+ wrapped format:
        #   {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": N}}}}
        # Other event_msg variants share the same envelope but lack token_count payload type.
        if msg.get("type") == "event_msg":
            payload = msg.get("payload", {})
            if isinstance(payload, dict) and payload.get("type") == "token_count":
                total = (
                    payload.get("info", {})
                    .get("total_token_usage", {})
                    .get("total_tokens")
                )
                if isinstance(total, int) and total > 0:
                    return total
            continue

        # Codex rollout flat format (pre-0.131.0): {"type": "token_count", "total_tokens": N, ...}
        # total_tokens is the cumulative context window used; directly usable.
        if msg.get("type") == "token_count":
            total = msg.get("total_tokens", 0)
            if total > 0:
                return total
            continue

        # Claude Code nests fields inside msg.message; also support flat format
        message = msg.get("message", {})
        if not isinstance(message, dict):
            message = {}

        # Try nested format first (msg.message.usage), then flat (msg.usage)
        usage = message.get("usage") or msg.get("usage") or {}
        total_in = (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        if total_in == 0:
            continue  # synthetic or empty -- skip

        # Check role: nested msg.message.role, or top-level msg.type, or flat msg.role
        role = message.get("role", "") or msg.get("type", "") or msg.get("role", "")
        if role == "assistant":
            return total_in

    return None


def _write_cache(cache_file, path):
    """Cache the discovered transcript path for subsequent calls."""
    try:
        with open(cache_file, 'w') as f:
            f.write(path)
    except (IOError, OSError):
        pass


def _read_cache(cache_file):
    """Read cached transcript path. Returns None if missing/unreadable."""
    try:
        with open(cache_file) as f:
            path = f.read().strip()
            return path if path else None
    except (IOError, OSError):
        return None


def _read_session_id_from_file(filepath):
    """Read the sessionId from the first line of a .jsonl transcript file.

    Returns the sessionId string, or None if not readable/parseable.
    """
    try:
        with open(filepath, 'r') as f:
            first_line = f.readline()
        if first_line:
            data = json.loads(first_line)
            sid = data.get("sessionId")
            if sid and isinstance(sid, str):
                return sid
    except (IOError, OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        if _active_session_dir:
            _log(_active_session_dir, f"WARN: Failed to read sessionId from {filepath}: {type(e).__name__}: {e}")
    return None


def _read_first_line_timestamp_ms(jsonl_path):
    """Return ms-epoch int for the first record in jsonl_path that carries a
    `timestamp` field; None on any failure.

    Deviation from the S2 sketch (name kept, semantics broadened per live-data
    verification): Claude Code's live JSONLs put `permission-mode` /
    `agent-setting` / `file-history-snapshot` on line 1 with no `timestamp`.
    A literal first-line-only read would make tier-0 permanently inert. We
    scan up to max_lines records looking for the first timestamped entry —
    in practice a `user` / `queue-operation` record appears by line 2-4.
    `timestamp` is ISO-8601-with-Z (e.g. "2026-04-18T00:20:38.049Z"); strip
    the Z and substitute `+00:00` so datetime.fromisoformat accepts it on
    Python <3.11.
    """
    max_lines = 50
    try:
        with open(jsonl_path, 'r') as f:
            for _ in range(max_lines):
                line = f.readline()
                if not line:
                    return None
                try:
                    data = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                ts = data.get("timestamp") if isinstance(data, dict) else None
                if not ts or not isinstance(ts, str):
                    continue
                if ts.endswith('Z'):
                    ts = ts[:-1] + '+00:00'
                try:
                    dt = datetime.fromisoformat(ts)
                except (ValueError, TypeError):
                    continue
                return int(dt.timestamp() * 1000)
    except (IOError, OSError):
        return None
    return None


def _read_cached_uuid(mapping_file):
    """Read the persisted Claude Code UUID from {session_dir}/claude-code-uuid.

    Returns the UUID string (whitespace-stripped), or None if missing/unreadable/empty.
    No UUID-shape validation is performed: the downstream matcher is a plain string
    compare against transcript first-line sessionId, so any garbage here will simply
    fail to match and the heuristic fallback will overwrite.
    """
    try:
        with open(mapping_file) as f:
            uuid = f.read().strip()
            return uuid if uuid else None
    except (IOError, OSError):
        return None


def _write_cached_uuid(mapping_file, uuid):
    """Persist the Claude Code UUID for subsequent fires of the same episode."""
    try:
        with open(mapping_file, 'w') as f:
            f.write(uuid)
    except (IOError, OSError):
        pass


def _tier3b_mapping_still_valid_for_episode(mapping_file, state_file):
    """Return (is_sticky, mtime_age_s). Sticky iff the existing mapping was
    written AFTER the current episode's cycle.state boundary + 100ms slack —
    aligned with tier-3a cross-episode guard (m_mtime >= s_mtime + 0.1).
    Caller must also verify tier-3a did not flag the mapping as mismatched;
    stickiness is an mtime-only predicate."""
    try:
        m_mtime = os.path.getmtime(mapping_file)
        s_mtime = os.path.getmtime(state_file)
    except OSError:
        return False, None
    if not _read_cached_uuid(mapping_file):
        return False, None
    return m_mtime > s_mtime + 0.1, s_mtime - m_mtime


def _claude_pid_from_proc_tree(session_dir):
    """Return the PID of the owning Claude Code process, or None on failure.

    Discovery strategy (two steps):
    1. Read {session_dir}/.claude.pid — the monitor writes this at episode start
       and it is the authoritative PID source. If the file exists and the value
       is a live process (os.kill(pid, 0) succeeds), return it immediately.
    2. Walk /proc/{pid}/stat parent chain from the current process (os.getpid())
       upward until a process whose comm field matches 'claude' or 'claude-code'
       is found, or we exhaust the chain (ppid == 0 or ppid == 1 = init boundary).

    Failure modes (all return None):
    - .claude.pid missing or unreadable
    - PID in .claude.pid is dead (stale file from crashed episode)
    - /proc not available (non-Linux host)
    - Permission error reading /proc/{pid}/stat
    - Proc tree walk reaches init without finding a claude process

    Returns int PID or None.
    """
    # Step 1: read .claude.pid written by the monitor
    pidfile = os.path.join(session_dir, '.claude.pid')
    try:
        with open(pidfile) as f:
            pid_val = int(f.read().strip())
        # Verify the process is alive
        os.kill(pid_val, 0)
        return pid_val
    except (IOError, OSError, ValueError):
        pass  # File missing, unreadable, or process dead — fall through to walk

    # Step 2: walk /proc parent chain from current PID
    try:
        current = os.getpid()
        visited = set()
        while current and current not in visited and current > 1:
            visited.add(current)
            stat_path = '/proc/{}/comm'.format(current)
            try:
                with open(stat_path) as f:
                    comm = f.read().strip()
                if comm in ('claude', 'claude-code'):
                    return current
            except (IOError, OSError):
                pass
            # Read parent PID from /proc/{pid}/stat field 4 (ppid)
            try:
                with open('/proc/{}/stat'.format(current)) as f:
                    stat_line = f.read()
                # stat format: pid (comm) state ppid ...
                # comm may contain spaces/parens; find the last ')' then split
                rparen = stat_line.rfind(')')
                if rparen == -1:
                    break
                fields = stat_line[rparen + 2:].split()
                if len(fields) < 2:
                    break
                current = int(fields[1])
            except (IOError, OSError, ValueError, IndexError):
                break
    except Exception:
        pass
    return None


def _pid_owns_file(pid, abs_path):
    """Return True if /proc/{pid}/fd/ has a symlink resolving to abs_path.

    Iterates up to 1024 fd entries (cap to bound scan time).
    Returns False on any /proc read error.
    """
    fd_dir = '/proc/{}/fd'.format(pid)
    try:
        fds = os.listdir(fd_dir)
    except (OSError, PermissionError):
        return False
    for fd in fds[:1024]:
        try:
            target = os.readlink(os.path.join(fd_dir, fd))
            if target == abs_path:
                return True
        except (OSError, PermissionError):
            continue
    return False


def find_transcript(session_dir, cwd):
    """Discover the Claude Code transcript path via /proc/{pid}/fd scanning,
    with transcript path caching and a directory-listing fallback.

    The hook cannot rely on the event payload for transcript_path (it's not
    present in PostToolUse events). Instead, we read the claude PID from
    .claude.pid in the session directory and scan its open file descriptors
    for a .jsonl transcript file.

    Discovery waterfall:
    0. Authoritative-transcript file ({session_dir}/authoritative-transcript.json).
       Written by bin/claude-session post-Popen with ground-truth transcript
       path, sessionId, and pre-Popen spawn timestamp. Verified on every fire
       via (a) sessionId equality against the JSONL's first-line sessionId,
       (b) jsonl first-available timestamp > claude_spawn_ts (primary
       cross-episode staleness anchor), (c) jsonl mtime > cycle.state mtime
       (secondary defense-in-depth). On any check failure, falls through to
       tier 1 with a TIER0=<reason> log line. Sessions launched without
       bin/claude-session (no authoritative file) skip this tier silently via
       the os.path.isfile guard.
    1. /proc/{pid}/fd scanning (authoritative when fd is open). Returns the
       first .jsonl candidate under ~/.claude/projects/ (subagent paths
       excluded). On success, writes the path to {session_dir}/transcript.path.
    2. Cache file ({session_dir}/transcript.path) — used when fd scanning
       returns nothing and the cached file was modified within 5 minutes;
       also gated by an episode-boundary mtime check vs cycle.state.
    3a. Dir-scan mapping-read branch — reads the persisted Claude Code UUID
        from {session_dir}/claude-code-uuid (populated on fire #1 of each
        episode) and returns the slugified-dir .jsonl whose first-line
        sessionId matches. This is the authoritative path on fire #2+ of
        every episode once fire #1 has populated the mapping. Gated by a
        cross-episode mtime check vs cycle.state and by episode-boundary
        cleanup in bin/claude-session.
    3b. Dir-scan PID-filtered heuristic (last resort) — runs on fire #1 of
        each episode (no mapping yet) and whenever the mapping-read branch
        found no match. First attempts PID-based candidate filtering via
        _claude_pid_from_proc_tree() + _pid_owns_file(): if exactly one
        candidate file is open by the target PID, selects it directly.
        If PID lookup fails or zero/multiple candidates match, falls back
        to the mtime/size heuristic on the full candidate list. On success
        (either path), reads the picked file's first-line sessionId and
        persists it so fire #2+ can use tier-3a deterministically.

    Known limitations:
    - Fire #1 of each episode uses the mtime/size heuristic and has no
      in-episode recovery if it lands on the wrong transcript (concurrent
      Claude Code windows racing mtime). Once fire #1 writes the mapping,
      fire #2+ will deterministically return the same (possibly wrong) file
      for the rest of the episode. Episode boundary (cycle.state mtime vs
      mapping mtime, plus bin/claude-session cleanup) recovers across
      episodes. This is not a regression — the pre-refactor heuristic could
      also land wrong on any fire — but the failure mode differs: today's
      old heuristic could drift between fires, while the new code locks in
      fire #1's pick for the episode.
    - CLAUDE_SESSION_ID is NOT used as part of transcript identity: it is a
      bin/claude-session-namespaced id ({epoch}-{pid}-{hash}) and does NOT
      match the Claude Code UUID in the transcript first-line sessionId.
      The 0/16 probe match-rate established this namespace mismatch. The
      env var is still read for logging/traceability only.
    - Tier-1 fd-scan success persists the claude-code-uuid mapping so a
      subsequent fd-failure transition does not re-run the tier-3b heuristic;
      the next fire reads the mapping deterministically via tier-3a instead.

    Claude Code does not keep the transcript fd persistently open (it opens
    and closes it per-write), so /proc/{pid}/fd scanning will often return
    None. The cache and mapping file ensure main-session transcript identity
    is preserved across calls where the fd is not open, preventing subagent
    transcripts (which may be newer on disk) from stealing the cached
    identity. Tier 0 above short-circuits this for sessions that run under
    bin/claude-session.

    Returns the transcript path string, or None if not found.
    """
    global _active_session_dir
    _active_session_dir = session_dir

    pidfile = os.path.join(session_dir, '.claude.pid')
    cache_file = os.path.join(session_dir, 'transcript.path')
    mapping_file = os.path.join(session_dir, 'claude-code-uuid')
    state_file = os.path.join(session_dir, 'cycle.state')

    # Tier -1: codex rollout JSONL discovery.
    # Fires when CODEX_SESSION_ID is present in env (set by _codex_hook_adapter.py
    # from the codex hook payload's session_id field) OR when ~/.codex/sessions/
    # exists (codex is installed and has run at least once). The codex rollout path
    # shape is ~/.codex/sessions/YYYY/MM/DD/rollout-{timestamp}-{uuid}.jsonl.
    #
    # Primary path: CODEX_SESSION_ID env var allows a targeted glob — avoids
    #   picking a concurrent codex session's rollout on multi-session machines.
    # Fallback: recency-based glob across all rollout-*.jsonl files — picks the
    #   most-recently-modified file, which is correct for the common single-session
    #   case. Racier than the primary path on concurrent sessions, acceptable since
    #   codex-as-orchestrator is typically run one session at a time.
    #
    # Early return on success: callers after Tier -1 (including Tier 0 authoritative
    # file and all fd-scan / cache / dir-scan tiers) are Claude-Code-specific and
    # would return wrong results for codex sessions.
    _codex_session_id = os.environ.get("CODEX_SESSION_ID", "")
    _codex_sessions_root = os.path.expanduser("~/.codex/sessions")
    # FIX (2026-05-26): condition was `if _codex_session_id or os.path.isdir(_codex_sessions_root):`
    # which fired the codex branch on ANY host with ~/.codex/sessions/ present, even from
    # Claude Code sessions where CODEX_SESSION_ID is unset. That caused cross-runtime
    # transcript contamination via the recency-mtime fallback — a Claude Code session would
    # read a sibling codex CLI's rollout file and report its token count as its own.
    # Now: only enter the codex branch when CODEX_SESSION_ID is explicitly set (the codex
    # hook adapter at .claude/hooks/_codex_hook_adapter.py injects this env var when the
    # hook runs from codex).
    if _codex_session_id:
        _codex_candidate = None
        # Targeted glob: find the rollout file whose name contains the UUID.
        _pattern = os.path.join(_codex_sessions_root, "**", f"*{_codex_session_id}*.jsonl")
        _matches = _glob.glob(_pattern, recursive=True)
        if _matches:
            # Most recently modified wins (handles re-runs of same session UUID).
            _codex_candidate = max(_matches, key=os.path.getmtime)
            _log(session_dir,
                 f"CODEX_TIER=-1 uuid_glob: found {os.path.basename(_codex_candidate)}"
                 f" (uuid={_codex_session_id[:12]}...)")
        else:
            _log(session_dir,
                 f"CODEX_TIER=-1 uuid_glob: no match for uuid={_codex_session_id[:12]}...")
        if _codex_candidate is not None:
            _write_cache(cache_file, _codex_candidate)
            return _codex_candidate

    # Tier 0: authoritative-transcript file (written by bin/claude-session
    # _file_watcher post-Popen). Trusts the wrapper's pre-hook pin, but
    # verifies on every fire via sessionId equality + spawn-ts staleness.
    # On any failure, falls through to tier-1 unchanged (no short-circuit).
    auth_file = os.path.join(session_dir, "authoritative-transcript.json")
    if os.path.isfile(auth_file):
        try:
            with open(auth_file) as _f:
                _auth = json.load(_f)
            _auth_path = _auth.get("transcript_path")
            _auth_uuid = _auth.get("session_uuid")
            _auth_spawn_ts = _auth.get("claude_spawn_ts")  # ms epoch
            _auth_ambiguity = bool(_auth.get("ambiguity_warned"))
            # FLAG 2: require spawn_ts to be a positive int/float. Truthy check
            # alone accepts negatives (clock-skew garbage, S1 sign-inversion bug)
            # which would trivially satisfy step 6 since jsonl_ts > negative is
            # always true. Rejecting non-positive as missing_fields keeps the
            # single observable (one tag) for "payload is untrustworthy".
            if (not _auth_path or not _auth_uuid
                    or not isinstance(_auth_spawn_ts, (int, float))
                    or _auth_spawn_ts <= 0):
                _log(session_dir, "TIER0=missing_fields — falling through")
            elif not os.path.isfile(_auth_path):
                _log(session_dir, f"TIER0=transcript_gone file={os.path.basename(_auth_path)} — falling through")
            else:
                _file_sid = _read_session_id_from_file(_auth_path)
                if _file_sid != _auth_uuid:
                    if _auth_ambiguity:
                        _log(session_dir, f"TIER0=concurrent_launch_misattribution recorded={_auth_uuid[:12]}... actual={(_file_sid or 'unreadable')[:12]}... — falling through")
                    else:
                        _log(session_dir, f"TIER0=uuid_mismatch recorded={_auth_uuid[:12]}... actual={(_file_sid or 'unreadable')[:12]}... — falling through")
                else:
                    # PRIMARY staleness anchor: jsonl first-available timestamp
                    # > spawn_ts. claude_spawn_ts was captured pre-Popen by the
                    # wrapper (S1) and cannot be rewritten by _file_watcher.
                    # This is the only cross-episode-safe freshness check.
                    _first_ts_ms = _read_first_line_timestamp_ms(_auth_path)
                    if _first_ts_ms is None:
                        _log(session_dir, "TIER0=jsonl_ts_unreadable — falling through")
                    elif _first_ts_ms <= _auth_spawn_ts:
                        _log(session_dir, f"TIER0=jsonl_older_than_spawn jsonl_ts={_first_ts_ms} spawn_ts={_auth_spawn_ts} — falling through")
                    else:
                        # SECONDARY (defense-in-depth, NOT load-bearing):
                        # jsonl mtime > cycle.state mtime. cycle.state is
                        # rewritten by _file_watcher <1s after Popen so the
                        # informative window is narrow. Kept for parity with
                        # tier-2 cache_stale semantics.
                        try:
                            _t_mtime = os.path.getmtime(_auth_path)
                            _cs_mtime = os.path.getmtime(state_file)
                            if _t_mtime <= _cs_mtime:
                                _log(session_dir, f"TIER0=stale transcript_mtime={_t_mtime} cycle_state_mtime={_cs_mtime} — falling through")
                            else:
                                _log(session_dir, f"TIER0=match file={os.path.basename(_auth_path)} uuid={_auth_uuid[:12]}... spawn_delta_ms={_first_ts_ms - _auth_spawn_ts}")
                                _write_cache(cache_file, _auth_path)
                                _write_cached_uuid(mapping_file, _auth_uuid)
                                return _auth_path
                        except OSError:
                            _log(session_dir, "TIER0=stat_failed — falling through")
        except (json.JSONDecodeError, IOError, OSError) as _exc:
            _log(session_dir, f"TIER0=read_failed {type(_exc).__name__} — falling through")

    try:
        with open(pidfile) as f:
            pid = int(f.read().strip())
    except (IOError, ValueError, OSError):
        # No PID file — can't scan /proc. Still try cache/fallback.
        pid = None

    # Tier 1: /proc/{pid}/fd scanning (authoritative when fd is open).
    # On WSL2 this path is typically empty because Claude Code does not
    # keep the transcript fd persistently open. On non-WSL2 with multiple
    # fd candidates, the first candidate is returned non-deterministically.
    # This matches the pre-existing dead-compare fallback behavior (the
    # deleted sessionId compare always fell through to fd_candidates[0]
    # anyway, since file_sid == env_session_id was a namespace mismatch)
    # and is out-of-scope for SI-1.
    fd_result = None
    if pid is not None:
        project_dir = os.path.expanduser('~/.claude/projects/')
        fd_dir = '/proc/{}/fd'.format(pid)
        fd_candidates = []
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(os.path.join(fd_dir, fd))
                    if (target.startswith(project_dir)
                            and target.endswith('.jsonl')
                            and '/subagents/' not in target):
                        fd_candidates.append(target)
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            pass

        if fd_candidates:
            fd_result = fd_candidates[0]
            _log(session_dir, f"fd scan: {len(fd_candidates)} candidate(s), returning first: {os.path.basename(fd_result)}")

    if fd_result:
        # Authoritative discovery — update cache and return.
        # Also persist the claude-code-uuid mapping so a subsequent fire
        # that falls back (e.g. fd not open) takes the deterministic
        # tier-3a mapping-read path rather than re-running the heuristic.
        _write_cache(cache_file, fd_result)
        picked_uuid = _read_session_id_from_file(fd_result)
        if picked_uuid:
            _write_cached_uuid(mapping_file, picked_uuid)
            _log(session_dir, f"fd scan: uuid={picked_uuid[:12]}... — mapping cached")
        else:
            _log(session_dir, f"fd scan: first-line sessionId unreadable for {os.path.basename(fd_result)} — mapping NOT cached")
        return fd_result

    if pid is not None:
        # Gate fd-scan noise: log once per episode using a sentinel file.
        # Each hook invocation is a fresh Python process, so module-level
        # state does not persist across calls. The sentinel file
        # {session_dir}/fd-scan-logged-episode stores the episode number of
        # the last logged failure. bin/claude-session deletes it at each
        # episode boundary, guaranteeing exactly one log line per episode.
        fd_log_sentinel = os.path.join(session_dir, 'fd-scan-logged-episode')
        should_log = True
        ep = _read_cycle_state_episode(state_file)
        if ep is not None:
            try:
                with open(fd_log_sentinel) as _f:
                    logged_ep = int(_f.read().strip())
                if logged_ep == ep:
                    should_log = False
            except (IOError, OSError, ValueError):
                pass  # Missing or corrupt sentinel -> log
        if should_log:
            _log(session_dir, "fd scan: no .jsonl fds found for pid={} — trying cache/fallback".format(pid))
            if ep is not None:
                try:
                    with open(fd_log_sentinel, 'w') as _f:
                        _f.write(str(ep))
                except (IOError, OSError):
                    pass  # Sentinel write failure is non-critical

    # Tier 2: cached path (no sessionId revalidation — see find_transcript
    # docstring "Known limitations" for rationale). The mapping-read branch
    # below corrects tier-2 poisoning across fires of the same episode.
    cached = _read_cache(cache_file)
    if cached and os.path.exists(cached):
        # Defense-in-depth: reject cache from a previous episode.
        # cycle.state is rewritten at each episode start; if the cache
        # file predates it, the transcript belongs to an earlier episode.
        try:
            cache_stale = os.path.getmtime(cache_file) < os.path.getmtime(state_file)
        except OSError:
            cache_stale = False  # If we can't check, allow the cache
        if not cache_stale:
            try:
                age = time.time() - os.path.getmtime(cached)
                if age < 300:  # 5 minutes — file still being written to
                    return cached
            except OSError:
                pass

    # Tier 3: dir-scan with mapping-read branch (3a) and heuristic fallback (3b).
    # Tier 3a is authoritative on fire #2+ of an episode once fire #1 has
    # populated {session_dir}/claude-code-uuid. Tier 3b is the fire-#1 (or
    # mapping-miss) fallback and writes the mapping on success.
    if cwd:
        project_dir_outer = os.path.expanduser('~/.claude/projects/')
        slug = _claude_project_slug(cwd)
        transcript_dir = os.path.join(project_dir_outer, slug)
        try:
            candidates = [
                f for f in os.listdir(transcript_dir)
                if f.endswith('.jsonl') and f != 'history.jsonl'
            ]
            if candidates:
                _log(session_dir, f"dir scan: {len(candidates)} .jsonl candidate(s) in {transcript_dir}")

                # Tier 3a: mapping-read branch. Read the persisted Claude Code
                # UUID from a prior fire and return the file whose first-line
                # sessionId matches. Both sides of this compare are in the
                # same Claude Code UUID namespace, so the env-var-vs-file-sid
                # namespace bug (R1 dead-code) cannot reoccur here.
                cached_uuid = _read_cached_uuid(mapping_file)
                mapping_stale = False
                mapping_known_bad = False
                if cached_uuid:
                    try:
                        # 100ms slack symmetric with stickiness predicate; same-second
                        # writes of mapping vs cycle.state are treated as cross-episode.
                        mapping_stale = os.path.getmtime(mapping_file) < os.path.getmtime(state_file) + 0.1
                    except OSError:
                        mapping_stale = False  # allow if we can't check
                    if mapping_stale:
                        _log(session_dir, f"dir scan: mapping rejected — claude-code-uuid older than cycle.state (cross-episode)")
                        cached_uuid = None
                        mapping_known_bad = True

                if cached_uuid:
                    for fname in candidates:
                        fpath = os.path.join(transcript_dir, fname)
                        file_sid = _read_session_id_from_file(fpath)
                        if file_sid == cached_uuid:
                            _log(session_dir, f"dir scan: mapping match — {fname} (uuid={cached_uuid[:12]}...)")
                            _write_cache(cache_file, fpath)
                            return fpath
                    _log(session_dir, f"WARN: mapping UUID {cached_uuid[:12]}... did not match any of {len(candidates)} files — falling through to heuristic; mapping will be overwritten")
                    mapping_known_bad = True

                # Tier 3b: PID-filtered mtime/size heuristic. Runs on fire
                # #1 (no mapping yet) or when tier-3a found no match.
                #
                # Step 1: attempt PID-based candidate filtering. Walk the
                # process tree from {session_dir}/.claude.pid to identify the
                # owning Claude Code PID, then restrict candidates to files
                # whose absolute path appears in /proc/{pid}/fd/ symlinks.
                # If exactly one PID-matched candidate remains, select it
                # (authoritative). If zero remain, or PID lookup fails, or
                # /proc is unreadable, fall through to the mtime heuristic on
                # the full candidate list.
                #
                # KNOWN LIMITATION: fire-#1 poisoning lock-in is NARROWED
                # but not eliminated. If the PID filter picks exactly one
                # candidate and it is the wrong one (stale PID still holds an
                # fd to a terminated-but-undeleted transcript from a prior
                # concurrent session), the lock-in still occurs. The mtime
                # delta logged below supports post-hoc diagnosis. Episode
                # boundary (cycle.state mtime > mapping mtime, plus
                # bin/claude-session cleanup) remains the recovery path.
                heuristic_candidates = candidates  # default: full list
                try:
                    target_pid = _claude_pid_from_proc_tree(session_dir)
                    if target_pid is not None:
                        pid_matched = [
                            f for f in candidates
                            if _pid_owns_file(target_pid, os.path.join(transcript_dir, f))
                        ]
                        if len(pid_matched) == 1:
                            # Authoritative PID match — select directly.
                            best = pid_matched[0]
                            result = os.path.join(transcript_dir, best)
                            try:
                                best_mtime_ts = os.path.getmtime(result)
                                mtime_delta = time.time() - best_mtime_ts
                                best_mtime_fmt = datetime.fromtimestamp(best_mtime_ts, tz=timezone.utc).strftime("%H:%M:%S")
                                _log(session_dir, f"PID_FILTER=matched_exactly_one pid={target_pid} file={best} mtime={best_mtime_fmt} mtime_delta_s={mtime_delta:.1f}")
                            except OSError:
                                _log(session_dir, f"PID_FILTER=matched_exactly_one pid={target_pid} file={best}")
                            _write_cache(cache_file, result)
                            picked_uuid = _read_session_id_from_file(result)
                            if picked_uuid:
                                is_sticky, _age = _tier3b_mapping_still_valid_for_episode(mapping_file, state_file)
                                if is_sticky and not mapping_known_bad:
                                    existing = _read_cached_uuid(mapping_file) or ""
                                    _log(session_dir, f"MAPPING_WRITE=skipped_sticky existing_uuid={existing[:12]}... picked_uuid={picked_uuid[:12]}...")
                                else:
                                    _write_cached_uuid(mapping_file, picked_uuid)
                                    _log(session_dir, f"MAPPING_WRITE=written source=pid_filter uuid={picked_uuid[:12]}... file={best}")
                            else:
                                _log(session_dir, f"PID filter initial pick {best} — first-line sessionId unreadable, mapping NOT cached")
                            return result
                        elif len(pid_matched) == 0:
                            _log(session_dir, f"PID_FILTER=0_candidates_fell_through pid={target_pid} — falling back to mtime heuristic")
                            # heuristic_candidates stays as full candidate list
                        else:
                            # Multiple PID-matched files — ambiguous; fall through
                            _log(session_dir, f"PID_FILTER=multiple_matched pid={target_pid} count={len(pid_matched)} — falling back to mtime heuristic")
                    else:
                        _log(session_dir, "PID_FILTER=failed_to_read_proc — falling back to mtime heuristic")
                except Exception as _pid_exc:
                    _log(session_dir, f"PID_FILTER=failed_to_read_proc exc={type(_pid_exc).__name__} — falling back to mtime heuristic")

                # Mtime/size heuristic fallback (runs when PID filter fell through).
                def _mtime(f):
                    return os.path.getmtime(os.path.join(transcript_dir, f))
                max_mtime = max(_mtime(f) for f in heuristic_candidates)
                # "Recent" = within 120s of the newest file
                recent = [f for f in heuristic_candidates if max_mtime - _mtime(f) < 120]
                if len(recent) > 1:
                    # Tie-break: smallest file is the newest conversation.
                    # Among same-size files, prefer newest mtime.
                    best = min(
                        recent,
                        key=lambda f: (os.path.getsize(os.path.join(transcript_dir, f)),
                                       -_mtime(f))
                    )
                else:
                    best = recent[0] if recent else max(heuristic_candidates, key=_mtime)
                result = os.path.join(transcript_dir, best)
                mtime_delta = None
                try:
                    best_size = os.path.getsize(result)
                    best_mtime_ts = os.path.getmtime(result)
                    mtime_delta = time.time() - best_mtime_ts
                    best_mtime = datetime.fromtimestamp(best_mtime_ts, tz=timezone.utc).strftime("%H:%M:%S")
                    _log_both(session_dir, f"HEURISTIC=selected_by_mtime file={best} recent_pool={len(recent)} size={best_size} mtime={best_mtime} mtime_delta_s={mtime_delta:.1f} WARN=may_be_wrong_if_concurrent")
                except OSError:
                    _log_both(session_dir, f"HEURISTIC=selected_by_mtime file={best} recent_pool={len(recent)} WARN=may_be_wrong_if_concurrent")
                # STALENESS-CEILING-FIX: Tier-3b rejects too-old files.
                # A transcript older than _HEURISTIC_MAX_MTIME_DELTA_S cannot
                # belong to the live session — its cached_read_input_tokens
                # totals are from a prior unrelated session and would trigger
                # a spurious cycle (see CYCLE-COUNT-BUG-diag-counter.md).
                # Fail open: return None so the caller skips cycle-pending.
                if mtime_delta is not None and mtime_delta > _HEURISTIC_MAX_MTIME_DELTA_S:
                    _log_both(
                        session_dir,
                        f"HEURISTIC=fail_open_stale_pick file={best} "
                        f"mtime_delta_s={mtime_delta:.1f} "
                        f"ceiling_s={_HEURISTIC_MAX_MTIME_DELTA_S} "
                        f"action=return_None"
                    )
                    return None
                # CYCLE-COUNT-BUG-FIX: Tier-3b fail-open.
                # Hoist the sessionId read to before the cache write so we can
                # gate on it. When recent_pool==1 and the picked file's first-line
                # sessionId is unreadable we cannot verify the file belongs to THIS
                # session — trusting it produces a confidently-wrong cycle-pending
                # (see CYCLE-COUNT-BUG-diag-threshold.md). Fail open: return None
                # so the caller skips the cycle decision for this fire.
                picked_uuid = _read_session_id_from_file(result)
                if len(recent) == 1 and picked_uuid is None:
                    _log_both(
                        session_dir,
                        f"HEURISTIC=fail_open_unverifiable file={best} "
                        f"reason=recent_pool_1_and_sessionId_unreadable "
                        f"action=return_None"
                    )
                    return None
                # Cache heuristic result to prevent re-discovery on every call
                _write_cache(cache_file, result)
                # Persist the picked file's UUID for fire #2+ deterministic
                # mapping-read. If sessionId unreadable, skip the mapping
                # write (strictly no worse than today).
                if picked_uuid:
                    is_sticky, _age = _tier3b_mapping_still_valid_for_episode(mapping_file, state_file)
                    if is_sticky and not mapping_known_bad:
                        existing = _read_cached_uuid(mapping_file) or ""
                        _log(session_dir, f"MAPPING_WRITE=skipped_sticky existing_uuid={existing[:12]}... picked_uuid={picked_uuid[:12]}...")
                    else:
                        _write_cached_uuid(mapping_file, picked_uuid)
                        _log(session_dir, f"MAPPING_WRITE=written source=heuristic uuid={picked_uuid[:12]}... file={best}")
                else:
                    _log(session_dir, f"heuristic initial pick {best} — first-line sessionId unreadable, mapping NOT cached")
                return result
        except (OSError, PermissionError) as e:
            _log(session_dir, f"WARN: dir scan failed: {type(e).__name__}: {e}")

    _log(session_dir, "ERROR: find_transcript returned None — all discovery methods failed")
    return None


def get_threshold(cwd):
    """Read cycling threshold from config. Priority:
    1. .claude/session-cycling.json
    2. .claude/settings.json sessionCycling.threshold
    3. fallback: 190000
    """
    config = os.path.join(cwd, ".claude", "session-cycling.json")
    if os.path.exists(config):
        try:
            with open(config) as f:
                return int(json.load(f).get("threshold", 190000))
        except (json.JSONDecodeError, ValueError, IOError):
            pass

    settings = os.path.join(cwd, ".claude", "settings.json")
    if os.path.exists(settings):
        try:
            with open(settings) as f:
                sc = json.load(f).get("sessionCycling", {})
                if "threshold" in sc:
                    return int(sc["threshold"])
        except (json.JSONDecodeError, ValueError, IOError):
            pass

    return 190000


def create_cycle_pending(pending_file, total_tokens):
    """Write cycle-pending JSON file. Content is diagnostic only;
    file presence is what matters for the cycling signal.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "tokens": total_tokens,
        "source": "hook",
        "created": ts,
    }
    try:
        tmp = pending_file + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(payload, f)
        os.replace(tmp, pending_file)
    except (IOError, OSError):
        pass


def _write_fail_closed_pending(session_dir, pending_file,
                               source="token-extraction-returned-none"):
    """Write a fail-closed cycle-pending sentinel (REQ-6).

    `source` distinguishes the fail-closed shapes so the injected directive
    and session-status display are not misattributed. Each maps to a distinct
    main() branch and a distinct inject_warning diagnosis arm:
    Listed in the same order as inject_warning's diagnosis branches so the
    two stay cross-referenceable:
      - "token-extraction-returned-none": the transcript WAS successfully
        discovered (Tier 0 matched the right file) and is a readable file, but
        extract_total_tokens returned None on it. Written by the RECOVERY-B
        branch after exhausting alt-slug recovery.
      - "transcript-unreadable": find_transcript DID return a path, but that
        path is not a readable regular file — it does not exist, is a directory,
        or is a broken symlink, so the RECOVERY-B isfile guard skipped it.
        Written by the unreadable-transcript branch. (A permission-unreadable
        regular file has isfile True and routes to RECOVERY-B / Block 2, not here.)
      - "transcript-discovery-failed": no transcript path was located at all
        (find_transcript returned None, including after RECOVERY-A alt-slug
        retry). Written by the transcript-missing branch.

    Writing cycle-pending forces session(action='status') to report
    cycle_pending=true rather than the misleading '0 tokens + cycle_pending=false'
    healthy state. tokens=0 signals an unknown count; operators should treat this
    as a non-healthy state requiring intervention.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "tokens": 0,
        "source": source,
        "created": ts,
    }
    try:
        tmp = pending_file + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(payload, f)
        os.replace(tmp, pending_file)
        _log(session_dir,
             f"RECOVERY=fail_closed: wrote cycle-pending source={source} "
             "(session status will show cycle_pending=true)")
    except (IOError, OSError):
        pass


# === Family A: per-episode input_tokens instrumentation (E4.1–E4.9) ===
# Reuses extract_total_tokens above; does NOT re-parse transcripts.
# total_tokens is passed in as a parameter from the already-computed value in main().

EPISODE_TOKENS_FILE_NAME = "episode-tokens.jsonl"
# schema_version: additive fields do not bump version; renames or type changes bump.
EPISODE_TOKENS_SCHEMA_VERSION = 1


def _read_cycle_state_episode(state_file):
    """Return the integer 'episode' field from cycle.state, or None on any error."""
    try:
        with open(state_file) as f:
            data = json.load(f)
        ep = data.get("episode")
        return int(ep) if isinstance(ep, (int, float)) else None
    except (IOError, OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _within_coldstart_grace(session_dir, grace_s=120):
    """True if the current episode is young enough that a tokenless transcript
    is plausibly still warming up rather than genuinely broken.

    Primary anchor: claude_spawn_ts in authoritative-transcript.json, matched
    to the current episode. Fallback anchor (cold-start window, when the
    primary is absent/unparseable): cycle.state mtime — see the except branch
    below for why the fallback exists and why it preserves the genuine
    fail-close. Fails toward False (do-not-suppress, fail-close) when neither
    anchor proves the episode is young, so a genuinely-broken or old session
    always eventually fail-closes.
    """
    auth_file = os.path.join(session_dir, "authoritative-transcript.json")
    state_file = os.path.join(session_dir, "cycle.state")
    try:
        with open(auth_file) as f:
            auth = json.load(f)
    except (IOError, OSError, json.JSONDecodeError):
        # Cold-start anchor-timing repair (Fix 1). The primary anchor,
        # authoritative-transcript.json, is written by bin/claude-session's
        # watcher only AFTER it detects the episode's JSONL — an UNBOUNDED delay
        # on episode 1 (the operator may not send a first message for minutes).
        # During that window the anchor is absent here, so the old behavior
        # (return False) fail-closed and a benign warmup tokenless reading
        # force-cycled the session on its first tool calls. Fall back to
        # cycle.state's mtime as the episode-start estimate: the launcher writes
        # cycle.state pre-Popen and the watcher updates it <1s post-Popen, and
        # it is rewritten ONLY at episode boundaries (never mid-episode), so its
        # mtime ~= the current episode's start. A genuinely OLD session (mtime
        # older than grace_s) still fail-closes — preserving the
        # high-context-corruption protection regardless of transcript size.
        try:
            age_s = time.time() - os.path.getmtime(state_file)
        except OSError:
            return False  # no episode anchor at all -> fail-close
        within = age_s < grace_s
        if within:
            _log(session_dir,
                 f"COLDSTART_GRACE: suppressing fail-closed write via cycle.state "
                 f"mtime fallback (age_s={age_s:.1f} < grace_s={grace_s}; "
                 f"authoritative-transcript.json absent/unparseable)")
        return within
    spawn_ts = auth.get("claude_spawn_ts")
    if not isinstance(spawn_ts, (int, float)) or spawn_ts <= 0:
        return False  # untrustworthy anchor (mirrors find_transcript FLAG 2)
    # Episode-match: reject a stale anchor left over from a prior episode.
    auth_ep = auth.get("episode")
    cur_ep = _read_cycle_state_episode(state_file)
    if auth_ep is None or cur_ep is None or int(auth_ep) != cur_ep:
        return False
    now_ms = int(time.time() * 1000)
    within = (now_ms - spawn_ts) < (grace_s * 1000)
    if within:
        _log(session_dir,
             f"COLDSTART_GRACE: suppressing fail-closed write "
             f"(elapsed_ms={now_ms - spawn_ts} < grace_ms={grace_s * 1000}, ep={cur_ep})")
    return within


def _episode_already_in_jsonl(jsonl_path, episode_number):
    """Defense-in-depth scan: return True if episode_number already has a JSONL row.

    O(lines) but expected lines <= max_episodes (~20 entries). This fallback runs
    only when the sentinel file is absent — the fast path is os.path.exists(sentinel).

    NOTE: If a debug user manually deletes BOTH the sentinel AND this JSONL row
    mid-run, dedup will re-emit for that episode. This is intentional: manual
    deletion of both dedup anchors resets the dedup, which is the expected outcome
    for a forced re-measurement. Downstream can also dedupe by (episode_number,
    session_id) if duplicate rows appear from a parallel-tool-call race (see
    emit_episode_tokens docstring).
    """
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("episode_number") == episode_number:
                    return True
    except (IOError, OSError, FileNotFoundError):
        pass
    return False


def emit_episode_tokens(session_dir, session_id, state_file, tool_name, total_tokens):
    """Append one JSONL line per episode to episode-tokens.jsonl.

    Called from main() immediately after extract_total_tokens() returns a non-None
    value. The total_tokens parameter is the already-computed value — this function
    does NOT re-parse the transcript (satisfies E4.1).

    Dedup is two-tier:
      1. Fast path: os.path.exists(sentinel_path) — one syscall, returns early.
      2. Fallback (JSONL scan): when sentinel is missing (e.g., deleted manually).
         On fallback hit, re-creates the sentinel so subsequent calls take the fast path.

    PARALLEL-TOOL-CALL RACE: if two PostToolUse hooks fire near-simultaneously
    within the same episode (possible with parallel tool calls), both may pass the
    sentinel check before either writes it, producing at most one duplicate JSONL row.
    The JSONL scan absorbs this only if one of the two processes completes its append
    before the other checks. Worst case: both append. Downstream analysis MUST dedupe
    by (episode_number, session_id) if duplicate rows matter for its calculations.

    All disk ops are wrapped in try/except — never raises, never blocks tool execution.
    Errors are logged to cycle-hook.log via _log().
    """
    try:
        episode = _read_cycle_state_episode(state_file)
        if episode is None:
            return

        sentinel_path = os.path.join(session_dir, f"episode-tokens-sentinel-{episode}")
        # Fast path: sentinel exists → already emitted for this episode.
        if os.path.exists(sentinel_path):
            return

        jsonl_path = os.path.join(session_dir, EPISODE_TOKENS_FILE_NAME)
        # Defense-in-depth: sentinel may have been manually deleted; check JSONL.
        if _episode_already_in_jsonl(jsonl_path, episode):
            # Re-create sentinel so subsequent calls take the O(1) fast path.
            try:
                open(sentinel_path, "w").close()
            except (IOError, OSError):
                pass
            return

        entry = {
            "schema_version": EPISODE_TOKENS_SCHEMA_VERSION,
            "episode_number": episode,
            "session_id": session_id,
            "start_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "tool_name": tool_name or "",
            "input_tokens_actual": total_tokens,
            "input_tokens_heuristic": None,  # reserved; populated by a future hook
            "delta": None,  # input_tokens_actual - input_tokens_heuristic when both present
        }
        try:
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except (IOError, OSError):
            _log(session_dir, f"emit_episode_tokens: JSONL append failed (ep={episode}) — sentinel NOT written")
            return  # Do NOT write sentinel — next call will retry the full dedup path

        try:
            open(sentinel_path, "w").close()
        except (IOError, OSError):
            _log(session_dir, f"emit_episode_tokens: sentinel write failed (ep={episode}) — next call will re-scan JSONL (still correct)")

        _log(session_dir, f"emit_episode_tokens: ep={episode} tokens={total_tokens} tool={tool_name or ''}")
    except Exception as e:
        _log(session_dir, f"emit_episode_tokens: unexpected error {type(e).__name__}: {e}")


# KEEP IN SYNC with .claude/hooks/critic-gate-tracker.py (_sentinel_session_dir)
def _sentinel_session_dir(session_id):
    """Canonical sentinel-directory path — MUST match critic-gate-tracker.py.

    Derive project root from __file__ (same logic as main() at lines
    489-492). Both cycle-hook.py and critic-gate-tracker.py live in
    .claude/hooks/, so the three-parents-up walk yields the same root.
    """
    # .claude/hooks/cycle-hook.py → project root = 3 parents up
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(cwd, ".agent_context", "sessions", session_id, "subagent-active")


def _get_sentinel_ttl(default=180):
    """Read sentinel_ttl from .claude/session-cycling.json with a default fallback.

    Both any_fresh_subagent_active() and cleanup_stale_sentinels() must use the
    same TTL source so that cleanup cannot delete sentinels that suppression still
    considers fresh. Uses the same path-derivation as get_threshold().
    """
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config = os.path.join(cwd, ".claude", "session-cycling.json")
    if os.path.exists(config):
        try:
            with open(config) as f:
                return int(json.load(f).get("sentinel_ttl", default))
        except (json.JSONDecodeError, ValueError, IOError):
            pass  # Ignore config errors — fall back to default
    return default


def clear_sentinel(session_id, tool_use_id):
    """Fail-soft unlink of {session_dir}/subagent-active/{tool_use_id}.sentinel.

    Called on PostToolUse/Agent for foreground agents to precisely clear
    the sentinel written by critic-gate-tracker.py at PreToolUse/Agent time.
    FileNotFoundError is swallowed (idempotent — background agent paths skip
    this call; the sentinel may already have TTL-expired).

    # sentinel dir layout derived via _sentinel_session_dir() helper
    """
    sentinel_dir = _sentinel_session_dir(session_id)
    sentinel_path = os.path.join(sentinel_dir, f"{tool_use_id}.sentinel")
    log_target = os.path.dirname(sentinel_dir)  # == session_dir
    try:
        os.unlink(sentinel_path)
        _log(log_target, f"cleared subagent sentinel {tool_use_id}.sentinel")
    except FileNotFoundError:
        _log(log_target, f"clear_sentinel no-op (missing): {tool_use_id}.sentinel")


def clear_bg_bash_sentinel(session_id, task_id, source="unknown"):
    """Fail-soft unlink of {sentinel_dir}/bg-bash-{task_id}.sentinel.

    Called by PostToolUse/BashOutput (on terminal status) and
    PreToolUse/KillShell. FileNotFoundError is swallowed — the sentinel
    may have already been removed by TTL or a prior clear.
    """
    if not task_id:
        return
    sentinel_dir = _sentinel_session_dir(session_id)
    path = os.path.join(sentinel_dir, f"bg-bash-{task_id}.sentinel")
    log_target = os.path.dirname(sentinel_dir)  # == session_dir
    try:
        os.unlink(path)
        _log(log_target, f"cleared bg-bash sentinel task_id={task_id} (source: {source})")
    except FileNotFoundError:
        _log(log_target, f"clear_bg_bash no-op (missing): task_id={task_id} (source: {source})")
    except OSError as e:
        _log(log_target, f"clear_bg_bash failed: {type(e).__name__}: {e} task_id={task_id}")


def _handle_post_bashoutput(event, session_id, session_dir):
    """PostToolUse/BashOutput: clear bg-bash sentinel on terminal status.

    Extracts task_id from tool_input (accepts task_id / shell_id / bash_id).
    Clears sentinel when status ∈ {completed, killed, failed}; no-op on 'running'
    or missing/malformed tool_response (H8-H12).
    """
    _log(session_dir, f"BASHOUT_DEBUG tool_input={json.dumps(event.get('tool_input'), default=str)[:300]} tool_response={json.dumps(event.get('tool_response'), default=str)[:300]}")

    tool_input = event.get('tool_input') or {}
    task_id = (
        tool_input.get('task_id')
        or tool_input.get('shell_id')
        or tool_input.get('bash_id')
        or ''
    )

    tool_response = event.get('tool_response')
    if not isinstance(tool_response, dict):
        # H12: malformed or missing tool_response — silent no-op
        return

    status = tool_response.get('status', '')
    if status in ('completed', 'killed', 'failed'):
        clear_bg_bash_sentinel(session_id, task_id, source=f"bashoutput-{status}")
    # status == 'running' or any other value: no-op (H10)


def _handle_pre_killshell(event, session_id, session_dir):
    """PreToolUse/KillShell: clear bg-bash sentinel for the targeted shell.

    Extracts task_id from tool_input (accepts task_id or deprecated shell_id).
    Sentinel deletion is fail-soft (H13-H14).
    """
    _log(session_dir, f"KILLSHELL_DEBUG tool_input={json.dumps(event.get('tool_input'), default=str)[:300]}")

    tool_input = event.get('tool_input') or {}
    task_id = tool_input.get('task_id') or tool_input.get('shell_id') or ''
    clear_bg_bash_sentinel(session_id, task_id, source="killshell")


def any_fresh_subagent_active(session_id, session_dir, ttl_seconds=None):
    """Return True if any sentinel in the session sentinel dir is fresh.

    A sentinel is fresh if its mtime is within `ttl_seconds` of now.
    Stale sentinels are logged and treated as absent (no unlink — that
    would race with concurrent writers spawning the next subagent).
    A missing directory is not an error; returns False.

    ttl_seconds defaults to the config value (sentinel_ttl in
    .claude/session-cycling.json, fallback 180). Pass an explicit value
    only in tests.

    GHOST-SENTINEL WINDOW (reduced by targeted clearing): background
    agent sentinels are now actively cleared at SubagentStop via
    _clear_background_sentinel_on_stop(). The ghost window persists
    only when: (a) tool_use_id was absent from the delegation-trace
    entry (synthetic sentinel key, uncorrelatable), (b) SubagentStop
    did not fire (agent crash), or (c) count-based correlation
    drifted (out-of-order completion). TTL remains as defense-in-depth.
    """
    if ttl_seconds is None:
        ttl_seconds = _get_sentinel_ttl()
    sentinel_dir = _sentinel_session_dir(session_id)
    try:
        entries = os.listdir(sentinel_dir)
    except (OSError, FileNotFoundError):
        return False
    now = time.time()
    has_fresh = False
    for name in entries:
        full = os.path.join(sentinel_dir, name)
        try:
            age = now - os.path.getmtime(full)
        except OSError:
            continue
        if age < ttl_seconds:
            has_fresh = True
            # don't break — log stale ones too for observability
            continue
        # Stale sentinel — log and treat as absent. No unlink (would
        # race with concurrent writers for the next subagent spawn).
        _log(
            session_dir,
            f"stale subagent sentinel {name} age={age:.0f}s >= TTL={ttl_seconds}s — treating as absent",
        )
    return has_fresh


def cleanup_stale_sentinels(session_id, session_dir, ttl_seconds=None):
    """Delete sentinel files in the subagent-active directory that have expired.

    Scans {sentinel_dir}/ for .sentinel files with mtime older than ttl_seconds
    and unlinks them. Fresh sentinels (age < ttl_seconds) are preserved.

    This cleanup is behaviorally identical to the TTL filtering in
    any_fresh_subagent_active() — stale sentinels are already invisible to
    suppression logic. Deleting them only reduces disk and log noise
    (specifically: POTENTIAL_MISFIRE log entries that fire when a stale
    sentinel is found during re-scan after a cycle warning).

    Fail-soft: FileNotFoundError and OSError are swallowed per-file to avoid
    disrupting the hook on concurrent writes. Missing sentinel directory is
    not an error — returns silently.

    ttl_seconds defaults to the config value (sentinel_ttl in
    .claude/session-cycling.json, fallback 180). Pass an explicit value
    only in tests.

    Called from main() on SubagentStop events and unconditionally on every
    PostToolUse fire (secondary defense for edge cases where SubagentStop
    never fires or targeted clearing fails).
    """
    if ttl_seconds is None:
        ttl_seconds = _get_sentinel_ttl()

    sentinel_dir = _sentinel_session_dir(session_id)
    try:
        entries = os.listdir(sentinel_dir)
    except (OSError, FileNotFoundError):
        return  # Missing directory is not an error

    now = time.time()
    for name in entries:
        full = os.path.join(sentinel_dir, name)
        try:
            age = now - os.path.getmtime(full)
        except OSError:
            continue
        if age < ttl_seconds:
            continue  # Fresh sentinel — do not delete
        try:
            os.unlink(full)
            _log(
                session_dir,
                f"cleanup_stale_sentinels: deleted {name} age={age:.0f}s >= TTL={ttl_seconds}s",
            )
        except FileNotFoundError:
            _log(
                session_dir,
                f"cleanup_stale_sentinels: {name} already gone (FileNotFoundError)",
            )
        except OSError as e:
            _log(
                session_dir,
                f"cleanup_stale_sentinels: failed to delete {name}: {type(e).__name__}: {e}",
            )


def _clear_background_sentinel_on_stop(session_id, session_dir):
    """Targeted sentinel clear for background agent that just completed.

    Uses delegation-trace.jsonl to find the tool_use_id of the completed
    background agent via count-based index correlation: the nth SubagentStop
    corresponds to the nth background launch in trace order.

    Counts ALL background launches (not just GENERATIVE_TYPES) because
    sentinels are created for ALL agent types (critic-gate-tracker.py:223-227).

    NOTE: Count-based correlation assumes SubagentStop events arrive in the
    same order as background agent launches. Out-of-order completion (common
    with multiple concurrent background agents) causes miscorrelation, which
    is a no-op via fail-soft clear_sentinel (FileNotFoundError swallowed).
    TTL handles uncleaned sentinels in those cases.

    Counter race: two concurrent SubagentStop events reading the same counter
    value means the counter advances by 1 instead of 2. Self-healing: the
    next SubagentStop re-processes the skipped index.

    Fail-soft: missing trace file, correlation failure, empty tool_use_id,
    or IOError on counter file all result in no-op (sentinel falls back to
    TTL cleanup).
    """
    trace_file = os.path.join(session_dir, "delegation-trace.jsonl")
    counter_file = os.path.join(session_dir, "sentinel-bg-stop-count")

    # Read how many SubagentStop events have been processed so far.
    stop_count = 0
    try:
        with open(counter_file) as f:
            stop_count = int(f.read().strip())
    except (FileNotFoundError, ValueError, IOError):
        pass  # First fire or corrupt — start from 0

    # Find the nth background entry in delegation-trace.jsonl (0-indexed).
    tool_use_id = None
    bg_index = 0
    try:
        with open(trace_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not entry.get("background"):
                    continue
                if bg_index == stop_count:
                    tool_use_id = entry.get("tool_use_id", "") or ""
                    break
                bg_index += 1
    except (FileNotFoundError, IOError) as e:
        _log(session_dir, f"_clear_background_sentinel_on_stop: trace read error: {type(e).__name__}: {e}")
        return

    if tool_use_id is None:
        # No correlated trace entry found (stop_count >= total bg launches).
        # Do NOT increment counter — no completion slot was consumed. If a new
        # background agent launches later, its entry will appear at this index.
        _log(
            session_dir,
            f"_clear_background_sentinel_on_stop: no trace entry at index {stop_count} — skipping",
        )
        return

    # Increment counter: this SubagentStop consumed a completion slot
    # regardless of whether we can clear its sentinel (empty tool_use_id).
    # Placed AFTER the None check (don't advance past non-existent entries)
    # but BEFORE clear_sentinel (crash between clear and increment = retry
    # on next SubagentStop = no-op since sentinel already gone).
    try:
        with open(counter_file, "w") as f:
            f.write(str(stop_count + 1))
    except IOError as e:
        _log(session_dir, f"_clear_background_sentinel_on_stop: counter write error: {type(e).__name__}: {e}")
        # Non-fatal — continue to attempt sentinel clear

    if not tool_use_id:
        # Entry found but tool_use_id absent (synthetic sentinel key, uncorrelatable).
        _log(
            session_dir,
            f"_clear_background_sentinel_on_stop: empty tool_use_id at index {stop_count} — falling back to TTL",
        )
        return

    # Clear the sentinel for the completed background agent.
    clear_sentinel(session_id, tool_use_id)
    _log(
        session_dir,
        f"sentinel-bg-clear: cleared bg sentinel at index {stop_count} tool_use_id={tool_use_id}",
    )


def _scan_claude_children(claude_pid):
    """Return set of integer child PIDs currently listed under /proc/{claude_pid}/task/*/children.

    Reads all thread-children files and unions the results. Empty set on any
    failure (missing /proc, permission error, or dead PID). Does NOT filter by
    comm — the snapshot-diff approach makes a comm filter unnecessary and would
    incorrectly drop children that exec'd between pre and post hooks.
    """
    import glob as _glob
    children = set()
    pattern = '/proc/{}/task/*/children'.format(claude_pid)
    try:
        for path in _glob.glob(pattern):
            try:
                with open(path) as f:
                    for token in f.read().split():
                        try:
                            children.add(int(token))
                        except ValueError:
                            pass
            except (IOError, OSError):
                pass
    except Exception:
        pass
    return children


def _read_proc_starttime(pid):
    """Return the integer starttime (clock ticks since boot) for pid, or None.

    Reads /proc/{pid}/stat. The stat format is:
      pid (comm) state ppid ... starttime ...
    comm can contain spaces and parens; we find the last ')' to skip it.
    starttime is field index 21 (0-based) AFTER the last ')' separator,
    which is field 22 in the 1-based /proc/pid/stat numbering.
    """
    try:
        with open('/proc/{}/stat'.format(pid)) as f:
            stat_line = f.read()
        rparen = stat_line.rfind(')')
        if rparen == -1:
            return None
        fields = stat_line[rparen + 2:].split()
        # fields[0]=state, fields[1]=ppid, ..., fields[19]=starttime (0-indexed)
        if len(fields) < 20:
            return None
        return int(fields[19])
    except (IOError, OSError, ValueError, IndexError):
        return None


def _bg_bash_prelaunch_sweep(prelaunch_dir, session_dir, max_age_s=60):
    """Delete stale prelaunch snapshot files older than max_age_s seconds.

    Defence against orphaned snapshots when PostToolUse never fires (crash,
    hook timeout). Called on every PreToolUse/Bash(bg=true) fire. Fail-soft.
    """
    if not os.path.isdir(prelaunch_dir):
        return
    now = time.time()
    try:
        for name in os.listdir(prelaunch_dir):
            if not name.endswith('.json'):
                continue
            path = os.path.join(prelaunch_dir, name)
            try:
                age = now - os.path.getmtime(path)
                if age >= max_age_s:
                    os.unlink(path)
                    _log(session_dir, f"bg_bash_prelaunch_sweep: deleted stale snapshot {name} age={age:.0f}s")
            except (IOError, OSError):
                pass
    except (IOError, OSError):
        pass


def _handle_pre_bash_bg(event, session_id, session_dir):
    """PreToolUse/Bash(run_in_background=true): write child-PID snapshot.

    Writes {session_dir}/bg-bash-prelaunch/{tool_use_id}.json with the current
    set of Claude's bash children so the PostToolUse branch can diff and
    identify the newly-launched shell.

    Fail-soft: any error is logged once; PostToolUse will fall back to the
    single-newest-child heuristic when the snapshot is missing (H0b).
    """
    tool_use_id = event.get('tool_use_id', '')
    if not tool_use_id:
        _log(session_dir, "bg_bash_pre: no tool_use_id — snapshot skipped")
        return

    prelaunch_dir = os.path.join(session_dir, 'bg-bash-prelaunch')

    # Stale-snapshot sweep first (H0c)
    _bg_bash_prelaunch_sweep(prelaunch_dir, session_dir)

    # Discover Claude PID
    claude_pid = _claude_pid_from_proc_tree(session_dir)
    if claude_pid is None:
        _log(session_dir, f"bg_bash_pre: could not determine claude_pid — snapshot skipped tool_use_id={tool_use_id}")
        return

    # Snapshot current children (no comm filter — diff handles attribution)
    pre_children = sorted(_scan_claude_children(claude_pid))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    payload = {
        'pre_children': pre_children,
        'claude_pid': claude_pid,
        'ts': ts,
    }

    try:
        os.makedirs(prelaunch_dir, exist_ok=True)
        snapshot_path = os.path.join(prelaunch_dir, f'{tool_use_id}.json')
        with open(snapshot_path, 'w') as f:
            json.dump(payload, f)
        _log(session_dir, f"bg_bash_pre: snapshot written tool_use_id={tool_use_id} pre_children={pre_children} claude_pid={claude_pid}")
    except (IOError, OSError) as e:
        _log(session_dir, f"bg_bash_pre: snapshot write failed tool_use_id={tool_use_id} err={type(e).__name__}: {e}")


def _handle_post_bash_bg(event, session_id, session_dir):
    """PostToolUse/Bash(run_in_background=true): write bg-bash sentinel via snapshot-diff.

    Reads the prelaunch snapshot, re-scans Claude's children, diffs to find
    the new PID, and writes {session_dir}/subagent-active/bg-bash-{task_id}.sentinel
    with JSON payload {pid, task_id, command, launch_time}.

    Fail-soft on all error paths — always writes a sentinel (possibly without pid).
    """
    tool_use_id = event.get('tool_use_id', '')
    tool_input = event.get('tool_input', {}) or {}
    tool_response = event.get('tool_response') or {}

    # H2: extract task_id — accept both casing variants defensively.
    # Instrumentation: log the first fire's tool_response for field-name confirmation.
    _log(session_dir, f"BG_BASH_DEBUG tool_response={json.dumps(tool_response, default=str)[:500]}")

    if isinstance(tool_response, dict):
        task_id = (tool_response.get('task_id') or tool_response.get('taskId') or '')
    else:
        task_id = ''

    if not task_id:
        _log(session_dir, f"bg_bash_post: missing task_id in tool_response tool_use_id={tool_use_id}")

    command = str(tool_input.get('command', '') or '')[:256]
    launch_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # H3: PID capture via snapshot-diff
    pid = None
    claude_pid = _claude_pid_from_proc_tree(session_dir)

    prelaunch_dir = os.path.join(session_dir, 'bg-bash-prelaunch')
    snapshot_path = os.path.join(prelaunch_dir, f'{tool_use_id}.json') if tool_use_id else None

    pre_children = None
    if snapshot_path and os.path.isfile(snapshot_path):
        try:
            with open(snapshot_path) as f:
                snap = json.load(f)
            pre_children = set(int(p) for p in (snap.get('pre_children') or []))
        except (IOError, OSError, json.JSONDecodeError, ValueError) as e:
            _log(session_dir, f"bg_bash_post: snapshot read failed tool_use_id={tool_use_id} err={type(e).__name__}: {e}")
    else:
        _log(session_dir, f"bg_bash_post: snapshot missing tool_use_id={tool_use_id} — falling back to newest-child heuristic")

    # Always clean up snapshot regardless of read success (H3 spec)
    if snapshot_path:
        try:
            os.unlink(snapshot_path)
        except FileNotFoundError:
            pass
        except (IOError, OSError) as e:
            _log(session_dir, f"bg_bash_post: snapshot unlink failed: {type(e).__name__}: {e}")

    if claude_pid is not None:
        post_children = _scan_claude_children(claude_pid)

        if pre_children is not None:
            # Snapshot-diff happy path
            new_pids = post_children - pre_children
            if len(new_pids) == 1:
                pid = next(iter(new_pids))
                _log(session_dir, f"bg_bash_post: snapshot-diff resolved pid={pid} tool_use_id={tool_use_id}")
            elif len(new_pids) == 0:
                _log(session_dir, f"bg_bash_post: bg_bash_no_new_pid tool_use_id={tool_use_id} post={sorted(post_children)} pre={sorted(pre_children)}")
            else:
                # >1 new PIDs: tiebreak by starttime (latest wins)
                best_pid = None
                best_start = -1
                for p in new_pids:
                    st = _read_proc_starttime(p)
                    if st is not None and st > best_start:
                        best_start = st
                        best_pid = p
                pid = best_pid if best_pid is not None else next(iter(new_pids))
                _log(session_dir, f"bg_bash_post: bg_bash_race_ambiguous tool_use_id={tool_use_id} new_pids={sorted(new_pids)} resolved_pid={pid}")
        else:
            # No snapshot — newest-child fallback
            if post_children:
                best_pid = None
                best_start = -1
                for p in post_children:
                    st = _read_proc_starttime(p)
                    if st is not None and st > best_start:
                        best_start = st
                        best_pid = p
                if best_pid is None:
                    best_pid = max(post_children)  # fallback: highest PID as proxy for newest
                pid = best_pid
                _log(session_dir, f"bg_bash_post: newest-child fallback resolved pid={pid} tool_use_id={tool_use_id}")
            else:
                _log(session_dir, f"bg_bash_post: no children found during fallback tool_use_id={tool_use_id}")
    else:
        _log(session_dir, f"bg_bash_post: could not determine claude_pid tool_use_id={tool_use_id}")

    # H4/H5: write sentinel — always, even if pid is None
    if not task_id:
        # Can't write a named sentinel without task_id; use tool_use_id as fallback key
        sentinel_key = f'bg-bash-{tool_use_id}' if tool_use_id else f'bg-bash-{time.time_ns()}'
        _log(session_dir, f"bg_bash_post: using fallback sentinel key {sentinel_key} (no task_id)")
    else:
        sentinel_key = f'bg-bash-{task_id}'

    payload = {
        'task_id': task_id,
        'command': command,
        'launch_time': launch_time,
    }
    if pid is not None:
        payload['pid'] = pid

    sentinel_dir = _sentinel_session_dir(session_id)
    sentinel_path = os.path.join(sentinel_dir, f'{sentinel_key}.sentinel')

    try:
        os.makedirs(sentinel_dir, exist_ok=True)
        with open(sentinel_path, 'w') as f:
            json.dump(payload, f)
        _log(session_dir, f"bg_bash_post: sentinel written {sentinel_key}.sentinel pid={pid} task_id={task_id}")
    except (IOError, OSError) as e:
        _log(session_dir, f"bg_bash_post: sentinel write failed {sentinel_key}.sentinel err={type(e).__name__}: {e}")


def determine_hook_event(event):
    """Return the correct hookEventName for the output wrapper.

    This is critical -- using the wrong event name causes output to be
    silently discarded by Claude Code.
    """
    hook_event = event.get("hook_event_name", "")
    if hook_event:
        return hook_event
    # Fallback: detect from payload structure
    if "agent_id" in event and "agent_transcript_path" in event:
        return "SubagentStop"
    return "PostToolUse"


def inject_warning(session_dir, event):
    """Output additionalContext telling the orchestrator to checkpoint.

    If cycle-pending-curator-contested-blocked exists, emits a higher-severity
    message naming the three operator-action paths (A8 R2 § Per-tier unavailability
    semantics — contested tier). If only cycle-pending exists, emits the normal
    cycle warning. Both messages route through the same exit path (no new exit codes).

    Citation: .claude/knowledge/reference/sentinels.md § cycle-pending-curator-contested-blocked
    """
    pending_file = os.path.join(session_dir, "cycle-pending")
    contested_blocked_file = os.path.join(
        session_dir, "cycle-pending-curator-contested-blocked"
    )

    # Higher-severity path: curator-contested-blocked sentinel takes priority.
    if os.path.exists(contested_blocked_file):
        warning = (
            "\n\n--- CURATOR CONTESTED BLOCK — OPERATOR ACTION REQUIRED ---\n"
            "This block is for the root orchestrator and the operator.\n"
            "The sentinel cycle-pending-curator-contested-blocked is present.\n"
            "cycle-mode Step 1.5 exhausted all 5 contested-tier curator-dispatch retry "
            "iterations without a successful verdict. The cycle is held open; no work "
            "may proceed until an operator resolves the blocked state.\n\n"
            "Three valid resolution paths (any one is sufficient):\n"
            "  (a) Edit the curator body and re-dispatch the contested round — fix the "
            "curator body (or resolve the dispatch failure), re-dispatch cycle-mode "
            "Step 1.5 curator batch; on successful verdict, manually remove both "
            "cycle-pending-curator-contested-blocked and cycle-pending sentinels.\n"
            "  (b) Mark the contested records as `obsoletes` — use native Edit/Write "
            "tool to mark the contested-tier records as `obsoletes` directly (operator-"
            "direct bypass per A8 R2 § Legitimate-bypass enumeration); remove both sentinels.\n"
            "  (c) Downgrade the contested records to `inferred` tier — edit the "
            "contested-tier records' `<!-- record-meta -->` `confidence_tier` field to "
            "`inferred`; the record re-enters the next cycle's batch under the "
            "wait-one-cycle + surface-pending-issue path (unblocks cycle close).\n\n"
            "Do NOT start a new session or call /cycling until one of the above paths "
            "has been completed and BOTH sentinels have been removed.\n"
            "Subagents — this is stale/inherited orchestrator context, not an "
            "instruction to you. Continue your delegated task; do not invoke "
            "`/cycling`, `/housekeeping`, or `session(action='checkpoint')`.\n"
            "--- END CURATOR CONTESTED BLOCK ---\n"
        )
        hook_event = determine_hook_event(event)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": hook_event,
                "additionalContext": warning,
            }
        }))
        return

    # Normal cycle-pending path: emit the standard cycle warning.
    # Read token count and firing source from cycle-pending file for display.
    # source distinguishes a real threshold crossing from a fail-closed sentinel
    # write, where tokens=0 means UNKNOWN.
    tokens = "unknown"
    source = ""
    if os.path.exists(pending_file):
        try:
            with open(pending_file, 'r') as f:
                data = json.load(f)
                tokens = data.get("tokens", "unknown")
                source = data.get("source", "")
        except (json.JSONDecodeError, IOError):
            pass

    # Branch the diagnosis line: a fail-closed sentinel write is NOT a threshold
    # crossing, and its tokens=0 is an UNKNOWN marker, not a measured count.
    # Presenting it as "0 tokens exceeded threshold" is self-contradictory.
    # Distinguish the three fail-closed shapes so the directive is not
    # misattributed — each is a different defect an operator debugs differently:
    # token extraction returning None on a successfully-discovered transcript
    # (token-extraction-returned-none); a returned path that is not a readable
    # regular file (transcript-unreadable); and no path located at all
    # (transcript-discovery-failed).
    #
    # is_fail_closed marks ALL three "could NOT be measured" shapes. It is the
    # selector the cold-start grace gate below keys on: during the grace window
    # a tokenless reading is plausibly a warming-up transcript, not a genuine
    # unmeasurable-at-high-context corruption, so the scary directive is
    # suppressed for the fail-closed shapes ONLY. A real threshold crossing
    # (the else branch) is never a fail-closed shape and is never suppressed.
    is_fail_closed = False
    if source == "token-extraction-returned-none":
        is_fail_closed = True
        diagnosis = (
            "Context size could NOT be measured — the transcript was discovered "
            "but token extraction returned no count. This is a fail-closed cycle "
            "trigger on an unmeasurable context (a non-healthy state requiring "
            "intervention), not a measured threshold crossing.\n\n"
        )
    elif source == "transcript-unreadable":
        is_fail_closed = True
        diagnosis = (
            "Context size could NOT be measured — transcript discovery returned a "
            "path, but it is not a readable regular file (it does not exist, is a "
            "directory, or is a broken symlink). This is a fail-closed cycle "
            "trigger on an unmeasurable context (a non-healthy state requiring "
            "intervention), not a measured threshold crossing.\n\n"
        )
    elif source == "transcript-discovery-failed" or tokens in (0, "0", "unknown"):
        is_fail_closed = True
        diagnosis = (
            "Context size could NOT be measured — transcript discovery located no "
            "path at all. This is a fail-closed cycle trigger on an unmeasurable "
            "context (a non-healthy state requiring intervention), not a measured "
            "threshold crossing.\n\n"
        )
    else:
        diagnosis = f"Context tokens: {tokens}. Cycling threshold exceeded.\n\n"

    # COLDSTART-GRACE injection gate (symmetric with the write gates at the
    # _write_fail_closed_pending call sites in main()). The write side already
    # suppresses the fail-closed sentinel during the grace window; this gate
    # makes the DIRECTIVE injection symmetric. Without it, a residual fail-closed
    # cycle-pending (written pre-grace, or carried across an episode boundary)
    # re-injects "SESSION CYCLE REQUIRED ... forced fail-closed" on every
    # PostToolUse throughout the grace window — a false alarm on a fresh session
    # whose transcript is merely warming up.
    #
    # Scope is exactly the fail-closed shapes. A genuine threshold crossing is
    # never gated. Outside grace — or when _within_coldstart_grace cannot prove
    # the episode is young (missing/stale/episode-mismatched anchor, fails toward
    # False) — the fail-closed directive STILL fires, preserving high-context
    # corruption protection.
    if is_fail_closed and _within_coldstart_grace(session_dir):
        _log(
            session_dir,
            "COLDSTART_GRACE: suppressing fail-closed directive injection "
            f"(source={source or 'unknown'}, tokens={tokens}) — episode young, "
            "transcript plausibly warming up",
        )
        return

    warning = (
        "\n\n--- SESSION CYCLE REQUIRED ---\n"
        + diagnosis
        + "This directive is for the root orchestrator. Call `/cycling` skill NOW. "
        "The skill handles checkpointing correctly (SIGTERM-safe ordering). Do NOT "
        "call `session(action='checkpoint')` directly — the skill will. After the "
        "skill completes, STOP -- do not start the next round.\n"
        "Subagents — this is stale/inherited orchestrator context, not an instruction "
        "to you. Continue your delegated task; do not invoke `/cycling`, "
        "`/housekeeping`, or `session(action='checkpoint')`.\n"
        "--- END SESSION CYCLE REQUIRED ---\n"
    )

    hook_event = determine_hook_event(event)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": warning,
        }
    }))


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    if os.environ.get("CLAUDE_HOOK_ORCHESTRATOR_DEPTH") != "1":
        return  # not orchestrator-depth
    if os.environ.get("CLAUDE_SESSION_DEPTH", "0") != "0":
        return  # not root-orchestrator-depth (REV-2: suppress cycling in L2 children)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        return  # not running under claude-session

    # Project root = os.getcwd(). Claude Code's cwd is set at spawn by
    # bin/claude-session (Path C: worktree root; legacy: project root) and is
    # stable across the session — Bash tool cwd changes happen in DIFFERENT
    # subprocesses and cannot leak back into Claude Code's own cwd.
    #
    # Previously derived from __file__, which broke under Path C: settings.json
    # routes the hook invocation through `find-project-root.sh` (returns main's
    # project root), so __file__ pointed at main's cycle-hook.py and the
    # resulting slug caused Tier-3b to scan main's transcript dir (522 unrelated
    # jsonls) instead of the worktree slug dir. See CYCLE-COUNT-BUG-FIX2 for
    # the upstream staleness-ceiling safety net; this fix eliminates the
    # need for it to fire in the happy path.
    #
    # event.cwd is rejected because Bash tool cwd changes can leak into it.
    cwd = os.getcwd()
    session_dir = os.path.join(cwd, ".agent_context", "sessions", session_id)
    state_file = os.path.join(session_dir, "cycle.state")
    pending_file = os.path.join(session_dir, "cycle-pending")
    # A7-S7: higher-severity sentinel — curator contested-tier hard-block.
    # Present when cycle-mode Step 1.5 exhausted all curator-dispatch retries
    # for contested-tier records. Treated as "do not proceed" with same priority
    # as cycle-pending in all pending-file guards below.
    # Citation: .claude/knowledge/reference/sentinels.md § cycle-pending-curator-contested-blocked
    contested_blocked_file = os.path.join(
        session_dir, "cycle-pending-curator-contested-blocked"
    )
    loop_terminated_file = os.path.join(session_dir, "loop-terminated")

    # Activation guard
    if not os.path.exists(state_file):
        return

    # BG-BASH-SENTINEL dispatch (W1): PreToolUse/Bash(bg=true) snapshot writer
    # and PostToolUse/Bash(bg=true) sentinel writer. Runs BEFORE cycle-check so
    # the sentinel is written before any_fresh_subagent_active() is consulted.
    hook_event_early = event.get("hook_event_name", "")
    tool_name_early = event.get("tool_name", "")
    is_bg_bash = (
        tool_name_early == "Bash"
        and bool((event.get("tool_input") or {}).get("run_in_background"))
    )
    if is_bg_bash and hook_event_early == "PreToolUse":
        _handle_pre_bash_bg(event, session_id, session_dir)
        return  # PreToolUse hooks do not count tokens; return after dispatch
    if is_bg_bash and hook_event_early == "PostToolUse":
        _handle_post_bash_bg(event, session_id, session_dir)
        return  # sentinel written; skip token counting for this event

    # BG-BASH-SENTINEL S2: PostToolUse/BashOutput and PreToolUse/KillShell
    # clear handlers. Runs before cycle-check so sentinels are cleaned before
    # any_fresh_subagent_active() or cleanup_stale_sentinels() is consulted.
    if tool_name_early in ("BashOutput", "AgentOutputTool", "BashOutputTool") and hook_event_early == "PostToolUse":
        _handle_post_bashoutput(event, session_id, session_dir)
        return  # clear-only event; skip token counting
    if tool_name_early == "KillShell" and hook_event_early == "PreToolUse":
        _handle_pre_killshell(event, session_id, session_dir)
        return  # PreToolUse hooks do not count tokens; return after dispatch

    # Stale sentinel cleanup on SubagentStop (Option 1a).
    # Runs before token counting so stale sentinels are gone before
    # any_fresh_subagent_active() or POTENTIAL_MISFIRE re-scan runs.
    hook_event = determine_hook_event(event)
    if hook_event == "SubagentStop":
        cleanup_stale_sentinels(session_id, session_dir)
        _clear_background_sentinel_on_stop(session_id, session_dir)

    # Option D: foreground Agent completion clears its sentinel precisely.
    # Check both top-level and tool_input.run_in_background — location not
    # verified across Claude Code versions; belt-and-suspenders handles both.
    if (event.get("hook_event_name") == "PostToolUse"
            and event.get("tool_name") == "Agent"):
        run_in_background = (event.get("run_in_background")
                             or event.get("tool_input", {}).get("run_in_background"))
        if run_in_background:
            _log(session_dir, "clear_sentinel skipped (background)")
        else:
            tool_use_id = event.get("tool_use_id", "")
            if tool_use_id:
                clear_sentinel(session_id, tool_use_id)
            else:
                _log(session_dir, "clear_sentinel skipped (no tool_use_id)")
        # Fall through: token counting, threshold check, and SI-4 Agent-
        # completion path all still run. This block ONLY clears
        # the sentinel; it does not alter main() control flow.

    # Secondary defense: clean stale sentinels on every hook fire.
    # Handles edge cases where SubagentStop never fires or correlation fails.
    # Idempotent — harmless to double-run when event is SubagentStop.
    cleanup_stale_sentinels(session_id, session_dir)

    # CYCLING-DEFER-FIX (Subtask 2, Axis B): loop-terminated injection branch.
    # Mirrors the cycle-pending mechanism but on an independent axis: when the
    # orchestrator writes {session_dir}/loop-terminated (at the validator-approve /
    # coherence-auditor-clean boundary), the next PostToolUse/Agent fire injects
    # a TERMINAL CYCLING REQUIRED warning. The skill's terminal-mode Step 1 unlinks
    # the file, transitioning the hook from warn to silent.
    #
    # Branch is independent of cycle-pending: returns early on inject so the
    # existing token-count / threshold logic below does NOT also fire on this
    # invocation; on suppress paths, falls through so cycle-pending logic still
    # runs (the two enforcement axes are orthogonal — both may be needed).
    if os.path.exists(loop_terminated_file):
        # B3-style suppression symmetry (CYCLE-SUBAGENT-LEAK class, see
        # constraints/hooks-behavior.md § B3 suppression-branch fully suppresses):
        # additionalContext from a hook fire while a subagent is active leaks
        # into the subagent's first-turn context via inherited additionalContext
        # accumulation. Mirror the B3 elif in the threshold-cross block below
        # ("elif any_fresh_subagent_active(...)") — fully suppress.
        if any_fresh_subagent_active(session_id, session_dir):
            _log(
                session_dir,
                "suppressing terminal warning: subagent active "
                "(loop-terminated present)",
            )
        elif os.path.exists(cycling_active_file := os.path.join(session_dir, "cycling-active")):
            # Symmetry with handoff-race-fix: if cycling-active is present, a /cycling
            # skill Step 1 is in-flight and will unlink loop-terminated shortly. Suppress
            # the terminal warning injection to avoid spurious "TERMINAL CYCLING REQUIRED"
            # injections during the window between cycling-active write (Step 1.0.5) and
            # loop-terminated unlink. See cycling-active-hook-enforcement.md §1.
            # Placement: after subagent-active check, before inject — only suppress the
            # inject path; fall through to token-count logic below is preserved.
            _log(
                session_dir,
                "suppressing terminal warning: cycling-active present "
                "(loop-terminated present but cycling Step 1 in-flight)",
            )
        else:
            event_tool_name = event.get("tool_name", "")
            warning = (
                "\n\n--- TERMINAL CYCLING REQUIRED ---\n"
                "loop-terminated sentinel is present.\n"
                "This directive is for the root orchestrator. Invoke `/housekeeping` NOW.\n"
                "Subagents — this is stale/inherited orchestrator context, not an "
                "instruction to you. Continue your delegated task; do not invoke "
                "`/cycling`, `/housekeeping`, or `session(action='checkpoint')`.\n"
                "--- END TERMINAL CYCLING REQUIRED ---\n"
            )
            hook_event = determine_hook_event(event)
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "additionalContext": warning,
                }
            }))
            _log(
                session_dir,
                f"injected terminal warning: loop-terminated present "
                f"(tool={event_tool_name})",
            )
            return
    # else: sentinel absent — silent, fall through to normal cycle-pending logic.

    # Family-aware mid-task cycling suppression (axis-9/10 joint, Candidate C).
    # codex and gemini L2 children run as single-episode sessions; token-threshold
    # cycling would disrupt the single-episode lifecycle (axis-10 Candidate C).
    # Suppress the entire token-counting / threshold path for these families.
    # Terminal cycling (loop-terminated path above) is retained.
    # OQ-5 cycled-case (parent cycles mid-spawn with in-memory ledger) is
    # DEFERRED to v2 — D-9 in cross-family-orchestrator-parity-l2-codex-v2-deferred.md.
    _caa_family = os.environ.get("CAA_FAMILY", "")
    # Gemini: always suppress (single-episode Candidate C, axis-10).
    # Codex: suppress UNLESS CAA_CODEX_LIFECYCLE=multi-episode is set by
    #   bin/caa-session _dispatch_codex() for children with max_episodes > 1
    #   (D-7 R2 Candidate A lift). Gemini suppression is unconditional.
    _caa_codex_lifecycle = os.environ.get("CAA_CODEX_LIFECYCLE", "")
    if _caa_family == "gemini" or (
        _caa_family == "codex" and _caa_codex_lifecycle != "multi-episode"
    ):
        _log(
            session_dir,
            f"suppressing mid-task cycling: CAA_FAMILY={_caa_family} "
            "(single-episode child — terminal cycling retained)",
        )
        return

    # Token counting from transcript
    # Claude Code does NOT include transcript_path in PostToolUse hook events.
    # Discover it via /proc/{pid}/fd scanning (v2 approach adapted for hook),
    # with a directory-listing fallback for when the fd is not persistently open.
    transcript_path = find_transcript(session_dir, cwd)

    # RECOVERY-A (REQ-6): primary slug returned None — cached UUID may have
    # disappeared from the active slug after a resume-timeout episode reset.
    # Retry with the alternate slug (main vs worktree) before accepting failure.
    if transcript_path is None:
        _alt_cwd_a = _infer_alt_cwd(session_dir, cwd)
        if _alt_cwd_a:
            _log(session_dir,
                 "RECOVERY-A: primary slug failed, retrying find_transcript "
                 f"with alt_cwd={os.path.basename(_alt_cwd_a)}")
            transcript_path = find_transcript(session_dir, _alt_cwd_a)
            if transcript_path:
                _log(session_dir,
                     f"RECOVERY-A: alt-slug found {os.path.basename(transcript_path)}")

    if not transcript_path:
        # CYCLE-SUPPRESS / CYCLING-ACTIVE gate — mirror the threshold-cross path's
        # bail below (cycle-suppress + cycling-active). When either sentinel is
        # present, cycling is forbidden (cycle-suppress: this episode is in post-loop
        # housekeeping) or already in-flight (cycling-active: a /cycling Step 1 owns
        # the mutex), so NEITHER the fail-closed write NOR the injection may fire for
        # this event: a write would leave cycle-pending residue the /cycling skill's
        # Step-1 ERROR block would refuse, and an inject would fire a spurious
        # "SESSION CYCLE REQUIRED" during an in-flight cycling step. Bail.
        # Accepted residual: a genuinely-broken session that hits a discovery/read
        # failure DURING post-loop housekeeping gets no fail-closed signal — that is
        # acceptable, because cycling is forbidden in that window by design.
        if os.path.exists(os.path.join(session_dir, "cycle-suppress")) \
                or os.path.exists(os.path.join(session_dir, "cycling-active")):
            _log(
                session_dir,
                "suppressing fail-closed write+inject: cycle-suppress or "
                "cycling-active present (transcript_path missing)",
            )
            return
        # Discovery failed entirely — find_transcript returned no path (including
        # after the RECOVERY-A alt-slug retry above). Fail-closed write symmetric
        # with RECOVERY-B below: a genuinely-broken session that cannot DISCOVER
        # its transcript past the cold-start grace window must still get a cycle
        # signal rather than silently return. Grace-gated so cold-start discovery
        # slowness — the common benign case (see
        # constraints/session-cycling-transcript-discovery-fails.md) — stays
        # suppressed. The write runs BEFORE the injection check so a newly-written
        # sentinel injects on this same fire.
        if not os.path.exists(pending_file) and not _within_coldstart_grace(session_dir):
            _write_fail_closed_pending(
                session_dir, pending_file,
                source="transcript-discovery-failed")
        # If cycle-pending (or the contested-blocked sentinel) exists — whether
        # just written above or pre-existing — inject warning. The contested-blocked
        # sentinel is treated identically; inject_warning() selects the severity.
        if os.path.exists(pending_file) or os.path.exists(contested_blocked_file):
            # CYCLE-SUBAGENT-LEAK gate (mirrors the B3 elif in the
            # threshold-cross block below — "elif any_fresh_subagent_active").
            # When a fresh subagent sentinel exists, this PostToolUse event
            # was triggered by a mid-dispatch subagent tool call; the
            # additionalContext emitted by inject_warning would be inherited
            # into that subagent's next-turn context and cause writer
            # subagents to self-halt on a directive they cannot honor
            # (/cycling is caller-allowlisted to the orchestrator). See:
            #   constraints/cycle-hook-injection-subagent-self-halt.md
            #   constraints/cycle-hook-injection-coherence-auditor-misroute.md
            # Suppression is safe: cycle-pending stays on disk, so the
            # next orchestrator-context fire (sentinel expired or a non-
            # subagent tool call arrives) re-enters this branch and injects.
            if any_fresh_subagent_active(session_id, session_dir):
                _log(
                    session_dir,
                    "suppressing cycle warning: subagent active "
                    "(transcript_path missing, cycle-pending present)",
                )
            else:
                inject_warning(session_dir, event)
        return

    total_tokens = extract_total_tokens(transcript_path)

    # RECOVERY-B (REQ-6): transcript found but no usage records — the
    # post-reset resume-timeout pattern where the worktree transcript contains
    # only title/name metadata lines (no assistant usage blocks).
    # Guard: only enter when the file actually exists (distinguishes tokenless
    # files from non-existent/unreadable transcripts, which are a separate
    # failure mode handled below with existing inject-if-pending logic).
    if total_tokens is None and os.path.isfile(transcript_path):
        _log(session_dir,
             f"RECOVERY-B: {os.path.basename(transcript_path)} tokenless — "
             "clearing mapping cache and retrying alt slug")
        # Clear stale mapping so the next find_transcript call is not poisoned
        # by the bad UUID that directed us to this tokenless transcript.
        _mapping_file_r = os.path.join(session_dir, 'claude-code-uuid')
        _cache_file_r = os.path.join(session_dir, 'transcript.path')
        for _stale_f in (_mapping_file_r, _cache_file_r):
            try:
                os.remove(_stale_f)
            except (FileNotFoundError, OSError):
                pass
        _alt_cwd_b = _infer_alt_cwd(session_dir, cwd)
        if _alt_cwd_b:
            _log(session_dir,
                 "RECOVERY-B: retrying find_transcript with alt_cwd="
                 f"{os.path.basename(_alt_cwd_b)}")
            _alt_path = find_transcript(session_dir, _alt_cwd_b)
            if _alt_path:
                _recovered = extract_total_tokens(_alt_path)
                _log(session_dir,
                     f"RECOVERY-B: alt_path={os.path.basename(_alt_path)} "
                     f"recovered_tokens={_recovered}")
                if _recovered is not None:
                    # Recovery succeeded; fall through to normal threshold check.
                    total_tokens = _recovered
        if total_tokens is None:
            # CYCLE-SUPPRESS / CYCLING-ACTIVE gate — same rationale as the
            # transcript-missing branch above (mirror the threshold-cross bail;
            # a write would leave residue the /cycling skill refuses, an inject
            # would fire spuriously during an in-flight cycling step).
            if os.path.exists(os.path.join(session_dir, "cycle-suppress")) \
                    or os.path.exists(os.path.join(session_dir, "cycling-active")):
                _log(
                    session_dir,
                    "suppressing fail-closed write+inject: cycle-suppress or "
                    "cycling-active present (token-extraction-returned-none)",
                )
                return
            # All recovery attempts exhausted (primary tokenless, alt tokenless/stale,
            # or no alt slug available). Write fail-closed sentinel so
            # session(action='status') shows cycle_pending=true rather than the
            # misleading '0 tokens + cycle_pending=false' healthy state.
            # The transcript WAS discovered here (this branch is reached only
            # after find_transcript returned a path and extract_total_tokens
            # returned None on it) — label it accurately so the injected
            # directive does not misattribute this to a discovery failure.
            if not os.path.exists(pending_file) and not _within_coldstart_grace(session_dir):
                _write_fail_closed_pending(
                    session_dir, pending_file,
                    source="token-extraction-returned-none")
            # Inject warning if pending (covers both newly-written and pre-existing).
            if os.path.exists(pending_file) or os.path.exists(contested_blocked_file):
                # CYCLE-SUBAGENT-LEAK gate — same rationale as the transcript-
                # missing branch above. See:
                #   constraints/cycle-hook-injection-subagent-self-halt.md
                #   constraints/cycle-hook-injection-coherence-auditor-misroute.md
                if any_fresh_subagent_active(session_id, session_dir):
                    _log(
                        session_dir,
                        "suppressing cycle warning: subagent active "
                        "(total_tokens=None after recovery, cycle-pending present)",
                    )
                else:
                    inject_warning(session_dir, event)
            return

    if total_tokens is None:
        # CYCLE-SUPPRESS / CYCLING-ACTIVE gate — same rationale as the transcript-
        # missing branch above (mirror the threshold-cross bail; a write would leave
        # residue the /cycling skill refuses, an inject would fire spuriously during
        # an in-flight cycling step).
        if os.path.exists(os.path.join(session_dir, "cycle-suppress")) \
                or os.path.exists(os.path.join(session_dir, "cycling-active")):
            _log(
                session_dir,
                "suppressing fail-closed write+inject: cycle-suppress or "
                "cycling-active present (transcript-unreadable)",
            )
            return
        # find_transcript returned a path, but it is non-existent/unreadable on
        # disk — extract_total_tokens returned None and the RECOVERY-B isfile
        # guard above skipped it (this branch is the not-a-regular-file case).
        # Fail-closed write symmetric with RECOVERY-B and the transcript-missing
        # branch: a genuinely-broken session that cannot READ its transcript past
        # the cold-start grace window must still get a cycle signal rather than
        # silently return. Grace-gated so cold-start slowness stays suppressed.
        # The write runs BEFORE the injection check so a newly-written sentinel
        # injects on this same fire.
        if not os.path.exists(pending_file) and not _within_coldstart_grace(session_dir):
            _write_fail_closed_pending(
                session_dir, pending_file,
                source="transcript-unreadable")
        # If cycle-pending (or contested-blocked sentinel) exists — whether just
        # written above or pre-existing — inject warning. inject_warning() selects
        # appropriate severity.
        if os.path.exists(pending_file) or os.path.exists(contested_blocked_file):
            # CYCLE-SUBAGENT-LEAK gate — same rationale as the transcript-
            # missing branch above. See:
            #   constraints/cycle-hook-injection-subagent-self-halt.md
            #   constraints/cycle-hook-injection-coherence-auditor-misroute.md
            if any_fresh_subagent_active(session_id, session_dir):
                _log(
                    session_dir,
                    "suppressing cycle warning: subagent active "
                    "(total_tokens=None, cycle-pending present)",
                )
            else:
                inject_warning(session_dir, event)
        return

    # Family A instrumentation — emit one JSONL line per episode.
    # Reuses already-computed total_tokens; does NOT re-parse the transcript.
    # Silent on all error paths; does not affect cycling decisions.
    emit_episode_tokens(
        session_dir=session_dir,
        session_id=session_id,
        state_file=state_file,
        tool_name=event.get("tool_name", ""),
        total_tokens=total_tokens,
    )

    # Check threshold
    threshold = get_threshold(cwd)
    if total_tokens >= threshold:
        # CYCLE-SUPPRESS gate (architecture.md §cycle-suppress sentinel,
        # verified 2026-04-22 spec / 2026-04-24 drift / fixed BP-1 of
        # design-manual-cycling). When cycle-suppress exists, this episode
        # is in post-loop housekeeping; cycling is forbidden. Bail before
        # create_cycle_pending() and inject_warning(): both writes would
        # cause the orchestrator to invoke /cycling cycle, which the skill's
        # step 1 cycle-suppress ERROR block would refuse — but only after
        # cycle-pending has been written, leaving residue. Bail here keeps
        # state clean.
        suppress_file = os.path.join(session_dir, "cycle-suppress")
        if os.path.exists(suppress_file):
            _log(
                session_dir,
                f"suppressing cycle warning+pending: cycle-suppress present "
                f"(tokens={total_tokens}, threshold={threshold})",
            )
            return
        # CYCLING-ACTIVE gate (handoff-race-fix, 2026-04-28). When cycling-active exists,
        # a /cycling handoff (or cycle/terminal) Step 1 is in-flight and owns the
        # cycling-active mutex. Bail before create_cycle_pending() and inject_warning() —
        # same rationale as cycle-suppress gate above. See cycling-active-hook-enforcement.md.
        # Backward-compat: cycle-mode enters with cycle-pending already present so
        # create_cycle_pending is a no-op; terminal-mode pre-writes cycle-suppress at Step 1
        # first action (cycle-suppress gate already fires before this one). Gate is benign
        # for both modes. See Axis 1 §5 backward-compat hazard section for full audit.
        if os.path.exists(cycling_active_file := os.path.join(session_dir, "cycling-active")):
            _log(
                session_dir,
                f"suppressing cycle warning+pending: cycling-active present "
                f"(tokens={total_tokens}, threshold={threshold})",
            )
            return
        # SI-4 HIGH #1 context: the orchestrator's own post-Agent PostToolUse
        # event carries tool_name == "Agent". The hook is firing at
        # orchestrator depth (guaranteed by the depth gate at :1543-1546).
        # Evidence for tool_name behavior: critic-gate-check.py:104 inline
        # comment ("PostToolUse:Agent has tool_name = 'Agent'"),
        # agent-latency-tracker.py:207 defensive guard.
        # IMPORTANT: those references prove the depth fact only. They are
        # NOT a directive that injection must occur — earlier comments here
        # claimed "must NEVER be suppressed regardless of sentinel state",
        # which predates CYCLE-SUBAGENT-LEAK discovery (2026-04-22) and
        # has been narrowed below.
        event_tool_name = event.get("tool_name", "")
        is_agent_completion_event = (event_tool_name == "Agent")

        if is_agent_completion_event:
            # Orchestrator post-Agent event. Two sub-cases on sentinel state:
            #
            # 1. Sentinel CLEAR (the common foreground-completion path).
            #    Option D's Block B above has already cleared the foreground
            #    subagent's sentinel before this branch runs. Safe to inject:
            #    the next inference is the orchestrator's, and the
            #    additionalContext is meant for it.
            #
            # 2. Sentinel FRESH (background-Agent siblings or A9 nested-Agent
            #    pattern). Option D's Block B SKIPS clear for background
            #    completions — so a sibling background subagent may still be
            #    running. Injecting here leaks additionalContext into the
            #    next subagent dispatch the orchestrator makes (which, in
            #    fan-out marathons, is the very next Agent invocation),
            #    reproducing CYCLE-SUBAGENT-LEAK. See:
            #      constraints/cycle-hook-injection-subagent-self-halt.md
            #      constraints/cycle-hook-injection-coherence-auditor-misroute.md
            #    Mitigation: still write cycle-pending (so the orchestrator
            #    sees the cycle signal on its next non-subagent fire), but
            #    suppress the inject until no sibling subagent is fresh.
            if not os.path.exists(pending_file):
                create_cycle_pending(pending_file, total_tokens)
            if any_fresh_subagent_active(session_id, session_dir):
                _log(
                    session_dir,
                    "suppressing cycle warning: subagent active on "
                    "PostToolUse/Agent (background sibling or nested-Agent "
                    "pattern — cycle-pending written, inject deferred)",
                )
            else:
                inject_warning(session_dir, event)
        elif any_fresh_subagent_active(session_id, session_dir):
            # In-subagent fire: a subagent is actively running (fresh
            # sentinel). Suppress warning+pending; orchestrator will
            # re-fire normally once the sentinel expires or a non-
            # subagent tool call arrives. Token counting and diagnostic
            # logging above still ran (CL-4.6).
            # B3 symmetry: this branch also suppresses the checkpoint-trigger
            # path — create_cycle_pending() and inject_warning() are both
            # absent here, so neither fires while a subagent is active.
            _log(
                session_dir,
                f"suppressing cycle warning+pending: subagent active "
                f"(tokens={total_tokens}, threshold={threshold})",
            )
            # CYCLE-SUBAGENT-LEAK fix: do NOT inject_warning while a
            # subagent is active. Prior probe-additionalcontext-routing.md
            # claim ("orchestrator only") was a JSONL-grep measurement
            # artifact — additionalContext is not persisted to JSONL per
            # constraints/platform/hooks-behavior.md:263-271, so absence in transcripts
            # did not prove absence in runtime context. Observed runtime:
            # warning DID reach subagent context (see
            # .agent_context/diag-planner-cycle-leak.md § "Channel (a)").
            # Orchestrator will re-see cycle-pending on its next
            # PostToolUse/Agent completion event (is_agent_completion_event
            # path above).
        else:
            # POTENTIAL_MISFIRE observability: re-scan sentinel dir to check
            # if stale sentinels exist. If any do, the TTL expired mid-run for
            # a long-running subagent and this warning may be spurious.
            # (any_fresh_subagent_active() returned False, so no fresh sentinels
            # exist by construction — any found here must be stale.)
            # Only emit when tool_name != "Agent" (per task spec).
            if event_tool_name != "Agent":
                sentinel_dir = _sentinel_session_dir(session_id)
                try:
                    _sentinel_entries = os.listdir(sentinel_dir)
                except (OSError, FileNotFoundError):
                    _sentinel_entries = []
                if _sentinel_entries:
                    _now = time.time()
                    _misfire_ttl = _get_sentinel_ttl()
                    _stale_parts = []
                    _race_parts = []
                    for _sname in _sentinel_entries:
                        _spath = os.path.join(sentinel_dir, _sname)
                        try:
                            _age = _now - os.path.getmtime(_spath)
                        except OSError:
                            continue
                        if _age >= _misfire_ttl:
                            _stale_parts.append(f"{_sname}={_age:.0f}s")
                        else:
                            # Fresh sentinel but any_fresh_subagent_active()
                            # returned False — TOCTOU race condition.
                            _race_parts.append(f"{_sname}={_age:.0f}s")
                    if _stale_parts:
                        _log(
                            session_dir,
                            f"POTENTIAL_MISFIRE: tool={event_tool_name} "
                            f"tokens={total_tokens} threshold={threshold} "
                            f"stale_sentinels=[{','.join(_stale_parts)}]",
                        )
                    if _race_parts:
                        _log(
                            session_dir,
                            f"SENTINEL_RACE: tool={event_tool_name} "
                            f"tokens={total_tokens} threshold={threshold} "
                            f"fresh_sentinels=[{','.join(_race_parts)}]",
                        )
            if not os.path.exists(pending_file):
                create_cycle_pending(pending_file, total_tokens)
            inject_warning(session_dir, event)
    elif total_tokens > 0:
        # CRITICAL: Clean stale cycle-pending if tokens dropped (after /clear or /compact)
        # Without this, a stale cycle-pending from before /clear would trigger
        # an unintended cycle on the next checkpoint call.
        try:
            os.remove(pending_file)
        except FileNotFoundError:
            pass  # Already removed by a concurrent process — safe to ignore
        # A7-S7: If the contested-blocked sentinel persists even after tokens drop,
        # still inject the higher-severity warning. The contested-blocked sentinel
        # is NOT cleared on /clear or /compact — operator action only.
        if os.path.exists(contested_blocked_file):
            if not any_fresh_subagent_active(session_id, session_dir):
                inject_warning(session_dir, event)
    # total_tokens == 0 cannot occur here (filtered by extract_total_tokens) — implicit no-op


if __name__ == "__main__":
    main()
