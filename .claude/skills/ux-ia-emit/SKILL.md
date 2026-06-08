---
name: ux-ia-emit
description: "Bootstrap or extend `.claude/knowledge/ux/ia.md` — the project's information-architecture artifact (surfaces list, navigation edges, user flows). Invoked by the orchestrator as a plan-prefix step before executing UX subtasks carrying `Blocked-on-IA: first-run`, and again when a UX-yes subtask introduces a surface not yet in `surfaces:`. Emits block-style YAML `surfaces:` frontmatter; flow-style arrays defeat plan-validator Check 6 stale-marker detection."
audience: subagent
caller-allowlist: [main, ux-designer]
allowed-tools:
  - mcp__context-tools__smart_read
  - mcp__context-tools__smart_write
  - mcp__context-tools__knowledge
  - mcp__context-tools__findings
---

## Purpose

You are invoked to write or extend `.claude/knowledge/ux/ia.md` — the project's information-architecture artifact. The file lists every named user-facing surface (screens, views, panels, commands, prompts), the navigation edges between them, and brief user-flow notes. Downstream consumers:

- `plan-validator-hook.py` Check 6 reads `surfaces:` from the frontmatter to validate `Blocked-on-IA:` markers on UX-yes subtasks.
- `ux-designer` cites surfaces from `surfaces:` in its sketch's `## Neighbor Surfaces` section.
- The orchestrator routes `Blocked-on-IA: <surface-name>` subtasks against this list.

Two invocation modes:

- **First-run bootstrap.** `.claude/knowledge/ux/ia.md` does not exist; the orchestrator spawns you as a plan-prefix step before executing any subtask carrying `Blocked-on-IA: first-run`. You synthesize the initial IA from the task brief and any prior planning artifacts.
- **Surface extension.** `ia.md` exists but a UX subtask introduces a new surface named in its `Blocked-on-IA: <surface-name>` field. You append the new surface entry — never rewrite existing entries unless the operator's task explicitly says so.

## Input contract

Fields read from the delegation prompt:

| Field | Required | Notes |
|-------|----------|-------|
| `mode` | yes | `bootstrap` or `extend`. |
| `new_surface_name` | conditional | Required when `mode=extend`. The surface slug to add. |
| `task_brief` | yes on bootstrap | The task or feature description that motivates IA emission. |

If `mode=extend` but `ia.md` does not exist, treat as bootstrap and emit a `findings` entry noting the mode mismatch.

## Procedure

**Bootstrap mode:**

1. Read the task brief and any referenced plan or design spec. Enumerate every distinct user-facing surface the project will need at the granularity of "a thing the user sees and acts on" — not every component, not every dialog state. Typical first-pass count: 3–12 surfaces.
2. For each surface, decide a stable slug (kebab-case, no spaces). Slugs are identity for the entire pipeline — `Blocked-on-IA` markers, neighbor-surface references, and validator cross-checks all key on these strings. Pick conservatively.
3. For each surface, draft a one-sentence role description naming what the user does there.
4. Sketch navigation edges between surfaces (which surface leads to which). Include only edges that are decided, not every possible reachability.
5. Write `.claude/knowledge/ux/ia.md` per § Output format.

**Extend mode:**

1. Read existing `.claude/knowledge/ux/ia.md`. Confirm `new_surface_name` is NOT already in `surfaces:`. If it is, no write — return the existing entry as already-present.
2. Append a new entry to the `surfaces:` block, plus a body section describing the surface and its navigation edges. Preserve every existing entry verbatim.
3. Re-emit the frontmatter using block-style YAML (see § Output format) — preserving block style is load-bearing.

## Output format — block-style surfaces frontmatter (load-bearing)

Write `.claude/knowledge/ux/ia.md`:

```markdown
---
surfaces:
  - home
  - settings
  - onboarding-step-1
  - onboarding-step-2
---

# Information Architecture

## home
One-sentence role. Navigation: → onboarding-step-1 (first-run only); → settings (any time).

## settings
One-sentence role. Navigation: ← home.

## onboarding-step-1
One-sentence role. Navigation: → onboarding-step-2.

## onboarding-step-2
One-sentence role. Navigation: → home (on completion).
```

**Block-style is mandatory** — emit `surfaces:` with one `- <slug>` per line, NOT inline `surfaces: [a, b, c]`. The plan-validator's `_parse_ia_surfaces()` reads flow-style arrays as empty `[]`, which silently defeats Check 6's stale-marker detection. Block-style is the contract for correct downstream behavior.

## Verification predicate

- `.claude/knowledge/ux/ia.md` exists after the run.
- The frontmatter `surfaces:` block parses as a non-empty list of strings (NOT an empty `[]` and NOT inline flow-style).
- Every body section heading (`## <slug>`) appears in `surfaces:` and vice versa.
- On extend mode, every surface present pre-run is still present post-run.

## Findings triggers

- `mode=extend` requested but `ia.md` absent → emit `findings(topic='ux-ia-emit-mode-mismatch', tags=['gotcha'])` and proceed as bootstrap.
- Synthesized a surface slug you are uncertain will hold up under planner naming conventions → emit `findings(topic='ux-ia-surface-slug-uncertain', content=<slug + rationale>, tags=['decision'])` so the next planner pass can rename early rather than late.
- Existing `surfaces:` block found in flow-style (`[a, b]`) when reading — emit `findings(topic='ux-ia-surfaces-flow-style', tags=['gotcha'])` and rewrite to block-style as part of your extend pass.
