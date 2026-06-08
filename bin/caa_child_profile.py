"""caa_child_profile.py — Load pipeline additions from a child's profile.

Internal-private helper used by all three L2 launcher entry-flow code paths
(bin/claude-session, bin/caa-session gemini arm, bin/caa-session codex arm)
to read profile.additions.pipelines before pipeline activation. Not part of
the public API.
"""

import json
import os


def _load_child_profile_pipelines(sidecar_dir: 'str | None') -> 'list[str]':
    """Return profile.additions.pipelines from {sidecar_dir}/child-profile.json.

    Returns [] on: sidecar_dir is None, absent file, JSON error, missing key,
    non-list value, or any other read failure. Never raises.
    """
    if not sidecar_dir:
        return []
    try:
        profile_path = os.path.join(sidecar_dir, 'child-profile.json')
        with open(profile_path, encoding='utf-8') as _f:
            data = json.load(_f)
        additions = data.get('profile', {}).get('additions', {}).get('pipelines', [])
        if not isinstance(additions, list):
            return []
        return [p for p in additions if isinstance(p, str)]
    except Exception:
        return []
