import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { cursorInput, editedFile, runAdapter, subagentName } from "./cursor-hook-adapter.mjs";

const REPO = path.resolve(import.meta.dir, "../..");
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "agent-hooks-"));
const has = (bin) => spawnSync("bash", ["-c", `command -v ${bin}`]).status === 0;

function fakePlugin(cursorHooks, scripts) {
  const root = tmp();
  fs.mkdirSync(path.join(root, "hooks"));
  fs.writeFileSync(path.join(root, "hooks", "hooks.json"), JSON.stringify({ version: 1, hooks: cursorHooks }));
  for (const [name, body] of Object.entries(scripts)) {
    fs.writeFileSync(path.join(root, "hooks", name), `#!/bin/bash\n${body}\n`, { mode: 0o755 });
  }
  return root;
}

describe("input translation", () => {
  test("finds the edited file from Claude Code and Codex edit tools", () => {
    expect(editedFile({ file_path: "/r/a.ts" })).toBe("/r/a.ts");
    expect(editedFile({ notebook_path: "/r/n.ipynb" })).toBe("/r/n.ipynb");
    expect(editedFile({ command: "*** Begin Patch\n*** Update File: src/b.rs\n@@\n-x\n+y\n*** End Patch" })).toBe("src/b.rs");
    expect(editedFile({})).toBe("");
  });

  test("strips the plugin prefix from Claude Code subagent names", () => {
    expect(subagentName("advisor:advisor-subagent")).toBe("advisor-subagent");
    expect(subagentName("advisor-subagent")).toBe("advisor-subagent");
  });

  test("maps agent hook input to each Cursor event's input", () => {
    const input = { session_id: "s1", turn_id: "t1", model: "opus", transcript_path: "/t.jsonl", last_assistant_message: "Done.", agent_type: "advisor:advisor-subagent", tool_input: { file_path: "src/a.ts" } };
    const opts = { projectDir: "/repo", loopCount: 2 };
    expect(cursorInput("afterFileEdit", input, opts)).toMatchObject({ conversation_id: "s1", generation_id: "t1", file_path: "/repo/src/a.ts", workspace_roots: ["/repo"] });
    expect(cursorInput("afterAgentResponse", input, opts)).toMatchObject({ text: "Done." });
    expect(cursorInput("stop", input, opts)).toMatchObject({ status: "completed", loop_count: 2 });
    expect(cursorInput("subagentStop", input, opts)).toMatchObject({ subagent_type: "advisor-subagent", status: "completed", summary: "Done." });
  });
});

describe("runAdapter", () => {
  const log = (dir) => fs.readFileSync(path.join(dir, "log"), "utf8").trim().split("\n");

  function plugin() {
    const out = tmp();
    const root = fakePlugin(
      {
        afterFileEdit: [{ command: `bash "\${CURSOR_PLUGIN_ROOT}/hooks/edit.sh"` }],
        afterAgentResponse: [{ command: `bash "\${CURSOR_PLUGIN_ROOT}/hooks/response.sh"` }],
        subagentStop: [{ command: `bash "\${CURSOR_PLUGIN_ROOT}/hooks/subagent.sh"`, matcher: "^advisor-subagent$" }],
        stop: [{ command: `bash "\${CURSOR_PLUGIN_ROOT}/hooks/stop.sh"`, loop_limit: 2 }],
      },
      {
        "edit.sh": `jq -r '"edit " + .file_path' >> ${out}/log`,
        "response.sh": `jq -r '"response " + .text' >> ${out}/log; pwd >> ${out}/cwd`,
        "subagent.sh": `jq -r '"subagent " + .subagent_type' >> ${out}/log`,
        "stop.sh": `jq -r '"stop " + (.loop_count|tostring)' >> ${out}/log; echo '{"followup_message": "run the review"}'`,
      },
    );
    return { root, out, opts: { pluginRoot: root, projectDir: out, dataDir: path.join(out, "data") } };
  }

  test.skipIf(!has("jq"))("PostToolUse runs afterFileEdit only for edit tools", () => {
    const { out, opts } = plugin();
    expect(runAdapter("PostToolUse", { session_id: "s", tool_name: "Read", tool_input: { file_path: "a" } }, opts).output).toBeNull();
    expect(fs.existsSync(path.join(out, "log"))).toBe(false);
    runAdapter("PostToolUse", { session_id: "s", tool_name: "apply_patch", tool_input: { command: "*** Add File: b.ts\n+x" } }, opts);
    expect(log(out)).toEqual([`edit ${path.join(out, "b.ts")}`]);
  });

  test.skipIf(!has("jq"))("Stop runs afterAgentResponse, then stop, in the project, and turns a followup into a block", () => {
    const { out, opts } = plugin();
    const { output, errors } = runAdapter("Stop", { session_id: "s", stop_hook_active: false, last_assistant_message: "All done." }, opts);
    expect(errors).toEqual([]);
    expect(output).toEqual({ decision: "block", reason: "run the review" });
    expect(log(out)).toEqual(["response All done.", "stop 0"]);
    expect(fs.readFileSync(path.join(out, "cwd"), "utf8").trim()).toBe(fs.realpathSync(out));
  });

  test.skipIf(!has("jq"))("counts consecutive follow-ups and honors the Cursor loop_limit", () => {
    const { out, opts } = plugin();
    const stop = (active) => runAdapter("Stop", { session_id: "s", stop_hook_active: active, last_assistant_message: "x" }, opts).output;
    expect(stop(false)).not.toBeNull();
    expect(stop(true)).not.toBeNull();
    expect(stop(true)).toBeNull();
    expect(stop(false)).not.toBeNull();
    expect(log(out).filter((l) => l.startsWith("stop"))).toEqual(["stop 0", "stop 1", "stop 2", "stop 0"]);
  });

  test.skipIf(!has("jq"))("SubagentStop applies the Cursor matcher to the unprefixed subagent name", () => {
    const { out, opts } = plugin();
    runAdapter("SubagentStop", { session_id: "s", agent_id: "a1", agent_type: "general-purpose" }, opts);
    runAdapter("SubagentStop", { session_id: "s", agent_id: "a2", agent_type: "advisor:advisor-subagent" }, opts);
    expect(log(out)).toEqual(["subagent advisor-subagent"]);
  });

  test("a failing Cursor hook is reported, never thrown, and blocks nothing", () => {
    const root = fakePlugin({ stop: [{ command: "exit 7" }] }, {});
    const out = tmp();
    const { output, errors } = runAdapter("Stop", { session_id: "s" }, { pluginRoot: root, projectDir: out, dataDir: out });
    expect(output).toBeNull();
    expect(errors[0]).toContain("exited 7");
  });
});

describe("the vendored plugins", () => {
  const hookFiles = (plugin) => ({
    manifest: JSON.parse(fs.readFileSync(path.join(REPO, "plugins", plugin, ".claude-plugin", "plugin.json"), "utf8")),
    agentHooks: JSON.parse(fs.readFileSync(path.join(REPO, "plugins", plugin, "agent-hooks", "hooks.json"), "utf8")),
  });

  test("each plugin with Cursor hooks points Claude Code and Codex at its generated agent hooks", () => {
    for (const plugin of fs.readdirSync(path.join(REPO, "plugins"))) {
      const cursorHooks = path.join(REPO, "plugins", plugin, "hooks", "hooks.json");
      if (plugin === "trust-ladder" || !fs.existsSync(cursorHooks)) continue;
      const { manifest, agentHooks } = hookFiles(plugin);
      expect(manifest.hooks).toBe("./agent-hooks/hooks.json");
      expect(fs.existsSync(path.join(REPO, "plugins", plugin, "agent-hooks", "cursor-hook-adapter.mjs"))).toBe(true);
      for (const [event, entries] of Object.entries(agentHooks.hooks)) {
        expect(entries[0].hooks[0].command).toBe(`node "\${CLAUDE_PLUGIN_ROOT}/agent-hooks/cursor-hook-adapter.mjs" ${event}`);
      }
    }
    expect(Object.keys(hookFiles("advisor").agentHooks.hooks).sort()).toEqual(["PostToolUse", "Stop", "SubagentStop"]);
    expect(Object.keys(hookFiles("continual-learning").agentHooks.hooks)).toEqual(["Stop"]);
  });

  test.skipIf(!has("jq"))("advisor: an edit then a finished turn asks for the pre-completion consult once, and a consult is logged", () => {
    const project = tmp();
    const advisorDir = path.join(project, ".cursor", "advisor");
    fs.mkdirSync(advisorDir, { recursive: true });
    fs.writeFileSync(path.join(advisorDir, "state.json"), JSON.stringify({ enabled: true, model: "inherit-parent", nudge: true, conversation_id: null }));
    const opts = { pluginRoot: path.join(REPO, "plugins", "advisor"), projectDir: project, dataDir: path.join(project, "data") };
    const base = { session_id: "conv-1", transcript_path: path.join(project, "t.jsonl") };

    runAdapter("PostToolUse", { ...base, tool_name: "Edit", tool_input: { file_path: path.join(project, "src", "a.ts") } }, opts);
    expect(fs.existsSync(path.join(advisorDir, "pending"))).toBe(true);

    const asking = runAdapter("Stop", { ...base, stop_hook_active: false, last_assistant_message: "Should I also update the docs?" }, opts);
    expect(asking.output).toBeNull();

    const done = runAdapter("Stop", { ...base, stop_hook_active: false, last_assistant_message: "The fix is in." }, opts);
    expect(done.errors).toEqual([]);
    expect(done.output.decision).toBe("block");
    expect(done.output.reason).toStartWith("[Advisor] Files changed since the last advisor consult");
    expect(done.output.reason).toContain("(model: inherit-parent)");
    expect(runAdapter("Stop", { ...base, stop_hook_active: true, last_assistant_message: "Consulted." }, opts).output).toBeNull();

    runAdapter("SubagentStop", { ...base, agent_id: "adv", agent_type: "advisor:advisor-subagent", last_assistant_message: "Verdict: proceed." }, opts);
    const state = JSON.parse(fs.readFileSync(path.join(advisorDir, "state.json"), "utf8"));
    expect(state.consults).toBe(1);
    expect(state.conversation_id).toBe("conv-1");
    expect(fs.readFileSync(path.join(advisorDir, "log.md"), "utf8")).toContain("Verdict: proceed.");
  });

  test.skipIf(!has("bun"))("continual-learning: its cadence hook asks for a memory update once enough turns pass", () => {
    const project = tmp();
    const transcript = path.join(project, "t.jsonl");
    fs.writeFileSync(transcript, "{}\n");
    const env = { ...process.env, CONTINUAL_LEARNING_MIN_TURNS: "2", CONTINUAL_LEARNING_MIN_MINUTES: "60" };
    const opts = { pluginRoot: path.join(REPO, "plugins", "continual-learning"), projectDir: project, dataDir: path.join(project, "data"), env };
    const turn = () => runAdapter("Stop", { session_id: "c", stop_hook_active: false, transcript_path: transcript }, opts);

    expect(turn().output).toBeNull();
    const second = turn();
    expect(second.errors).toEqual([]);
    expect(second.output.reason).toStartWith("Run the `continual-learning` skill now.");
    expect(turn().output).toBeNull();
    expect(fs.existsSync(path.join(project, ".cursor", "hooks", "state", "continual-learning.json"))).toBe(true);
  });
});
