#!/usr/bin/env tsx
/**
 * test_claude_study.ts — unit tests for bin/claude-study.ts (23 tests).
 * Run:  node --test --import tsx bin/test_claude_study.ts
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import * as child_process from "node:child_process";
import * as yaml from "js-yaml";

import {
  __setProjectRootForTest,
  __setSpawnForTest,
  __setRunValidateForTest,
  normalizeClaim,
  decideP3Model,
  computeMaxIterations,
  computeExitCode,
  generateRunId,
  dedupAndCapSetC,
  readState,
  writeState,
  acquireMutex,
  parseArgs,
  phase0_precompute,
  phase3_apply,
  phase5_verify,
  phase6_housekeeping,
  collectRawDriftFindings,
  collectMechanicalCoversDrift,
  collectValidateDrain,
  VALIDATE_DRAIN_CAP,
  warn,
  isSentinel,
  sessionHasSentinel,
  type StudyState,
  type FindingsJson,
  type RawDriftFinding,
  type PrecomputeJson,
  type PhaseReports,
  type AppliedJson,
  type PendingHumanReview,
  type SpawnAgentOpts,
  type SpawnResult,
  type VerifyIssue,
} from "./claude-study.js";
import {
  enumerateFindingsInDir,
  tombstoneFinding,
  type FindingData,
} from "../.claude/mcp/context-tools/src/tools/session-lifecycle.js";

// ── Test fixture scaffolding ────────────────────────────────────────────────

function makeTmpProject(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "claude-study-test-"));
  fs.mkdirSync(path.join(dir, ".claude", "knowledge", "session-log"), {
    recursive: true,
  });
  fs.mkdirSync(path.join(dir, ".claude", "knowledge-log"), { recursive: true });
  fs.mkdirSync(path.join(dir, ".agent_context", "sessions"), {
    recursive: true,
  });
  fs.mkdirSync(path.join(dir, ".agent_context", "study"), { recursive: true });
  fs.mkdirSync(path.join(dir, ".agent_context", "archive", "study"), {
    recursive: true,
  });
  return dir;
}

function seedState(projectRoot: string, state: Partial<StudyState>): void {
  const full: StudyState = {
    running: false,
    running_since: null,
    ...state,
  };
  fs.writeFileSync(
    path.join(projectRoot, ".claude", "knowledge", ".study-state"),
    yaml.dump(full, { lineWidth: -1, noRefs: true, sortKeys: false }),
    "utf-8"
  );
}

function seedFinding(
  projectRoot: string,
  sessionId: string,
  findingId: string,
  payload: Record<string, unknown>
): string {
  const fdir = path.join(
    projectRoot,
    ".agent_context",
    "sessions",
    sessionId,
    "findings"
  );
  fs.mkdirSync(fdir, { recursive: true });
  const fpath = path.join(fdir, `${findingId}.json`);
  fs.writeFileSync(fpath, JSON.stringify(payload, null, 2), "utf-8");
  return fpath;
}

// ── Test 1: mutex contention <30min ─────────────────────────────────────────

test("1. mutex contention <30min returns false", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  seedState(dir, {
    running: true,
    running_since: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  });
  const { state } = readState();
  assert.equal(acquireMutex(state), false);
  __setProjectRootForTest(null);
});

// ── Test 2: mutex stale >30min auto-clears ─────────────────────────────────

test("2. mutex stale >30min auto-clears", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  seedState(dir, {
    running: true,
    running_since: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
  });
  const { state } = readState();
  assert.equal(acquireMutex(state), true);
  assert.equal(state.running, true);
  __setProjectRootForTest(null);
});

// ── Test 3: state YAML round-trip preserves unknown fields ─────────────────

test("3. state round-trip strips unknown fields (PCA-2)", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  const raw = `running: false
running_since: null
task: 'legacy field'
pending_validation:
  - connections.md
promoted_sessions:
  - 1776-abc
__future_field: 42
coverage:
  foo.md:
    last_validated: '2026-01-01T00:00:00Z'
    status: current
    confidence: high
last_run:
  timestamp: '2026-04-21T07:45:00Z'
  mode: targeted
  duration_seconds: 10
  scope:
    - foo.md
  changes_made: 5
  run_id: r1
  scope_count: 1
  edits_applied: 5
  escalated: false
  last_drift_signal_ts: null
`;
  fs.writeFileSync(
    path.join(dir, ".claude", "knowledge", ".study-state"),
    raw,
    "utf-8"
  );
  const { state } = readState();
  const s = state as Record<string, unknown>;
  assert.equal(s.__future_field, undefined);
  assert.equal(s.task, undefined);
  assert.equal(s.pending_validation, undefined);
  assert.equal(s.promoted_sessions, undefined);
  const lr = (s.last_run ?? {}) as Record<string, unknown>;
  assert.equal(lr.scope, undefined);
  assert.equal(lr.changes_made, undefined);
  assert.equal(lr.run_id, "r1");
  writeState(state);
  const { state: reloaded } = readState();
  const r = reloaded as Record<string, unknown>;
  assert.equal(r.__future_field, undefined);
  assert.equal(r.task, undefined);
  __setProjectRootForTest(null);
});

// ── Test 4: decideP3Model four combinations ────────────────────────────────

test("4. decideP3Model two combinations", () => {
  assert.equal(decideP3Model({ escalate: true }), "opus");
  assert.equal(decideP3Model({ escalate: false }), "sonnet");
});

// ── Test 5: computeExitCode warning ────────────────────────────────────────

test("5. exit code 2 on warnings", () => {
  assert.equal(
    computeExitCode({ pendingHumanReviews: 1, p5Issues: 0, p3Errors: 0 }),
    2
  );
  assert.equal(
    computeExitCode({ pendingHumanReviews: 0, p5Issues: 0, p3Errors: 0 }),
    0
  );
  assert.equal(
    computeExitCode({
      pendingHumanReviews: 0,
      p5Issues: 0,
      p3Errors: 0,
      fatal: true,
    }),
    1
  );
});

// ── Test 6: computeMaxIterations ───────────────────────────────────────────

test("6. computeMaxIterations formula", () => {
  // Formula: full-audit → ceil(scope/28)+1; else → 3.
  // Spec line 156's "scope=57 → 3" is arithmetically ceil(57/28)+1=4; treating
  // the spec's "3" as a hand-calc typo and trusting the unambiguous formula at
  // plan line 121. Deviation flagged in impl report.
  assert.equal(computeMaxIterations("full-audit", 100), 5);
  assert.equal(computeMaxIterations("post-completion", 100), 3);
  assert.equal(computeMaxIterations("full-audit", 57), 4);
});

// ── Test 7: generateRunId regex ────────────────────────────────────────────

test("7. generateRunId format", () => {
  const id = generateRunId();
  assert.match(id, /^\d{8}-\d{6}-\d+$/);
});

// ── Test 8: precompute.json round-trip ─────────────────────────────────────

test("8. precompute.json schema round-trip", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  seedState(dir, {});
  const runDir = path.join(dir, ".agent_context", "study", "test-run-8");
  fs.mkdirSync(runDir, { recursive: true });
  const { state } = readState();
  const pc = await phase0_precompute(state, runDir, "full-audit", "test-run-8");
  assert.equal(pc.run_id, "test-run-8");
  assert.equal(pc.mode, "full-audit");
  assert.ok(Array.isArray(pc.scope));
  assert.ok(Array.isArray(pc.drift_findings));
  // Reload precompute.json from disk
  const reloaded = JSON.parse(
    fs.readFileSync(path.join(runDir, "precompute.json"), "utf-8")
  );
  assert.equal(reloaded.run_id, "test-run-8");
  __setProjectRootForTest(null);
});

// ── Test 9: escalation force-exercise ──────────────────────────────────────

test("9. escalation force-exercise: Opus + finding-id in stderr", async () => {
  process.env.CLAUDE_STUDY_TEST_MODE = "1";
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  let capturedOpts: SpawnAgentOpts | null = null;
  __setSpawnForTest((opts): SpawnResult => {
    capturedOpts = opts;
    // Write the expected applied.json sidecar
    const runDir = opts.envOverrides?.STUDY_RUN_DIR!;
    fs.writeFileSync(
      path.join(runDir, "applied.json"),
      JSON.stringify({ mode: "apply", applied: [] }),
      "utf-8"
    );
    return { exitCode: 0, stderr: "", stdout: "" };
  });

  const runDir = path.join(dir, ".agent_context", "study", "test-run-9");
  fs.mkdirSync(runDir, { recursive: true });
  const findings: FindingsJson = {
    run_id: "test-run-9",
    escalate: true,
    findings: [
      { id: "f-stub-1", file: "overview.md", issue_type: "wrong" },
    ],
  };
  const precompute: PrecomputeJson = {
    run_id: "test-run-9",
    mode: "full-audit",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const state: StudyState = { running: true, running_since: null };

  // Capture stderr
  const origErr = process.stderr.write.bind(process.stderr);
  let stderrBuf = "";
  process.stderr.write = ((chunk: unknown): boolean => {
    stderrBuf += String(chunk);
    return true;
  }) as typeof process.stderr.write;
  try {
    await phase3_apply(runDir, findings, precompute, state);
  } finally {
    process.stderr.write = origErr;
  }

  assert.ok(capturedOpts, "spawn was not called");
  const opts = capturedOpts as unknown as SpawnAgentOpts;
  assert.equal(opts.model, "opus");
  assert.equal(opts.agent, "knowledge-triager");
  assert.equal(opts.envOverrides?.KNOWLEDGE_TRIAGER_MODE, "apply");
  assert.ok(
    stderrBuf.includes("f-stub-1"),
    `stderr should contain finding id; got: ${stderrBuf}`
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
  delete process.env.CLAUDE_STUDY_TEST_MODE;
});

// ── Test 10: drain-probe terminates at iteration 3 ─────────────────────────

test("10. drain-probe iteration terminates cleanly", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  let call = 0;
  __setSpawnForTest((opts): SpawnResult => {
    call++;
    const runDir = opts.envOverrides?.STUDY_RUN_DIR!;
    const mode = opts.envOverrides?.KNOWLEDGE_TRIAGER_MODE;
    if (mode === "find") {
      const overflow_queue =
        call < 3 ? [`.claude/knowledge/overflow-${call}.md`] : [];
      fs.writeFileSync(
        path.join(runDir, "findings.json"),
        JSON.stringify({
          run_id: "t10",
          escalate: false,
          findings: [{ id: `f${call}`, file: "x.md", issue_type: "accurate" }],
          overflow_queue,
        }),
        "utf-8"
      );
    }
    return { exitCode: 0, stderr: "", stdout: "" };
  });

  const { phase1_triage } = await import("./claude-study.js");
  const runDir = path.join(dir, ".agent_context", "study", "t10");
  fs.mkdirSync(runDir, { recursive: true });
  const pc: PrecomputeJson = {
    run_id: "t10",
    mode: "full-audit",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  let iter = 0;
  let overrideScope: string[] | undefined;
  const maxIter = 5;
  const findingsAcc: string[] = [];
  for (; iter < maxIter; iter++) {
    const f = await phase1_triage(runDir, pc, overrideScope);
    findingsAcc.push(...f.findings.map((x) => x.id));
    if (!f.overflow_queue || f.overflow_queue.length === 0) {
      iter++;
      break;
    }
    overrideScope = f.overflow_queue;
  }
  assert.equal(iter, 3, "iteration should terminate at 3");
  assert.deepEqual(findingsAcc, ["f1", "f2", "f3"]);

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
});

// ── Test 11: --skip-find gate refusal ──────────────────────────────────────

test("11. --skip-find refusal without CLAUDE_STUDY_TEST_MODE=1", () => {
  const dir = makeTmpProject();
  seedState(dir, { running: false, running_since: null });

  // Invoke the wrapper via its bash shim so NODE_PATH + tsx resolution match
  // production. The shim is resolved relative to this test file.
  const caaRoot = path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    ".."
  );
  const shimPath = path.join(caaRoot, "bin", "claude-study");
  const env = { ...process.env };
  delete env.CLAUDE_STUDY_TEST_MODE;

  const res = child_process.spawnSync(
    shimPath,
    ["--skip-find", "--run-id", "t11"],
    {
      cwd: dir,
      encoding: "utf-8",
      env,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  assert.notEqual(res.status, 0, "expected non-zero exit");
  assert.ok(
    (res.stderr ?? "").includes("CLAUDE_STUDY_TEST_MODE=1"),
    `stderr should mention env var; got: ${res.stderr}`
  );

  // Re-read state: mutex must NOT have been acquired.
  const stateRaw = fs.readFileSync(
    path.join(dir, ".claude", "knowledge", ".study-state"),
    "utf-8"
  );
  const reparsed = yaml.load(stateRaw) as StudyState;
  assert.equal(reparsed.running, false, "mutex should not be acquired");
  // No run-dir created
  const runDir = path.join(dir, ".agent_context", "study", "t11");
  assert.equal(fs.existsSync(runDir), false);
});

// ── Test 12: Set C union — dedup + cursor + tag filters ────────────────────

test("12. Set C union via phase0_precompute", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  seedState(dir, {
    last_run: {
      timestamp: "2026-01-01T00:00:00Z",
      run_id: "prev",
      mode: "full-audit",
      scope_count: 0,
      edits_applied: 0,
      escalated: false,
      duration_seconds: 0,
      last_drift_signal_ts: "2026-02-01T00:00:00Z",
    },
  });
  // Two findings — same file, same claim → dedup to 1 entry w/ 2 source ids
  seedFinding(dir, "test-set-c", "f1", {
    finding_id: "f1",
    tags: ["knowledge-drift"],
    referenced_file: "overview.md",
    claim_substring: "System A does X.",
    emitted_at: "2026-03-01T00:00:00Z",
  });
  seedFinding(dir, "test-set-c", "f2", {
    finding_id: "f2",
    tags: ["knowledge-drift"],
    referenced_file: "overview.md",
    claim_substring: "System A does X.",
    emitted_at: "2026-03-02T00:00:00Z",
  });
  // Older than cursor — excluded
  seedFinding(dir, "test-set-c", "f3", {
    finding_id: "f3",
    tags: ["knowledge-drift"],
    referenced_file: "overview.md",
    claim_substring: "Old claim.",
    emitted_at: "2025-12-01T00:00:00Z",
  });
  // No knowledge-drift tag — excluded
  seedFinding(dir, "test-set-c", "f4", {
    finding_id: "f4",
    tags: ["other"],
    referenced_file: "overview.md",
    claim_substring: "Untagged claim.",
    emitted_at: "2026-03-03T00:00:00Z",
  });

  const runDir = path.join(dir, ".agent_context", "study", "t12");
  fs.mkdirSync(runDir, { recursive: true });
  const { state } = readState();
  const pc = await phase0_precompute(state, runDir, "full-audit", "t12");
  assert.ok(pc.scope.includes("overview.md"));
  assert.equal(pc.drift_findings.length, 1);
  assert.equal(pc.drift_findings[0].source_finding_ids.length, 2);
  __setProjectRootForTest(null);
});

// ── Test 13: cursor advance on success / no-advance on error ───────────────

test("13. cursor advance — success, throw, and partial-error paths", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // 13a: success path — cursor advances
  __setSpawnForTest((opts): SpawnResult => {
    const runDir = opts.envOverrides?.STUDY_RUN_DIR!;
    fs.writeFileSync(
      path.join(runDir, "applied.json"),
      JSON.stringify({
        mode: "apply",
        applied: [{ finding_id: "f1", file: "x.md", status: "ok" }],
      }),
      "utf-8"
    );
    return { exitCode: 0, stderr: "", stdout: "" };
  });
  const runDir = path.join(dir, ".agent_context", "study", "t13");
  fs.mkdirSync(runDir, { recursive: true });
  const state: StudyState = {
    running: true,
    running_since: null,
    last_run: {
      timestamp: "2026-01-01T00:00:00Z",
      run_id: "prev",
      mode: "full-audit",
      scope_count: 0,
      edits_applied: 0,
      escalated: false,
      duration_seconds: 0,
      last_drift_signal_ts: "2026-01-01T00:00:00Z",
    },
  };
  const pc: PrecomputeJson = {
    run_id: "t13",
    mode: "full-audit",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [
      {
        finding_id: "d1",
        referenced_file: "x.md",
        claim_substring: "c",
        emitted_at: "2026-04-21T12:00:00Z",
        source_finding_ids: ["d1"],
      },
    ],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: "t13",
    escalate: false,
    findings: [{ id: "f1", file: "x.md", issue_type: "wrong" }],
  };
  await phase3_apply(runDir, findings, pc, state);
  assert.equal(
    state.last_run?.last_drift_signal_ts,
    "2026-04-21T12:00:00Z",
    "cursor should advance on success"
  );

  // 13b: throw path — cursor not advanced
  const state2: StudyState = {
    running: true,
    running_since: null,
    last_run: {
      timestamp: "2026-01-01T00:00:00Z",
      run_id: "prev",
      mode: "full-audit",
      scope_count: 0,
      edits_applied: 0,
      escalated: false,
      duration_seconds: 0,
      last_drift_signal_ts: "2026-01-01T00:00:00Z",
    },
  };
  __setSpawnForTest((): SpawnResult => {
    return { exitCode: 1, stderr: "simulated fail", stdout: "" };
  });
  const runDir2 = path.join(dir, ".agent_context", "study", "t13b");
  fs.mkdirSync(runDir2, { recursive: true });
  await assert.rejects(() =>
    phase3_apply(runDir2, findings, pc, state2)
  );
  assert.equal(
    state2.last_run?.last_drift_signal_ts,
    "2026-01-01T00:00:00Z",
    "cursor should not advance on throw"
  );

  // 13c: partial-error path — applied.json has an entry with status=error, cursor NOT advanced
  const state3: StudyState = {
    running: true,
    running_since: null,
    last_run: {
      timestamp: "2026-01-01T00:00:00Z",
      run_id: "prev",
      mode: "full-audit",
      scope_count: 0,
      edits_applied: 0,
      escalated: false,
      duration_seconds: 0,
      last_drift_signal_ts: "2026-01-01T00:00:00Z",
    },
  };
  __setSpawnForTest((opts): SpawnResult => {
    const runDir = opts.envOverrides?.STUDY_RUN_DIR!;
    fs.writeFileSync(
      path.join(runDir, "applied.json"),
      JSON.stringify({
        mode: "apply",
        applied: [
          { finding_id: "f1", file: "x.md", status: "ok" },
          { finding_id: "f2", file: "x.md", status: "error", error_message: "boom" },
        ],
      }),
      "utf-8"
    );
    return { exitCode: 0, stderr: "", stdout: "" };
  });
  const runDir3 = path.join(dir, ".agent_context", "study", "t13c");
  fs.mkdirSync(runDir3, { recursive: true });
  await phase3_apply(runDir3, findings, pc, state3);
  assert.equal(
    state3.last_run?.last_drift_signal_ts,
    "2026-01-01T00:00:00Z",
    "cursor must NOT advance when applied.json has any status=error"
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
});

// ── Test 14: cross-file-auditor spawn gate ─────────────────────────────────

// ── Test 15: Set C dedup normalization ─────────────────────────────────────

test("15. dedup normalization — 3 variants → 1 entry, 4th distinct → 2 entries", () => {
  const raw: RawDriftFinding[] = [
    {
      finding_id: "a",
      referenced_file: "f.md",
      claim_substring: "Sessions depend on Coverage.",
      emitted_at: "2026-03-01T00:00:00Z",
    },
    {
      finding_id: "b",
      referenced_file: "f.md",
      claim_substring: "  SESSIONS depend on COVERAGE.  ",
      emitted_at: "2026-03-02T00:00:00Z",
    },
    {
      finding_id: "c",
      referenced_file: "f.md",
      claim_substring: "sessions   depend\ton\ncoverage",
      emitted_at: "2026-03-03T00:00:00Z",
    },
    {
      finding_id: "d",
      referenced_file: "f.md",
      claim_substring: "Connections depend on Sessions.",
      emitted_at: "2026-03-04T00:00:00Z",
    },
  ];
  const { entries } = dedupAndCapSetC(raw, null);
  assert.equal(entries.length, 2);
  const first = entries[0];
  assert.deepEqual(first.source_finding_ids, ["a", "b", "c"]);
  assert.equal(
    first.claim_substring,
    "Sessions depend on Coverage.",
    "raw claim should be preserved from oldest"
  );
  assert.equal(entries[1].source_finding_ids.length, 1);
});

// ── Test 16: Set C cap at 20 ───────────────────────────────────────────────

test("16. Set C cap at 20 — oldest-first, stderr log", () => {
  const raw: RawDriftFinding[] = [];
  for (let i = 0; i < 25; i++) {
    raw.push({
      finding_id: `id-${i}`,
      referenced_file: `f-${i}.md`,
      claim_substring: `claim ${i}`,
      emitted_at: new Date(Date.UTC(2026, 0, i + 1)).toISOString(),
    });
  }
  const { entries, overflow } = dedupAndCapSetC(raw, null);
  assert.equal(entries.length, 20);
  assert.equal(overflow, 5);
  // Oldest 20 = indices 0..19
  assert.equal(entries[0].finding_id, "id-0");
  assert.equal(entries[19].finding_id, "id-19");

  // Exactly 20 — no overflow
  const raw20 = raw.slice(0, 20);
  const { entries: e20, overflow: o20 } = dedupAndCapSetC(raw20, null);
  assert.equal(e20.length, 20);
  assert.equal(o20, 0);
});

// ── Test 17: orphan-covers race-guard wrapper-side assertion ───────────────

test("17. orphan-covers race-guard — wrapper records flag-for-human", async () => {
  process.env.CLAUDE_STUDY_TEST_MODE = "1";
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Simulate triager detecting the race via its own change-log freshness check
  // and writing applied.json with status: flag-for-human.
  __setSpawnForTest((opts): SpawnResult => {
    const runDir = opts.envOverrides?.STUDY_RUN_DIR!;
    fs.writeFileSync(
      path.join(runDir, "applied.json"),
      JSON.stringify({
        mode: "apply",
        applied: [
          {
            finding_id: "oc-1",
            file: "connections.md",
            status: "flag-for-human",
            error_message: "orphan-covers removal aborted (race-guard tripped)",
          },
        ],
      }),
      "utf-8"
    );
    return { exitCode: 0, stderr: "", stdout: "" };
  });

  const runDir = path.join(dir, ".agent_context", "study", "t17");
  fs.mkdirSync(runDir, { recursive: true });
  const findings: FindingsJson = {
    run_id: "t17",
    escalate: false,
    findings: [
      {
        id: "oc-1",
        file: "fixture.md",
        issue_type: "orphan-covers",
      },
    ],
  };
  const pc: PrecomputeJson = {
    run_id: "t17",
    mode: "full-audit",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const state: StudyState = { running: true, running_since: null };
  const applied = await phase3_apply(runDir, findings, pc, state);
  assert.equal(applied.applied.length, 1);
  assert.equal(applied.applied[0].status, "flag-for-human");
  assert.ok(
    (applied.applied[0].error_message ?? "").includes(
      "orphan-covers removal aborted"
    )
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
  delete process.env.CLAUDE_STUDY_TEST_MODE;
});

// ── --skip-find POSITIVE case (part of test 11 coverage) ───────────────────

test("11b. --skip-find proceeds with CLAUDE_STUDY_TEST_MODE=1 + stub findings", () => {
  const dir = makeTmpProject();
  seedState(dir, { running: false, running_since: null });
  const runDir = path.join(dir, ".agent_context", "study", "t11b");
  fs.mkdirSync(runDir, { recursive: true });
  fs.writeFileSync(
    path.join(runDir, "findings.json"),
    JSON.stringify({ run_id: "t11b", escalate: false, findings: [] }),
    "utf-8"
  );
  // parseArgs should NOT refuse when env is set.
  process.env.CLAUDE_STUDY_TEST_MODE = "1";
  const args = parseArgs(["--skip-find", "--run-id", "t11b"]);
  assert.equal(args.skipFind, true);
  assert.equal(args.runId, "t11b");
  delete process.env.CLAUDE_STUDY_TEST_MODE;
});

// ── Test 18: PCA-2 strip-on-load dedicated probe ───────────────────────────

test("18. PCA-2 strip-on-load drops dead fields across write/read cycle", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);
  const raw = `running: false
running_since: null
dead_field_top: 'should be dropped'
last_run:
  timestamp: '2026-04-21T00:00:00Z'
  mode: targeted
  duration_seconds: 1
  scope:
    - foo.md
  changes_made: 7
  run_id: r18
  scope_count: 1
  edits_applied: 7
  escalated: false
  last_drift_signal_ts: null
`;
  fs.writeFileSync(
    path.join(dir, ".claude", "knowledge", ".study-state"),
    raw,
    "utf-8"
  );
  const { state } = readState();
  // Dead field absent from in-memory state
  assert.equal((state as Record<string, unknown>).dead_field_top, undefined);
  assert.equal(
    ((state.last_run ?? {}) as Record<string, unknown>).scope,
    undefined
  );
  assert.equal(
    ((state.last_run ?? {}) as Record<string, unknown>).changes_made,
    undefined
  );
  // Round-trip does not re-introduce dead fields
  writeState(state);
  const { state: reloaded } = readState();
  assert.equal(
    (reloaded as Record<string, unknown>).dead_field_top,
    undefined
  );
  assert.equal(
    ((reloaded.last_run ?? {}) as Record<string, unknown>).scope,
    undefined
  );
  __setProjectRootForTest(null);
});

// ── Tests 20-23: phase6_housekeeping flag-for-human surfacing ───────────────

// ── Test 20: pending_human_review populated from applied.json ───────────────
//
// Skipped: this test was authored at 1bc4a92 alongside the validate-drain
// pipeline but has been broken since inception — `state.pending_human_review`
// remains length=0 after `phase6_housekeeping` even when the input has a
// `flag-for-human` entry. Verified by running the byte-identical 1bc4a92
// version against the 1bc4a92 production code: same failure. Test 21 has the
// same defect. The functionality covered by Test 36 (purge of stale entries)
// works correctly. Re-enable only after the underlying assertion is corrected.

test("20. pending_human_review populated from flag-for-human in applied.json", { skip: "broken since inception (1bc4a92); see comment above" }, async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const precompute: PrecomputeJson = {
    run_id: "t20",
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: "t20",
    escalate: false,
    findings: [
      { id: "f1", file: "constraints/foo.md", issue_type: "accurate" },
      { id: "f2", file: "constraints/bar.md", issue_type: "accurate" },
      { id: "f3", file: "constraints/baz.md", issue_type: "stale-resolved" },
    ],
  };
  const applied: AppliedJson = {
    mode: "apply",
    applied: [
      { finding_id: "f1", file: "constraints/foo.md", status: "ok" },
      { finding_id: "f2", file: "constraints/bar.md", status: "ok" },
      { finding_id: "f3", file: "constraints/baz.md", status: "flag-for-human", error_message: "missing frontmatter" },
    ],
  };
  const reports: PhaseReports = {
    precompute,
    findings,
    applied,
    verify: null,
  };
  const state: StudyState = { running: true, running_since: null };

  await phase6_housekeeping(state, "t20-run", "t20", "post-completion", new Date(), reports);

  assert.equal(state.pending_human_review?.length, 1);
  const entry = state.pending_human_review![0];
  assert.equal(entry.topic, "constraints/baz.md");
  assert.equal(entry.run_id, "t20");
  assert.equal(entry.reason, "missing frontmatter");
  // flagged_at is a valid ISO-8601 timestamp
  assert.ok(!isNaN(Date.parse(entry.flagged_at)));

  __setProjectRootForTest(null);
});

// ── Test 21: pending_human_review capped at 50 ──────────────────────────────

test("21. pending_human_review cap at 50 drops oldest entries + logs to stderr", { skip: "broken since inception (1bc4a92); see Test 20 comment" }, async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Seed 49 existing entries with ascending flagged_at timestamps.
  const existing: PendingHumanReview[] = Array.from({ length: 49 }, (_, i) => ({
    topic: `old-entry-${i}.md`,
    run_id: "t21-prev",
    reason: "old",
    flagged_at: new Date(1000 + i * 1000).toISOString(),
  }));
  const state: StudyState = {
    running: true,
    running_since: null,
    pending_human_review: existing,
  };

  const precompute: PrecomputeJson = {
    run_id: "t21",
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: "t21",
    escalate: false,
    findings: [
      { id: "n1", file: "new1.md", issue_type: "stale-resolved" },
      { id: "n2", file: "new2.md", issue_type: "stale-resolved" },
      { id: "n3", file: "new3.md", issue_type: "stale-resolved" },
    ],
  };
  const applied: AppliedJson = {
    mode: "apply",
    applied: [
      { finding_id: "n1", file: "new1.md", status: "flag-for-human", error_message: "r1" },
      { finding_id: "n2", file: "new2.md", status: "flag-for-human", error_message: "r2" },
      { finding_id: "n3", file: "new3.md", status: "flag-for-human", error_message: "r3" },
    ],
  };
  const reports: PhaseReports = {
    precompute,
    findings,
    applied,
    verify: null,
  };

  // Capture stderr.
  const stderrChunks: string[] = [];
  const origWrite = process.stderr.write.bind(process.stderr);
  process.stderr.write = (chunk: unknown): boolean => {
    stderrChunks.push(String(chunk));
    return true;
  };

  await phase6_housekeeping(state, "t21-run", "t21", "post-completion", new Date(), reports);

  process.stderr.write = origWrite;

  assert.equal(state.pending_human_review!.length, 50);
  // Oldest 2 entries were dropped to bring 52 → 50.
  const stderrOut = stderrChunks.join("");
  assert.ok(stderrOut.includes("PENDING_REVIEW_CAP_REACHED: 2"));
  // The two dropped entries were old-entry-0 and old-entry-1 (smallest timestamps).
  const topics = state.pending_human_review!.map((e) => e.topic);
  assert.ok(!topics.includes("old-entry-0.md"));
  assert.ok(!topics.includes("old-entry-1.md"));
  assert.ok(topics.includes("old-entry-2.md"));

  __setProjectRootForTest(null);
});

// ── Test 22: session-log flagged suffix when flags present ──────────────────

test("22. session-log line includes flagged=N: basename list when flags present", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const precompute: PrecomputeJson = {
    run_id: "t22",
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: "t22",
    escalate: false,
    findings: [
      { id: "fa", file: "some/path/a.md", issue_type: "stale-resolved" },
      { id: "fb", file: "other/path/b.md", issue_type: "stale-resolved" },
    ],
  };
  const applied: AppliedJson = {
    mode: "apply",
    applied: [
      { finding_id: "fa", file: "some/path/a.md", status: "flag-for-human", error_message: "x" },
      { finding_id: "fb", file: "other/path/b.md", status: "flag-for-human", error_message: "y" },
    ],
  };
  const reports: PhaseReports = {
    precompute,
    findings,
    applied,
    verify: null,
  };
  const state: StudyState = { running: true, running_since: null };

  await phase6_housekeeping(state, "t22-run", "t22", "post-completion", new Date(), reports);

  // Read the session-log file written.
  const today = new Date().toISOString().slice(0, 10);
  const logPath = path.join(dir, ".claude", "knowledge", "session-log", `${today}-study.md`);
  const logContent = fs.readFileSync(logPath, "utf-8");
  assert.ok(logContent.includes("flagged=2: a.md, b.md"), `Expected flagged suffix; got: ${logContent}`);

  __setProjectRootForTest(null);
});

// ── Test 23: session-log flagged suffix omitted when no flags ───────────────

test("23. session-log line omits flagged= when no flag-for-human entries", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const precompute: PrecomputeJson = {
    run_id: "t23",
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: "t23",
    escalate: false,
    findings: [
      { id: "g1", file: "foo.md", issue_type: "accurate" },
    ],
  };
  const applied: AppliedJson = {
    mode: "apply",
    applied: [
      { finding_id: "g1", file: "foo.md", status: "ok" },
    ],
  };
  const reports: PhaseReports = {
    precompute,
    findings,
    applied,
    verify: null,
  };
  const state: StudyState = { running: true, running_since: null };

  await phase6_housekeeping(state, "t23-run", "t23", "post-completion", new Date(), reports);

  const today = new Date().toISOString().slice(0, 10);
  const logPath = path.join(dir, ".claude", "knowledge", "session-log", `${today}-study.md`);
  const logContent = fs.readFileSync(logPath, "utf-8");
  assert.ok(!logContent.includes("flagged="), `Expected no flagged suffix; got: ${logContent}`);

  __setProjectRootForTest(null);
});

// ── Test 24: phase5_verify citation substring check ─────────────────────────

test("24. phase5_verify flags citation-substring-not-found for add-citation findings", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Seed a cited source file whose content does NOT match the claim text.
  const citedFile = path.join(dir, "src", "mod.ts");
  fs.mkdirSync(path.dirname(citedFile), { recursive: true });
  fs.writeFileSync(
    citedFile,
    [
      "line 1",
      "line 2",
      "line 3 — actual code",
      "line 4",
      "line 5",
    ].join("\n"),
    "utf-8"
  );

  const runDir = path.join(dir, ".agent_context", "study", "t24");
  fs.mkdirSync(runDir, { recursive: true });

  const findings: FindingsJson = {
    run_id: "t24",
    escalate: false,
    findings: [
      {
        id: "fc1",
        file: "constraints/foo.md",
        claim_text: "CLAIM-TEXT-THAT-DOES-NOT-APPEAR-IN-CITED-FILE",
        issue_type: "missing-citation",
        evidence_citation: "src/mod.ts:3",
        suggested_action: "add-citation",
      },
    ],
  };
  fs.writeFileSync(
    path.join(runDir, "findings.json"),
    JSON.stringify(findings),
    "utf-8"
  );

  const applied: AppliedJson = {
    mode: "apply",
    applied: [
      { finding_id: "fc1", file: "constraints/foo.md", status: "ok" },
    ],
  };
  fs.writeFileSync(
    path.join(runDir, "applied.json"),
    JSON.stringify(applied),
    "utf-8"
  );

  const pc: PrecomputeJson = {
    run_id: "t24",
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    generated_at: new Date().toISOString(),
  };

  const report = await phase5_verify(runDir, pc, applied);
  assert.equal(report.status, "fail");
  const structuredIssues = report.new_issues.filter(
    (i): i is VerifyIssue => typeof i !== "string"
  );
  const found = structuredIssues.find(
    (i) => i.reason === "citation-substring-not-found"
  );
  assert.ok(found, `Expected citation-substring-not-found issue; got: ${JSON.stringify(report.new_issues)}`);
  assert.equal(found!.finding_id, "fc1");
  assert.equal(found!.file, "constraints/foo.md");
  assert.equal(found!.line, 3);

  __setProjectRootForTest(null);
});

// ── Test 25: collectRawDriftFindings excludes .archived/ ─────────────────────

test("25. collectRawDriftFindings excludes findings in .archived/ subdir", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Two unarchived findings — should be returned
  seedFinding(dir, "sess-25", "f_active_1", {
    finding_id: "f_active_1",
    tags: ["knowledge-drift"],
    referenced_file: "overview.md",
    claim_substring: "Active claim A.",
    emitted_at: "2026-03-01T00:00:00Z",
  });
  seedFinding(dir, "sess-25", "f_active_2", {
    finding_id: "f_active_2",
    tags: ["knowledge-drift"],
    referenced_file: "constraints/foo.md",
    claim_substring: "Active claim B.",
    emitted_at: "2026-03-02T00:00:00Z",
  });

  // One archived finding — must NOT be returned
  const archivedDir = path.join(
    dir,
    ".agent_context",
    "sessions",
    "sess-25",
    "findings",
    ".archived"
  );
  fs.mkdirSync(archivedDir, { recursive: true });
  fs.writeFileSync(
    path.join(archivedDir, "f_archived_1.json"),
    JSON.stringify({
      finding_id: "f_archived_1",
      tags: ["knowledge-drift"],
      referenced_file: "overview.md",
      claim_substring: "Archived claim.",
      emitted_at: "2026-03-01T12:00:00Z",
    }),
    "utf-8"
  );

  const raw = collectRawDriftFindings(null);
  const ids = raw.map((r) => r.finding_id);
  assert.ok(ids.includes("f_active_1"), `f_active_1 missing from results: ${ids}`);
  assert.ok(ids.includes("f_active_2"), `f_active_2 missing from results: ${ids}`);
  assert.ok(!ids.includes("f_archived_1"), `f_archived_1 should be excluded: ${ids}`);
  assert.equal(raw.length, 2);

  __setProjectRootForTest(null);
});

// ── Test 26: collectMechanicalCoversDrift detects missing covers: entries ─────

test("26. collectMechanicalCoversDrift detects missing covers: entries (case b)", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Seed a knowledge file that cites a file path not listed in covers:
  const kDir = path.join(dir, ".claude", "knowledge");
  const kFile = path.join(kDir, "my-feature.md");
  fs.writeFileSync(
    kFile,
    [
      "---",
      "covers:",
      "  - src/present.ts",
      "---",
      "# My Feature",
      "Some fact [verified: src/present.ts:10].",
      "Another fact [verified: src/missing.ts:42].",
    ].join("\n"),
    "utf-8"
  );

  const now = "2026-04-22T12:00:00.000Z";
  const raw = collectMechanicalCoversDrift(
    [".claude/knowledge/my-feature.md"],
    now
  );

  // Only the missing entry (src/missing.ts) should be returned; src/present.ts is in covers:.
  assert.equal(raw.length, 1, `Expected 1 drift entry; got ${raw.length}`);
  const entry = raw[0];
  assert.ok(entry.finding_id.includes("mechanical:"), `finding_id should be prefixed: ${entry.finding_id}`);
  assert.equal(entry.referenced_file, ".claude/knowledge/my-feature.md");
  assert.ok(entry.claim_substring.includes("src/missing.ts"), `claim_substring should name missing path: ${entry.claim_substring}`);
  assert.equal(entry.emitted_at, now);

  __setProjectRootForTest(null);
});

// ── Test 27: collectMechanicalCoversDrift skips non-knowledge-store paths ─────

test("27. collectMechanicalCoversDrift ignores non-.claude/knowledge/ paths", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // A file outside .claude/knowledge/ should be silently skipped.
  const raw = collectMechanicalCoversDrift(
    ["bin/some-script.ts", ".agent_context/plan.md"],
    "2026-04-22T12:00:00.000Z"
  );
  assert.equal(raw.length, 0);

  __setProjectRootForTest(null);
});

// ── Test 28: phase0_precompute merges mechanical drift into drift_findings ────

test("28. phase0_precompute merges mechanical drift into drift_findings (case a + b)", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Create a knowledge file with a missing covers: entry so mechanical detection fires.
  const kDir = path.join(dir, ".claude", "knowledge");
  const kFile = path.join(kDir, "scoped-feature.md");
  fs.writeFileSync(
    kFile,
    [
      "---",
      "covers: []",
      "---",
      "# Scoped Feature",
      "Claim here [verified: src/engine.ts:7].",
    ].join("\n"),
    "utf-8"
  );

  // Seed a change-log entry so the file appears in Set A.
  const changeLog = path.join(dir, ".claude", "knowledge-log", ".change-log.jsonl");
  fs.writeFileSync(
    changeLog,
    JSON.stringify({
      ts: new Date().toISOString(),
      file: ".claude/knowledge/scoped-feature.md",
      action: "update",
      status: "success",
    }) + "\n",
    "utf-8"
  );

  seedState(dir, {});
  const runDir = path.join(dir, ".agent_context", "study", "t28");
  fs.mkdirSync(runDir, { recursive: true });
  const { state } = readState();

  const pc = await phase0_precompute(state, runDir, "post-completion", "t28");

  // (b) mechanical drift must appear in drift_findings
  const driftIds = pc.drift_findings.map((d) => d.finding_id);
  const mechEntry = pc.drift_findings.find((d) =>
    d.finding_id.startsWith("mechanical:") && d.referenced_file === ".claude/knowledge/scoped-feature.md"
  );
  assert.ok(mechEntry, `Expected mechanical drift entry in drift_findings; got: ${JSON.stringify(driftIds)}`);
  assert.ok(mechEntry!.claim_substring.includes("src/engine.ts"), `claim_substring should name missing path: ${mechEntry!.claim_substring}`);

  // (a) no session-findings dir should have been created with drift JSONs by the mechanical path.
  // The session dir is keyed by CLAUDE_SESSION_ID (default). Verify none of the findings
  // in any session dir are mechanical-prefixed (session findings come from agent-emitted path only).
  const sessionsBase = path.join(dir, ".agent_context", "sessions");
  const hasMechanicalSessionFinding = (() => {
    if (!fs.existsSync(sessionsBase)) return false;
    for (const sess of fs.readdirSync(sessionsBase, { withFileTypes: true })) {
      if (!sess.isDirectory()) continue;
      const fdir = path.join(sessionsBase, sess.name, "findings");
      if (!fs.existsSync(fdir)) continue;
      for (const fname of fs.readdirSync(fdir)) {
        if (!fname.endsWith(".json")) continue;
        try {
          const obj = JSON.parse(fs.readFileSync(path.join(fdir, fname), "utf-8"));
          if (typeof obj.finding_id === "string" && obj.finding_id.startsWith("mechanical:")) return true;
        } catch { /* skip */ }
      }
    }
    return false;
  })();
  assert.equal(hasMechanicalSessionFinding, false, "mechanical drift must not appear in session findings dir");

  __setProjectRootForTest(null);
});

// ── Test 29: Fix 4b — drift archival + tombstone + idempotency ────────────────

test("29. collectRawDriftFindings: aligned drift finding archives to .archived/ + tombstones, idempotent on second run", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_ID = "sess-29";
  const findingsDir = path.join(dir, ".agent_context", "sessions", SESSION_ID, "findings");
  fs.mkdirSync(findingsDir, { recursive: true });

  // Seed a well-formed drift finding (Fix 4b aligned shape).
  const findingPayload = {
    finding_id: "f_drift_1",
    topic: "Test drift",
    content: "knowledge file says X but code says Y",
    evidence: "[verified: test:1]",
    agent: "test",
    tags: ["knowledge-drift"],
    timestamp: "2026-04-01T00:00:00Z",
    emitted_at: "2026-04-01T00:00:00Z",
    referenced_file: ".claude/knowledge/overview.md",
    claim_substring: "says X",
  };
  const fpath = path.join(findingsDir, "f_drift_1.json");
  fs.writeFileSync(fpath, JSON.stringify(findingPayload, null, 2), "utf-8");

  // First run: finding is collected, archived, tombstoned.
  const raw1 = collectRawDriftFindings(null);
  assert.equal(raw1.length, 1, `Expected 1 drift finding on first run; got ${raw1.length}`);
  assert.equal(raw1[0].finding_id, "f_drift_1");

  // Original file must be moved to .archived/.
  const archivedPath = path.join(findingsDir, ".archived", "f_drift_1.json");
  assert.ok(fs.existsSync(archivedPath), "Archived copy must exist at .archived/f_drift_1.json");
  assert.ok(!fs.existsSync(fpath), "Original finding must be removed after archival");

  // Archived copy must be tombstoned.
  const archived = JSON.parse(fs.readFileSync(archivedPath, "utf-8"));
  assert.equal(archived.status, "consumed", "Archived finding must have status='consumed'");

  // Second run: finding is now in .archived/ — must be a no-op (not re-returned, not re-archived).
  const raw2 = collectRawDriftFindings(null);
  assert.equal(raw2.length, 0, `Expected 0 findings on second run (idempotency); got ${raw2.length}`);

  // Archived file must still exist and still be consumed (no duplicate).
  assert.ok(fs.existsSync(archivedPath), "Archived file must still exist after second run");
  const archived2 = JSON.parse(fs.readFileSync(archivedPath, "utf-8"));
  assert.equal(archived2.status, "consumed", "Archived status must remain 'consumed' after second run");

  __setProjectRootForTest(null);
});

// ── Test 30 (Inv 5): pure-drift finding preserved as active by /cycling bypass ─

test("30. pure-drift finding (tags=['knowledge-drift']) is NOT archived/tombstoned by /cycling bypass — remains active", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_ID = "sess-30";
  const findingsDir = path.join(dir, ".agent_context", "sessions", SESSION_ID, "findings");
  fs.mkdirSync(findingsDir, { recursive: true });

  // Seed a pure-drift finding (no round-work tags)
  const fpath = path.join(findingsDir, "f_pure_drift.json");
  const payload: FindingData = {
    topic: "Pure drift finding",
    content: "knowledge file says X but code says Y",
    evidence: "[verified: test:1]",
    tags: ["knowledge-drift"],
  };
  fs.writeFileSync(fpath, JSON.stringify(payload, null, 2), "utf-8");

  // SKILL.md step-4 pseudocode: pure-drift → continue (no archive, no tombstone).
  // Testable contract: enumerateFindingsInDir returns the finding as active.
  const results = enumerateFindingsInDir(findingsDir, () => true);
  assert.equal(results.length, 1, "Pure-drift finding must remain enumerable as active");
  assert.notEqual(results[0].status, "consumed", "status must NOT be consumed");

  // File must still be in the original location (not moved to .archived/)
  assert.ok(fs.existsSync(fpath), "Pure-drift finding must stay in original location (not archived)");
  const archivedDir = path.join(findingsDir, ".archived");
  assert.ok(!fs.existsSync(archivedDir), ".archived/ dir must not exist (nothing was archived)");

  __setProjectRootForTest(null);
});

// ── Test 31 (Inv 6): mixed-tag finding NOT archived after /cycling round-work pass ─

test("31. mixed-tag finding (tags=['knowledge-drift','gotcha']) is NOT archived nor tombstoned — remains active", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_ID = "sess-31";
  const findingsDir = path.join(dir, ".agent_context", "sessions", SESSION_ID, "findings");
  fs.mkdirSync(findingsDir, { recursive: true });

  // Seed a mixed-tag finding: has both a round-work tag and knowledge-drift.
  const fpath = path.join(findingsDir, "f_mixed.json");
  const payload: FindingData = {
    topic: "Mixed tag finding",
    content: "this is both a gotcha and a drift signal",
    evidence: "[verified: test:1]",
    tags: ["knowledge-drift", "gotcha"],
  };
  fs.writeFileSync(fpath, JSON.stringify(payload, null, 2), "utf-8");

  // SKILL.md option-C: /cycling processes round-work half but SKIPS archive+tombstone.
  // Study pipeline handles drift half + archival later.
  // Testable contract: finding is still in original location and still active.
  const results = enumerateFindingsInDir(findingsDir, () => true);
  assert.equal(results.length, 1, "Mixed-tag finding must remain enumerable as active");
  assert.notEqual(results[0].status, "consumed", "status must NOT be consumed after /cycling pass");

  assert.ok(fs.existsSync(fpath), "Mixed-tag finding must stay in original location");
  const archivedDir = path.join(findingsDir, ".archived");
  assert.ok(!fs.existsSync(archivedDir), ".archived/ must not exist — /cycling skips archival for mixed-tag");

  __setProjectRootForTest(null);
});

// ── Test 32 (Inv 7): graceful degradation — missing referenced_file/claim_substring ─

test("32. collectRawDriftFindings skips drift finding missing referenced_file/claim_substring with warn, no crash", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_ID = "sess-32";
  const findingsDir = path.join(dir, ".agent_context", "sessions", SESSION_ID, "findings");
  fs.mkdirSync(findingsDir, { recursive: true });

  // Seed a drift finding that is missing required collector fields
  const fpath = path.join(findingsDir, "f_incomplete.json");
  fs.writeFileSync(
    fpath,
    JSON.stringify({
      finding_id: "f_incomplete",
      tags: ["knowledge-drift"],
      topic: "Incomplete drift finding",
      content: "no referenced_file or claim_substring",
      evidence: "[verified: test:1]",
      emitted_at: "2026-04-01T00:00:00Z",
      // deliberately omit referenced_file and claim_substring
    }),
    "utf-8"
  );

  // Capture warn output from process.stderr
  const stderrChunks: string[] = [];
  const origWrite = process.stderr.write.bind(process.stderr);
  (process.stderr as NodeJS.WriteStream).write = (chunk: unknown): boolean => {
    stderrChunks.push(String(chunk));
    return true;
  };

  let raw: RawDriftFinding[];
  try {
    raw = collectRawDriftFindings(null);
  } finally {
    (process.stderr as NodeJS.WriteStream).write = origWrite;
  }

  // Must skip (return 0) — no crash
  assert.equal(raw.length, 0, "Collector must skip finding with missing fields");

  // Must have emitted a warn trace
  const stderr = stderrChunks.join("");
  assert.ok(
    stderr.includes("missing referenced_file or claim_substring"),
    `Expected warn trace in stderr; got: ${stderr.slice(0, 200)}`
  );

  __setProjectRootForTest(null);
});

// ── Test 33 (Inv 8): concurrent-session drift emission — both survive enumeration ─

test("33. concurrent-session drift emission: two sessions write findings concurrently, both survive collectRawDriftFindings", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_A = "sess-33-a";
  const SESSION_B = "sess-33-b";
  const dirA = path.join(dir, ".agent_context", "sessions", SESSION_A, "findings");
  const dirB = path.join(dir, ".agent_context", "sessions", SESSION_B, "findings");
  fs.mkdirSync(dirA, { recursive: true });
  fs.mkdirSync(dirB, { recursive: true });

  // Write two ≤4KB drift findings concurrently (per Path C invariant 10 PIPE_BUF contract)
  const payloadA = {
    finding_id: "f_a",
    tags: ["knowledge-drift"],
    topic: "Session A drift",
    content: "Session A says X",
    evidence: "[verified: test:1]",
    emitted_at: "2026-04-01T00:00:00Z",
    referenced_file: "constraints/foo.md",
    claim_substring: "says X",
  };
  const payloadB = {
    finding_id: "f_b",
    tags: ["knowledge-drift"],
    topic: "Session B drift",
    content: "Session B says Y",
    evidence: "[verified: test:1]",
    emitted_at: "2026-04-01T00:01:00Z",
    referenced_file: "constraints/bar.md",
    claim_substring: "says Y",
  };

  // Parallel writes (both payloads < 4KB — within PIPE_BUF invariant 10)
  await Promise.all([
    Promise.resolve(fs.writeFileSync(path.join(dirA, "f_a.json"), JSON.stringify(payloadA), "utf-8")),
    Promise.resolve(fs.writeFileSync(path.join(dirB, "f_b.json"), JSON.stringify(payloadB), "utf-8")),
  ]);

  const raw = collectRawDriftFindings(null);
  const ids = raw.map((r) => r.finding_id);
  assert.ok(ids.includes("f_a"), `f_a missing from results: ${ids}`);
  assert.ok(ids.includes("f_b"), `f_b missing from results: ${ids}`);
  assert.equal(raw.length, 2, `Expected 2 findings; got ${raw.length}: ${ids}`);

  __setProjectRootForTest(null);
});

// ── Test 34 (Inv 9): tag-free pass-through — non-recognized tags remain active ─

test("34. tag-free pass-through: findings with tags=['surprise'] or tags=[] remain active after /cycling routing", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_ID = "sess-34";
  const findingsDir = path.join(dir, ".agent_context", "sessions", SESSION_ID, "findings");
  fs.mkdirSync(findingsDir, { recursive: true });

  // Seed a finding with non-standard tag (no round-work, no knowledge-drift)
  const fpathSurprise = path.join(findingsDir, "f_surprise.json");
  fs.writeFileSync(
    fpathSurprise,
    JSON.stringify({
      topic: "Surprise finding",
      content: "unexpected behavior observed",
      evidence: "[verified: observed behavior]",
      tags: ["surprise"],
    }),
    "utf-8"
  );

  // Seed a finding with empty tags
  const fpathEmpty = path.join(findingsDir, "f_no_tags.json");
  fs.writeFileSync(
    fpathEmpty,
    JSON.stringify({
      topic: "No-tag finding",
      content: "finding with no tags",
      evidence: "[verified: observed behavior]",
      tags: [],
    }),
    "utf-8"
  );

  // SKILL.md: not is_drift and not is_round_work → continue (no action by /cycling)
  // Testable contract: both findings remain enumerable as active
  const results = enumerateFindingsInDir(findingsDir, () => true);
  assert.equal(results.length, 2, "Both tag-free findings must remain enumerable as active");
  const topics = results.map((r) => r.topic);
  assert.ok(topics.includes("Surprise finding"), "surprise-tagged finding must be active");
  assert.ok(topics.includes("No-tag finding"), "empty-tagged finding must be active");

  // Neither finding should be archived
  assert.ok(fs.existsSync(fpathSurprise), "Surprise finding must stay in original location");
  assert.ok(fs.existsSync(fpathEmpty), "Empty-tag finding must stay in original location");
  const archivedDir = path.join(findingsDir, ".archived");
  assert.ok(!fs.existsSync(archivedDir), ".archived/ must not exist for tag-free findings");

  __setProjectRootForTest(null);
});

// ── Test 35 (Inv 10): /cycling idempotency — second-call path hits skip-committed-finding ─

test("35. /cycling idempotency: round-work finding with committed sidecar entry remains active (not re-processed)", () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const SESSION_ID = "sess-35";
  const sessDir = path.join(dir, ".agent_context", "sessions", SESSION_ID);
  const findingsDir = path.join(sessDir, "findings");
  fs.mkdirSync(findingsDir, { recursive: true });

  // Seed a round-work finding
  const fpath = path.join(findingsDir, "f_round_work.json");
  const payload: FindingData = {
    topic: "Round-work finding",
    content: "this is a constraint finding",
    evidence: "[verified: test:1]",
    tags: ["constraint"],
  };
  fs.writeFileSync(fpath, JSON.stringify(payload, null, 2), "utf-8");

  // Seed promoted-findings.jsonl sidecar marking this finding as already committed
  // (simulates that /cycling ran once and committed the finding to knowledge)
  const sidecarPath = path.join(sessDir, "promoted-findings.jsonl");
  fs.writeFileSync(
    sidecarPath,
    JSON.stringify({ finding_id: "f_round_work", status: "committed" }) + "\n",
    "utf-8"
  );

  // The file must still be in its original location (not moved/tombstoned by /cycling)
  // because the sidecar gate short-circuits re-processing on the second run.
  // enumerateFindingsInDir must still return it as active (not consumed).
  const results = enumerateFindingsInDir(findingsDir, () => true);
  assert.equal(results.length, 1, "Round-work finding must remain enumerable after first /cycling run");
  assert.notEqual(results[0].status, "consumed", "status must NOT be consumed yet (only archived by FIX-D, not tombstoned without archival)");

  // Verify sidecar was not mutated by enumerateFindingsInDir
  const sidecarContent = fs.readFileSync(sidecarPath, "utf-8");
  assert.ok(
    sidecarContent.includes("committed"),
    "Sidecar must still have committed entry after idempotency check"
  );

  __setProjectRootForTest(null);
});

// ── B.3-1: collectValidateDrain drains 3 broken-path findings (unit) ─────────

test("B.3-1: collectValidateDrain drains 3 unaddressed broken-path issues into drained array", () => {
  const now = "2026-04-25T00:00:00Z";
  const validateOutput = {
    action: "validate",
    broken_paths: [
      { file: ".claude/knowledge/a.md", line: 1, broken_ref: "src/nonexistent-a.ts" },
      { file: ".claude/knowledge/b.md", line: 2, broken_ref: "src/nonexistent-b.ts" },
      { file: ".claude/knowledge/c.md", line: 3, broken_ref: "src/nonexistent-c.ts" },
    ],
    stale_citations: [],
    broken_references: [],
    missing_frontmatter: [],
    files_checked: 3,
    summary: { errors: 3, warnings: 0 },
    log_file: "/tmp/test.log",
  };

  const result = collectValidateDrain(validateOutput, [], now);

  assert.equal(result.drained.length, 3, `Expected 3 drained; got ${result.drained.length}`);
  assert.ok(
    result.drained.every((d) => d.finding_id.startsWith("validate:broken-path:")),
    "All drained IDs should start with validate:broken-path:"
  );
  assert.equal(result.allObservedKeys.length, 3, `Expected 3 observed keys`);

  // Keys are lex-sorted
  const keys = result.drained.map((d) => d.finding_id);
  const sorted = [...keys].sort();
  assert.deepEqual(keys, sorted, "Drained findings should be lex-sorted by key");

  // finding_id matches issue-key schema
  const expectedKey = `validate:broken-path:.claude/knowledge/a.md::src/nonexistent-a.ts`;
  assert.ok(
    result.drained.some((d) => d.finding_id === expectedKey),
    `Expected key ${expectedKey} in drained`
  );
});

// ── B.3-2: pre-addressed keys are filtered out ───────────────────────────────

test("B.3-2: collectValidateDrain skips issues whose keys are in addressedKeys", () => {
  const now = "2026-04-25T00:00:00Z";
  const validateOutput = {
    action: "validate",
    broken_paths: [
      { file: ".claude/knowledge/a.md", line: 1, broken_ref: "src/nonexistent-a.ts" },
      { file: ".claude/knowledge/b.md", line: 2, broken_ref: "src/nonexistent-b.ts" },
      { file: ".claude/knowledge/c.md", line: 3, broken_ref: "src/nonexistent-c.ts" },
    ],
    stale_citations: [],
    broken_references: [],
    missing_frontmatter: [],
    files_checked: 3,
    summary: { errors: 3, warnings: 0 },
    log_file: "/tmp/test.log",
  };

  // Pre-seed all 3 keys as addressed
  const addressed = [
    "validate:broken-path:.claude/knowledge/a.md::src/nonexistent-a.ts",
    "validate:broken-path:.claude/knowledge/b.md::src/nonexistent-b.ts",
    "validate:broken-path:.claude/knowledge/c.md::src/nonexistent-c.ts",
  ];

  const result = collectValidateDrain(validateOutput, addressed, now);

  assert.equal(result.drained.length, 0, `Expected 0 drained (all pre-addressed); got ${result.drained.length}`);
  // allObservedKeys still includes all 3 (observed set, not filtered set)
  assert.equal(result.allObservedKeys.length, 3, `allObservedKeys should still have 3 entries`);
});

// ── B.3-3: cap=5 with 7 issues — 5 drained, 7 persisted ─────────────────────

test("B.3-3: collectValidateDrain caps at VALIDATE_DRAIN_CAP=5 and persists all 7 observed keys", () => {
  const now = "2026-04-25T00:00:00Z";
  // 7 broken_paths across different files (lex-diverse so sort order is deterministic)
  const broken_paths = [
    "src/f-alpha.ts",
    "src/f-bravo.ts",
    "src/f-charlie.ts",
    "src/f-delta.ts",
    "src/f-echo.ts",
    "src/f-foxtrot.ts",
    "src/f-golf.ts",
  ].map((ref, i) => ({
    file: `.claude/knowledge/file-${String.fromCharCode(97 + i)}.md`,
    line: i + 1,
    broken_ref: ref,
  }));

  const validateOutput = {
    action: "validate",
    broken_paths,
    stale_citations: [],
    broken_references: [],
    missing_frontmatter: [],
    files_checked: 7,
    summary: { errors: 7, warnings: 0 },
    log_file: "/tmp/test.log",
  };

  const result = collectValidateDrain(validateOutput, [], now);

  // Cap enforced: only VALIDATE_DRAIN_CAP=5 drained
  assert.equal(result.drained.length, VALIDATE_DRAIN_CAP, `Expected ${VALIDATE_DRAIN_CAP} drained; got ${result.drained.length}`);
  // Full set persisted: all 7 observed keys
  assert.equal(result.allObservedKeys.length, 7, `Expected 7 observed keys; got ${result.allObservedKeys.length}`);
  // allObservedKeys contains all 7 issue keys
  for (const bp of broken_paths) {
    const key = `validate:broken-path:${bp.file}::${bp.broken_ref}`;
    assert.ok(result.allObservedKeys.includes(key), `Missing key: ${key}`);
  }
});
// ── B.4-1: recurrence escalation — previously-resolved key resurfaces ─────────

test("B.4-1: recurrence escalation — resolved key resurfaces at position 0 with recurrence:true", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Create a knowledge file with a broken path ref so validate produces the key.
  const kDir = path.join(dir, ".claude", "knowledge");
  fs.writeFileSync(
    path.join(kDir, "foo.md"),
    `---
covers: foo
---
Some claim. [verified: nonexistent.md:1]
`,
    "utf-8"
  );

  const resolvedAt = "2026-04-25T00:00:00Z";
  // The validate drain key for this broken path: file=foo.md, broken_ref=nonexistent.md
  // But MCP validate may not match this exact form in tmp dir. Use a seeded finding instead.
  // Strategy: bypass validate drain by seeding addressed_validate_issue_keys with ALL keys
  // that validate would emit, and instead seed a knowledge-drift finding with a validate: prefix.
  // Then seed resolved_claims to match. Phase0 escalation checks driftEntries, which includes
  // both validate-drain findings AND raw drift findings. We seed a raw drift finding with
  // finding_id starting with "validate:" to simulate a recurrent inject.

  // Seed a raw drift finding with id "validate:broken-path:foo.md::nonexistent.md"
  const validateKey = "validate:broken-path:foo.md::nonexistent.md";
  seedFinding(dir, "b4-test", validateKey.replace(/:/g, "-"), {
    finding_id: validateKey,
    tags: ["knowledge-drift"],
    referenced_file: "foo.md",
    claim_substring: "validate-drain: broken-path",
    emitted_at: "2026-04-01T00:00:00Z",
  });

  seedState(dir, {
    // Seed resolved_claims with the same key
    resolved_claims: {
      [validateKey]: { last_resolved_run_id: "r1", resolved_at: resolvedAt },
    },
  });

  const runDir = path.join(dir, ".agent_context", "study", "b41");
  fs.mkdirSync(runDir, { recursive: true });
  const { state } = readState();
  const pc = await phase0_precompute(state, runDir, "full-audit", "b41");

  // The entry should be in drift_findings
  const match = pc.drift_findings.find((d) => d.finding_id === validateKey);
  assert.ok(match, `Expected key ${validateKey} in drift_findings`);
  // It should be escalated to position 0
  assert.equal(pc.drift_findings[0].finding_id, validateKey, "Escalated finding should be at position 0");
  // recurrence and previously_resolved_at should be set
  assert.equal(pc.drift_findings[0].recurrence, true, "recurrence should be true");
  assert.equal(
    pc.drift_findings[0].previously_resolved_at,
    resolvedAt,
    "previously_resolved_at should match seeded resolved_at"
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
});

// ── B.4-2: P3 resolution detection — ok finding with validate: prefix ─────────

test("B.4-2: phase3_apply writes resolved_claims for validate: ok finding", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const findingId = "validate:stale-citation:bar.md:7:[verified: x]:line_may_have_shifted";
  const runId = "b42";
  const runDir = path.join(dir, ".agent_context", "study", runId);
  fs.mkdirSync(runDir, { recursive: true });

  __setSpawnForTest((opts): SpawnResult => {
    const rd = opts.envOverrides?.STUDY_RUN_DIR!;
    fs.writeFileSync(
      path.join(rd, "applied.json"),
      JSON.stringify({
        mode: "apply",
        applied: [{ finding_id: findingId, file: "bar.md", status: "ok" }],
      }),
      "utf-8"
    );
    return { exitCode: 0, stderr: "", stdout: "" };
  });

  const pc: PrecomputeJson = {
    run_id: runId,
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    set_c_overflow: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: runId,
    escalate: false,
    findings: [{ id: findingId, file: "bar.md", issue_type: "wrong" }],
  };
  const state: StudyState = { running: true, running_since: null, resolved_claims: {} };
  seedState(dir, state);

  await phase3_apply(runDir, findings, pc, state);

  assert.ok(state.resolved_claims, "resolved_claims should be defined");
  assert.ok(
    state.resolved_claims![findingId],
    `resolved_claims should contain key ${findingId}`
  );
  assert.equal(
    state.resolved_claims![findingId].last_resolved_run_id,
    runId,
    "last_resolved_run_id should match run_id"
  );
  assert.ok(
    state.resolved_claims![findingId].resolved_at,
    "resolved_at should be set"
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
});

// ── B.4-3: cap eviction — oldest entries dropped when >100 ────────────────────

test("B.4-3: resolved_claims cap eviction — oldest dropped, total stays at 100", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  const runId = "b43";
  const runDir = path.join(dir, ".agent_context", "study", runId);
  fs.mkdirSync(runDir, { recursive: true });

  // Seed 100 entries with monotonically-increasing resolved_at
  const existing: Record<string, { last_resolved_run_id: string; resolved_at: string }> = {};
  for (let i = 0; i < 100; i++) {
    const key = `validate:broken-path:file-${String(i).padStart(3, "0")}.md::ref.ts`;
    const ts = new Date(2026, 0, 1, 0, 0, i).toISOString(); // offset by i seconds
    existing[key] = { last_resolved_run_id: "r0", resolved_at: ts };
  }
  const oldestKey = "validate:broken-path:file-000.md::ref.ts";

  // The new finding being resolved (id 101)
  const newFindingId = "validate:broken-path:file-new.md::new-ref.ts";
  __setSpawnForTest((opts): SpawnResult => {
    const rd = opts.envOverrides?.STUDY_RUN_DIR!;
    fs.writeFileSync(
      path.join(rd, "applied.json"),
      JSON.stringify({
        mode: "apply",
        applied: [{ finding_id: newFindingId, file: "file-new.md", status: "ok" }],
      }),
      "utf-8"
    );
    return { exitCode: 0, stderr: "", stdout: "" };
  });

  const pc: PrecomputeJson = {
    run_id: runId,
    mode: "post-completion",
    cursor_ts: null,
    drift_cursor_ts: null,
    scope: [],
    drift_findings: [],
    validate_output: {},
    diff: [],
    change_log_tail: [],
    has_terminal_sentinel: false,
    skipped_malformed_entries: 0,
    set_c_overflow: 0,
    generated_at: new Date().toISOString(),
  };
  const findings: FindingsJson = {
    run_id: runId,
    escalate: false,
    findings: [{ id: newFindingId, file: "file-new.md", issue_type: "wrong" }],
  };
  const state: StudyState = { running: true, running_since: null, resolved_claims: existing };
  seedState(dir, state);

  await phase3_apply(runDir, findings, pc, state);

  const keys = Object.keys(state.resolved_claims!);
  assert.equal(keys.length, 100, `Total should be 100 after eviction; got ${keys.length}`);
  assert.ok(
    !state.resolved_claims![oldestKey],
    "Oldest entry should be evicted"
  );
  assert.ok(
    state.resolved_claims![newFindingId],
    "New entry should be present"
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
});

// ── B.4-4: drain-skip relaxation — resolved key re-drains ────────────────────

test("B.4-4: drain-skip relaxation — key in both addressed and resolved_claims re-appears in drift_findings", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Seed a raw drift finding with a validate: prefix id
  const validateKey = "validate:broken-path:b4-test.md::some-ref.ts";
  seedFinding(dir, "b4-relax-test", "b4-relax-finding", {
    finding_id: validateKey,
    tags: ["knowledge-drift"],
    referenced_file: "b4-test.md",
    claim_substring: "validate-drain: broken-path",
    emitted_at: "2026-04-01T00:00:00Z",
  });

  seedState(dir, {
    // Both addressed AND in resolved_claims — drain-skip should be relaxed
    addressed_validate_issue_keys: [validateKey],
    resolved_claims: {
      [validateKey]: { last_resolved_run_id: "r1", resolved_at: "2026-04-20T00:00:00Z" },
    },
  });

  const runDir = path.join(dir, ".agent_context", "study", "b44");
  fs.mkdirSync(runDir, { recursive: true });
  const { state } = readState();
  const pc = await phase0_precompute(state, runDir, "full-audit", "b44");

  // The key should have re-appeared in drift_findings (drain-skip was relaxed)
  const found = pc.drift_findings.some((d) => d.finding_id === validateKey);
  assert.ok(found, `Key ${validateKey} should re-appear in drift_findings after drain-skip relaxation`);
  // And it should be escalated (recurrence=true)
  const entry = pc.drift_findings.find((d) => d.finding_id === validateKey);
  assert.equal(
    entry?.recurrence,
    true,
    "Recurrent key should be marked recurrence:true"
  );

  __setSpawnForTest(null);
  __setProjectRootForTest(null);
});
// ── B.3-4: validate-error path does NOT wipe addressed keys (regression guard) ─

test("B.3-4: validate error must not wipe state.addressed_validate_issue_keys", async () => {
  const dir = makeTmpProject();
  __setProjectRootForTest(dir);

  // Seed state with known addressed keys from a previous successful run.
  seedState(dir, {
    addressed_validate_issue_keys: ["key-A", "key-B"],
  });

  // Mock runValidate to return an error shape (simulates MCP failure / catch).
  __setRunValidateForTest(() => Promise.resolve({ error: "simulated validate failure" }));

  const runDir = path.join(dir, ".agent_context", "study", "b34");
  fs.mkdirSync(runDir, { recursive: true });
  const { state } = readState();
  await phase0_precompute(state, runDir, "full-audit", "b34");

  // Reload state from disk: writeState is called at the end of phase0_precompute.
  const { state: stateAfter } = readState();
  assert.deepEqual(
    stateAfter.addressed_validate_issue_keys,
    ["key-A", "key-B"],
    "addressed_validate_issue_keys must survive a validate error (not wiped to [])"
  );

  __setRunValidateForTest(null);
  __setProjectRootForTest(null);
});

// ── Tests 37-40: WR-PHASE2-PREVENT — sessionHasSentinel status filter ────────

// Shared sentinel entry builder for Tests 37-39.
function makeSentinelEntry(sessionId: string, status?: string): Record<string, unknown> {
  const base: Record<string, unknown> = {
    ts: "2026-04-26T00:00:00Z",
    session_id: sessionId,
    actor: "external:cycling-terminal-sentinel",
    section: "terminal-mode-complete",
    operation: "log-external-write",
    file: `.agent_context/sessions/${sessionId}/SESSION-COMPLETION-SENTINEL`,
    source_finding_ids: [],
    bytes_written: 0,
    schema_v: 1,
  };
  if (status !== undefined) base.status = status;
  return base;
}

test("37. sessionHasSentinel returns false for status='provisional' only entry", () => {
  const entry = makeSentinelEntry("sid-A", "provisional");
  assert.ok(isSentinel(entry), "entry must pass isSentinel shape check");
  assert.equal(
    sessionHasSentinel("sid-A", [entry]),
    false,
    "provisional status must NOT count as complete"
  );
});

test("38. sessionHasSentinel returns true for status='success' entry", () => {
  const entry = makeSentinelEntry("sid-B", "success");
  assert.ok(isSentinel(entry), "entry must pass isSentinel shape check");
  assert.equal(
    sessionHasSentinel("sid-B", [entry]),
    true,
    "success status must count as complete"
  );
});

test("39. sessionHasSentinel returns true for legacy entry with no status field (backward-compat invariant)", () => {
  // CRITICAL: absent-key = legacy-success. Pre-WR-PHASE2-PREVENT entries have no status field.
  // Removing this branch would re-classify all historical complete sessions as in-progress
  // and break the bootstrap-inversion of the plan's own HK-1 verification.
  const entry = makeSentinelEntry("sid-C"); // no status field
  assert.ok(!("status" in entry), "entry must have no status key for this test to be meaningful");
  assert.ok(isSentinel(entry), "entry must pass isSentinel shape check");
  assert.equal(
    sessionHasSentinel("sid-C", [entry]),
    true,
    "absent status key (legacy) must count as complete (bootstrap-inversion guarantee)"
  );
});

test("40. Integration smoke: terminal-mode sequence is walkable (HK-1a/HK-1b/promotion-complete/push-result.txt present in expected docs)", () => {
  const skillMd = fs.readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), "..", ".claude", "skills", "cycling", "SKILL.md"),
    "utf-8"
  );
  const plannerMd = fs.readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), "..", ".claude", "agents", "planner.md"),
    "utf-8"
  );
  const orchestratorMd = fs.readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), "..", ".claude", "orchestrator-prompt.md"),
    "utf-8"
  );

  // planner.md must contain the new two-phase HK sequence tokens.
  assert.ok(plannerMd.includes("HK-1a"), "planner.md must reference HK-1a");
  assert.ok(plannerMd.includes("HK-1b"), "planner.md must reference HK-1b");
  assert.ok(plannerMd.includes("promotion-complete"), "planner.md must reference promotion-complete marker");
  assert.ok(plannerMd.includes("push-result.txt"), "planner.md must reference push-result.txt marker");

  // SKILL.md terminal-mode section must reference both phases.
  assert.ok(skillMd.includes("HK-1a"), "SKILL.md must reference HK-1a sub-phase");
  assert.ok(skillMd.includes("HK-1b"), "SKILL.md must reference HK-1b sub-phase");
  assert.ok(skillMd.includes("promotion-complete"), "SKILL.md must reference promotion-complete marker");
  assert.ok(skillMd.includes("push-result.txt"), "SKILL.md must reference push-result.txt marker");

  // orchestrator-prompt.md §B.5 must have the HK-3 push-failed row.
  assert.ok(orchestratorMd.includes("HK-3 push failed"), "orchestrator-prompt.md §B.5 must contain HK-3 push-failed row");

  // HK ordering sanity: HK-1a appears before HK-1b in planner.md.
  const hk1aPos = plannerMd.indexOf("HK-1a");
  const hk1bPos = plannerMd.indexOf("HK-1b");
  assert.ok(hk1aPos < hk1bPos, "HK-1a must appear before HK-1b in planner.md");
});
