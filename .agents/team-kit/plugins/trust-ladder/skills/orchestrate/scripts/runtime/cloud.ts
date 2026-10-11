import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import {
  type BackendImpl,
  deadlineError,
  deadlinePassed,
  type Probe,
} from "./backend.ts";
import {
  applyPatch,
  cloneAt,
  commitAll,
  ensureDraftPr,
  prepareWorktree,
  pushBranch,
  recentMessages,
  remoteTip,
  repoKey,
  repoTopLevel,
} from "./git.ts";
import {
  isAlive,
  killGroup,
  onPath,
  readExitCode,
  run,
  runStatus,
  spawnDetached,
} from "./process.ts";
import { orchestrateHome, type RunRecord } from "./registry.ts";

// Cloud runs. Neither Claude Code nor Codex offers a command that reads a cloud session's final
// message, so the handoff travels through git:
//
// - Claude Code cloud sessions can push. The agent pushes its branch with one last, empty commit
//   whose message is `orchestrate-handoff <nonce>` followed by the handoff. The loop polls the
//   branch and reads that commit. A session that never delivers runs into maxDurationMinutes.
// - Codex cloud tasks cannot push. The agent writes its handoff to `.orchestrate-handoff.md`.
//   When `codex cloud status` reports READY, the loop applies the task's diff to the task's
//   branch in a local worktree, takes the handoff file out, commits, and pushes.

export const HANDOFF_COMMIT_PREFIX = "orchestrate-handoff";
export const HANDOFF_FILE = ".orchestrate-handoff.md";
const SESSION_RE =
  /https:\/\/claude\.ai\/code\/[A-Za-z0-9_-]+|\b(?:session|cse)_[A-Za-z0-9]{6,}\b/;
const CODEX_TASK_RE = /https?:\/\/\S+\/tasks\/([A-Za-z0-9_-]+)/;
/** One argument to `claude --cloud`. Linux caps a single argument at 128 KiB. */
export const MAX_CLOUD_PROMPT_BYTES = 120_000;
const SESSION_WAIT_MS = Number(
  process.env.ORCHESTRATE_SESSION_WAIT_MS ?? 180_000
);

function file(rec: RunRecord, name: string): string {
  return join(rec.dir, name);
}

function requireBinary(binary: string): void {
  if (!onPath(binary))
    throw new Error(
      `${binary} is not on PATH; install it and sign in before starting ${binary} cloud agents`
    );
}

function started(rec: RunRecord): void {
  rec.startedAt = new Date().toISOString();
  rec.status = "running";
}

/** The handoff in a `orchestrate-handoff <nonce>` commit message, or null when it is not one of ours. */
export function handoffFromCommitMessage(
  body: string,
  nonce: string
): string | null {
  const [first, ...rest] = body.replace(/\r\n/g, "\n").split("\n");
  if (first.trim() !== `${HANDOFF_COMMIT_PREFIX} ${nonce}`) return null;
  return rest.join("\n").replace(/^\n+/, "").trimEnd();
}

// Claude Code cloud ---------------------------------------------------------------------------

export const claudeCloud: BackendImpl = {
  label: "Claude Code (cloud)",
  pollMs: 60_000,
  unsupported: {
    cancel:
      "Claude Code has no command that stops a cloud session; stop it from its page on claude.ai/code",
    stream:
      "a cloud session's events are only visible on its page on claude.ai/code",
  },
  delivery(rec) {
    const pr = rec.openPR
      ? `\n- After the push, open a draft pull request for \`${rec.branch}\` with \`gh pr create --draft\` (or your GitHub tools) and put its URL in your handoff. Do NOT merge it.`
      : "";
    return `

## Delivery (cloud session)

- Before your first commit, check out your branch: \`git checkout -B ${rec.branch}\`. Commit your work on it.
- When you are done, whatever your status, deliver your handoff as the message of one last, empty commit, and push:

\`\`\`bash
cat > /tmp/orchestrate-handoff.md <<'HANDOFF'
${HANDOFF_COMMIT_PREFIX} ${rec.nonce}

<your handoff, in exactly the structure above>
HANDOFF
git commit --allow-empty -F /tmp/orchestrate-handoff.md
git push --force origin HEAD:refs/heads/${rec.branch}
\`\`\`

- The first line must be exactly \`${HANDOFF_COMMIT_PREFIX} ${rec.nonce}\`. The orchestrator reads only that commit; without it your work counts as lost.${pr}
`;
  },
  async start(rec, prompt) {
    requireBinary("claude");
    const bytes = Buffer.byteLength(prompt);
    if (bytes > MAX_CLOUD_PROMPT_BYTES) {
      throw new Error(
        `prompt is ${bytes} bytes; a cloud session takes at most ${MAX_CLOUD_PROMPT_BYTES}. Shorten the brief or the upstream handoffs, or run this task locally`
      );
    }
    rec.workdir = join(orchestrateHome(), "clones", rec.id);
    cloneAt(rec.repoUrl, rec.startingRef, rec.workdir);
    // Remember the branch tip before the session starts, so a handoff pushed at once still counts.
    rec.extra.seenTip = remoteTip(rec.repoUrl, rec.branch) ?? "";
    const proc = spawnDetached({
      // A prompt starting with "-" would be read as a flag.
      argv: [
        "claude",
        "--cloud",
        prompt.startsWith("-") ? `Task:\n${prompt}` : prompt,
      ],
      cwd: rec.workdir,
      stdoutFile: file(rec, "out.log"),
      stderrFile: file(rec, "err.log"),
      exitFile: file(rec, "exit"),
    });
    rec.pid = proc.pid;
    const deadline = Date.now() + SESSION_WAIT_MS;
    for (;;) {
      const text = [file(rec, "out.log"), file(rec, "err.log")]
        .map(p => (existsSync(p) ? readFileSync(p, "utf8") : ""))
        .join("\n");
      const found = SESSION_RE.exec(text)?.[0];
      if (found) {
        rec.sessionUrl = found.startsWith("https://")
          ? found
          : `https://claude.ai/code/${found}`;
        rec.sessionId = found.startsWith("https://")
          ? (found.split("/").pop() ?? found)
          : found;
        started(rec);
        return;
      }
      const code = readExitCode(file(rec, "exit"));
      if (code !== null || !isAlive(rec.pid)) {
        throw new Error(
          `claude --cloud exited ${code ?? "?"} before starting a session: ${text.trim().slice(-400) || "no output"}`
        );
      }
      if (Date.now() >= deadline) {
        killGroup(rec.pid);
        throw new Error(
          `claude --cloud printed no session in ${Math.round(SESSION_WAIT_MS / 1000)}s; see ${file(rec, "out.log")}`
        );
      }
      await sleep(500);
    }
  },
  probe(rec) {
    const tip = remoteTip(rec.repoUrl, rec.branch);
    if (tip && tip !== rec.extra.seenTip) {
      const commits = recentMessages(rec.repoUrl, rec.branch);
      for (const commit of commits) {
        const handoff = handoffFromCommitMessage(commit.body, rec.nonce);
        if (handoff !== null) return { status: "finished", result: handoff };
      }
      // Only a successful read moves the mark; a failed fetch is retried on the next poll.
      if (commits.length > 0) rec.extra.seenTip = tip;
    }
    if (deadlinePassed(rec)) {
      if (rec.workdir) rmSync(rec.workdir, { recursive: true, force: true });
      return { status: "error", error: deadlineError(rec) };
    }
    return { status: "running" };
  },
  settle(rec) {
    if (rec.result) ensureDraftPr(rec, rec.result);
    if (rec.workdir) rmSync(rec.workdir, { recursive: true, force: true });
    return null;
  },
  cancel() {
    throw new Error(this.unsupported.cancel);
  },
  async followUp(prev, next, text) {
    requireBinary("claude");
    if (!prev.sessionId)
      throw new Error(`${prev.id} has no cloud session to message`);
    // The session answers with a new handoff commit, under this run's own nonce.
    next.extra.seenTip = remoteTip(next.repoUrl, next.branch) ?? "";
    run(
      [
        "claude",
        "-p",
        `${text}${claudeCloud.delivery(next)}`,
        "--cloud",
        prev.sessionId,
      ],
      {
        timeoutMs: 120_000,
      }
    );
    next.sessionId = prev.sessionId;
    next.sessionUrl = prev.sessionUrl;
    started(next);
  },
};

// Codex cloud ---------------------------------------------------------------------------------

export function parseCodexStatus(
  text: string
): "ready" | "pending" | "error" | "applied" | null {
  const line = text.split("\n").find(l => l.trim().length > 0) ?? "";
  const match = /^\s*\[([A-Z_]+)\]/.exec(line);
  if (!match) return null;
  switch (match[1]) {
    case "READY":
      return "ready";
    case "APPLIED":
      return "applied";
    case "ERROR":
      return "error";
    case "PENDING":
    case "IN_PROGRESS":
    case "RUNNING":
      return "pending";
    default:
      return null;
  }
}

export const codexCloud: BackendImpl = {
  label: "Codex (cloud)",
  pollMs: 60_000,
  unsupported: {
    cancel:
      "Codex has no command that cancels a cloud task; stop it from its page on chatgpt.com/codex",
    stream:
      "a cloud task's events are only visible on its page on chatgpt.com/codex",
    followUp: "Codex has no command that sends a message to a cloud task",
  },
  delivery(rec) {
    return `

## Delivery (Codex cloud task)

- You cannot push or open a pull request. Leave your work as changes in the repository; the orchestrator applies your diff to \`${rec.branch}\` and pushes it.
- As your last change, write your handoff, in exactly the structure above, to \`${HANDOFF_FILE}\` at the repository root. The orchestrator takes that file out of your diff and reads only it.
`;
  },
  async start(rec, prompt) {
    requireBinary("codex");
    if (!rec.codexEnv)
      throw new Error(
        "codex cloud needs a Codex cloud environment id (plan.codexEnv or task.codexEnv)"
      );
    const cwd = rec.localRepo ?? repoTopLevel();
    writeFileSync(file(rec, "prompt.md"), prompt);
    const out = runStatus(
      [
        "codex",
        "cloud",
        "exec",
        "--env",
        rec.codexEnv,
        "--branch",
        rec.startingRef,
        "-",
      ],
      {
        cwd,
        input: prompt,
        timeoutMs: 180_000,
      }
    );
    writeFileSync(file(rec, "out.log"), `${out.stdout}\n${out.stderr}`);
    const match = CODEX_TASK_RE.exec(`${out.stdout}\n${out.stderr}`);
    if (out.code !== 0 || !match) {
      throw new Error(
        `codex cloud exec exited ${out.code} without a task: ${(out.stderr || out.stdout).trim().slice(-400) || "no output"}`
      );
    }
    rec.sessionUrl = match[0];
    rec.sessionId = match[1];
    // The commit the task started from, so its diff is applied to the same base even if the branch moves.
    rec.extra.startSha = remoteTip(rec.repoUrl, rec.startingRef) ?? "";
    started(rec);
  },
  probe(rec) {
    const out = runStatus(
      ["codex", "cloud", "status", rec.sessionId as string],
      { timeoutMs: 120_000 }
    );
    const status = parseCodexStatus(`${out.stdout}\n${out.stderr}`);
    if (status === "ready" || status === "applied") {
      rec.extra.title = (out.stdout.split("\n")[0] ?? "")
        .replace(/^\s*\[[A-Z_]+\]\s*/, "")
        .trim();
      return { status: "finished" };
    }
    if (status === "error")
      return {
        status: "error",
        error: `codex cloud task ${rec.sessionId} ended with ERROR (failed or cancelled)`,
      };
    if (deadlinePassed(rec))
      return { status: "error", error: deadlineError(rec) };
    return { status: "running" };
  },
  settle(rec): Probe | null {
    const diff = runStatus(
      ["codex", "cloud", "diff", rec.sessionId as string],
      { timeoutMs: 180_000 }
    );
    if (diff.code !== 0)
      return {
        status: "error",
        error: `codex cloud diff failed: ${diff.stderr.trim().slice(-300)}`,
        retry: true,
      };
    const worktree = join(
      orchestrateHome(),
      "worktrees",
      repoKey(rec.repoUrl),
      rec.id
    );
    rec.workdir = worktree;
    try {
      prepareWorktree({
        repo: rec.localRepo ?? repoTopLevel(),
        repoUrl: rec.repoUrl,
        startingRef: rec.extra.startSha || rec.startingRef,
        branch: rec.branch,
        path: worktree,
        runId: rec.id,
      });
    } catch (err) {
      return {
        status: "error",
        error: `could not prepare ${rec.branch}: ${(err as Error).message}`,
        retry: true,
      };
    }
    const applied = applyPatch(worktree, diff.stdout);
    if (!applied.ok)
      return {
        status: "error",
        error: `the task's diff does not apply to ${rec.startingRef}: ${applied.detail.slice(-300)}`,
      };
    const handoffPath = join(worktree, HANDOFF_FILE);
    let handoff = "";
    if (existsSync(handoffPath)) {
      handoff = readFileSync(handoffPath, "utf8");
      runStatus(["git", "rm", "-q", "-f", "--", HANDOFF_FILE], {
        cwd: worktree,
      });
      rmSync(handoffPath, { force: true });
    }
    const committed = commitAll(
      worktree,
      `${rec.name}: ${rec.extra.title || "codex cloud task"}\n\nCodex cloud task: ${rec.sessionUrl}\n`
    );
    if (!committed.ok)
      return {
        status: "error",
        error: `could not commit the task's diff: ${committed.detail}`,
      };
    const pushed = pushBranch(worktree, rec.repoUrl, rec.branch);
    if (!pushed.ok)
      return {
        status: "error",
        error: `could not push ${rec.branch}: ${pushed.detail}`,
        retry: true,
      };
    ensureDraftPr(rec, handoff);
    return { status: "finished", result: handoff };
  },
  cancel() {
    throw new Error(this.unsupported.cancel);
  },
  async followUp() {
    throw new Error(this.unsupported.followUp);
  },
};
