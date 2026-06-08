"""§B eligibility predicate — single source of truth.

This module is THE definition of the §B eligibility predicate for the
triage-routine. Both call-sites — the launcher pre-check
(`bin/claude-session::_triage_pre_orchestrator_check`) and the
orchestrator's in-session selection step
(`.claude/orchestrator-prompt.md § Triage-Tick Procedure`) — import and
call `eligible_issues()`. Re-implementing the predicate inline at
either call-site is FORBIDDEN by load-bearing-constraint #17 of
design-triage-routine-recommendation.md.

Division of labor with §K Actions-with-care (constraint #14):
- This module reads ONLY the issue record (severity, tags,
  related_artifacts, suggested_approach). It applies the static
  predicates from design-triage-routine-axis-B-protocol.md §3 and §5.
- The §K Actions-with-care list is a LIVE coupling read from
  `.claude/orchestrator-prompt.md § Actions with care` at runtime by
  the orchestrator's autonomy classifier — NOT imported here. The
  orchestrator may RE-CLASSIFY any returned record with
  `_classification='auto-resolve'` to `'always-escalate'` after
  consulting the live §K list.

The `_classification` key on returned records is SYNTHESIZED at call
time — it is NOT a stored record-schema field. The `_` prefix signals
this convention: the field does not appear in `.claude/knowledge/reference/
issue-queue-schema.md § Record schema` and must not be persisted to the
issue queue.

Allow-list glob matching uses `fnmatch.fnmatch()` (NOT `pathlib.PurePosixPath.
match()`). On Python 3.12, `PurePosixPath('docs/sub/foo.md').match('docs/**')`
returns False — `**` does NOT match across path separators in PurePath.match()
until Python 3.13. `fnmatch.fnmatch()` handles `**` as a multi-segment wildcard
on all supported Python versions.
"""
from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from typing import Literal

# ---------------------------------------------------------------------------
# Public type alias — closed enum mirroring design-axis-B § 5
# ---------------------------------------------------------------------------

Classification = Literal["auto-resolve", "auto-defer", "always-escalate"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Allow-list scope per design-recommendation.md Open Question #5 (v1).
# Paths in related_artifacts matching ANY of these globs are considered
# \"in-scope\" for auto-resolve. Use fnmatch.fnmatch() for matching — see
# module docstring for Python-version rationale.
ALLOW_LIST_GLOBS: tuple[str, ...] = (
    "docs/**",
    ".claude/knowledge/**",
)

# Hedge tokens per axis-B § 5.1 (case-insensitive substring match).
# Presence of any hedge token in suggested_approach promotes med → auto-defer.
HEDGE_TOKENS: tuple[str, ...] = (
    "may need",
    "could",
    "design choice",
    "tradeoff",
    "unclear",
    "open question",
    "investigate",
)

# Tags that force always-escalate per axis-B § 5.3.
# MIRRORS `.claude/orchestrator-prompt.md § Actions with care` (constraint #14).
ESCALATE_TAGS: frozenset[str] = frozenset({
    "architectural",
    "cross-subsystem",
    "schema-change",
    "security",
    "auth",
    "human-required",
    "human-contested",
    "data-migration",
})

# Tags that trigger exclusion from the eligible set per axis-B § 3.1 F3.
# Prefix-match: any tag starting with "triage-active-" is excluded (in-flight lock).
EXCLUDE_TAGS_PREFIX: tuple[str, ...] = ("triage-active-",)
# Literal-match exclusions (exact tag equality).
EXCLUDE_TAGS_LITERAL: frozenset[str] = frozenset({
    "operator-active",
    "triage-deferred",
    "triage-escalated",
    "triage-stuck",
    "human-contested",
    "human-required",
    "hold",
})

# Severity values that force always-escalate per axis-B § 5.3.
ESCALATE_SEVERITIES: frozenset[str] = frozenset({"high"})

# Age floor per axis-B § 3.1 F2: issue must be at least this many hours old.
AGE_FLOOR_HOURS: int = 24

# Agent name used by the triage routine itself (self-origin exclusion, § 3.1 F4).
TRIAGE_AGENT_ID: str = "triage-tick"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def eligible_issues(records: list[dict]) -> list[dict]:
    """Apply §B eligibility predicate to a list of issue records.

    Args:
        records: list of issue records as returned by
            `issues(action='query', ...)`. Each record MUST conform to
            `.claude/knowledge/reference/issue-queue-schema.md § Record schema`.
            Required fields read by this function: `id`, `severity`, `tags`,
            `status`, `related_artifacts`, `suggested_approach`, `created_at`,
            `origin`. Missing optional fields are tolerated (treated as
            `None` / `[]`).

    Returns:
        A new list of shallow-copied records, each with a `_classification`
        key whose value is one of:
          - `'auto-resolve'`    — eligible for autonomous fix
          - `'auto-defer'`      — eligible for a deferral mutation only
          - `'always-escalate'` — eligible for escalation mutation only

        The `_classification` key is SYNTHESIZED (not stored) — see module
        docstring.

        Records failing §3 filters (status, age, tag exclusion, self-origin)
        are NOT included. The returned list preserves input order; ranking
        is the caller's responsibility (§3.2 lives at the call-site).

    The returned record is a SHALLOW COPY — the caller may mutate
    `_classification` on the copy without affecting the input record.
    Other fields are NOT deep-copied; treat nested lists/dicts as read-only.
    """
    result: list[dict] = []
    for record in records:
        if not _passes_filter(record):
            continue
        copy = dict(record)
        copy["_classification"] = _classify(record)
        result.append(copy)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _passes_filter(record: dict) -> bool:
    """Return True iff the record passes all §3.1 F1–F4 filters.

    F1 — status: only 'open' and 'triaged' are eligible. In practice callers
         pre-filter via query; this is a safety-net check.
    F2 — age gate: created_at must be ≥ AGE_FLOOR_HOURS hours ago.
    F3 — tag exclusion: literal-match and prefix-match tag sets.
    F4 — self-origin: exclude records filed by the triage routine itself.
    """
    # F1: status guard (safety net — query should pre-filter)
    status = record.get("status", "")
    if status not in ("open", "triaged"):
        return False

    # F2: age gate
    created_at = record.get("created_at")
    if created_at and not _is_old_enough(created_at):
        return False

    # F3: tag exclusion
    tags: list[str] = record.get("tags") or []
    for tag in tags:
        if tag in EXCLUDE_TAGS_LITERAL:
            return False
        if any(tag.startswith(prefix) for prefix in EXCLUDE_TAGS_PREFIX):
            return False

    # F4: self-origin exclusion — guard against origin being None or non-dict
    origin = record.get("origin")
    origin_agent: str | None = (
        origin.get("agent") if isinstance(origin, dict) else None
    )
    if origin_agent == TRIAGE_AGENT_ID:
        return False

    return True


def _is_old_enough(created_at: str) -> bool:
    """Return True iff created_at is at least AGE_FLOOR_HOURS hours before now.

    Tolerates trailing 'Z' (UTC) and '+00:00' offsets.
    Returns True (pass through) on parse failure — let the orchestrator decide.
    """
    try:
        # Normalise 'Z' suffix to '+00:00' for fromisoformat() on Python <3.11
        ts_str = created_at.rstrip("Z") + "+00:00" if created_at.endswith("Z") else created_at
        created = datetime.fromisoformat(ts_str)
        now = datetime.now(tz=timezone.utc)
        age_hours = (now - created).total_seconds() / 3600
        return age_hours >= AGE_FLOOR_HOURS
    except (ValueError, TypeError):
        # Unparseable timestamp — default-pass so caller sees the record
        return True


def _classify(record: dict) -> Classification:
    """Given a §3-passing record, return the §5 classification.

    Default-deny: any uncertainty → 'always-escalate'.
    Visible to tests; not part of the public API contract.

    §5.3 always-escalate fires first (most restrictive).
    §5.1 auto-resolve requires all predicates to fire.
    §5.2 auto-defer is the fallback.
    """
    tags: list[str] = record.get("tags") or []
    severity: str = record.get("severity") or ""
    related: list[str] = record.get("related_artifacts") or []
    approach: str = record.get("suggested_approach") or ""

    # §5.3: always-escalate — severity or escalate-tag present
    if severity in ESCALATE_SEVERITIES:
        return "always-escalate"
    if any(t in ESCALATE_TAGS for t in tags):
        return "always-escalate"

    # §5.1: auto-resolve — all predicates must fire
    # P1: no hedge tokens in suggested_approach (case-insensitive)
    approach_lower = approach.lower()
    has_hedge = any(token in approach_lower for token in HEDGE_TOKENS)

    # P2: all related_artifacts are within the allow-list scope
    in_scope = _all_in_scope(related)

    # P3: severity must be 'low' or 'med' (not 'high' — already handled above)
    is_low_med = severity in ("low", "med", "")

    if in_scope and is_low_med and not has_hedge:
        return "auto-resolve"

    # §5.2: auto-defer fallback
    return "auto-defer"


def _all_in_scope(related_artifacts: list[str]) -> bool:
    """Return True iff ALL paths in related_artifacts match at least one
    ALLOW_LIST_GLOB.

    An empty artifact list is considered in-scope (no out-of-scope evidence).
    Uses fnmatch.fnmatch() — see module docstring for Python-version rationale.
    """
    if not related_artifacts:
        return True
    return all(
        any(fnmatch.fnmatch(path, glob) for glob in ALLOW_LIST_GLOBS)
        for path in related_artifacts
    )
