#!/usr/bin/env python3
"""
Findings-lifecycle deferred-fix watchpoint probe.

Registered 2026-04-21 after FIX-A + FIX-D shipped (commit 84efb10).
Evidence-based decision deferred FIX-B/C/E as LOW-IMPACT. Three triggers
exist to re-evaluate each if reality diverges from the empirical baseline.

Run manually:
    python3 bin/claude-study-watchpoints.py

Exit codes:
    0 — all watchpoints clean
    1 — one or more watchpoints fired (see stderr for details)
    2 — probe failure (env/IO error)

Rationale + decision history: `.claude/knowledge/decisions/findings-lifecycle-deferred-fixes.md`
and memory `project_findings_lifecycle_watchpoints.md`.

Triggers:
1. **FIX-B:** two `cycling-terminal-sentinel` entries within 5 min from different session_ids
   → concurrent /cycling detected; re-evaluate FIX-B cross-session content-hash ledger.
2. **FIX-C:** `last_drift_signal_ts` in `.study-state` moves backward across git history
   → parallel study-run cursor stomping; re-evaluate FIX-C per-session cursor.
3. **FIX-E:** any finding JSON tagged both `knowledge-drift` and `marathon-*`
   → dual-consumer tag collision; re-evaluate FIX-E tombstone on FindingData.
4. **DRIFT-OVERFLOW:** `set_c_overflow > 0` in any `.agent_context/study/*/precompute.json`
   from the last 30 days → SET_C_CAP=20 is being hit; re-evaluate raising the cap or
   re-ordering archival relative to the cap to prevent silent signal loss.
   See `research-drift-dedup.md § Layer 4` and `findings-lifecycle-deferred-fixes.md`.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGE_LOG = ROOT / ".claude" / "knowledge-log" / ".change-log.jsonl"
STUDY_STATE = ROOT / ".claude" / "knowledge" / ".study-state"
SESSIONS_DIR = ROOT / ".agent_context" / "sessions"
STUDY_DIR = ROOT / ".agent_context" / "study"


def fire(name: str, detail: str) -> None:
    print(f"WATCHPOINT FIRED: {name}", file=sys.stderr)
    print(f"  {detail}", file=sys.stderr)


def check_fix_b_concurrent_cycling() -> int:
    """Trigger: two cycling-terminal-sentinel entries from different
    session_ids within a 5-minute window. Indicates concurrent /cycling.
    """
    if not CHANGE_LOG.exists():
        return 0
    entries: list[tuple[datetime, str]] = []
    with CHANGE_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("actor") != "external:cycling-terminal-sentinel":
                continue
            if e.get("section") != "terminal-mode-complete":
                continue
            ts_str = e.get("ts")
            sid = e.get("session_id")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            entries.append((ts, sid or "null"))
    entries.sort()
    hits: list[str] = []
    window = timedelta(minutes=5)
    for i, (ts_a, sid_a) in enumerate(entries):
        for ts_b, sid_b in entries[i + 1 :]:
            if ts_b - ts_a > window:
                break
            if sid_a != sid_b:
                hits.append(
                    f"{ts_a.isoformat()} ({sid_a}) <-> {ts_b.isoformat()} ({sid_b})"
                )
    if hits:
        fire(
            "FIX-B concurrent /cycling",
            f"{len(hits)} concurrent sentinel pair(s):\n    "
            + "\n    ".join(hits[:5])
            + ("" if len(hits) <= 5 else f"\n    ... +{len(hits) - 5} more"),
        )
        return 1
    return 0


def check_fix_c_cursor_backward() -> int:
    """Trigger: `.study-state.last_run.last_drift_signal_ts` moved backward
    across git history. Indicates parallel study-run cursor stomping.
    """
    if not STUDY_STATE.exists():
        return 0
    try:
        log = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", "--", str(STUDY_STATE)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[probe] git log failed: {exc}", file=sys.stderr)
        return 2
    commits = [c for c in log.stdout.splitlines() if c]
    prior_ts: str | None = None
    prior_commit: str | None = None
    for commit in commits:
        try:
            show = subprocess.run(
                ["git", "show", f"{commit}:.claude/knowledge/.study-state"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            continue
        ts_line = [
            ln
            for ln in show.stdout.splitlines()
            if "last_drift_signal_ts:" in ln
        ]
        if not ts_line:
            continue
        raw = ts_line[0].split(":", 1)[1].strip()
        # Strip YAML quotes if present
        val = raw.strip().strip("'\"")
        if val in ("null", "~", ""):
            continue
        if prior_ts is not None and val < prior_ts:
            fire(
                "FIX-C cursor backward",
                f"last_drift_signal_ts moved BACKWARD: "
                f"{prior_commit[:8]} had {prior_ts}, {commit[:8]} has {val}",
            )
            return 1
        prior_ts = val
        prior_commit = commit
    return 0


def check_fix_e_dual_tagged() -> int:
    """Trigger: any single finding JSON carries BOTH `knowledge-drift` AND
    any `marathon-*` tag. Indicates dual-consumer consumption of same finding.
    """
    if not SESSIONS_DIR.exists():
        return 0
    hits: list[str] = []
    for session_dir in SESSIONS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        findings_dir = session_dir / "findings"
        if not findings_dir.is_dir():
            continue
        for finding_path in findings_dir.glob("*.json"):
            try:
                data = json.loads(finding_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            tags = data.get("tags") or []
            has_drift = "knowledge-drift" in tags
            has_marathon = any(
                isinstance(t, str) and t.startswith("marathon-") for t in tags
            )
            if has_drift and has_marathon:
                hits.append(str(finding_path.relative_to(ROOT)))
    if hits:
        fire(
            "FIX-E dual-tagged finding",
            f"{len(hits)} finding(s) tagged knowledge-drift AND marathon-*:\n    "
            + "\n    ".join(hits[:5])
            + ("" if len(hits) <= 5 else f"\n    ... +{len(hits) - 5} more"),
        )
        return 1
    return 0


def check_set_c_overflow() -> int:
    """Trigger: any precompute.json from the last 30 days has set_c_overflow > 0.
    Indicates SET_C_CAP=20 was hit and drift findings were silently dropped
    after archival. Re-evaluate raising SET_C_CAP or re-ordering archive/cap.
    See: research-drift-dedup.md § Layer 4; findings-lifecycle-deferred-fixes.md.
    """
    if not STUDY_DIR.exists():
        return 0
    cutoff = datetime.now().astimezone() - timedelta(days=30)
    hits: list[tuple[str, int]] = []
    for precompute_path in STUDY_DIR.glob("*/precompute.json"):
        try:
            mtime = datetime.fromtimestamp(
                precompute_path.stat().st_mtime
            ).astimezone()
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            data = json.loads(precompute_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        overflow = data.get("set_c_overflow")
        if not isinstance(overflow, int):
            continue
        if overflow > 0:
            run_id = precompute_path.parent.name
            hits.append((run_id, overflow))
    if hits:
        hits.sort(key=lambda t: t[0])  # sort by run_id (timestamp-prefixed)
        max_overflow = max(v for _, v in hits)
        summary_lines = [f"{run_id}: overflow={cnt}" for run_id, cnt in hits[:5]]
        fire(
            "DRIFT-OVERFLOW set_c_overflow",
            f"{len(hits)} run(s) hit SET_C_CAP in last 30 days "
            f"(max overflow={max_overflow}); re-evaluate SET_C_CAP or "
            f"archive/cap ordering.\n    "
            + "\n    ".join(summary_lines)
            + ("" if len(hits) <= 5 else f"\n    ... +{len(hits) - 5} more"),
        )
        return 1
    return 0


def main() -> int:
    rc = 0
    for fn in (
        check_fix_b_concurrent_cycling,
        check_fix_c_cursor_backward,
        check_fix_e_dual_tagged,
        check_set_c_overflow,
    ):
        try:
            rc |= fn()
        except Exception as exc:  # noqa: BLE001 — probe robustness
            print(f"[probe] {fn.__name__} crashed: {exc}", file=sys.stderr)
            rc = 2
    if rc == 0:
        print("[watchpoints] all clean — FIX-B/C/E deferral still valid; SET_C overflow not observed in last 30 days")
    return rc


if __name__ == "__main__":
    sys.exit(main())
