import { setTimeout as sleep } from "node:timers/promises";

import type { BackendImpl, Probe } from "./backend.ts";
import { claudeCloud, codexCloud } from "./cloud.ts";
import { claudeLocal, codexLocal } from "./local.ts";
import {
  type AgentRecord,
  activeRunOnBranch,
  agentViewUrl,
  listAgents,
  loadAgent,
  loadRun,
  newAgentId,
  newNonce,
  newRunId,
  type RunRecord,
  runDirFor,
  saveAgent,
  saveRun,
  withRunLock,
} from "./registry.ts";
import type {
  AgentCreateOptions,
  Backend,
  Run,
  RunOperation,
  RunResult,
  RunStatus,
  RuntimeMode,
  SDKAgent,
  SDKMessage,
} from "./types.ts";

export { agentViewUrl } from "./registry.ts";
export type * from "./types.ts";

/** Errors the runtime raises for bad input or a missing CLI, as opposed to a run that failed. */
export class AgentRuntimeError extends Error {
  override name = "AgentRuntimeError";
}

const DEFAULT_MAX_MINUTES: Record<RuntimeMode, number> = {
  cloud: 240,
  local: 180,
};
const PLANNER_MAX_MINUTES = 24 * 60;

function pollOverride(): number | null {
  const raw = process.env.ORCHESTRATE_POLL_MS;
  const parsed = raw ? Number.parseInt(raw, 10) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function backendFor(backend: Backend, mode: RuntimeMode): BackendImpl {
  if (backend === "claude") return mode === "cloud" ? claudeCloud : claudeLocal;
  if (backend === "codex") return mode === "cloud" ? codexCloud : codexLocal;
  throw new AgentRuntimeError(`unknown backend ${String(backend)}`);
}

function isTerminal(status: RunStatus): boolean {
  return status !== "running";
}

function resultOf(rec: RunRecord): RunResult {
  return {
    id: rec.id,
    status: rec.status,
    result: rec.result ?? undefined,
    durationMs: rec.durationMs ?? undefined,
    error: rec.error ?? undefined,
    git: { branches: [{ branch: rec.branch, repoUrl: rec.repoUrl }] },
    model: rec.model,
  };
}

const MAX_SETTLE_ATTEMPTS = 5;

/** A run's own poll interval, so several callers (wait, stream, the watchdog) share one probe. */
function minProbeGapMs(rec: RunRecord): number {
  return Math.floor(
    (pollOverride() ?? backendFor(rec.backend, rec.mode).pollMs) / 2
  );
}

/**
 * Probe once and persist what changed, under the run's lock. Settles a finished run once; a
 * settle that fails for a transient reason is retried on later probes, up to five times.
 * Returns the freshest record whether or not this call probed.
 */
function refresh(rec: RunRecord, opts: { force?: boolean } = {}): RunRecord {
  if (isTerminal(rec.status)) return rec;
  const locked = withRunLock(rec.id, () => {
    const current = loadRun(rec.id);
    if (isTerminal(current.status)) return current;
    const last = Number(current.extra.lastProbeAt ?? 0);
    if (!opts.force && Date.now() - last < minProbeGapMs(current))
      return current;
    current.extra.lastProbeAt = String(Date.now());
    const impl = backendFor(current.backend, current.mode);
    let probe = impl.probe(current);
    if (probe.status === "finished" && !current.settled) {
      current.result = probe.result ?? current.result;
      let settled: Probe | null;
      try {
        settled = impl.settle(current);
      } catch (err) {
        settled = {
          status: "error",
          error: `settling the run failed: ${(err as Error).message}`,
          retry: true,
        };
      }
      const attempts = Number(current.extra.settleAttempts ?? 0) + 1;
      current.extra.settleAttempts = String(attempts);
      if (
        settled?.status === "error" &&
        settled.retry &&
        attempts < MAX_SETTLE_ATTEMPTS
      ) {
        current.error = `${settled.error} (will retry, attempt ${attempts} of ${MAX_SETTLE_ATTEMPTS})`;
        saveRun(current);
        return current;
      }
      current.settled = true;
      probe = settled
        ? { ...settled, result: settled.result ?? probe.result }
        : probe;
    }
    if (isTerminal(probe.status)) {
      current.status = probe.status;
      current.result = probe.result ?? current.result;
      current.error = probe.error ?? null;
      current.finishedAt = new Date().toISOString();
      current.durationMs = current.startedAt
        ? Date.parse(current.finishedAt) - Date.parse(current.startedAt)
        : null;
    }
    saveRun(current);
    return current;
  });
  return locked ?? loadRun(rec.id);
}

class RunHandle implements Run {
  private rec: RunRecord;

  constructor(rec: RunRecord) {
    this.rec = rec;
  }

  get id(): string {
    return this.rec.id;
  }

  get agentId(): string {
    return this.rec.agentId;
  }

  get status(): RunStatus {
    return this.rec.status;
  }

  private impl(): BackendImpl {
    return backendFor(this.rec.backend, this.rec.mode);
  }

  supports(op: RunOperation): boolean {
    return !this.impl().unsupported[op];
  }

  unsupportedReason(op: RunOperation): string | undefined {
    return this.impl().unsupported[op];
  }

  async wait(): Promise<RunResult> {
    const pollMs = pollOverride() ?? this.impl().pollMs;
    for (;;) {
      this.rec = refresh(loadRun(this.rec.id));
      if (isTerminal(this.rec.status)) return resultOf(this.rec);
      await sleep(pollMs);
    }
  }

  async *stream(): AsyncGenerator<SDKMessage> {
    const impl = this.impl();
    const pollMs = pollOverride() ?? Math.min(impl.pollMs, 1_000);
    if (!impl.events) {
      // Cloud runs show no events; a heartbeat per poll keeps watchers from calling them idle.
      const heartbeat = pollOverride() ?? impl.pollMs;
      while (!isTerminal(refresh(loadRun(this.rec.id)).status)) {
        yield { type: "status", status: "running" };
        await sleep(heartbeat);
      }
      return;
    }
    let offset = 0;
    for (;;) {
      const read = impl.events(this.rec, offset);
      offset = read.offset;
      for (const event of read.events) yield event;
      if (
        isTerminal(refresh(loadRun(this.rec.id)).status) &&
        read.events.length === 0
      ) {
        const last = impl.events(this.rec, offset);
        for (const event of last.events) yield event;
        return;
      }
      await sleep(pollMs);
    }
  }

  async cancel(): Promise<void> {
    const impl = this.impl();
    const loaded = loadRun(this.rec.id);
    const reason = impl.unsupported.cancel;
    if (reason)
      throw new AgentRuntimeError(
        loaded.sessionUrl ? `${reason}: ${loaded.sessionUrl}` : reason
      );
    const done = withRunLock(this.rec.id, () => {
      const rec = loadRun(this.rec.id);
      if (isTerminal(rec.status)) return rec;
      impl.cancel(rec);
      rec.status = "cancelled";
      rec.finishedAt = new Date().toISOString();
      rec.error = "cancelled by operator";
      saveRun(rec);
      return rec;
    });
    if (!done)
      throw new AgentRuntimeError(
        `${this.rec.id} is busy in another process; try again`
      );
    this.rec = done;
  }
}

function newRunRecord(
  agent: AgentRecord,
  followUpOf: string | null
): RunRecord {
  const id = newRunId();
  const now = new Date();
  const maxMinutes =
    agent.spec.maxDurationMinutes ??
    (agent.spec.role === "subplanner" || agent.spec.role === "root"
      ? PLANNER_MAX_MINUTES
      : DEFAULT_MAX_MINUTES[agent.mode]);
  return {
    version: 1,
    id,
    agentId: agent.agentId,
    name: agent.name,
    backend: agent.backend,
    mode: agent.mode,
    role: agent.spec.role,
    model: agent.spec.model,
    repoUrl: agent.spec.repoUrl,
    startingRef: agent.spec.startingRef,
    branch: agent.spec.branch,
    localRepo: agent.spec.localRepo,
    localSandbox: agent.spec.localSandbox,
    codexEnv: agent.spec.codexEnv,
    openPR: agent.spec.openPR,
    prBase: agent.spec.prBase,
    nonce: newNonce(),
    createdAt: now.toISOString(),
    startedAt: null,
    finishedAt: null,
    deadlineAt: new Date(now.getTime() + maxMinutes * 60_000).toISOString(),
    status: "running",
    result: null,
    error: null,
    durationMs: null,
    dir: runDirFor(id),
    workdir: null,
    pid: null,
    sessionId: null,
    sessionUrl: null,
    followUpOf,
    settled: false,
    extra: {},
  };
}

function promptText(prompt: string | { text: string }): string {
  return typeof prompt === "string" ? prompt : prompt.text;
}

class AgentHandle implements SDKAgent {
  constructor(private readonly record: AgentRecord) {}

  get agentId(): string {
    return this.record.agentId;
  }

  async send(prompt: string | { text: string }): Promise<Run> {
    const agent = loadAgent(this.record.agentId);
    const impl = backendFor(agent.backend, agent.mode);
    const previous = agent.runIds.at(-1)
      ? loadRun(agent.runIds.at(-1) as string)
      : null;
    if (previous && !isTerminal(refresh(previous, { force: true }).status)) {
      throw new AgentRuntimeError(
        `agent_busy: ${agent.agentId} is still running ${previous.id}`
      );
    }
    if (!previous) {
      const holder = activeRunOnBranch(agent.spec.repoUrl, agent.spec.branch);
      if (holder && !isTerminal(refresh(holder, { force: true }).status)) {
        throw new AgentRuntimeError(
          `${agent.spec.branch} is in use by ${holder.name} (run ${holder.id}); stop it or wait for it before starting another agent on that branch`
        );
      }
    }
    const rec = newRunRecord(agent, previous?.id ?? null);
    saveRun(rec);
    try {
      if (previous) {
        if (impl.unsupported.followUp)
          throw new AgentRuntimeError(impl.unsupported.followUp);
        await impl.followUp(previous, rec, promptText(prompt), agent);
      } else {
        await impl.start(
          rec,
          `${promptText(prompt)}${impl.delivery(rec)}`,
          agent
        );
      }
    } catch (err) {
      rec.status = "error";
      rec.error = (err as Error).message;
      rec.finishedAt = new Date().toISOString();
      saveRun(rec);
      throw err instanceof AgentRuntimeError
        ? err
        : new AgentRuntimeError(rec.error);
    }
    saveRun(rec);
    agent.runIds.push(rec.id);
    agent.sessionId = rec.sessionId ?? agent.sessionId;
    agent.sessionUrl = rec.sessionUrl ?? agent.sessionUrl;
    saveAgent(agent);
    return new RunHandle(rec);
  }

  async [Symbol.asyncDispose](): Promise<void> {}
}

function create(opts: AgentCreateOptions): AgentHandle {
  const repo = opts.cloud.repos[0];
  if (!repo)
    throw new AgentRuntimeError(
      "Agent.create needs cloud.repos[0] with url and startingRef"
    );
  const rt = opts.runtime ?? {};
  const backend = rt.backend ?? "claude";
  const role = rt.role ?? "worker";
  const mode: RuntimeMode =
    role === "subplanner" || role === "root" ? "local" : (rt.mode ?? "cloud");
  if (!rt.branch)
    throw new AgentRuntimeError(
      "Agent.create needs runtime.branch: the branch the agent pushes"
    );
  const record: AgentRecord = {
    version: 1,
    agentId: newAgentId(),
    name: opts.name ?? "agent",
    createdAt: new Date().toISOString(),
    backend,
    mode,
    sessionId: null,
    sessionUrl: null,
    runIds: [],
    spec: {
      model: opts.model ?? null,
      repoUrl: repo.url,
      startingRef: repo.startingRef,
      branch: rt.branch,
      role,
      localRepo: rt.localRepo ?? null,
      localSandbox: rt.localSandbox ?? "full",
      codexEnv: rt.codexEnv ?? null,
      openPR: opts.cloud.autoCreatePR ?? false,
      prBase: rt.prBase ?? null,
      maxDurationMinutes: rt.maxDurationMinutes ?? null,
    },
  };
  saveAgent(record);
  return new AgentHandle(record);
}

export const Agent = {
  async create(opts: AgentCreateOptions): Promise<SDKAgent> {
    return create(opts);
  },

  async resume(agentId: string): Promise<SDKAgent> {
    return new AgentHandle(loadAgent(agentId));
  },

  async getRun(
    runId: string,
    _opts: { agentId?: string; runtime?: string; apiKey?: string } = {}
  ): Promise<Run> {
    return new RunHandle(refresh(loadRun(runId)));
  },

  async list(
    _opts: { runtime?: string; limit?: number } = {}
  ): Promise<{ items: Array<Record<string, unknown>> }> {
    const limit = _opts.limit ?? 50;
    return {
      items: listAgents()
        .slice(0, limit)
        .map(agent => {
          const last = agent.runIds.at(-1);
          let latestRun: Record<string, unknown> | null = null;
          if (last) {
            try {
              const rec = refresh(loadRun(last));
              latestRun = { id: rec.id, status: rec.status };
            } catch {
              latestRun = null;
            }
          }
          return {
            agentId: agent.agentId,
            name: agent.name,
            createdAt: agent.createdAt,
            latestRun,
          };
        }),
    };
  },

  async listRuns(
    agentId: string,
    _opts: { runtime?: string; limit?: number } = {}
  ): Promise<{ items: Array<{ id: string; status: RunStatus }> }> {
    const agent = loadAgent(agentId);
    const ids = [...agent.runIds].reverse().slice(0, _opts.limit ?? 20);
    return {
      items: ids
        .map(id => refresh(loadRun(id)))
        .map(rec => ({ id: rec.id, status: rec.status })),
    };
  },
};

/** Cancel a run by id, for tools that walk a tree outside a loaded workspace. */
export async function cancelRun(runId: string): Promise<void> {
  const run = await Agent.getRun(runId);
  await run.cancel();
}

export function viewUrl(agentId: string | null | undefined): string | null {
  return agentViewUrl(agentId);
}

/** Where a run works and logs, for the operator. */
export function describeRun(runId: string): {
  workdir: string | null;
  log: string;
  sessionUrl: string | null;
  branch: string;
} {
  const rec = loadRun(runId);
  return {
    workdir: rec.workdir,
    log: `${rec.dir}/out.log`,
    sessionUrl: rec.sessionUrl,
    branch: rec.branch,
  };
}
