# Subtask 11: Proxy ConverseRequest v2 wire fields [peer-review]

**Description**: Ensure the proxy's `ConverseRequest` carries the v2 turn-2+ wire fields and threads them into the LangGraph run config so the agent state sees them. The proxy lives inside the submodule at `conversational-search/conversational-proxy/`. Per the architect Integration Points + §4 schema and `TURN_STATE_ENVELOPE_FIELDS` Channel-3:

- VERIFY first what `main`'s `ConverseRequest` (`conversational-proxy/app/schemas/conversation_schema_v2.py`, ~line 52) already carries (status is "unknown — proxy on main, not in agent branch" per gap analysis §16). The rebase brings the proxy in; some fields may already exist.
- ADD any missing: `is_engagement_of_preview: bool`, `chat_takeover_trigger: bool`, `fork_card_filter_value: str | None`. (`is_engagement_of_preview` is the FE-owned side-channel that turn-2+ handling — Subtask 12 — leans on; the FE owns the eager/deferred firing toggle and sends this flag.)
- Thread the new fields through `conversation_service.converse` / `conversation_router_v2.py` into the LangGraph run config so the agent's Channel-3 per-turn-SSE fields are populated.

**Cross-repo enum-replication constraint (load-bearing):** the proxy CANNOT import `canonical_enums`. If any added field carries an enum value, replicate the vocabulary proxy-side and keep parity tests green (the existing pattern: TIER/COMPOSITION/CLASSIFIER_PATH replicated in `tier_signal_computer.py`; FIRING_MODE in `conversation_service.py`). Confirm the boundary contract: `tracker_id` is REQUIRED at /converse (the empty-string default was a boundary-contract violation — do not regress it).

**Agent**: implementer

**Knowledge**:
- `docs/v2-design/design-v2-detection-response-shapes.md § Integration Points` (point 4: the 3 ConverseRequest fields + threading via `conversation_service.converse`) — REFERENCE.
- `docs/_handoff-pack/03 · Handoff brief.md` § 4 (schema extensions).
- `.claude/knowledge/constraints/conversational-proxy-cross-repo-enum-replication.md` — proxy import block + parity-test pattern.
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md § v2 boundary-contract violations` — `tracker_id` required at /converse.
- `conversational-search/src/conversational_search/agent/canonical_enums.py § TURN_STATE_ENVELOPE_FIELDS` Channel-3 (the per-turn-SSE fields).

**Dependencies**: Subtask 1 (the agent-side Channel-3 state fields `chat_takeover_trigger` / `fork_card_filter_value` must exist for the threaded wire to populate). Runs in PARALLEL with the agent-side spine (3/5/6) once 1 lands.

**Context files**:
- `{session_dir}/` Subtask 1 impl-report — the agent-side Channel-3 field names the proxy must thread into.

**Expected output**: `ConverseRequest` carries the 3 v2 fields (only the missing ones added); they thread into the LangGraph run config; cross-repo enum/field parity tests green; `tracker_id`-required boundary preserved. Targeted test: a /converse request with the new fields populates the agent's Channel-3 state; parity tests pass. Build green (submodule + proxy). impl-report names which fields were already present vs added, and confirms no new LLM call introduced (proxy is wire-only).

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason (the field set and threading path are specified by Integration Points + §4; verify-then-add is mechanical).

**UX phase**: no
