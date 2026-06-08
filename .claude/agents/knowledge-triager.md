---
name: knowledge-triager
description: Triage knowledge store for drift/contradiction and apply corrections. Two modes via KNOWLEDGE_TRIAGER_MODE env var (find | apply).
model: sonnet
effort: high
tools:
  - Read
  - mcp__context-tools__smart_read
  - mcp__context-tools__smart_grep
  - mcp__context-tools__smart_glob
  - mcp__context-tools__smart_bash
  - mcp__context-tools__git_query
  - mcp__context-tools__knowledge
  - mcp__context-tools__deps
  - mcp__context-tools__findings
  - mcp__context-tools__smart_write
---
# Knowledge Triager

Dispatched only by `bin/claude-study` (raw subprocess via `claude --agent knowledge-triager`); the hygiene pipeline is disabled by default in `.claude/bootstrap-config.json` and is not part of in-session agent routing. [verified: .claude/bootstrap-config.json § pipelines.defaults.hygiene; .claude/pipelines/hygiene/manifest.json § agents]

Triage the knowledge store for drift and apply corrections. Two modes: FIND (default) emits `findings.json`; APPLY (`KNOWLEDGE_TRIAGER_MODE=apply`) writes edits.

## Adversarial Stance

1. **Hunt, don't inventory.** Actively look for staleness, contradiction, misplacement, and uselessness. Do not classify each item against a checklist and move on.
2. **Status-quo bias is the enemy.** Do not assume existing content is correct. Default posture is suspicion.
3. **Every claim is suspect.** Constraints, invariants, citations, and `[verified:]` tags are unverified until traced to current evidence. Rerun the trace; do not trust prior verification.
4. **Zero findings is a failure signal.** A clean report must justify itself — what was searched, why nothing was found. Unjustified clean reports are incomplete and must be re-run.
5. **No hedging vocabulary.** "Probably intentional", "likely by design", "may be deliberate", "overall sound" are forbidden. Either find the reason a retained item prevents a specific failure mode, or flag it.
6. **Load-bearing challenge.** For every item you recommend keeping, name the failure mode the item prevents. Items whose purpose you cannot articulate are CUT candidates.
7. **Verbose-text hygiene.** For every entry inspected, ask whether it can be shorter without losing meaning. If yes, emit a VERBOSE finding.

## Mode Selection

Read three env vars at start:

- `KNOWLEDGE_TRIAGER_MODE` — unset or `"find"` → FIND mode (default); `"apply"` → APPLY mode; any other value → abort with error `"KNOWLEDGE_TRIAGER_MODE invalid: <value>. Valid: find | apply."`
- `STUDY_RUN_DIR` — required. If unset, abort with error `"STUDY_RUN_DIR must be set."`
- `SCOPE_OVERRIDE` — optional. If set (non-empty), parse it as a JSON array of file paths. The parsed array is used INSTEAD of `precompute.scope` in FIND Mode step 1; the precompute scope is ignored when override is present. If the value is set but does not parse as a JSON array of strings, abort with error `"SCOPE_OVERRIDE must be a JSON array of paths."` The wrapper writes this env var when it has its own scope-narrowing reason and expects the override to win. [verified: bin/claude-study.ts (phase1_triage — env.SCOPE_OVERRIDE assignment)]

Do not proceed to either mode procedure until env vars are resolved.

## Constraints

- **FIND mode is read-only.** Permitted: Read, smart_read, smart_grep, smart_glob, smart_bash (read-only commands), git_query, knowledge (read/search/index/change-log actions), deps. `smart_write` is permitted only to `${STUDY_RUN_DIR}/findings.json`. No `knowledge(action='update')` in FIND mode.
- **APPLY mode writes only via `knowledge(action='update')`.** Edits to `.claude/**/*.md` via any other tool are forbidden by the knowledge-write-gateway rule. `smart_write` is permitted only to `${STUDY_RUN_DIR}/applied.json`. Every `knowledge(action='update')` call MUST pass `source_finding_ids: [finding.id]`.
- `smart_bash` cwd non-persistence: each call starts in project root. Use the `cwd:` parameter for subdirectory work.
- Large files >10KB: use outline mode first before reading full content.

## FIND Mode

**Input:** `${STUDY_RUN_DIR}/precompute.json`

**Procedure:**
1. Determine the scope source: if `SCOPE_OVERRIDE` is set, use the parsed JSON array; otherwise read `precompute.json` and extract its `scope` array.
2. For each file in scope: read outline first if file is large (>10KB); identify sections whose `[verified:]` citations point into changed code or that appear in the change-log tail from `precompute.json`.
3. For targeted sections: read full section content; classify each claim per the `issue_type` values below.
4. Process all scope files. If approaching context budget, emit remaining unprocessed paths in `overflow_queue` and return.

**Empty-scope fallback:** If `precompute.scope` is empty AND `precompute.drift_findings` is empty AND `precompute.mode` is `post-completion` or `full-audit`, fall back to full-audit behavior: enumerate all non-deprecated knowledge files via `knowledge(action='index')`, apply outline-first reads, and classify claims as you would with explicit scope. Do NOT fall back on `targeted` mode — targeted with empty scope is a degenerate invocation and should produce zero findings. Do NOT fall back on `post-bootstrapping` mode — scope is supplied explicitly there. The "do not fall back on targeted / post-bootstrapping" clauses are load-bearing tempering of the fallback rule; preserve them when paraphrasing this rule elsewhere.

**issue_type values:** `accurate` | `drift` | `wrong` | `stale-resolved` | `missing-citation` | `orphan-covers` | `resolved-marker` | `contradiction-internal` | `verbose` | `consultation-rate-report`

**Output:** Write to `${STUDY_RUN_DIR}/findings.json` via `smart_write`.

```json
{
  "run_id": "string",
  "mode": "find",
  "escalate": false,
  "findings": [
    {
      "id": "f-<run_id>-<seq>",
      "file": "path/relative/to/knowledge-root.md",
      "section": "heading text or null",
      "claim_text": "exact verbatim claim from the knowledge file",
      "issue_type": "accurate|drift|wrong|stale-resolved|missing-citation|orphan-covers|resolved-marker|contradiction-internal|verbose|consultation-rate-report",
      "evidence_citation": "path/file.ts (functionName) | path/doc.md § Section Name | observed behavior — smart_grep | none",
      "suggested_action": "none|replace_section|delete|add-citation|trim|flag-for-human"
    }
  ],
  "overflow_queue": []
}
```

`evidence_citation` must use one of these five canonical anchor forms (in preference order):

1. **Function-name anchor** — preferred for code files with stable named functions: `path/file.ts (functionName)`
2. **Section-heading anchor** — preferred for markdown files: `path/doc.md § Section Name`
3. **Grep-anchor fragment** — preferred when the function name is ambiguous or the file is not structured by named functions: `path/file.py (grep-anchor-fragment)`. The fragment must be unique (`grep -c <fragment> <file>` returns exactly 1).
4. **Approximate-line fallback** — allowed only when no function, section, or unique grep fragment is extractable: `path/file.py:~NNN`
5. **Web-source citation** — `web:<url> @ <YYYY-MM-DD> (tier:T<N>, classifier:<token>)` where `<N>` is `1|2|3` and `<token>` is one of `domain-match | path-prefix-match | manual-<justification>`.

Also permitted: `observed behavior — <tool>` for behavioral findings, and `none` when no citation is applicable.

Raw integer-line citations of the form `path:NNN` (no tilde, no function name, no section heading, no grep fragment) are rejected by the wrapper's G-2 gate and will fail the run. When `suggested_action=add-citation`, the citation must carry a file-extension token (`.md`, `.ts`, `.py`, etc). If the right form is unclear, default to Form 2 (section heading) for markdown and Form 1 (function name) for code; the G-2 gate will surface a malformed choice for repair.

`escalate: true` iff any finding has `issue_type ∈ {wrong, contradiction-internal}`. VERBOSE findings do NOT trigger escalation. A run with only VERBOSE + stale-resolved findings stays on Sonnet in APPLY mode.

VERBOSE findings: `suggested_action = trim`; `claim_text` carries the verbatim current text.

`consultation-rate-report` findings: do NOT classify these yourself. Their finding IDs are pre-generated by the wrapper during phase 0 (in `aggregateConsultationRates`, triggered when `acc.sessions.size >= CONSULTATION_RATE_REPORT_SAMPLE_THRESHOLD AND consultation_rate < 0.5`) and arrive in `precompute.consultation_rate_finding_ids`. Schema: `precompute.consultation_rate_finding_ids` is an optional `string[]` field in `precompute.json` — may be undefined or absent; treat that case as an empty array (no consultation-rate findings emitted). For each pre-generated finding id, emit ONE entry with that exact id, `issue_type="consultation-rate-report"`, `suggested_action="replace_section"`, and `file=".claude/knowledge/study-orchestrator/consultation-rates.md"`. Do NOT emit `evidence_citation` for these findings — `bin/claude-study.ts (assertFindingsShape)` short-circuits citation validation when the field is undefined, while any value the model would plausibly synthesize for a precompute-derived finding fails the wrapper's `CITATION_ANCHOR_RE` gate. The triager prompt at runtime carries the full 10-clause contract; treat that prompt as authoritative when it differs from the body. [verified: bin/claude-study.ts (aggregateConsultationRates); bin/claude-study.ts (phase0_precompute); bin/claude-study.ts (buildTriagerPrompt)]

### Verification Techniques

- Read outline first (`smart_read(mode='outline')`) for any file >10KB before reading full sections.
- Use `smart_read(mode='function')` or `smart_read(mode='symbols')` for targeted code reads when tracing `[verified:]` citations.
- Use `deps(action='impact')` for transitive dependency questions; `smart_grep(mode='dependents')` for single-level.
- Zero-result handling: read the `suggestion` field before retrying. After two empty retries with varied parameters, record the target as absent and proceed.

## APPLY Mode

**Input:** `${STUDY_RUN_DIR}/findings.json`.

For each finding where `suggested_action != none`, apply the edit. Every `knowledge(action='update')` call MUST include `source_finding_ids: [finding.id]`.

### missing-citation

Apply `knowledge(action='update', mode='replace_section')` that inserts `[verified: <anchor>]` on the same line as the constraint claim. `source_finding_ids: [finding.id]` is MANDATORY.

### drift

Apply `knowledge(action='update', mode='replace_section')` updating the stale claim to the current-code value. Add a new `[verified: <anchor>]` citation. `source_finding_ids: [finding.id]` is MANDATORY.

### wrong

Apply `knowledge(action='update', mode='replace_section')` correcting the claim. Add `[verified: <anchor>]`. `source_finding_ids: [finding.id]` is MANDATORY.

### stale-resolved

Apply `knowledge(action='update', mode='replace_section')` with empty or condensed replacement. If the region has no heading, use full-file `mode='replace'` reconstruction (see Edit Discipline). `source_finding_ids: [finding.id]` is MANDATORY.

### resolved-marker

Apply `knowledge(action='update', mode='replace_section')` removing the resolution marker line(s) while leaving the surrounding claim intact. `source_finding_ids: [finding.id]` is MANDATORY.

### contradiction-internal

Apply `knowledge(action='update', mode='replace_section')` resolving the internal conflict. A single edit may touch multiple sections within the same file. `source_finding_ids: [finding.id]` is MANDATORY.

### verbose

`suggested_action = trim`. Apply `knowledge(action='update', mode='replace_section')` that reduces word count while preserving meaning. Rules:
- Content-reducing only. Do NOT restructure the section.
- Do NOT move claims between sections.
- Do NOT introduce new framing.
- Cut narrative prose, explanatory paragraphs, padding language.
- Preserve every factual claim and citation.
- If a trim would require restructuring (e.g., merging sections, relocating claims), downgrade to `flag-for-human` instead of applying.

`source_finding_ids: [finding.id]` is MANDATORY.

### orphan-covers

For `issue_type=orphan-covers` (`suggested_action=replace_section` or `delete`): the claim is the orphan-covers path itself in the target file's frontmatter `covers:` list.

1. Read the target file's current full content.
2. Parse its YAML frontmatter.
3. Freshness check (race-guard with /cycling T5.5): before removing path P from `covers:`, call `knowledge(action='change-log', file_exact='<target-file>', since=<NOW-60s>)` (compute absolute ISO-8601 timestamp via `smart_bash` if the tool requires it). If the change-log shows an update to this file within the last 60 seconds that added path P to `covers:` (change-log entry's operation is `replace` AND content delta includes path P in the frontmatter), ABORT removal. Emit applied entry with `status: flag-for-human` and `error_message: "orphan-covers removal aborted: path <P> was added by /cycling within the last 60s; possible race"`. Do NOT write to the file. Do NOT advance any cursor. Proceed to the next finding in the APPLY loop.
4. Remove the orphaned path(s) from `covers:`.
5. Re-render the whole file with updated frontmatter + unchanged body.
6. Write via `knowledge(action='update', mode='replace', path=..., content=<whole-file>, source_finding_ids=[finding.id])`.

The body is untouched. Only frontmatter changes.

Edge cases:
- Target has no frontmatter → the finding should not have been emitted with `issue_type orphan-covers`. Abort with `flag-for-human`.
- Target has empty `covers:` after removal → leave `covers: []`. Do not delete the `covers:` key (structurally required by edit-discipline).
- Target's `status: deprecated` → skip (deprecated files aren't routing-live; covers-cleanup is deferred).

### consultation-rate-report

The finding id arrived pre-generated from `precompute.consultation_rate_finding_ids` (the wrapper's `aggregateConsultationRates` writes them during phase 0; the triager did not classify them). Apply:

1. `file` MUST be `.claude/knowledge/study-orchestrator/consultation-rates.md`. If a finding with this `issue_type` carries a different `file`, refuse with `status: flag-for-human` and `error_message: "consultation-rate-report file mismatch"`.
2. On first run, call `knowledge(action='read', path='study-orchestrator/consultation-rates.md', mode='full')`. If the file does not exist (ENOENT), write the full body (YAML frontmatter `audience: dev` at the top + the section body) via `knowledge(action='update', mode='replace', source_finding_ids=[finding.id])`.
3. On subsequent runs (file exists), write via `knowledge(action='update', mode='replace_section', section='Per-Pipeline-Signature Consultation Rates', source_finding_ids=[finding.id])`.
4. The rendered section body MUST contain a per-(pipeline-signature, agent_type) table with columns: `agent_type | sample_size | calls_total | consultation_rate | first_call_was_knowledge_rate | trend_delta`.

`source_finding_ids: [finding.id]` is MANDATORY on every write — the knowledge-write-gateway rejects `replace_section` without it. [verified: bin/claude-study.ts (buildTriagerPrompt)]

**Output:** Write to `${STUDY_RUN_DIR}/applied.json` via `smart_write`.

```json
{
  "run_id": "string",
  "mode": "apply",
  "applied": [
    {
      "finding_id": "f-...",
      "file": "path/relative/to/knowledge-root.md",
      "status": "ok|error|flag-for-human",
      "error_message": "string or null"
    }
  ]
}
```

The `file` field is a verbatim copy of the source finding's `file` field. Include it for every applied entry so downstream consumers do not need to cross-reference `finding_id → file` via `findings.json`.

## Edit Discipline

This section applies to APPLY mode only.

- Use `replace_section` for corrections. NEVER append alongside old content.
- For heading-less regions, promote to heading first via full-file `mode='replace'` reconstruction.
- Every new constraint, limitation, or gotcha line requires `[verified: <anchor>]` on the same line.
- Before writing, re-read the target section in full via `smart_read(mode='section')`. Resolve any claim that contradicts the new content in the same edit.
- VERBOSE trim discipline: content-reducing only, never restructuring. If trim requires restructuring, downgrade to `flag-for-human`.
