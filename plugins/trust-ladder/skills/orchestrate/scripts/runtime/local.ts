import { randomUUID } from "node:crypto";
import {
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  readSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

import {
  type BackendImpl,
  deadlineError,
  deadlinePassed,
  type Probe,
} from "./backend.ts";
import {
  commitAll,
  ensureDraftPr,
  hasUnpushedCommits,
  prepareWorktree,
  pushBranch,
  repoKey,
  repoTopLevel,
} from "./git.ts";
import {
  isSameProcess,
  killGroup,
  onPath,
  processStartTime,
  readExitCode,
  spawnDetached,
} from "./process.ts";
import { orchestrateHome, type RunRecord } from "./registry.ts";
import type { ModelSelection, SDKMessage } from "./types.ts";

// Local runs: a headless Claude Code (`claude -p`) or Codex (`codex exec`) process in its own git
// worktree, detached so it survives the loop that started it. Its final message is the handoff.

export const OUT = "out.log";
const ERR = "err.log";
const EXIT = "exit";
const PROMPT = "prompt.md";
const LAST_MESSAGE = "last-message.md";

function file(rec: RunRecord, name: string): string {
  return join(rec.dir, name);
}

function param(model: ModelSelection | null, id: string): string | undefined {
  return model?.params?.find(p => p.id === id)?.value;
}

function tail(path: string, chars = 600): string {
  if (!existsSync(path)) return "";
  const text = readFileSync(path, "utf8");
  return text.slice(-chars).trim();
}

function readJsonLines(path: string): Array<Record<string, unknown>> {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .flatMap(line => {
      const trimmed = line.trim();
      if (!trimmed.startsWith("{")) return [];
      try {
        return [JSON.parse(trimmed) as Record<string, unknown>];
      } catch {
        return [];
      }
    });
}

/** Read whole lines appended to `path` since `offset`. A partial last line waits for the next read. */
export function readNewLines(
  path: string,
  offset: number
): { lines: string[]; offset: number } {
  if (!existsSync(path)) return { lines: [], offset };
  const size = statSync(path).size;
  if (size <= offset) return { lines: [], offset };
  const fd = openSync(path, "r");
  try {
    const buf = Buffer.alloc(size - offset);
    readSync(fd, buf, 0, buf.length, offset);
    const text = buf.toString("utf8");
    const end = text.lastIndexOf("\n");
    if (end < 0) return { lines: [], offset };
    return {
      lines: text.slice(0, end).split("\n"),
      offset: offset + Buffer.byteLength(text.slice(0, end + 1)),
    };
  } finally {
    closeSync(fd);
  }
}

function worktreePath(rec: RunRecord): string {
  return join(orchestrateHome(), "worktrees", repoKey(rec.repoUrl), rec.id);
}

function localRepoFor(rec: RunRecord): string {
  return rec.localRepo ?? repoTopLevel();
}

function requireBinary(binary: string): void {
  if (!onPath(binary)) {
    throw new Error(
      `${binary} is not on PATH; install it and sign in before running local ${binary} agents`
    );
  }
}

function localDelivery(rec: RunRecord): string {
  const pr = rec.openPR
    ? `\n- After pushing, open a draft pull request for \`${rec.branch}\` with \`gh pr create --draft\`. Do NOT merge it.`
    : "";
  return `

## Delivery (local run)

- You are in a git worktree with \`${rec.branch}\` checked out. Commit your work on it.
- Push it before you finish: \`git push --force origin HEAD:refs/heads/${rec.branch}\` (the branch belongs to this task; an earlier attempt may have left it). If you cannot push, say so in your handoff; the orchestrator commits and pushes what you leave in the worktree.${pr}
- Your final message is your handoff. The orchestrator reads nothing else.
`;
}

function startedFields(rec: RunRecord): void {
  rec.startedAt = new Date().toISOString();
  rec.status = "running";
}

/** After a local run: commit what the agent left uncommitted, push the branch, open the draft PR. */
function settleLocal(rec: RunRecord): Probe | null {
  if (!rec.workdir || !existsSync(rec.workdir)) return null;
  const leftover = commitAll(
    rec.workdir,
    `${rec.name}: work the agent left uncommitted\n`
  );
  if (!leftover.ok) {
    return {
      status: "error",
      error: `could not commit what the agent left in ${rec.workdir}: ${leftover.detail}`,
    };
  }
  if (hasUnpushedCommits(rec.workdir, rec.branch, rec.repoUrl)) {
    const pushed = pushBranch(rec.workdir, rec.repoUrl, rec.branch);
    if (!pushed.ok) {
      return {
        status: "error",
        error: `the run finished but pushing ${rec.branch} failed: ${pushed.detail}`,
        retry: true,
      };
    }
  }
  ensureDraftPr(rec, rec.result ?? "");
  return null;
}

type ResultParser = (rec: RunRecord, exitCode: number | null) => Probe | null;

function exitedWithout(rec: RunRecord, code: number): Probe {
  return {
    status: "error",
    error: `process exited ${code} without a final message: ${tail(file(rec, ERR)) || tail(file(rec, OUT))}`,
  };
}

function processProbe(rec: RunRecord, parse: ResultParser): Probe {
  // Read the exit code once, before parsing, so a process that exits between two reads is not
  // seen as both still running and finished without output.
  const code = readExitCode(file(rec, EXIT));
  const parsed = parse(rec, code);
  if (parsed) return parsed;
  if (code !== null) return exitedWithout(rec, code);
  if (!isSameProcess(rec.pid, rec.extra.pidStart)) {
    // The wrapper may have written its exit code just after the first read.
    const late = readExitCode(file(rec, EXIT));
    if (late !== null) return parse(rec, late) ?? exitedWithout(rec, late);
    return {
      status: "error",
      error: `process ${rec.pid} is gone and left no exit code (killed or the machine restarted)`,
    };
  }
  if (deadlinePassed(rec)) {
    killGroup(rec.pid);
    return { status: "error", error: deadlineError(rec) };
  }
  return { status: "running" };
}

// Claude Code ---------------------------------------------------------------------------------

function claudeArgs(rec: RunRecord, extra: string[]): string[] {
  const args = [
    "claude",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    ...extra,
  ];
  args.push(
    "--permission-mode",
    rec.localSandbox === "workspace" ? "auto" : "bypassPermissions"
  );
  if (rec.model?.id) args.push("--model", rec.model.id);
  const effort = param(rec.model, "effort");
  if (effort) args.push("--effort", effort);
  return args;
}

function parseClaudeResult(
  rec: RunRecord,
  _exitCode: number | null
): Probe | null {
  const result = readJsonLines(file(rec, OUT)).findLast(
    line => line.type === "result"
  );
  if (!result) return null;
  const text = typeof result.result === "string" ? result.result : "";
  if (
    result.is_error === true ||
    (typeof result.subtype === "string" && result.subtype !== "success")
  ) {
    return {
      status: "error",
      error: `${String(result.subtype ?? "error")}: ${text}`.trim(),
      result: text,
    };
  }
  return { status: "finished", result: text };
}

function claudeEvents(
  rec: RunRecord,
  offset: number
): { events: SDKMessage[]; offset: number } {
  const read = readNewLines(file(rec, OUT), offset);
  const events: SDKMessage[] = [];
  for (const line of read.lines) {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }
    if (parsed.type !== "assistant") continue;
    const content = ((parsed.message as { content?: unknown[] } | undefined)
      ?.content ?? []) as Array<Record<string, unknown>>;
    const text = content
      .filter(b => b.type === "text")
      .map(b => String(b.text ?? ""))
      .join("");
    if (text)
      events.push({
        type: "assistant",
        message: { content: [{ type: "text", text }] },
      });
    for (const block of content) {
      if (block.type === "tool_use") {
        events.push({
          type: "tool_call",
          name: String(block.name ?? "tool"),
          status: "started",
          call_id: String(block.id ?? ""),
        });
      }
      if (block.type === "thinking" && typeof block.thinking === "string") {
        events.push({ type: "thinking", text: block.thinking });
      }
    }
  }
  return { events, offset: read.offset };
}

function spawnIn(rec: RunRecord, argv: string[], prompt: string): void {
  writeFileSync(file(rec, PROMPT), prompt);
  const proc = spawnDetached({
    argv,
    cwd: rec.workdir as string,
    stdinFile: file(rec, PROMPT),
    stdoutFile: file(rec, OUT),
    stderrFile: file(rec, ERR),
    exitFile: file(rec, EXIT),
    env: {
      ORCHESTRATE_HOME: orchestrateHome(),
      ORCHESTRATE_RUN_ID: rec.id,
      ORCHESTRATE_AGENT_ID: rec.agentId,
    },
  });
  rec.pid = proc.pid;
  rec.extra.pidStart = processStartTime(proc.pid) ?? "";
  startedFields(rec);
}

export const claudeLocal: BackendImpl = {
  label: "Claude Code (local)",
  pollMs: 3_000,
  unsupported: {},
  delivery: localDelivery,
  async start(rec, prompt) {
    requireBinary("claude");
    rec.workdir = worktreePath(rec);
    prepareWorktree({
      repo: localRepoFor(rec),
      repoUrl: rec.repoUrl,
      startingRef: rec.startingRef,
      branch: rec.branch,
      path: rec.workdir,
      runId: rec.id,
    });
    rec.sessionId = randomUUID();
    spawnIn(rec, claudeArgs(rec, ["--session-id", rec.sessionId]), prompt);
  },
  probe: rec => processProbe(rec, parseClaudeResult),
  settle: settleLocal,
  events: claudeEvents,
  cancel(rec) {
    if (isSameProcess(rec.pid, rec.extra.pidStart)) killGroup(rec.pid);
  },
  async followUp(prev, next, text) {
    requireBinary("claude");
    if (!prev.sessionId)
      throw new Error(`${prev.id} has no session to continue`);
    next.workdir = prev.workdir;
    next.sessionId = prev.sessionId;
    spawnIn(next, claudeArgs(next, ["--resume", prev.sessionId]), text);
  },
};

// Codex ---------------------------------------------------------------------------------------

function codexArgs(rec: RunRecord, resumeThread?: string): string[] {
  const args = [
    "codex",
    "exec",
    "--json",
    "-o",
    file(rec, LAST_MESSAGE),
    "-C",
    rec.workdir as string,
  ];
  if (rec.localSandbox === "workspace") args.push("-s", "workspace-write");
  else args.push("--dangerously-bypass-approvals-and-sandbox");
  if (rec.model?.id) args.push("-m", rec.model.id);
  const effort = param(rec.model, "effort");
  if (effort)
    args.push(
      "-c",
      `model_reasoning_effort="${effort.replace(/[^a-z-]/gi, "")}"`
    );
  if (resumeThread) args.push("resume", resumeThread);
  args.push("-");
  return args;
}

function parseCodexResult(rec: RunRecord, code: number | null): Probe | null {
  const lines = readJsonLines(file(rec, OUT));
  const thread = lines.find(l => l.type === "thread.started");
  if (
    thread &&
    typeof thread.thread_id === "string" &&
    rec.sessionId !== thread.thread_id
  ) {
    rec.sessionId = thread.thread_id;
  }
  if (code === null) return null;
  const failed = lines.findLast(
    l => l.type === "turn.failed" || l.type === "error"
  );
  const last = existsSync(file(rec, LAST_MESSAGE))
    ? readFileSync(file(rec, LAST_MESSAGE), "utf8")
    : null;
  if (code === 0 && last !== null) return { status: "finished", result: last };
  const why = failed
    ? JSON.stringify(failed.error ?? failed.message ?? failed)
    : tail(file(rec, ERR));
  return {
    status: "error",
    error: `codex exec exited ${code}: ${why}`,
    result: last ?? undefined,
  };
}

function codexEvents(
  rec: RunRecord,
  offset: number
): { events: SDKMessage[]; offset: number } {
  const read = readNewLines(file(rec, OUT), offset);
  const events: SDKMessage[] = [];
  for (const line of read.lines) {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }
    if (parsed.type !== "item.completed" && parsed.type !== "item.started")
      continue;
    const item = (parsed.item ?? {}) as Record<string, unknown>;
    if (item.type === "agent_message" && parsed.type === "item.completed") {
      events.push({
        type: "assistant",
        message: { content: [{ type: "text", text: String(item.text ?? "") }] },
      });
    } else if (item.type === "reasoning" && parsed.type === "item.completed") {
      events.push({ type: "thinking", text: String(item.text ?? "") });
    } else if (
      parsed.type === "item.started" &&
      typeof item.type === "string"
    ) {
      events.push({
        type: "tool_call",
        name: item.type,
        status: "started",
        call_id: String(item.id ?? ""),
      });
    }
  }
  return { events, offset: read.offset };
}

export const codexLocal: BackendImpl = {
  label: "Codex (local)",
  pollMs: 3_000,
  unsupported: {},
  delivery: localDelivery,
  async start(rec, prompt) {
    requireBinary("codex");
    rec.workdir = worktreePath(rec);
    prepareWorktree({
      repo: localRepoFor(rec),
      repoUrl: rec.repoUrl,
      startingRef: rec.startingRef,
      branch: rec.branch,
      path: rec.workdir,
      runId: rec.id,
    });
    spawnIn(rec, codexArgs(rec), prompt);
  },
  probe: rec => processProbe(rec, parseCodexResult),
  settle: settleLocal,
  events: codexEvents,
  cancel(rec) {
    if (isSameProcess(rec.pid, rec.extra.pidStart)) killGroup(rec.pid);
  },
  async followUp(prev, next, text) {
    requireBinary("codex");
    if (!prev.sessionId) parseCodexResult(prev, readExitCode(file(prev, EXIT)));
    const thread = prev.sessionId;
    if (!thread) throw new Error(`${prev.id} has no Codex thread to continue`);
    next.workdir = prev.workdir;
    next.sessionId = thread;
    spawnIn(next, codexArgs(next, thread), text);
  },
};
