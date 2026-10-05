// The agent runtime the orchestrate engine drives: Claude Code or Codex, in the cloud or as a
// local headless process. The engine sees one small API (Agent.create, agent.send, Agent.getRun,
// run.wait, run.stream, run.cancel); each backend implements it with the CLIs those tools ship.

export type Backend = "claude" | "codex";
export type RuntimeMode = "cloud" | "local";
export type LocalSandbox = "full" | "workspace";

export type RunStatus = "running" | "finished" | "error" | "cancelled";

export interface ModelSelection {
  /** What the CLI's model flag takes: an alias such as `opus`, or a full model id. */
  id: string;
  /** Extra settings. `effort` maps to `--effort` (Claude Code) or `model_reasoning_effort` (Codex). */
  params?: Array<{ id: string; value: string }>;
}

export interface RuntimeSpec {
  backend: Backend;
  mode: RuntimeMode;
  /** Codex cloud environment id. Required for codex in the cloud. */
  codexEnv?: string;
  /** What a local process may do. `full` runs without approvals or sandbox. */
  localSandbox?: LocalSandbox;
  /** The branch this agent must push its work to. */
  branch: string;
  /** Wall-clock budget; the run becomes an error past it. */
  maxDurationMinutes?: number;
  /** Planners must run where they can start other agents: a local process. */
  role?: "worker" | "subplanner" | "verifier" | "root";
  /** A local clone of the repository to add worktrees from. Defaults to the current directory's repository. */
  localRepo?: string;
  /** Base branch for the draft pull request of a task with openPR. */
  prBase?: string;
}

export interface AgentCreateOptions {
  name?: string;
  model?: ModelSelection;
  cloud: {
    repos: Array<{ url: string; startingRef: string }>;
    autoCreatePR?: boolean;
  };
  runtime?: Partial<RuntimeSpec>;
  /** Accepted and ignored, for callers written against a hosted agent API. */
  apiKey?: string;
}

export interface RunGitInfo {
  branches: Array<{ branch?: string | null; repoUrl?: string }>;
}

export interface RunResult {
  id: string;
  status: RunStatus;
  /** The agent's final message: its handoff. */
  result?: string;
  durationMs?: number;
  error?: string;
  git?: RunGitInfo;
  model?: ModelSelection | null;
}

export interface ContentBlock {
  type: "text" | "tool_use" | "thinking";
  text?: string;
  name?: string;
}

export type SDKMessage =
  | { type: "assistant"; message: { content: ContentBlock[] } }
  | { type: "thinking"; text: string }
  | {
      type: "tool_call";
      name?: string;
      status?: string;
      call_id?: string;
      args?: unknown;
    }
  | { type: "status"; status: string }
  | { type: "task"; text?: string }
  | { type: "system"; [key: string]: unknown };

export type RunOperation = "cancel" | "stream" | "followUp";

export interface Run {
  readonly id: string;
  readonly agentId: string;
  readonly status: RunStatus;
  wait(): Promise<RunResult>;
  stream(): AsyncIterable<SDKMessage>;
  cancel(): Promise<void>;
  supports(op: RunOperation): boolean;
  unsupportedReason(op: RunOperation): string | undefined;
}

export interface SDKAgent {
  readonly agentId: string;
  send(prompt: string | { text: string }): Promise<Run>;
  [Symbol.asyncDispose](): Promise<void>;
}
