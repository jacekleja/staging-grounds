# Subtask 2: Tier classifier wire (proxy→agent) + shadow-mode calibration [solution-design] [peer-review]

**Description**: Complete the tier classifier wiring. The proxy-side hot-path tier heuristic already exists (`conversational-search/conversational-proxy/app/service/tier_signal_computer.py`, on main, brought in by the rebase) but the proxy→agent tier-injection wire is PARTIAL: today the agent gets `tier` from the LLM prefix (`_parse_dispatch_prefix`), not from the proxy's `TierSignalComputer` output.

**[solution-design] required FIRST** — there are two defensible shapes the implementer must NOT silently pick between:
- **(A) LLM-prefix tier** (current): the LLM emits `TIER:` and `_parse_dispatch_prefix` decodes it. Cheaper to keep; no new wire.
- **(B) proxy-injected tier**: the proxy computes tier via `TierSignalComputer` and injects it on the `ConverseRequest` / run config; the agent reads it pre-LLM in `compile_system_prompt` / state init. Deterministic, no LLM dependence, but a new wire field + cross-repo coupling.
The solution-designer picks A or B with rationale, considering: the §5 signals are pure arithmetic over data already on the wire (architect rejected LLM-tier as needless cost); the facet_config TTL coupling (a stale facet_config silently corrupts tier signals); and the SHADOW-MODE constraint below.

**SHADOW MODE (Operator Decision #1 default — record in impl-report):** ship §5's `80 / 2000 / 12000` + entropy thresholds in shadow mode — log `tier` + `tier_signals` + `classifier_path` to `turn_events`, do NOT switch composition on the live thresholds yet. The classifier becomes observable before it is load-bearing; calibration happens against logged traffic. The (mode,tier)→composition table (Subtask 6) reads the tier but the live-switch is gated behind calibration.

**Cross-repo enum-replication constraint (load-bearing):** the proxy CANNOT import `canonical_enums`. If shape B adds a wire field carrying an enum, the value vocabulary must be replicated proxy-side and kept parity-tested. ALSO FIX: `tier_signal_computer.py`'s parity test `test_tier_signal_computer.py § TestEnumParity.AGENT_ENUMS_PATH` carries a hard-coded worktree path (`session-1779224125-...`) that breaks in every other checkout — replace with a worktree-relative or env-driven path as part of this subtask.

**Agent**: solution-designer (design phase), then implementer (build phase). Orchestrator inserts pre-flight-gate after the solution-designer output per its standard flow.

**Knowledge**:
- `docs/_handoff-pack/03 · Handoff brief.md` § 5 (tier signals, 3-path architecture, boundary thresholds) and § 9.3 (Phase 3 hot-path version).
- `.claude/knowledge/constraints/conversational-proxy-cross-repo-enum-replication.md` — proxy import block + the parity tests + the hard-coded worktree-path bug to fix.
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md § v2 boundary-contract violations` — facet_config TTL coupling to tier classification; `tracker_id` required at /converse.
- `conversational-search/src/conversational_search/agent/canonical_enums.py` § TIER_ENUM / CLASSIFIER_PATH_ENUM / TIER_EXTRA_STATES.

**Dependencies**: Subtask 1 (tier-signal log fields must exist in state.py first).

**Context files**:
- `{session_dir}/` Subtask 1 impl-report — confirms tier-signal log fields landed and their names.

**Expected output**: solution-designer artifact picks A or B with rationale + the shadow-mode wiring shape; implementer lands the wire, the shadow-mode `turn_events` logging, and the parity-path fix. Targeted tests: existing `test_tier_signal_computer.py` (incl. the fixed parity path) passes; a new test asserts tier is logged but composition is NOT live-switched in shadow mode. Build green. impl-report records the assumed default (shadow mode), the A/B choice, and the LLM-call count per turn-1 path touched (MUST remain 1; shape B should reduce LLM dependence, not add a call).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: yes (A vs B is two defensible shapes with cross-repo and call-budget consequences — solution-designer picks before implementation).

**UX phase**: no
