#!/usr/bin/env python3
"""Lint script: per-record size check on .claude/knowledge/**/*.md files.

H2-bounded record definition:
  - Each '## <heading>' line starts a new record.
  - The record body includes the '## <heading>' line itself plus all subsequent
    lines up to (but not including) the next '## ' heading or EOF.
  - Line count = total lines in that span.

Threshold:
  PER_RECORD_LINE_THRESHOLD = 120

Findings emitted when a record body's line count exceeds 120:
  <file>:<H2-start-line>: record-size: H2 "<heading>" is <N> lines (>120)

Frontmatter key 'lint: digest-collection':
  - Files carrying this marker are EXEMPT from file-size warnings (future
    extension; no file-size warning is emitted in this iteration).
  - The per-record 120-line threshold STILL fires inside digest-collection
    files — a 450-line single record is wrong even inside a digest.

Exit codes:
  0 — no findings.
  1 — one or more record-size findings.
  2 — invocation error (project root not found, bad --single-file path, etc.).

Run:
    python3 bin/lint_knowledge_record_size.py [--root <path>]
    python3 bin/lint_knowledge_record_size.py --single-file <path>
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

# Per-record line threshold. Records whose H2-bounded body exceeds this are flagged.
PER_RECORD_LINE_THRESHOLD = 120

# YAML frontmatter lint-field value that marks a file as a digest collection.
# Exempt from file-size warnings (future); per-record threshold still fires.
_LINT_DIGEST_COLLECTION = "digest-collection"

# Matches the start of an H2 heading line.
_H2_RE = re.compile(r"^## (.+)$")

# Matches the inline-list form of the frontmatter exempt-headings key:
#   lint-record-size-exempt: ['heading1', 'heading2', ...]
_EXEMPT_RE = re.compile(r"^lint-record-size-exempt:\s*\[([^\]]*)\]\s*$")


def _project_root(override: str | None = None) -> Path:
    """Derive project root from --root arg, git, or __file__ location (bin/ -> parent)."""
    if override:
        return Path(override).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: this script lives in bin/; project root is one level up.
        return Path(__file__).resolve().parent.parent


def _parse_frontmatter_lint_field(lines: list[str]) -> str | None:
    """Parse YAML frontmatter and return the value of the 'lint:' field.

    Accepts both bare scalar form (lint: digest-collection) and list form
    (lint: [digest-collection, ...]). Returns the first token found, or None
    if the field is absent or frontmatter is missing.

    The frontmatter must start on line 1 with '---'.
    """
    if not lines or lines[0].rstrip() != "---":
        return None

    lint_scalar_re = re.compile(r"^lint:\s*(\S+)\s*$")
    lint_list_re = re.compile(r"^lint:\s*\[([^\]]+)\]")

    for line in lines[1:]:
        stripped = line.rstrip()
        if stripped == "---":
            return None
        m = lint_scalar_re.match(stripped)
        if m:
            return m.group(1)
        m = lint_list_re.match(stripped)
        if m:
            # Return the first comma-separated token in the list.
            first = m.group(1).split(",")[0].strip()
            return first if first else None

    return None


def _parse_frontmatter_exempt_headings(lines: list[str]) -> list[str]:
    """Parse YAML frontmatter and return the list of heading substrings to exempt.

    Reads the inline-list form:
        lint-record-size-exempt: ['heading1', 'heading2']

    Returns the list of substrings, or [] if the key is absent or frontmatter is missing.
    """
    if not lines or lines[0].rstrip() != "---":
        return []

    for line in lines[1:]:
        stripped = line.rstrip()
        if stripped == "---":
            return []
        m = _EXEMPT_RE.match(stripped)
        if m:
            result = []
            for item in m.group(1).split(","):
                item = item.strip().strip("'\"")
                if item:
                    result.append(item)
            return result

    return []


def _split_into_h2_records(lines: list[str]) -> list[tuple[str, int, int]]:
    """Split file lines into H2-bounded records.

    Returns list of (heading_text, h2_line_number_1indexed, body_line_count).
    h2_line_number is the 1-indexed line number of the '## ' heading line.
    body_line_count includes the heading line itself.
    """
    records: list[tuple[str, int, int]] = []
    current_heading: str | None = None
    current_start_lineno: int = 0
    current_line_count: int = 0

    for i, line in enumerate(lines):
        lineno = i + 1  # 1-indexed
        m = _H2_RE.match(line.rstrip("\n").rstrip("\r"))
        if m:
            # Flush previous record.
            if current_heading is not None:
                records.append((current_heading, current_start_lineno, current_line_count))
            current_heading = m.group(1).strip()
            current_start_lineno = lineno
            current_line_count = 1
        elif current_heading is not None:
            current_line_count += 1

    # Flush final record.
    if current_heading is not None:
        records.append((current_heading, current_start_lineno, current_line_count))

    return records


def _check_file(filepath: Path) -> list[tuple[str, int, str]]:
    """Check a single .md file for oversized H2-bounded records.

    Returns list of (filepath_str, lineno, message) finding tuples.
    lineno is the 1-indexed line number of the '## ' heading.
    """
    findings: list[tuple[str, int, str]] = []
    filepath_str = str(filepath)

    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        # Unreadable file — skip silently; this lint has no place to report I/O errors.
        return findings

    stripped_lines = [l.rstrip() for l in lines]

    # Parse frontmatter lint field (determines file-size exemption; does NOT
    # suppress per-record threshold — both digest-collection and regular files
    # get the same per-record check).
    lint_value = _parse_frontmatter_lint_field(stripped_lines)
    _is_digest_collection = lint_value == _LINT_DIGEST_COLLECTION  # noqa: F841 — future use

    # Parse per-record exemptions: headings matching any listed substring are skipped.
    exempt_substrings = _parse_frontmatter_exempt_headings(stripped_lines)

    records = _split_into_h2_records(lines)
    for heading, start_lineno, line_count in records:
        if line_count > PER_RECORD_LINE_THRESHOLD:
            if any(sub in heading for sub in exempt_substrings):
                continue
            findings.append((
                filepath_str,
                start_lineno,
                f"H2 \"{heading}\" is {line_count} lines (>{PER_RECORD_LINE_THRESHOLD})",
            ))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Per-record size lint for .claude/knowledge/**/*.md files.\n\n"
            f"Emits a finding for every H2-bounded record body that exceeds\n"
            f"{PER_RECORD_LINE_THRESHOLD} lines. The per-record threshold fires\n"
            "inside 'lint: digest-collection' files as well — the digest-collection\n"
            "marker exempts files from file-size warnings only (not yet implemented).\n\n"
            "Output format: <file>:<line>: record-size: <message>\n\n"
            "Exit 0: no findings. Exit 1: findings present. Exit 2: invocation error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        default=None,
        help="Project root override (default: auto-detected via git or bin/ parent).",
    )
    parser.add_argument(
        "--single-file",
        metavar="PATH",
        default=None,
        help=(
            "Check a single .md file only (absolute or relative path). "
            "Skips the full corpus walk."
        ),
    )
    args = parser.parse_args()

    try:
        root = _project_root(args.root)
    except Exception as exc:
        print(f"ERROR: cannot determine project root: {exc}", file=sys.stderr)
        return 2

    # --single-file mode.
    if args.single_file:
        single = Path(args.single_file)
        if not single.is_absolute():
            single = Path.cwd() / single
        if not single.is_file():
            print(f"ERROR: --single-file path not found: {single}", file=sys.stderr)
            return 2
        file_findings = _check_file(single)
        if file_findings:
            for filepath_str, lineno, message in file_findings:
                print(f"{filepath_str}:{lineno}: record-size: {message}")
            print(f"\n{len(file_findings)} finding(s) found.", file=sys.stderr)
            return 1
        print(f"OK: {single} — no oversized records.", file=sys.stderr)
        return 0

    # Full corpus walk.
    knowledge_root = root / ".claude" / "knowledge"
    if not knowledge_root.exists():
        print(f"ERROR: .claude/knowledge/ not found at {knowledge_root}", file=sys.stderr)
        return 2

    all_findings: list[tuple[str, int, str]] = []
    files_scanned = 0

    for filepath in sorted(knowledge_root.rglob("*.md")):
        if not filepath.is_file():
            continue
        # Skip .claude/knowledge/archive/** — archive copies are out-of-scope for this lint.
        rel = filepath.relative_to(knowledge_root)
        if rel.parts and rel.parts[0] == "archive":
            continue
        files_scanned += 1
        all_findings.extend(_check_file(filepath))

    if all_findings:
        for filepath_str, lineno, message in all_findings:
            print(f"{filepath_str}:{lineno}: record-size: {message}")
        print(
            f"\n{len(all_findings)} finding(s) in {files_scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {files_scanned} file(s) scanned, no oversized records.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
