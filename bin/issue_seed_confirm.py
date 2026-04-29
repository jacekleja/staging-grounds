#!/usr/bin/env python3
"""Operator-confirm CLI for D13 seed-sweep candidates.

Reads .agent_context/issues-seed-candidates.jsonl (main repo root).
Per candidate: y=file via issue_registry.file_issue, n=skip, e=edit then file, s=stop.
Idempotent via candidate_dedupe_key collision-skip.
Hard-cap: 100 candidates per run (D13(e)); excess truncated with stderr warning.

Design reference: design-issue-queue.md §D13(d).
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional

# Reuse issue_registry's main_root resolver and file_issue API directly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import issue_registry  # noqa: E402

_HARD_CAP = 100
_VALID_KEYS = {'y', 'n', 'e', 's', ''}


def _candidates_path(main_root: pathlib.Path) -> pathlib.Path:
    """Path to issues-seed-candidates.jsonl under main_root."""
    return main_root / '.agent_context' / 'issues-seed-candidates.jsonl'


def _read_candidates(path: pathlib.Path) -> list:
    """Read JSONL candidates. Returns [] on missing/empty.

    Malformed lines are stderr-warned and skipped.
    """
    if not path.exists():
        return []
    candidates = []
    with open(str(path), 'r', encoding='utf-8') as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f'Warning: malformed JSONL at line {lineno}: {e}', file=sys.stderr)
    return candidates


def _render_candidate(candidate: dict, idx: int, total: int) -> None:
    """Print title/summary/severity/related_artifacts for operator review."""
    print(f'\n--- Candidate {idx}/{total} ---')
    print(f'  Title    : {candidate.get("title", "(no title)")}')
    print(f'  Severity : {candidate.get("severity_heuristic", "(unknown)")}')
    summary = candidate.get('summary', '')
    if summary:
        # Truncate long summaries for readability
        if len(summary) > 300:
            summary = summary[:297] + '...'
        print(f'  Summary  : {summary}')
    artifacts = candidate.get('related_artifacts', [])
    if artifacts:
        print(f'  Artifacts: {", ".join(str(a) for a in artifacts[:3])}')
    claim = candidate.get('claim_excerpt', '')
    if claim:
        if len(claim) > 200:
            claim = claim[:197] + '...'
        print(f'  Claim    : {claim}')


def _prompt_decision() -> str:
    """Loop on input() until y/n/e/s received. Default empty -> 'n'. Return single lowercase char."""
    while True:
        try:
            raw = input('  Action [y=file / n=skip / e=edit / s=stop] (default n): ').strip().lower()
        except EOFError:
            # Non-TTY / piped input: treat as 's' (stop) rather than crashing.
            return 's'
        if raw in _VALID_KEYS:
            return raw or 'n'
        print(f'  Invalid key "{raw}". Please enter y, n, e, or s.', file=sys.stderr)


def _edit_candidate(candidate: dict) -> Optional[dict]:
    """Open $EDITOR (default vi) on a tempfile of pretty-printed JSON.

    Re-parse; return None on parse error (caller re-prompts same candidate).
    """
    editor = os.environ.get('EDITOR', 'vi')
    with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False, encoding='utf-8') as tmp:
        json.dump(candidate, tmp, indent=2, ensure_ascii=False)
        tmppath = tmp.name
    try:
        result = subprocess.run([editor, tmppath])
        if result.returncode != 0:
            print(f'Editor exited with code {result.returncode}; candidate unchanged.', file=sys.stderr)
            return None
        with open(tmppath, 'r', encoding='utf-8') as fh:
            text = fh.read()
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f'Parse error after edit: {e}; candidate unchanged.', file=sys.stderr)
        return None
    finally:
        try:
            os.unlink(tmppath)
        except OSError:
            pass


def _file_one(candidate: dict, sweep_run_at: str, main_root: pathlib.Path) -> dict:
    """Translate candidate -> issue_registry.file_issue call.

    Returns file_issue's dict result verbatim.
    """
    origin = {
        'agent': 'researcher-seed',
        'sweep_run_at': sweep_run_at,
        'session_id': None,
        'finding_id': None,
        'hygiene_run_id': None,
        'filed_at_round': None,
    }
    return issue_registry.file_issue(
        title=candidate.get('title', ''),
        summary=candidate.get('summary', ''),
        severity=candidate.get('severity_heuristic', 'low'),
        dedupe_key=candidate.get('candidate_dedupe_key', ''),
        origin=origin,
        tags=candidate.get('tags') or [],
        suggested_approach=candidate.get('suggested_approach'),
        related_artifacts=candidate.get('related_artifacts') or [],
        main_root=main_root,
    )


def _process_loop(candidates: list, main_root: pathlib.Path, auto_yes: bool = False) -> dict:
    """Iterate candidates with interactive prompt.

    Returns summary dict {filed, skipped, edited, errors, stopped_early}.
    """
    sweep_run_at = datetime.now(timezone.utc).isoformat()
    summary = {'filed': 0, 'skipped': 0, 'edited': 0, 'errors': 0, 'stopped_early': False}
    total = len(candidates)

    for idx, candidate in enumerate(candidates, start=1):
        _render_candidate(candidate, idx, total)

        while True:
            if auto_yes:
                decision = 'y'
            else:
                decision = _prompt_decision()

            if decision == 's':
                remaining = total - idx
                print(f'\nStopped at candidate {idx}/{total}; {remaining} candidate(s) not reviewed.')
                summary['stopped_early'] = True
                return summary

            elif decision == 'n':
                print(f'  Skipped candidate {idx}.')
                summary['skipped'] += 1
                break

            elif decision == 'e':
                edited = _edit_candidate(candidate)
                if edited is None:
                    # Parse error or editor failure; re-prompt same candidate
                    continue
                candidate = edited
                summary['edited'] += 1
                decision = 'y'
                # Fall through to 'y' processing below

            if decision == 'y':
                result = _file_one(candidate, sweep_run_at, main_root)
                if result.get('error') == 'dedupe-collision':
                    existing_id = result.get('existing_id', '?')
                    print(f'  Skipped (already filed): existing_id={existing_id}')
                    summary['skipped'] += 1
                elif result.get('error'):
                    err = result['error']
                    print(f'  Error filing candidate: {err} — {result}', file=sys.stderr)
                    summary['errors'] += 1
                    # Re-prompt so operator can 'e' to fix or 'n' to skip
                    if not auto_yes:
                        continue
                else:
                    print(f'  Filed: {result.get("id", "?")}')
                    summary['filed'] += 1
                break

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='issue_seed_confirm.py',
        description='Operator-confirm CLI for D13 seed-sweep candidates.',
    )
    parser.add_argument(
        '--candidates-path',
        dest='candidates_path',
        default=None,
        help='Override path to issues-seed-candidates.jsonl (default: auto-resolved from main_root)',
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        default=False,
        help='Auto-confirm all candidates (for testing; skips interactive prompt)',
    )
    args = parser.parse_args()

    try:
        main_root = issue_registry._resolve_main_root()
    except issue_registry.IssueRegistryError as e:
        print(f'Error resolving main root: {e}', file=sys.stderr)
        return 2

    if args.candidates_path:
        cand_path = pathlib.Path(args.candidates_path)
    else:
        cand_path = _candidates_path(main_root)

    if not cand_path.exists():
        print(f'No candidates file at {cand_path}; nothing to confirm.')
        return 0

    try:
        candidates = _read_candidates(cand_path)
    except OSError as e:
        print(f'Error reading candidates file: {e}', file=sys.stderr)
        return 2

    if not candidates:
        print('Candidates file is empty; nothing to confirm.')
        return 0

    if len(candidates) > _HARD_CAP:
        print(
            f'Warning: candidates file has {len(candidates)} entries; processing first {_HARD_CAP} '
            f'(D13(e) hard-cap). Re-run after operator review for the rest.',
            file=sys.stderr,
        )
        candidates = candidates[:_HARD_CAP]

    print(f'D13 seed-sweep review: {len(candidates)} candidate(s) from {cand_path}')

    try:
        summary = _process_loop(candidates, main_root, auto_yes=args.yes)
    except KeyboardInterrupt:
        # Treat Ctrl-C as 's' (stop); exit with 130 per POSIX signal convention.
        total = len(candidates)
        print(f'\nInterrupted. Summary: {summary if "summary" in dir() else "unknown"}', file=sys.stderr)
        return 130

    print(
        f'\nDone. Filed: {summary["filed"]} | Skipped: {summary["skipped"]} | '
        f'Edited: {summary["edited"]} | Errors: {summary["errors"]}'
        + (' | Stopped early.' if summary['stopped_early'] else '.')
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
