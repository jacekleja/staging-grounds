"""capability_probe.py — Pre-spawn capability probe for the caa-session launcher.

Called from bin/caa-session BEFORE any family-CLI subprocess is spawned.
Iterates manifest['required_capabilities'] and verifies each is reachable
in the resolved family arm.  Fail-fast on any "missing" status.

Status taxonomy returned by arm.probe_capabilities():
  "available"              — capability confirmed present; probe passes.
  "missing: <reason>"      — capability absent; raises CapabilityProbeError.
  "deferred per Topic 10"  — check not yet implemented for this family;
                             treated as soft-pass (WARNING logged, no fail-fast).

The probe assumes manifests have already passed C12 (unknown-token gate) so
every token in required_capabilities is a known vocabulary member.  Unknown
tokens that slip through are treated as "missing" (safe-fail).
"""

import sys

# Sentinel returned by arm probe routines for capabilities whose check is
# deferred pending A-3/A-4 work.  Callers that start with this prefix (e.g.
# "deferred per Topic 10 — MCP") are also treated as soft-pass.
DEFERRED_STATUS = "deferred per Topic 10"


class CapabilityProbeError(Exception):
    """Raised when a required capability is not reachable in the target family arm.

    Attributes:
        capability:   the capability token that failed.
        family_name:  the target family (codex, gemini, claude, …).
        probe_status: the raw status string returned by arm.probe_capabilities().
    """

    def __init__(self, capability: str, family_name: str, probe_status: str) -> None:
        self.capability = capability
        self.family_name = family_name
        self.probe_status = probe_status
        super().__init__(
            f"missing capability: {capability}; declared in manifest['required_capabilities'] "
            f"but not reachable in {family_name} arm"
        )


def run_capability_probe(
    arm,
    required_capabilities: list,
    family_name: str,
) -> None:
    """Probe each required capability against the family arm.

    On success:             returns normally (no side effects).
    On missing capability:  raises CapabilityProbeError — caller converts to sys.exit(1).
    On deferred capability: logs a WARNING to stderr and continues.

    No-op when required_capabilities is empty.
    """
    if not required_capabilities:
        return

    results = arm.probe_capabilities(required_capabilities)

    for cap in required_capabilities:
        status = results.get(
            cap,
            f"missing: capability '{cap}' not found in probe results for {family_name} arm",
        )
        if status == "available":
            continue
        if status == DEFERRED_STATUS or status.startswith(DEFERRED_STATUS):
            print(
                f"[caa-session] WARNING: capability {cap!r} probe deferred "
                f"for {family_name} arm ({status}); proceeding without verification",
                file=sys.stderr,
            )
            continue
        # Any other status — including "missing: ..." — is a hard failure.
        raise CapabilityProbeError(cap, family_name, status)
