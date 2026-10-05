import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { execFileSync, spawnSync } from "node:child_process";
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

// The whole engine, through its CLI, against the fake `claude` and `codex` CLIs and a local bare
// repository as the remote. Runs in subprocesses, so no module mock from another test applies.

const SCRIPTS = resolve(import.meta.dir, "..");
const FAKES = resolve(import.meta.dir, "support", "fake-agents");
let root: string;
let originUrl: string;
let clone: string;
let env: Record<string, string>;

function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", args, {
    cwd,
    stdio: "pipe",
    env: { ...process.env, ...env },
  })
    .toString()
    .trim();
}

function cli(args: string[], cwd = clone) {
  return spawnSync(process.execPath, [join(SCRIPTS, "cli.ts"), ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, ...env },
    timeout: 120_000,
  });
}

function calls(): Array<{ bin: string; args: string[]; cwd: string }> {
  const log = env.FAKE_LOG;
  return existsSync(log)
    ? readFileSync(log, "utf8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .map(l => JSON.parse(l))
    : [];
}

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "orchestrate-e2e-"));
  env = {
    PATH: `${FAKES}:${process.env.PATH}`,
    ORCHESTRATE_HOME: join(root, "home"),
    ORCHESTRATE_POLL_MS: "50",
    ORCHESTRATE_SWEEP_MS: "100",
    FAKE_LOG: join(root, "calls.jsonl"),
    FAKE_MODE: "ok",
    GIT_AUTHOR_NAME: "t",
    GIT_AUTHOR_EMAIL: "t@example.com",
    GIT_COMMITTER_NAME: "t",
    GIT_COMMITTER_EMAIL: "t@example.com",
    SLACK_BOT_TOKEN: "",
  };
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
  writeFileSync(join(clone, "README.md"), "seed\n");
  git(clone, "add", ".");
  git(clone, "commit", "-q", "-m", "seed");
  git(clone, "push", "-q", "origin", "HEAD:refs/heads/main");
});

afterAll(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("orchestrate end to end", () => {
  test("a Claude Code worker and a Codex verifier run locally and hand off", () => {
    const workspace = join(clone, ".orchestrate", "e2e");
    mkdirSync(workspace, { recursive: true });
    writeFileSync(
      join(workspace, "plan.json"),
      JSON.stringify({
        goal: "add fake.txt",
        rootSlug: "e2e",
        baseBranch: "main",
        repoUrl: originUrl,
        syncStateToGit: false,
        backend: "claude",
        runtime: "local",
        tasks: [
          {
            name: "build-it",
            type: "worker",
            scopedGoal: "write fake.txt",
            acceptance: ["fake.txt exists"],
          },
          {
            name: "check-it",
            type: "verifier",
            verifies: "build-it",
            backend: "codex",
            scopedGoal: "check fake.txt",
          },
        ],
      })
    );

    const preview = cli(["prompt", workspace, "build-it"]);
    expect(preview.status).toBe(0);
    expect(preview.stdout).toContain("Push exactly `orch/e2e/build-it`");

    const ran = cli(["run", "--root", "--max-runtime-sec", "60", workspace]);
    expect(ran.stderr + ran.stdout).toContain("handed-off");
    expect(ran.status).toBe(0);

    const state = JSON.parse(
      readFileSync(join(workspace, "state.json"), "utf8")
    );
    const byName = Object.fromEntries(
      state.tasks.map((t: { name: string }) => [t.name, t])
    );
    expect(byName["build-it"].status).toBe("handed-off");
    expect(byName["check-it"].status).toBe("handed-off");
    expect(byName["check-it"].startingRef).toBe("orch/e2e/build-it");
    expect(byName["build-it"].verification).toBe("unit-test-verified");
    expect(
      readFileSync(join(workspace, "handoffs", "build-it.md"), "utf8")
    ).toContain("## Status\nsuccess");

    const [worker, verifier] = calls();
    expect(worker.bin).toBe("claude");
    expect(worker.args).toEqual(expect.arrayContaining(["--model", "sonnet"]));
    expect(verifier.bin).toBe("codex");
    expect(verifier.args).toEqual(
      expect.arrayContaining([
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="high"',
      ])
    );
    expect(git(clone, "ls-remote", "--heads", "origin")).toContain(
      "refs/heads/orch/e2e/check-it"
    );
  }, 60_000);

  test("kickoff starts the root planner as a local process in its own worktree", () => {
    env.FAKE_MODE = "hang";
    const out = cli([
      "kickoff",
      "smoke: try the planner",
      "--repo",
      originUrl,
      "--ref",
      "main",
      "--runtime",
      "local",
    ]);
    expect(out.status).toBe(0);
    const started = JSON.parse(out.stdout.trim().split("\n").at(-1) as string);
    expect(started.branch).toBe("orch/smoke/root");
    expect(started.workspace).toEndWith("/.orchestrate/smoke");
    let root = calls().find(
      c => c.bin === "claude" && c.cwd === started.worktree
    );
    for (let i = 0; !root && i < 100; i++) {
      Bun.sleepSync(50);
      root = calls().find(
        c => c.bin === "claude" && c.cwd === started.worktree
      );
    }
    expect(root?.bin).toBe("claude");
    expect(root?.args).toEqual(expect.arrayContaining(["--model", "opus"]));
    const prompt = readFileSync(
      join(env.ORCHESTRATE_HOME, "runs", started.runId, "prompt.md"),
      "utf8"
    );
    expect(prompt).toContain(
      "You are the root planner for: smoke: try the planner"
    );
    expect(prompt).toContain('`runtime: "local"`');
    expect(prompt).toContain(join(SCRIPTS, "cli.ts"));

    const again = cli([
      "kickoff",
      "smoke: try the planner",
      "--repo",
      originUrl,
      "--ref",
      "main",
    ]);
    expect(again.stdout).toContain("adopting");
    const pid = JSON.parse(
      readFileSync(
        join(env.ORCHESTRATE_HOME, "runs", started.runId, "run.json"),
        "utf8"
      )
    ).pid;
    process.kill(-pid, "SIGTERM");
    env.FAKE_MODE = "ok";
  }, 30_000);

  test("a plan that sends a subplanner to the cloud or Codex to the cloud without an environment is refused", () => {
    const workspace = join(clone, ".orchestrate", "bad");
    mkdirSync(workspace, { recursive: true });
    writeFileSync(
      join(workspace, "plan.json"),
      JSON.stringify({
        goal: "g",
        rootSlug: "bad",
        baseBranch: "main",
        repoUrl: originUrl,
        tasks: [
          {
            name: "sub",
            type: "subplanner",
            runtime: "cloud",
            scopedGoal: "x",
          },
          { name: "w", type: "worker", backend: "codex", scopedGoal: "y" },
        ],
      })
    );
    const out = cli(["run", workspace]);
    expect(out.status).toBe(2);
    expect(out.stderr).toContain("runs locally");
    expect(out.stderr).toContain("needs codexEnv");
  });
});
