"""Resolver for CAA_CAMPAIGN_ID per-episode discovery.

Extracted from bin/claude-session for unit-testability — the launcher is a
script (not on sys.path) so embedding inline would require runpy/importlib
test wiring.

Priority chain (short-circuit at first slug-regex-valid value):
  P1: cli_campaign        -- --campaign <slug> CLI flag (argparse-side also validates)
  P2: cycle-checkpoint    -- most-recent {state_dir}/cycle-checkpoint_*.json[campaign_id]
  P3: session-dir sidecar -- {state_dir}/campaign-id (single-line slug)
  P4: unset               -- return '' (consumers fall back to session-scoped path)
"""

import glob
import json
import os
import re
import sys

# Byte-equal to .claude/hooks/user-intent-capture.py:70 SLUG_RE (load-bearing --
# a slug accepted by the launcher but rejected by the hook would cause a silent
# coverage gap where campaign-id capture fails for the episode).
_CAMPAIGN_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


def _resolve_campaign_id_for_episode(
    cli_campaign,
    state_dir,
    cycle_state_path,   # noqa: ARG001 — reserved for future use (P2 may need it)
    logger=None,
):
    """Resolve campaign-id for the upcoming episode's CAA_CAMPAIGN_ID env var.

    Args:
        cli_campaign: value from --campaign CLI flag, or None.
        state_dir: session state directory (contains cycle-checkpoint_*.json and campaign-id sidecar).
        cycle_state_path: path to cycle.state (reserved; P2 uses state_dir glob).
        logger: optional callable(str) for advisory log lines; defaults to stderr.

    Returns:
        Validated slug str OR '' (never None). Never raises.
    """
    def _log(msg):
        if logger is not None:
            logger(msg)
        else:
            print(msg, file=sys.stderr, flush=True)

    _log('campaign-id resolve: trying P1..P4')

    # --- P1: CLI flag ---
    if cli_campaign and _CAMPAIGN_SLUG_RE.match(cli_campaign):
        _log(f'campaign-id resolved: P1 (CLI flag) -> "{cli_campaign}"')
        return cli_campaign
    if cli_campaign:
        # Argparse-side already exits 2 on invalid; resolver-side is defense-in-depth.
        _log(f'campaign-id P1 skip: CLI value "{cli_campaign}" fails slug regex')

    # --- P2: most-recent cycle-checkpoint ---
    try:
        ckpt_pattern = os.path.join(state_dir, 'cycle-checkpoint_*.json')
        candidates = glob.glob(ckpt_pattern)
        if candidates:
            newest = max(candidates, key=os.path.getmtime)
            with open(newest, encoding='utf-8') as f:
                data = json.load(f)
            ckpt_campaign = data.get('campaign_id')
            if ckpt_campaign and isinstance(ckpt_campaign, str) and _CAMPAIGN_SLUG_RE.match(ckpt_campaign):
                _log(f'campaign-id resolved: P2 (cycle-checkpoint) -> "{ckpt_campaign}"')
                return ckpt_campaign
            if ckpt_campaign:
                _log(f'campaign-id P2 skip: checkpoint campaign_id "{ckpt_campaign}" fails slug regex or is invalid type')
    except (IOError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # Silent fallthrough — P2 is best-effort; missing/corrupt checkpoint is normal on first episode.
        pass

    # --- P3: session-dir sidecar ---
    sidecar_path = os.path.join(state_dir, 'campaign-id')
    try:
        with open(sidecar_path, encoding='utf-8') as f:
            sidecar_value = f.read().strip()
        if sidecar_value and _CAMPAIGN_SLUG_RE.match(sidecar_value):
            _log(f'campaign-id resolved: P3 (sidecar) -> "{sidecar_value}"')
            return sidecar_value
        if sidecar_value:
            # Operator may have hand-edited the file with an invalid value — log advisory.
            _log(
                f'campaign-id P3 skip: sidecar value "{sidecar_value}" fails slug regex '
                f'^[a-z][a-z0-9-]{{2,63}}$ — treating as unresolved'
            )
    except (IOError, OSError):
        # Sidecar absent on first episode — silent fallthrough.
        pass

    # --- P4: unresolved ---
    _log('campaign-id unresolved: falling back to empty string (consumers use session-scoped path)')
    return ''
