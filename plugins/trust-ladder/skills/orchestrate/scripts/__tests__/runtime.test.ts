import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  test,
} from "bun:test";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// Other test files mock "#agent-runtime" for the whole process; the query string loads the real module.
const REAL_RUNTIME = "../runtime/index.ts?real";
const runtime: typeof import("../runtime/index.ts") = await import(
  REAL_RUNTIME
);
const { Agent, AgentRuntimeError, describeRun } = runtime;

import {
  handoffFromCommitMessage,
  parseCodexStatus,
} from "../runtime/cloud.ts";
import { isAlive } from "../runtime/process.ts";
import { loadRun } from "../runtime/registry.ts";

const FAKES = resolve(import.meta.dir, "support", "fake-agents");
let root: string;
let originUrl: string;
let clone: string;
let fakeLog: string;
const priorEnv = { ...process.env };

function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", args, { cwd, stdio: "pipe" }).toString().trim();
}

function remoteFile(branch: string, path: string): string | null {
  try {
    return execFileSync(
      "git",
      ["--git-dir", join(root, "origin.git"), "show", `${branch}:${path}`],
      { stdio: "pipe" }
    ).toString();
  } catch {
    return null;
  }
}

function calls(): Array<{ bin: string; args: string[]; cwd: string }> {
  if (!existsSync(fakeLog)) return [];
  return readFileSync(fakeLog, "utf8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map(l => JSON.parse(l));
}

async function spawn(
  runtime: Record<string, unknown>,
  prompt = "do the thing",
  extra: Record<string, unknown> = {}
) {
  const agent = await Agent.create({
    name: "t/worker",
    model: { id: "sonnet" },
    cloud: {
      repos: [{ url: originUrl, startingRef: "main" }],
      autoCreatePR: false,
    },
    runtime: { localRepo: clone, ...runtime } as never,
    ...extra,
  });
  const run = await agent.send(prompt);
  return { agent, run };
}

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "orchestrate-runtime-"));
  execFileSync("git", [
    "init",
    "-q",
    "--bare",
    "-b",
    "main",
    join(root, "origin.git"),
  ]);
  originUrl = `file://${join(root, "origin.git")}`;
  clone = join(root, "clone");
  execFileSync("git", ["clone", "-q", originUrl, clone], { stdio: "pipe" });
  Object.assign(process.env, {
    GIT_AUTHOR_NAME: "t",
    GIT_AUTHOR_EMAIL: "t@example.com",
    GIT_COMMITTER_NAME: "t",
    GIT_COMMITTER_EMAIL: "t@example.com",
  });
  writeFileSync(join(clone, "README.md"), "seed\n");
  git(clone, "add", ".");
  git(clone, "commit", "-q", "-m", "seed");
  git(clone, "push", "-q", "origin", "HEAD:refs/heads/main");
});

beforeEach(() => {
  const home = join(root, `home-${Math.random().toString(16).slice(2)}`);
  mkdirSync(home, { recursive: true });
  fakeLog = join(home, "calls.jsonl");
  Object.assign(process.env, {
    ORCHESTRATE_HOME: home,
    ORCHESTRATE_POLL_MS: "50",
    ORCHESTRATE_SESSION_WAIT_MS: "20000",
    FAKE_LOG: fakeLog,
    FAKE_MODE: "ok",
    FAKE_ORIGIN: originUrl,
    FAKE_COUNTER: join(home, "counter"),
    PATH: `${FAKES}:${priorEnv.PATH}`,
  });
});

afterAll(() => {
  process.env = priorEnv;
  rmSync(root, { recursive: true, force: true });
});

describe("local Claude Code runs", () => {
  test("run in their own worktree, push the branch, and hand back the final message", async () => {
    const { run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/claude-local",
    });
    const result = await run.wait();
    expect(result.status).toBe("finished");
    expect(result.result).toContain("## Status\nsuccess");
    expect(result.git?.branches[0]?.branch).toBe("orch/t/claude-local");
    expect(remoteFile("orch/t/claude-local", "fake.txt")).not.toBeNull();
    const [call] = calls();
    expect(call.args.slice(0, 5)).toEqual([
      "-p",
      "--output-format",
      "stream-json",
      "--verbose",
      "--session-id",
    ]);
    expect(call.args).toContain("bypassPermissions");
    expect(call.args).toEqual(expect.arrayContaining(["--model", "sonnet"]));
    expect(call.cwd).toBe(describeRun(run.id).workdir as string);
    const prompt = readFileSync(join(loadRun(run.id).dir, "prompt.md"), "utf8");
    expect(prompt).toStartWith("do the thing");
    expect(prompt).toContain("## Delivery (local run)");
  });

  test("stream the agent's text and tool calls", async () => {
    const { run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/claude-stream",
    });
    const events = [];
    for await (const event of run.stream()) events.push(event);
    expect(events).toContainEqual({
      type: "assistant",
      message: { content: [{ type: "text", text: "working" }] },
    });
    expect(events.some(e => e.type === "tool_call" && e.name === "Bash")).toBe(
      true
    );
  });

  test("the workspace sandbox uses auto mode", async () => {
    const { run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/claude-auto",
      localSandbox: "workspace",
    });
    await run.wait();
    expect(calls()[0].args).toEqual(
      expect.arrayContaining(["--permission-mode", "auto"])
    );
  });

  test("an error result, a crash, and a missing push all become errors or get pushed", async () => {
    process.env.FAKE_MODE = "fail";
    const failed = await (
      await spawn({
        backend: "claude",
        mode: "local",
        branch: "orch/t/claude-fail",
      })
    ).run.wait();
    expect(failed.status).toBe("error");
    expect(failed.error).toContain("error_max_turns");

    process.env.FAKE_MODE = "crash";
    const crashed = await (
      await spawn({
        backend: "claude",
        mode: "local",
        branch: "orch/t/claude-crash",
      })
    ).run.wait();
    expect(crashed.status).toBe("error");
    expect(crashed.error).toContain("exited 3");

    process.env.FAKE_MODE = "nopush";
    const unpushed = await (
      await spawn({
        backend: "claude",
        mode: "local",
        branch: "orch/t/claude-nopush",
      })
    ).run.wait();
    expect(unpushed.status).toBe("finished");
    expect(remoteFile("orch/t/claude-nopush", "fake.txt")).not.toBeNull();
  });

  test("a run past its time budget is stopped as cap-hit", async () => {
    process.env.FAKE_MODE = "hang";
    const { run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/claude-hang",
      maxDurationMinutes: 0.01,
    });
    const result = await run.wait();
    expect(result.status).toBe("error");
    expect(result.error).toStartWith("cap-hit");
    await Bun.sleep(200);
    expect(isAlive(loadRun(run.id).pid)).toBe(false);
  });

  test("cancel stops the process; a busy agent refuses a second message; a finished one resumes its session", async () => {
    process.env.FAKE_MODE = "hang";
    const { agent, run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/claude-cancel",
    });
    await expect(agent.send("again")).rejects.toThrow("agent_busy");
    await run.cancel();
    expect((await Agent.getRun(run.id)).status).toBe("cancelled");
    await Bun.sleep(200);
    expect(isAlive(loadRun(run.id).pid)).toBe(false);

    process.env.FAKE_MODE = "ok";
    const next = await agent.send("follow up");
    expect((await next.wait()).status).toBe("finished");
    const resumed = calls().at(-1);
    expect(resumed?.args).toEqual(
      expect.arrayContaining(["--resume", loadRun(run.id).sessionId as string])
    );
    const runs = await Agent.listRuns(agent.agentId);
    expect(runs.items.map(r => r.status)).toEqual(["finished", "cancelled"]);
  });
});

describe("local Codex runs", () => {
  test("record the thread, read the last message, and push the branch", async () => {
    const { run } = await spawn({
      backend: "codex",
      mode: "local",
      branch: "orch/t/codex-local",
    });
    const result = await run.wait();
    expect(result.status).toBe("finished");
    expect(result.result).toContain("wrote hello.txt");
    expect(loadRun(run.id).sessionId).toBe("thread-fake-7");
    expect(remoteFile("orch/t/codex-local", "codex.txt")).toBe("codex\n");
    const args = calls()[0].args;
    expect(args.slice(0, 2)).toEqual(["exec", "--json"]);
    expect(args).toContain("--dangerously-bypass-approvals-and-sandbox");
    expect(args.at(-1)).toBe("-");
  });

  test("a failed turn is an error, and a follow-up resumes the thread", async () => {
    process.env.FAKE_MODE = "fail";
    const { agent, run } = await spawn({
      backend: "codex",
      mode: "local",
      branch: "orch/t/codex-fail",
      localSandbox: "workspace",
    });
    const result = await run.wait();
    expect(result.status).toBe("error");
    expect(result.error).toContain("model refused");
    expect(calls()[0].args).toEqual(
      expect.arrayContaining(["-s", "workspace-write"])
    );
    process.env.FAKE_MODE = "ok";
    await (await agent.send("try again")).wait();
    const resumed = calls().at(-1)?.args ?? [];
    expect(
      resumed.slice(resumed.indexOf("resume"), resumed.indexOf("resume") + 2)
    ).toEqual(["resume", "thread-fake-7"]);
  });
});

describe("Claude Code cloud sessions", () => {
  test("deliver the handoff as the message of a commit carrying the run's nonce", async () => {
    const { run } = await spawn({
      backend: "claude",
      mode: "cloud",
      branch: "orch/t/claude-cloud",
    });
    const rec = loadRun(run.id);
    expect(rec.sessionUrl).toBe("https://claude.ai/code/session_fake123");
    const prompt = calls()[0].args[1];
    expect(prompt).toContain(`orchestrate-handoff ${rec.nonce}`);
    expect(prompt).toContain("git checkout -B orch/t/claude-cloud");
    const result = await run.wait();
    expect(result.status).toBe("finished");
    expect(result.result).toStartWith("## Status\nsuccess");
    expect(remoteFile("orch/t/claude-cloud", "fake-cloud.txt")).toBe(
      "from the cloud\n"
    );
    expect(run.supports("cancel")).toBe(false);
    await expect(run.cancel()).rejects.toThrow(
      "https://claude.ai/code/session_fake123"
    );
  });

  test("ignore a handoff commit with another run's nonce until the time budget runs out", async () => {
    process.env.FAKE_MODE = "wrongnonce";
    const { run } = await spawn({
      backend: "claude",
      mode: "cloud",
      branch: "orch/t/claude-stale",
      maxDurationMinutes: 0.02,
    });
    const result = await run.wait();
    expect(result.status).toBe("error");
    expect(result.error).toStartWith("cap-hit");
  });

  test("refuse a prompt too long for one argument, and report a CLI that starts no session", async () => {
    await expect(
      spawn(
        { backend: "claude", mode: "cloud", branch: "orch/t/too-long" },
        "x".repeat(130_000)
      )
    ).rejects.toBeInstanceOf(AgentRuntimeError);
    process.env.FAKE_MODE = "nosession";
    await expect(
      spawn({ backend: "claude", mode: "cloud", branch: "orch/t/nosession" })
    ).rejects.toThrow("before starting a session");
  });

  test("handoff commit messages parse only with the right nonce", () => {
    expect(
      handoffFromCommitMessage(
        "orchestrate-handoff abc\n\n## Status\nsuccess\n",
        "abc"
      )
    ).toBe("## Status\nsuccess");
    expect(
      handoffFromCommitMessage("orchestrate-handoff abd\n\nx", "abc")
    ).toBeNull();
    expect(handoffFromCommitMessage("fix: something", "abc")).toBeNull();
  });
});

describe("Codex cloud tasks", () => {
  test("apply the task's diff to the branch, take the handoff file out, and push", async () => {
    const { run } = await spawn({
      backend: "codex",
      mode: "cloud",
      branch: "orch/t/codex-cloud",
      codexEnv: "env-1",
    });
    expect(loadRun(run.id).sessionUrl).toBe(
      "https://chatgpt.com/codex/tasks/task_e_fake42"
    );
    expect(calls()[0].args).toEqual([
      "cloud",
      "exec",
      "--env",
      "env-1",
      "--branch",
      "main",
      "-",
    ]);
    const result = await run.wait();
    expect(result.status).toBe("finished");
    expect(result.result).toContain("wrote hello.txt");
    expect(remoteFile("orch/t/codex-cloud", "hello.txt")).toBe("hello\n");
    expect(
      remoteFile("orch/t/codex-cloud", ".orchestrate-handoff.md")
    ).toBeNull();
  });

  test("an ERROR status is an error, and a task without an environment never starts", async () => {
    process.env.FAKE_MODE = "cloud-error";
    const { run } = await spawn({
      backend: "codex",
      mode: "cloud",
      branch: "orch/t/codex-error",
      codexEnv: "env-1",
    });
    expect((await run.wait()).status).toBe("error");
    await expect(
      spawn({ backend: "codex", mode: "cloud", branch: "orch/t/codex-noenv" })
    ).rejects.toThrow("environment id");
  });

  test("status lines parse to ready, pending, error", () => {
    expect(parseCodexStatus("[READY] t\nx")).toBe("ready");
    expect(parseCodexStatus("[PENDING] t")).toBe("pending");
    expect(parseCodexStatus("[ERROR] t")).toBe("error");
    expect(parseCodexStatus("nonsense")).toBeNull();
  });
});

describe("review fixes", () => {
  test("a second agent never takes a branch a running agent holds", async () => {
    process.env.FAKE_MODE = "hang";
    const { run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/held",
    });
    await expect(
      spawn({ backend: "claude", mode: "local", branch: "orch/t/held" })
    ).rejects.toThrow("is in use by");
    await run.cancel();
    process.env.FAKE_MODE = "ok";
    const again = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/held",
    });
    expect((await again.run.wait()).status).toBe("finished");
  });

  test("a respawn overwrites the branch an earlier attempt pushed, and the script pushes over it too", async () => {
    const first = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/again",
    });
    expect((await first.run.wait()).status).toBe("finished");
    process.env.FAKE_MODE = "nopush";
    const second = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/again",
    });
    const result = await second.run.wait();
    expect(result.status).toBe("finished");
    const local = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: describeRun(second.run.id).workdir as string,
    })
      .toString()
      .trim();
    expect(
      execFileSync("git", [
        "ls-remote",
        originUrl,
        "refs/heads/orch/t/again",
      ]).toString()
    ).toStartWith(local);
  });

  test("the script commits and pushes what the agent left uncommitted", async () => {
    process.env.FAKE_MODE = "nocommit";
    const { run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/leftover",
    });
    expect((await run.wait()).status).toBe("finished");
    expect(remoteFile("orch/t/leftover", "fake.txt")).not.toBeNull();
  });

  test("a Codex cloud settle that fails once is retried, not lost", async () => {
    process.env.FAKE_MODE = "diff-flaky";
    const { run } = await spawn({
      backend: "codex",
      mode: "cloud",
      branch: "orch/t/codex-cloud",
      codexEnv: "env-1",
    });
    const result = await run.wait();
    expect(result.status).toBe("finished");
    expect(Number(loadRun(run.id).extra.settleAttempts)).toBe(2);
    expect(remoteFile("orch/t/codex-cloud", "hello.txt")).toBe("hello\n");
  });

  test("a follow-up to a cloud session waits for a new handoff under a new nonce", async () => {
    const { agent, run } = await spawn({
      backend: "claude",
      mode: "cloud",
      branch: "orch/t/cloud-follow",
    });
    await run.wait();
    const next = await agent.send("now also update the docs");
    expect(next.status).toBe("running");
    const result = await next.wait();
    expect(result.status).toBe("finished");
    expect(result.result).toContain("follow-up");
    expect(loadRun(next.id).nonce).not.toBe(loadRun(run.id).nonce);
  });
});

describe("planners", () => {
  test("a subplanner or root always runs locally, whatever mode is asked", async () => {
    const { run } = await spawn({
      backend: "claude",
      mode: "cloud",
      branch: "orch/t/sub",
      role: "subplanner",
    });
    expect(loadRun(run.id).mode).toBe("local");
    await run.wait();
  });

  test("Agent.list reports agents with their latest run", async () => {
    const { agent, run } = await spawn({
      backend: "claude",
      mode: "local",
      branch: "orch/t/listed",
    });
    await run.wait();
    const listed = await Agent.list();
    expect(listed.items[0]).toMatchObject({
      agentId: agent.agentId,
      name: "t/worker",
      latestRun: { id: run.id, status: "finished" },
    });
  });
});
