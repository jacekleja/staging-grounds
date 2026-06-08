# Branch State Report: feat/pr-b2-core-production-features

**Executive summary.** The branch `feat/pr-b2-core-production-features` exists in the `conversational-search` submodule (not the parent repo), is 18 commits ahead / 10 behind `main`, and contains a production-ready response-shape selector (`turn1_selector.py`) with 257-line test coverage — but does NOT contain the full v2 mode/tier/composition dispatcher. That work lives on `feat/v2-campaign` (33 commits ahead, last commit 2026-05-26), which has mode/tier/composition state fields, a working `_parse_dispatch_prefix` decoder in `graph.py`, and 9,743 insertions across 46 files. The architect's "design-from-scratch" framing was based on a branch misidentification: `feat/v2-campaign` is the correct v2 base, and CONTINUE-EXISTING on that lineage is the right posture.

---

## 1. Does the branch exist?

The branch `feat/pr-b2-core-production-features` does NOT exist in the parent (`luigis-box`) repo. It DOES exist in the `conversational-search` submodule at `/home/fanderman/projects/luigis-box/conversational-search/`. [Verified: `conversational-search/git branch -a`]

- Exact local ref: `feat/pr-b2-core-production-features`
- Latest commit SHA: `07eeb6d69d58e0f22db7aaeaa9151cad230089e6`
- Author/date: Jacek Leja, 2026-05-15
- Subject: `prompts: tighten TURN1_PREVIEW_INSTRUCTION teaser to 1 sentence / <=15 words`
- Position vs `main`: **18 ahead, 10 behind** [Verified: `git rev-list --left-right --count main...feat/pr-b2-core-production-features`]

The 10 commits `main` is ahead are infra-sprint PRs #26–#33 (Kimi K2.5 default, agent-side cache, Bedrock guardrails, etc.) merged after this branch was last touched.

---

## 2. What's on it vs main?

18 files changed, 4,694 insertions, 934 deletions. [Verified: `git diff --stat main...feat/pr-b2-core-production-features`]

Key files added/modified:
- `src/conversational_search/agent/turn1_selector.py` — +200 lines (new)
- `src/conversational_search/agent/graph.py` — heavily modified
- `src/conversational_search/agent/state.py` — +14 lines
- `src/conversational_search/agent/custom_events.py` — +71 (new)
- `tests/unit/test_turn1_selector.py` — +257 (new)
- `uv.lock` — +2,586 (lock update)

**Commit narrative** (oldest → newest): Bedrock TCP knobs → Cerebras provider → verifier gate → Tier 1 UX price normalization → language-routing fixes → TRACKER_FILTER_OVERRIDES → TRACKER_FACET_CONFIG → facet-discovery agent precedence → turn-1 smart-path (retire first_turn_init, add response-shape selector + lbx.work_status) → preview redesign (single-LLM-call) → prompt tightening. [Verified: `git log --oneline main..feat/pr-b2-core-production-features`]

This is infra hardening + early turn-1 smart path. It is not a v2 mode dispatcher branch.

---

## 3. Does it implement v2 detection/response?

### turn1_selector.py — response-shape selector

Present, tested (257-line test file), production-quality. [Verified: `git show feat/pr-b2-core-production-features:src/conversational_search/agent/turn1_selector.py`]

`select_response_shape(payload)` returns one of `"products_only"`, `"products_plus_chips"`, `"chips_only"`, `"zero_hit_recovery"`. However:
- The function carries the comment **"RESERVED — not currently invoked; planned for turn-2+ injection (see Q4)"**
- Only `select_chips` is imported and called in `graph.py` (lines 182, 211). `select_response_shape` is wired but unused.
- The vocabulary (`products_only`, `products_plus_chips`, `chips_only`, `zero_hit_recovery`) does NOT match the handoff brief's 4 composition shapes (`refinement_chips`, `refinement_chips_with_hatch`, `question_led`, `hard_fork`). This is an earlier-generation shape set.

### tier classifier

Absent. No `tier_signal_computer.py`. No tier-signal logic (`top_share_max`, `axis_entropy`, `filled_axes`, `has_brand_token`) in graph.py or turn1_selector.py. [Verified: grep on branch]

### mode dispatcher (7-mode)

Absent. No routing for `gift_advisor`, `comparison`, `advice`, `support`, `out_of_scope`, `unsafe`. [Verified: graph.py grep on feat/pr-b2-core-production-features]

### composition renderer (4 shapes)

Absent. `refinement_chips`, `refinement_chips_with_hatch`, `question_led`, `hard_fork` vocabulary not present.

### state envelope v2 fields

`state.py` adds only `work_status_phase_index`. Fields `browse_intent`, `chat_takeover_trigger`, `mode_stack`, `fork_card_filter_value`, `tier`, `composition`, `mode` are all absent. [Verified: state.py grep returned empty on this branch]

### proxy ConverseRequest v2 wire fields

No proxy directory exists on this branch. [Verified: `git show feat/pr-b2-core-production-features:src/conversational_search/conversational_proxy/` → fatal error]

---

## 4. Maturity and mergeability

- Test coverage on added code is solid: 257 lines for turn1_selector, 783-line rework of graph_emit tests, 410-line tools tests. [Verified: diff --stat]
- No WIP/TODO/STUB markers found in sampled files.
- Last commit: 2026-05-15 — 24 days stale.
- 10 commits behind main due to infra-sprint merges. Rebase conflict risk is moderate (graph.py was heavily touched in both lines).
- No draft PR metadata visible.

---

## 5. Is it the intended base for the v2 phase?

The handoff brief (§9) phases the work as: Phase 1 (facet stack) → Phase 2 (browse hatch) → Phase 3 (tier classifier) → Phase 4 (question-led) → Phase 5 (mode dispatcher). [Verified: `docs/_handoff-pack/03 · Handoff brief.md § 9`]

`feat/pr-b2-core-production-features` covers Phase 1–2 era infra + early turn-1 smart path. The name "PR B2 / core production features" aligns with the `ce31381` commit on main: `"feat(agent): core production features — Bedrock knobs, Cerebras, finalize gate, facet probe generalization"` — this branch is its unmerged ancestor. The "B2" refers to a production-hardening PR block, not to the v2 detection architecture.

---

## 6. Other relevant branches

### `feat/v2-campaign` — the actual v2 detection/tier/composition branch

- **33 commits ahead of main, 10 behind.** Last commit: 2026-05-26. [Verified: `git rev-list --left-right --count main...feat/v2-campaign`]
- 46 files changed, 9,743 insertions, 884 deletions. [Verified: git diff --stat]
- `state.py` contains: `tier: str`, `composition: str`, `mode_stack: list[str]`, `mode_stack_depth: int`, `mode_at_compile: str | None` — all the v2 envelope fields. [Verified: grep on feat/v2-campaign:state.py]
- `graph.py` contains `_parse_dispatch_prefix()` decoding `MODE:`, `TIER:`, `COMPOSITION:` structured-output prefixes; writes them into state. [Verified: grep lines 128–416 on feat/v2-campaign:graph.py]
- Commit narrative includes: `mode-stack LIFO (D.4)`, `folded dispatcher (D.2-(i) prefix decode) + Mode-B short-circuit`, `tier-gated LLM budget`, `prompt_fingerprint + mode_at_compile`, `dispatcher gate FM-3 test`, `turn1_signature_cache Postgres schema`. [Verified: git log --oneline]

### `staging/v2-sprint-2026-06-05`

Local-only staging branch. Absorbs main's recent infra PRs (#30–#33) into the v2-campaign work — likely the intended merge-prep branch.

### `feat/v2-cache-firing-mode`

On origin. Cache/firing-mode sub-feature; likely a sub-branch of the v2 campaign.

---

## Decision

**Recommendation: BASE = feat/v2-campaign, CONTINUE-EXISTING** (with one operator confirmation needed)

The decisive evidence:

1. `feat/pr-b2-core-production-features` is a production-hardening branch (infra, Cerebras, turn-1 smart path) that does not implement the v2 detection/tier/composition architecture. The architect's assumption that it held v2 work was a misidentification.

2. `feat/v2-campaign` is the actual holder: mode/tier/composition in state, dispatch-prefix decoder in graph.py, 33 commits, 9,743 lines, tested, last touched 2026-05-26.

3. Design-from-scratch on master would throw away ~33 commits of scaffolding that directly implements the handoff brief's Phase 3–5 architecture. Continuing on `feat/v2-campaign` is unambiguously correct.

**One confirmation needed:** Is `staging/v2-sprint-2026-06-05` the rebased form of `feat/v2-campaign` that should be used as the working base (already absorbing infra-sprint PRs), or is `feat/v2-campaign` itself the starting point? Either way the verdict is CONTINUE-EXISTING on the v2-campaign lineage. The operator should confirm which of the two is the active working branch before the architect begins.

Pre-emission self-audit: 14 citations verified, 6 sections present, 2 contradictions checked (state.py field absence on pr-b2 confirmed; mode dispatcher absence on pr-b2 confirmed).
