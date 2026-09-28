import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { COMPAT_END, COMPAT_START, compatBlock, projectHasCompat } from "../lib/compat.mjs";
import { MODES, nextActiveModes, reminderText } from "../lib/modes.mjs";
import teamKit from "../pi/team-kit.ts";

const PLUGIN = path.resolve(import.meta.dir, "..");
const REPO = path.resolve(PLUGIN, "..", "..");
const HOOK = path.join(PLUGIN, "hooks", "team-kit-hook.mjs");
const RUN_MODEL = path.join(PLUGIN, "skills", "run-model", "scripts", "run-model.sh");
const LINK_SKILLS = path.join(PLUGIN, "skills", "setup-team-kit", "scripts", "link-skills.sh");

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "team-kit-test-"));

describe("sticky modes", () => {
  test("the embedded reminder matches the vendored poteto-mode frontmatter", () => {
    const skill = fs.readFileSync(path.join(REPO, "plugins/pstack/skills/poteto-mode/SKILL.md"), "utf8");
    const reminder = /^reminder: (.*)$/m.exec(skill)[1];
    expect(MODES.find((m) => m.skill === "poteto-mode").reminder).toBe(reminder);
  });

  test("every agent's invocation syntax turns the mode on", () => {
    for (const prompt of ["/poteto-mode fix it", "/pstack:poteto-mode go", "/skill:poteto-mode go", "$poteto-mode go", "please /poteto-mode"]) {
      expect([...nextActiveModes(prompt, new Set())]).toEqual(["poteto-mode"]);
    }
  });

  test("mentioning the mode in prose does not turn it on", () => {
    expect(nextActiveModes("is poteto-mode worth it?", new Set()).size).toBe(0);
  });

  test("saying so turns the mode off", () => {
    for (const prompt of ["exit poteto mode", "please stop poteto-mode now", "/poteto-mode off", "opt out of poteto mode"]) {
      expect(nextActiveModes(prompt, new Set(["poteto-mode"])).size).toBe(0);
    }
  });

  test("the reminder names the mode and how to leave it", () => {
    expect(reminderText(new Set())).toBeNull();
    const text = reminderText(new Set(["poteto-mode"]));
    expect(text).toContain("/poteto-mode is pinned");
    expect(text).toContain('say "exit poteto mode"');
  });
});

describe("compat block", () => {
  test("is delimited by its markers", () => {
    const block = compatBlock();
    expect(block.startsWith(COMPAT_START)).toBe(true);
    expect(block.endsWith(COMPAT_END)).toBe(true);
  });

  test("is detected in AGENTS.md at or above the working directory, up to the repo root", () => {
    const repo = tmp();
    fs.mkdirSync(path.join(repo, ".git"));
    fs.mkdirSync(path.join(repo, "app", "src"), { recursive: true });
    expect(projectHasCompat(path.join(repo, "app", "src"))).toBe(false);
    fs.writeFileSync(path.join(repo, "AGENTS.md"), `# Rules\n\n${compatBlock()}\n`);
    expect(projectHasCompat(path.join(repo, "app", "src"))).toBe(true);
  });
});

function runHook(event, input, dataDir) {
  const result = spawnSync("node", [HOOK, event], {
    input: JSON.stringify(input),
    env: { ...process.env, CLAUDE_PLUGIN_DATA: dataDir },
    encoding: "utf8",
  });
  expect(result.status).toBe(0);
  return result.stdout.trim() ? JSON.parse(result.stdout) : null;
}

describe("Claude Code and Codex hook", () => {
  test("session start injects the compat block only when the project lacks it", () => {
    const repo = tmp();
    const data = tmp();
    const out = runHook("session-start", { cwd: repo, session_id: "a" }, data);
    expect(out.hookSpecificOutput.hookEventName).toBe("SessionStart");
    expect(out.hookSpecificOutput.additionalContext).toBe(compatBlock());
    fs.writeFileSync(path.join(repo, "AGENTS.md"), compatBlock());
    expect(runHook("session-start", { cwd: repo, session_id: "a" }, data)).toBeNull();
  });

  test("poteto-mode stays pinned on later prompts until the user opts out", () => {
    const data = tmp();
    const prompt = (text) => runHook("user-prompt-submit", { cwd: data, session_id: "s", prompt: text }, data);
    expect(prompt("/pstack:poteto-mode fix the scroll drift")).toBeNull();
    const pinned = prompt("now add a test");
    expect(pinned.hookSpecificOutput.hookEventName).toBe("UserPromptSubmit");
    expect(pinned.hookSpecificOutput.additionalContext).toContain(MODES[0].reminder);
    expect(prompt("exit poteto mode")).toBeNull();
    expect(prompt("thanks")).toBeNull();
  });

  test("sessions do not share mode state", () => {
    const data = tmp();
    runHook("user-prompt-submit", { cwd: data, session_id: "one", prompt: "/poteto-mode go" }, data);
    expect(runHook("user-prompt-submit", { cwd: data, session_id: "two", prompt: "hello" }, data)).toBeNull();
  });
});

function fakePi() {
  const handlers = {};
  return { handlers, on: (event, handler) => (handlers[event] = handler) };
}

describe("Pi extension", () => {
  test("adds the compat section and pins poteto-mode across prompts", async () => {
    const pi = fakePi();
    teamKit(pi);
    await pi.handlers.session_start({}, { cwd: tmp() });
    const turn = async (text) => {
      expect(await pi.handlers.input({ text })).toEqual({ action: "continue" });
      const event = { systemPromptOptions: { sections: {} } };
      await pi.handlers.before_agent_start(event);
      return event.systemPromptOptions.sections;
    };
    const first = await turn("/skill:poteto-mode build the feature");
    expect(first["team-kit-compat"]).toBe(compatBlock());
    expect(first["team-kit-mode"]).toBeUndefined();
    expect((await turn("next step"))["team-kit-mode"]).toContain(MODES[0].reminder);
    expect((await turn("exit poteto mode"))["team-kit-mode"]).toBeUndefined();
  });

  test("skips the compat section when the project already carries it", async () => {
    const repo = tmp();
    fs.writeFileSync(path.join(repo, "AGENTS.md"), compatBlock());
    const pi = fakePi();
    teamKit(pi);
    await pi.handlers.session_start({}, { cwd: repo });
    await pi.handlers.input({ text: "hello" });
    const event = { systemPromptOptions: { sections: {} } };
    await pi.handlers.before_agent_start(event);
    expect(event.systemPromptOptions.sections).toEqual({});
  });
});

function stubBin(dir, name, script) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, `#!/usr/bin/env bash\n${script}\n`);
  fs.chmodSync(file, 0o755);
  return file;
}

function runModel(args, env, input = "review this diff") {
  const result = spawnSync(RUN_MODEL, args, { input, env: { ...process.env, ...env }, encoding: "utf8" });
  const [first, ...rest] = result.stdout.split("\n");
  return { status: result.status, route: JSON.parse(first), rest: rest.join("\n").trim(), stderr: result.stderr };
}

describe("run-model", () => {
  const missing = { TEAMKIT_CLAUDE_BIN: "/nonexistent/claude", TEAMKIT_CODEX_BIN: "/nonexistent/codex", TEAMKIT_PI_BIN: "/nonexistent/pi" };

  test("Claude Code alone: its own family is native, GPT falls back", () => {
    expect(runModel(["--from", "claude", "--model", "claude-opus-5-5-max"], missing).route).toEqual({
      route: "native",
      how: "Use the Agent tool with model: opus.",
      command: "",
    });
    const gpt = runModel(["--from", "claude", "--model", "gpt-6-sol-max"], missing);
    expect(gpt.status).toBe(3);
    expect(gpt.route.route).toBe("fallback");
  });

  test("Codex alone: GPT is native, Claude falls back", () => {
    expect(runModel(["--from", "codex", "--model", "gpt-6-sol-max"], missing).route.route).toBe("native");
    expect(runModel(["--from", "codex", "--model", "claude-opus-5-5-max"], missing).status).toBe(3);
  });

  test("Pi alone: GPT routes to the openai-codex provider, Claude needs the anthropic provider", () => {
    expect(runModel(["--from", "pi", "--model", "gpt-6-sol-max"], missing).route.how).toBe(
      "Use the subagent tool with model openai-codex/gpt-6-sol and thinking max.",
    );
    expect(runModel(["--from", "pi", "--model", "claude-opus-5-5-max"], missing).route.how).toContain("anthropic/claude-opus-5-5");
  });

  test("Claude Code with Codex installed runs the GPT seat read-only and prints its answer", () => {
    const bin = tmp();
    const argsFile = path.join(bin, "args");
    const codex = stubBin(bin, "codex", `printf '%s\\n' "$@" > ${argsFile}; out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done; cat > /dev/null; echo "seat answer" > "$out"`);
    const result = runModel(["--from", "claude", "--model", "gpt-6-sol-max"], { ...missing, TEAMKIT_CODEX_BIN: codex });
    expect(result.status).toBe(0);
    expect(result.route.route).toBe("external");
    expect(result.rest).toBe("seat answer");
    const args = fs.readFileSync(argsFile, "utf8").split("\n");
    expect(args.slice(0, 7)).toEqual(["exec", "-m", "gpt-6-sol", "-c", 'model_reasoning_effort="max"', "-s", "read-only"]);
  });

  test("Codex with Claude installed runs the Claude seat in plan mode on stdin", () => {
    const bin = tmp();
    const claude = stubBin(bin, "claude", 'echo "args: $*"; echo "stdin: $(cat)"');
    const result = runModel(["--from", "codex", "--model", "claude-opus-5-5-max"], { ...missing, TEAMKIT_CLAUDE_BIN: claude });
    expect(result.rest).toBe("args: -p --model claude-opus-5-5 --effort max --permission-mode plan\nstdin: review this diff");
  });

  test("inherit-parent never runs anything", () => {
    expect(runModel(["--from", "codex", "--model", "inherit-parent"], missing).route.route).toBe("native");
  });

  test("bad input explains itself", () => {
    const result = spawnSync(RUN_MODEL, ["--from", "cursor", "--model", "x"], { encoding: "utf8" });
    expect(result.status).toBe(2);
    expect(result.stderr).toContain("--from must be claude, codex, or pi");
  });
});

describe("link-skills", () => {
  test("refuses to replace a real skill directory and reports the conflict", () => {
    const repo = tmp();
    for (const root of [".cursor/skills", ".agents/skills"]) {
      fs.mkdirSync(path.join(repo, root, "verify-app"), { recursive: true });
      fs.writeFileSync(path.join(repo, root, "verify-app", "SKILL.md"), "---\nname: verify-app\ndescription: x\n---\n");
    }
    const result = spawnSync(LINK_SKILLS, [repo], { encoding: "utf8" });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("conflict: .cursor/skills/verify-app and .agents/skills/verify-app");
  });
});
