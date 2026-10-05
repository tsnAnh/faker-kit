import type { AgentRecord, RunRecord } from "./registry.ts";
import type { RunOperation, RunStatus, SDKMessage } from "./types.ts";

export interface Probe {
  status: RunStatus;
  result?: string;
  error?: string;
  /** A settle step that failed for a reason worth retrying (network, a CLI timeout). */
  retry?: boolean;
}

/** One way to run an agent: Claude Code or Codex, in the cloud or as a local process. */
export interface BackendImpl {
  readonly label: string;
  /** How often `wait` polls. Cloud backends poll GitHub or a CLI, so they poll slowly. */
  readonly pollMs: number;
  readonly unsupported: Partial<Record<RunOperation, string>>;
  /** Text appended to every prompt: how this agent delivers its work and its handoff. */
  delivery(rec: RunRecord): string;
  /** Start the agent. Fills in pid, sessionId, sessionUrl, workdir on `rec`. */
  start(rec: RunRecord, prompt: string, agent: AgentRecord): Promise<void>;
  /** Where the run is now. Called repeatedly; must be cheap enough to poll. */
  probe(rec: RunRecord): Probe;
  /** After a run finishes: push the branch, apply a cloud diff, open a PR. May turn the run into an error. */
  settle(rec: RunRecord): Probe | null;
  /** New events from the agent's log since `offset` (bytes). Local backends only. */
  events?(
    rec: RunRecord,
    offset: number
  ): { events: SDKMessage[]; offset: number };
  cancel(rec: RunRecord): void;
  /** Continue the agent's session with another message, as a new run. */
  followUp(
    prev: RunRecord,
    next: RunRecord,
    text: string,
    agent: AgentRecord
  ): Promise<void>;
}

export function deadlinePassed(rec: RunRecord, now = Date.now()): boolean {
  return !!rec.deadlineAt && now > Date.parse(rec.deadlineAt);
}

export function deadlineError(rec: RunRecord): string {
  const minutes =
    rec.deadlineAt && rec.startedAt
      ? Math.round(
          (Date.parse(rec.deadlineAt) - Date.parse(rec.startedAt)) / 60_000
        )
      : null;
  return `cap-hit: no handoff within ${minutes ?? "the"} minutes (maxDurationMinutes); the run was stopped or abandoned`;
}
