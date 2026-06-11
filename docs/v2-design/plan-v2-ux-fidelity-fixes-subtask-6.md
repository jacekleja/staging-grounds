# Subtask 6: LIVE verify FIX-2 + FIX-3 — affordances on the right surfaces

**Description**: Confirm against the live stack that the chat affordance now appears on a decisive/narrow product-search response, that `type_it_out` now appears on a question_led/broad-browse response, and that the chat affordance does NOT appear on an unsafe-refuse response. Apply the freshness methodology + host workarounds from `signature-cache-validation-freshness-report.md`: force a fresh MISS per read, python-json-DECODE the SSE. Stack: proxy `http://127.0.0.1:8000`, langgraph `http://127.0.0.1:2024`, tracker `8760-9189`.

**Reads to perform (force MISS each):**
- **Decisive/narrow query** (a query that classifies to `decisive` → `refinement_chips`; pick one that yields a low result count per the tier thresholds): assert the preview block now contains `chat_affordance` with the localized `chat_affordance_label` and `style: dashed_pill`.
- **Broad-browse query** (classifies to `exploratory` → `question_led`): assert the preview block now contains `type_it_out` with `enabled: true`, the localized `type_it_out` label, and `style: free_text`.
- **Unsafe query** (hits the unsafe deflect): assert the response is the hard-refuse single sentence and contains NO `chat_affordance` key (FIX-2 must not leak to the refuse path).

**Agent**: validator

**Knowledge**:
- `.claude/knowledge/decisions/conversational-search-v2-discovery-digest.md` (§ Axis A.2.3 — chat_affordance server-emitted)

**Dependencies**: 3

**Context files**:
- `/home/fanderman/projects/luigis-box/docs/v2-design/signature-cache-validation-freshness-report.md` — freshness / MISS-decode methodology + host workarounds.
- `/home/fanderman/projects/luigis-box/docs/v2-design/v2-final-state-gap-closure-conformance-report.md` — prior live-verification report shape to mirror.

**Expected output**: A verification report with the three reads' decoded-SSE evidence, written to `/home/fanderman/projects/luigis-box/.agent_context/worktrees/session-1781106672-6469-2465b2ba685c/docs/v2-design/_runs/fix2-fix3-affordances-live-verify.md`. `## Verification` split: Exercised (the three MISS reads, with tier/mode routing confirmed) / Not-exercised (anything not reachable on this tracker, with bounded reason). Return message states PASS/FAIL per read.

**active_rubrics**: ["code-vs-spec", "constraint-compliance"]

**Design phase**: no with reason verification-exercise-only.

**UX phase**: no — live payload verification, not surface authoring.
