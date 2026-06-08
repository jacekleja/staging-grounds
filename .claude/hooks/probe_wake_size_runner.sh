#!/usr/bin/env bash
# probe_wake_size_runner.sh — launches an isolated probe session for the
# asyncRewake wake-message size-ceiling canary.
#
# Usage:
#   probe_wake_size_runner.sh --size-kib N
#
# N must be a positive integer. The runner creates a fresh temp session dir
# under /tmp/asyncrewake-canary-<N>kib-<TS>/, writes an isolated settings.json
# that registers ONLY probe-wake-size.py on Stop with asyncRewake: true, then
# launches claude with that settings file and captures the transcript.
#
# Argument convention: named flag --size-kib N (not positional).

set -euo pipefail

usage() {
    echo "Usage: $0 --size-kib N" >&2
    echo "  N: positive integer (size in KiB)" >&2
    exit 1
}

# --- Parse --size-kib N ------------------------------------------------------
SIZE_KIB=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --size-kib)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --size-kib requires an argument" >&2
                usage
            fi
            SIZE_KIB="$2"
            shift 2
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            usage
            ;;
    esac
done

if [[ -z "$SIZE_KIB" ]]; then
    echo "ERROR: --size-kib is required" >&2
    usage
fi

# Validate: must be a positive integer
if ! [[ "$SIZE_KIB" =~ ^[0-9]+$ ]] || [[ "$SIZE_KIB" -le 0 ]]; then
    echo "ERROR: --size-kib must be a positive integer, got: '$SIZE_KIB'" >&2
    exit 1
fi

# --- Compute derived values --------------------------------------------------
SIZE_BYTES=$(( SIZE_KIB * 1024 ))
TS=$(date -u +%Y%m%dT%H%M%SZ)
PROBE_DIR="/tmp/asyncrewake-canary-${SIZE_KIB}kib-${TS}"
mkdir -p "$PROBE_DIR"

# --- Resolve REPO_ROOT -------------------------------------------------------
REPO_ROOT=$(git rev-parse --show-toplevel)

# --- Warn for large payloads -------------------------------------------------
if [[ "$SIZE_BYTES" -ge 65536 ]]; then
    SIZE_TOKENS=$(( SIZE_BYTES / 4 ))  # rough approximation: 4 bytes/token
    echo "WARNING: probe payload ${SIZE_BYTES} bytes ≈ ${SIZE_TOKENS} tokens; this WILL burn significant probe-session budget" >&2
fi

# --- Write isolated settings file --------------------------------------------
# Uses single-quotes around $PROBE_DIR and $SIZE_BYTES to embed the resolved
# values directly into the JSON (not shell-expanded at hook-fire time).
cat > "$PROBE_DIR/settings.json" <<EOF
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "PROBE_SESSION_DIR='${PROBE_DIR}' PROBE_WAKE_SIZE_BYTES=${SIZE_BYTES} python3 ${REPO_ROOT}/.claude/hooks/probe-wake-size.py",
            "asyncRewake": true
          }
        ]
      }
    ]
  }
}
EOF

# --- Launch claude ------------------------------------------------------------
echo "Launching probe session: PROBE_DIR=$PROBE_DIR SIZE_BYTES=$SIZE_BYTES" >&2

set +e
claude \
    --settings "$PROBE_DIR/settings.json" \
    --output-format stream-json \
    --verbose \
    --include-partial-messages \
    -p "Say hello." \
    > "$PROBE_DIR/transcript.jsonl" \
    2> "$PROBE_DIR/stderr.log"
CLAUDE_EXIT=$?
set -e

# --- Report ------------------------------------------------------------------
echo "$PROBE_DIR/transcript.jsonl"

if [[ "$CLAUDE_EXIT" -ne 0 ]]; then
    echo "WARNING: claude exited with code $CLAUDE_EXIT; check $PROBE_DIR/stderr.log" >&2
    exit "$CLAUDE_EXIT"
fi

exit 0
