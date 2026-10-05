import { randomBytes, randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import type {
  Backend,
  LocalSandbox,
  ModelSelection,
  RunStatus,
  RuntimeMode,
} from "./types.ts";

// Every run this machine starts is recorded under ORCHESTRATE_HOME (default ~/.orchestrate), so
// a restarted `run` loop can find its local processes and cloud sessions again. Runs are local
// to the machine that started them, which is also the only place their CLIs are signed in.

export function orchestrateHome(): string {
  return process.env.ORCHESTRATE_HOME || join(homedir(), ".orchestrate");
}

export interface RunRecord {
  version: 1;
  id: string;
  agentId: string;
  name: string;
  backend: Backend;
  mode: RuntimeMode;
  role: string;
  model: ModelSelection | null;
  repoUrl: string;
  startingRef: string;
  branch: string;
  localRepo: string | null;
  localSandbox: LocalSandbox;
  codexEnv: string | null;
  openPR: boolean;
  /** Random token the agent writes into its handoff commit, so a stale commit never counts. */
  nonce: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  deadlineAt: string | null;
  status: RunStatus;
  result: string | null;
  error: string | null;
  durationMs: number | null;
  /** Run directory: prompt, logs, exit code. */
  dir: string;
  /** Where the agent works: a worktree (local) or the clone a cloud session starts from. */
  workdir: string | null;
  pid: number | null;
  /** Backend session or thread id: Claude Code session id, Codex thread id, or cloud task id. */
  sessionId: string | null;
  sessionUrl: string | null;
  /** The run this one continues, for follow-up messages. */
  followUpOf: string | null;
  /** Set once the script has pushed or verified the branch after the run ended. */
  settled: boolean;
  /** Base branch for a draft pull request when openPR is set. */
  prBase: string | null;
  /** Backend scratch values, such as the last branch tip a cloud poll saw. */
  extra: Record<string, string>;
}

export interface AgentRecord {
  version: 1;
  agentId: string;
  name: string;
  createdAt: string;
  backend: Backend;
  mode: RuntimeMode;
  sessionId: string | null;
  sessionUrl: string | null;
  runIds: string[];
  spec: {
    model: ModelSelection | null;
    repoUrl: string;
    startingRef: string;
    branch: string;
    role: string;
    localRepo: string | null;
    localSandbox: LocalSandbox;
    codexEnv: string | null;
    openPR: boolean;
    prBase: string | null;
    maxDurationMinutes: number | null;
  };
}

export function runsDir(): string {
  return join(orchestrateHome(), "runs");
}

export function agentsDir(): string {
  return join(orchestrateHome(), "agents");
}

export function newAgentId(): string {
  return `orch-${randomUUID()}`;
}

export function newRunId(): string {
  return `run-${randomUUID()}`;
}

export function newNonce(): string {
  return randomBytes(9).toString("hex");
}

function writeJsonAtomic(path: string, value: unknown): void {
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`);
  renameSync(tmp, path);
}

function safeId(id: string): string {
  if (!/^[A-Za-z0-9._-]+$/.test(id)) throw new Error(`invalid id: ${id}`);
  return id;
}

export function runDirFor(runId: string): string {
  return join(runsDir(), safeId(runId));
}

export function saveRun(rec: RunRecord): void {
  mkdirSync(rec.dir, { recursive: true });
  writeJsonAtomic(join(rec.dir, "run.json"), rec);
}

export function loadRun(runId: string): RunRecord {
  const path = join(runDirFor(runId), "run.json");
  if (!existsSync(path)) throw new Error(`unknown run ${runId} (no ${path})`);
  return JSON.parse(readFileSync(path, "utf8")) as RunRecord;
}

export function saveAgent(rec: AgentRecord): void {
  mkdirSync(agentsDir(), { recursive: true });
  writeJsonAtomic(join(agentsDir(), `${safeId(rec.agentId)}.json`), rec);
}

export function loadAgent(agentId: string): AgentRecord {
  const path = join(agentsDir(), `${safeId(agentId)}.json`);
  if (!existsSync(path))
    throw new Error(`unknown agent ${agentId} (no ${path})`);
  return JSON.parse(readFileSync(path, "utf8")) as AgentRecord;
}

export function tryLoadAgent(
  agentId: string | null | undefined
): AgentRecord | null {
  if (!agentId) return null;
  try {
    return loadAgent(agentId);
  } catch {
    return null;
  }
}

export function listAgents(): AgentRecord[] {
  if (!existsSync(agentsDir())) return [];
  return readdirSync(agentsDir())
    .filter(name => name.endsWith(".json"))
    .flatMap(name => {
      try {
        return [
          JSON.parse(
            readFileSync(join(agentsDir(), name), "utf8")
          ) as AgentRecord,
        ];
      } catch {
        return [];
      }
    })
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

/** The link a human opens to watch an agent: its cloud session, or its local log file. */
export function agentViewUrl(
  agentId: string | null | undefined
): string | null {
  const agent = tryLoadAgent(agentId);
  if (!agent) return null;
  if (agent.sessionUrl) return agent.sessionUrl;
  const last = agent.runIds.at(-1);
  return last ? `file://${join(runDirFor(last), "out.log")}` : null;
}

const LOCK_STALE_MS = 15 * 60_000;

/**
 * Run `fn` holding the run's lock, so two processes (a `run` loop, `tail`, a kickoff listing)
 * never probe or settle the same run at once. Returns null when another process holds it.
 */
export function withRunLock<T>(runId: string, fn: () => T): T | null {
  const lock = join(runDirFor(runId), "lock");
  try {
    mkdirSync(lock);
  } catch {
    try {
      if (Date.now() - statSync(lock).mtimeMs < LOCK_STALE_MS) return null;
      rmSync(lock, { recursive: true, force: true });
      mkdirSync(lock);
    } catch {
      return null;
    }
  }
  try {
    return fn();
  } finally {
    rmSync(lock, { recursive: true, force: true });
  }
}

/** A run that is still running on `branch` of `repoUrl`, if any: two agents must never share a task branch. */
export function activeRunOnBranch(
  repoUrl: string,
  branch: string
): RunRecord | null {
  if (!existsSync(runsDir())) return null;
  for (const id of readdirSync(runsDir())) {
    try {
      const rec = JSON.parse(
        readFileSync(join(runsDir(), id, "run.json"), "utf8")
      ) as RunRecord;
      if (
        rec.status === "running" &&
        rec.repoUrl === repoUrl &&
        rec.branch === branch
      )
        return rec;
    } catch {
      // A half-written or foreign directory; not a run.
    }
  }
  return null;
}
