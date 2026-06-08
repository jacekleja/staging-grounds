"""Dispatch-child process-class predicate helpers.

Resolves the CAA_CHILD_SIDECAR_DIR env-var overload: both dispatch-agent.ts
and bin/claude-session set that var, but with opposite-polarity hook-activation
requirements. A second discriminator, CAA_DISPATCH_CHILD=1, is set ONLY by
dispatch-agent.ts to distinguish the two child classes.

Public surface:
  is_dispatched_child()          -> bool   — True iff spawned by dispatch-agent.ts
  is_l2_child()                  -> bool   — True iff L2-sidecar child (not dispatched)
  exit_if_dispatched_child(...)  -> None   — sys.exit(0) guard for D-SUP hooks

Import pattern (top of hook file, alongside stdlib imports):
  from _dispatch_child_guard import exit_if_dispatched_child

Call pattern (first line of main()):
  exit_if_dispatched_child()
"""

import os
import sys


def is_dispatched_child() -> bool:
    """Return True iff this process is a claude-subprocess child spawned by
    dispatch-agent.ts (NOT an L2-sidecar child).

    Discriminates between the two CAA_CHILD_SIDECAR_DIR-bearing contexts:
    - dispatched-child:   CAA_DISPATCH_CHILD == "1"  (set ONLY by dispatch-agent.ts)
    - L2-sidecar:         CAA_CHILD_SIDECAR_DIR set, CAA_DISPATCH_CHILD unset
    - root-orchestrator:  neither var set
    """
    return os.environ.get("CAA_DISPATCH_CHILD") == "1"


def is_l2_child() -> bool:
    """Return True iff this process is an L2-sidecar child session.

    Used by l2-heartbeat-emitter / l2-abort-now-gate to discriminate L2-only
    activation from dispatched-child suppression. An L2 child has the sidecar
    env var set but is NOT a dispatched claude-subprocess.
    """
    return bool(os.environ.get("CAA_CHILD_SIDECAR_DIR")) and not is_dispatched_child()


def exit_if_dispatched_child(reason: str = "") -> None:
    """Call at the very top of main() in every D-SUP hook. No-op for parent
    orchestrator and L2 children; sys.exit(0) for dispatched children.

    Prints a diagnostic to stderr when reason is non-empty AND
    CAA_DEBUG_HOOK_GUARD=1 is set (debug-only path, never fires in production).
    """
    if is_dispatched_child():
        if reason and os.environ.get("CAA_DEBUG_HOOK_GUARD") == "1":
            print(f"[dispatch-child-guard] suppressing hook: {reason}", file=sys.stderr)
        sys.exit(0)
