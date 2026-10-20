#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `Usage: node scripts/e2e/kit-e2e.mjs [--artifact PATH]

Runs the kit's plugin-side scripts and hooks for real in temp directories: the SessionStart and
UserPromptSubmit hooks, link-skills.sh, the hook adapter (a stand-in plugin and the vendored advisor and
continual-learning plugins), install-kit-skills.sh, install-pack.mjs for every
pack, and the agnostic pack's agent-checks on its good and bad fixture repositories.

Writes a JSON transcript to --artifact (default: kit-e2e-transcript.json in the temp run folder). Its digest
is the SHA-256 of every step name, result, and observed value with temp paths normalized, so two runs of the
same code print the same digest. Exits 1 when any step fails.
`;

const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const TRUST_LADDER = path.join(KIT, "plugins", "trust-ladder");
const HOOK = path.join(TRUST_LADDER, "hooks", "faker-kit-hook.mjs");
const SETUP_SCRIPTS = path.join(TRUST_LADDER, "skills", "setup-faker-kit", "scripts");
const LINK_SKILLS = path.join(SETUP_SCRIPTS, "link-skills.sh");
const INSTALL_PACK = path.join(SETUP_SCRIPTS, "install-pack.mjs");
const INSTALL_KIT_SKILLS = path.join(TRUST_LADDER, "skills", "setup-cloud-agents", "scripts", "install-kit-skills.sh");
const ADAPTER = path.join(KIT, "upstream", "agent-hooks", "hook-adapter.mjs");
const PACKS = path.join(TRUST_LADDER, "packs");
const COMPAT_START = "<!-- faker-kit:compat:start -->";
const COMPAT_END = "<!-- faker-kit:compat:end -->";

const args = process.argv.slice(2);
if (args.includes("-h") || args.includes("--help")) {
  process.stdout.write(USAGE);
  process.exit(0);
}

const work = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "faker-kit-e2e-")));
const artifact = args.includes("--artifact") ? path.resolve(args[args.indexOf("--artifact") + 1]) : path.join(work, "kit-e2e-transcript.json");
const steps = [];
let counter = 0;

const tmp = (label = "dir") => {
  const dir = path.join(work, `${String(++counter).padStart(3, "0")}-${label}`);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
};
const read = (...parts) => fs.readFileSync(path.join(...parts), "utf8");
const write = (file, text, mode) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, mode ? { mode } : undefined);
};
const run = (command, argv, options = {}) => spawnSync(command, argv, { encoding: "utf8", timeout: 180_000, ...options });
const git = (cwd, ...argv) => run("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...argv], { cwd });
const exists = (...parts) => fs.existsSync(path.join(...parts));

const normalize = (value) => {
  let text = JSON.stringify(value) ?? "null";
  for (const root of [work, os.tmpdir(), KIT]) text = text.split(root).join(root === KIT ? "<kit>" : "<tmp>");
  return JSON.parse(text);
};

function step(name, body) {
  let outcome;
  try {
    outcome = body();
  } catch (error) {
    outcome = { ok: false, observed: { error: String(error.message ?? error) } };
  }
  const record = { step: name, ok: Boolean(outcome.ok), observed: normalize(outcome.observed ?? null) };
  steps.push(record);
  const label = outcome.skipped ? "skip" : record.ok ? "ok  " : "FAIL";
  process.stdout.write(`${label} ${name}${record.ok ? "" : `: ${JSON.stringify(record.observed).slice(0, 300)}`}${outcome.skipped ? `: ${record.observed.skipped}` : ""}\n`);
}

function runHook(event, input, data) {
  const done = run("node", [HOOK, event], { input: JSON.stringify(input), env: { ...process.env, CLAUDE_PLUGIN_DATA: data } });
  return { status: done.status, stdout: done.stdout.trim() ? JSON.parse(done.stdout) : null, stderr: done.stderr };
}

const compatText = () => {
  const text = read(TRUST_LADDER, "references", "compat.md");
  return text.slice(text.indexOf(COMPAT_START), text.indexOf(COMPAT_END) + COMPAT_END.length);
};

step("SessionStart injects the compat block only when the project lacks it, found at or above the working directory up to the repository root", () => {
  const repo = tmp("repo");
  fs.mkdirSync(path.join(repo, ".git"));
  fs.mkdirSync(path.join(repo, "app", "src"), { recursive: true });
  const data = tmp("data");
  const nested = path.join(repo, "app", "src");
  const first = runHook("session-start", { cwd: nested, session_id: "a" }, data);
  write(path.join(repo, "AGENTS.md"), `# Rules\n\n${compatText()}\n`);
  const agents = runHook("session-start", { cwd: nested, session_id: "a" }, data);
  fs.rmSync(path.join(repo, "AGENTS.md"));
  write(path.join(repo, "CLAUDE.md"), compatText());
  const claude = runHook("session-start", { cwd: nested, session_id: "a" }, data);
  const block = first.stdout?.hookSpecificOutput;
  return {
    ok: first.status === 0 && block?.hookEventName === "SessionStart" && block.additionalContext === compatText() && block.additionalContext.startsWith(COMPAT_START)
      && block.additionalContext.endsWith(COMPAT_END) && agents.stdout === null && claude.stdout === null,
    observed: { injectedChars: block?.additionalContext?.length, withAgentsMd: agents.stdout, withClaudeMd: claude.stdout },
  };
});

step("UserPromptSubmit pins /faker-mode on every invocation form, reminds on later prompts, and unpins when the user opts out", () => {
  const data = tmp("data");
  const prompt = (session, text) => runHook("user-prompt-submit", { cwd: data, session_id: session, prompt: text }, data).stdout?.hookSpecificOutput?.additionalContext ?? null;
  const observed = {};
  for (const [index, entry] of ["/faker-mode fix it", "/pstack:faker-mode go", "please /faker-mode"].entries()) {
    const session = `pin-${index}`;
    observed[`enter ${entry}`] = prompt(session, entry);
    observed[`later ${entry}`] = prompt(session, "now add a test");
  }
  for (const [index, entry] of ["exit faker mode", "please stop faker-mode now", "/faker-mode off", "opt out of faker mode"].entries()) {
    const session = `unpin-${index}`;
    prompt(session, "/faker-mode go");
    observed[`leave ${entry}`] = [prompt(session, entry), prompt(session, "thanks")];
  }
  observed.prose = [prompt("prose", "is faker-mode worth it?"), prompt("prose", "hello")];
  prompt("one", "/faker-mode go");
  observed.otherSession = prompt("two", "hello");
  const reminder = observed["later /faker-mode fix it"];
  const entered = Object.entries(observed).filter(([key]) => key.startsWith("enter")).every(([, value]) => value === null);
  const later = Object.entries(observed).filter(([key]) => key.startsWith("later")).every(([, value]) => value === reminder);
  const left = Object.entries(observed).filter(([key]) => key.startsWith("leave")).every(([, value]) => value[0] === null && value[1] === null);
  return {
    ok: entered && later && left && reminder.includes("/faker-mode is pinned") && reminder.includes('say "exit faker mode"') && observed.prose.every((v) => v === null) && observed.otherSession === null,
    observed,
  };
});

step("the hook refuses an unknown event", () => {
  const done = run("node", [HOOK, "pre-commit"], { input: "{}" });
  return { ok: done.status === 1 && done.stderr.includes('unknown event "pre-commit"'), observed: { status: done.status, stderr: done.stderr.trim() } };
});

step("faker-agent and the compat block tell agents to run /faker-mode as a command", () => {
  const agent = read(KIT, "plugins", "pstack", "agents", "faker-agent.md");
  const compat = read(TRUST_LADDER, "references", "compat.md");
  return { ok: agent.includes("`/faker-mode`") && compat.includes("`/faker-mode`"), observed: null };
});

step("link-skills.sh refuses to replace a real skill directory and reports the conflict", () => {
  const repo = tmp("repo");
  for (const root of [".claude/skills", ".agents/skills"]) write(path.join(repo, root, "verify-app", "SKILL.md"), "---\nname: verify-app\ndescription: x\n---\n");
  const done = run(LINK_SKILLS, [repo]);
  return { ok: done.status === 1 && done.stderr.includes("conflict: .claude/skills/verify-app and .agents/skills/verify-app"), observed: { status: done.status, stderr: done.stderr.trim() } };
});

function adapter(pluginRoot, event, input, project, data, env = {}) {
  const done = run("node", [path.join(pluginRoot, "agent-hooks", "hook-adapter.mjs"), event], {
    input: JSON.stringify(input),
    env: { ...process.env, CLAUDE_PROJECT_DIR: project, CLAUDE_PLUGIN_DATA: data, ...env },
  });
  return { status: done.status, output: done.stdout.trim() ? JSON.parse(done.stdout) : null, stderr: done.stderr.trim() };
}

function standInPlugin(hooks, scripts) {
  const root = tmp("plugin");
  write(path.join(root, "agent-hooks", "hook-adapter.mjs"), read(ADAPTER));
  write(path.join(root, "hooks", "hooks.json"), JSON.stringify({ version: 1, hooks }));
  for (const [name, body] of Object.entries(scripts)) write(path.join(root, "hooks", name), `#!/bin/bash\n${body}\n`, 0o755);
  return root;
}

const lines = (file) => (fs.existsSync(file) ? read(file).trim().split("\n").map((line) => JSON.parse(line)) : []);

step("the hook adapter runs upstream afterFileEdit hooks only for edit tools, with absolute paths", () => {
  const out = tmp("out");
  const project = tmp("project");
  const root = standInPlugin({ afterFileEdit: [{ command: `bash "\${CLAUDE_PLUGIN_ROOT}/hooks/edit.sh"` }] }, { "edit.sh": `cat >> ${out}/edit.log; echo >> ${out}/edit.log` });
  const data = tmp("data");
  const base = { session_id: "s", model: "opus" };
  const read_ = adapter(root, "PostToolUse", { ...base, tool_name: "Read", tool_input: { file_path: "a" } }, project, data);
  const write_ = adapter(root, "PostToolUse", { ...base, tool_name: "Write", tool_input: { file_path: "b.ts" } }, project, data);
  const notebook = adapter(root, "PostToolUse", { ...base, tool_name: "NotebookEdit", tool_input: { notebook_path: "/r/n.ipynb" } }, project, data);
  const seen = fs.existsSync(path.join(out, "edit.log")) ? read(out, "edit.log").split("\n").filter(Boolean).map((line) => JSON.parse(line)) : [];
  return {
    ok: [read_, write_, notebook].every((r) => r.status === 0 && r.output === null) && seen.length === 2 && seen[0].file_path === path.join(project, "b.ts") && seen[0].conversation_id === "s"
      && seen[0].hook_event_name === "afterFileEdit" && seen[0].workspace_roots[0] === project && seen[1].file_path === "/r/n.ipynb",
    observed: { runs: seen.map((s) => s.file_path) },
  };
});

step("the hook adapter runs afterAgentResponse then stop in the project and turns a follow-up into a block, honoring loop_limit", () => {
  const out = tmp("out");
  const project = tmp("project");
  const root = standInPlugin(
    {
      afterAgentResponse: [{ command: `bash "\${CLAUDE_PLUGIN_ROOT}/hooks/response.sh"` }],
      stop: [{ command: `bash "\${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh"`, loop_limit: 2 }],
    },
    {
      "response.sh": `cat > ${out}/response.json; pwd > ${out}/cwd`,
      "stop.sh": `cat >> ${out}/stop.log; echo >> ${out}/stop.log; echo '{"followup_message": "run the review"}'`,
    },
  );
  const data = tmp("data");
  const stop = (active) => adapter(root, "Stop", { session_id: "s", stop_hook_active: active, last_assistant_message: "All done." }, project, data);
  const first = stop(false);
  const response = JSON.parse(read(out, "response.json"));
  const sequence = [first.output, stop(true).output, stop(true).output, stop(false).output];
  const counts = read(out, "stop.log").split("\n").filter(Boolean).map((line) => JSON.parse(line).loop_count);
  return {
    ok: first.stderr === "" && response.text === "All done." && fs.realpathSync(read(out, "cwd").trim()) === project
      && JSON.stringify(first.output) === JSON.stringify({ decision: "block", reason: "run the review" }) && sequence.map((o) => o !== null).join() === "true,true,false,true" && counts.join() === "0,1,2,0",
    observed: { blocks: sequence.map((o) => o !== null), loopCounts: counts },
  };
});

step("the hook adapter applies a SubagentStop matcher to the unprefixed subagent name", () => {
  const out = tmp("out");
  const project = tmp("project");
  const root = standInPlugin({ subagentStop: [{ command: `bash "\${CLAUDE_PLUGIN_ROOT}/hooks/subagent.sh"`, matcher: "^advisor-subagent$" }] }, { "subagent.sh": `cat >> ${out}/subagent.log; echo >> ${out}/subagent.log` });
  const data = tmp("data");
  adapter(root, "SubagentStop", { session_id: "s", agent_id: "a1", agent_type: "general-purpose" }, project, data);
  adapter(root, "SubagentStop", { session_id: "s", agent_id: "a2", agent_type: "advisor:advisor-subagent", last_assistant_message: "Verdict." }, project, data);
  const seen = lines(path.join(out, "subagent.log"));
  return { ok: seen.length === 1 && seen[0].subagent_type === "advisor-subagent" && seen[0].summary === "Verdict.", observed: { runs: seen.map((s) => s.subagent_type) } };
});

step("a failing upstream hook is reported on stderr, exits 0, and blocks nothing", () => {
  const project = tmp("project");
  const root = standInPlugin({ stop: [{ command: "exit 7" }] }, {});
  const done = adapter(root, "Stop", { session_id: "s" }, project, tmp("data"));
  return { ok: done.status === 0 && done.output === null && done.stderr.includes("exited 7"), observed: { status: done.status, stderr: done.stderr } };
});

step("the hook adapter refuses an unsupported event without failing the agent", () => {
  const root = standInPlugin({}, {});
  const done = adapter(root, "PreToolUse", { session_id: "s" }, tmp("project"), tmp("data"));
  return { ok: done.status === 0 && done.output === null && done.stderr.includes('unsupported event "PreToolUse"'), observed: { stderr: done.stderr } };
});

step("each vendored plugin with upstream hooks points Claude Code at its generated agent hooks", () => {
  const checked = [];
  for (const plugin of fs.readdirSync(path.join(KIT, "plugins")).sort()) {
    if (plugin === "trust-ladder" || !exists(KIT, "plugins", plugin, "hooks", "hooks.json")) continue;
    const manifest = JSON.parse(read(KIT, "plugins", plugin, ".claude-plugin", "plugin.json"));
    const agentHooks = JSON.parse(read(KIT, "plugins", plugin, "agent-hooks", "hooks.json"));
    const events = Object.keys(agentHooks.hooks);
    const wired = manifest.hooks === "./agent-hooks/hooks.json" && exists(KIT, "plugins", plugin, "agent-hooks", "hook-adapter.mjs")
      && events.every((event) => agentHooks.hooks[event][0].hooks[0].command === `node "\${CLAUDE_PLUGIN_ROOT}/agent-hooks/hook-adapter.mjs" ${event}`);
    checked.push({ plugin, wired, events });
  }
  const events = (name) => checked.find((c) => c.plugin === name)?.events.sort();
  return { ok: checked.length > 0 && checked.every((c) => c.wired) && JSON.stringify(events("advisor")) === JSON.stringify(["PostToolUse", "Stop", "SubagentStop"]) && JSON.stringify(events("continual-learning")) === JSON.stringify(["Stop"]), observed: checked };
});

step("advisor: an edit then a finished turn asks for the pre-completion consult once, and a consult is logged", () => {
  const project = tmp("project");
  const advisorDir = path.join(project, ".agents", "advisor");
  write(path.join(advisorDir, "state.json"), JSON.stringify({ enabled: true, model: "inherit-parent", nudge: true, conversation_id: null }));
  const plugin = path.join(KIT, "plugins", "advisor");
  const data = tmp("data");
  const base = { session_id: "conv-1", transcript_path: path.join(project, "t.jsonl") };
  adapter(plugin, "PostToolUse", { ...base, tool_name: "Edit", tool_input: { file_path: path.join(project, "src", "a.ts") } }, project, data);
  const pending = exists(advisorDir, "pending");
  const asking = adapter(plugin, "Stop", { ...base, stop_hook_active: false, last_assistant_message: "Should I also update the docs?" }, project, data);
  const done = adapter(plugin, "Stop", { ...base, stop_hook_active: false, last_assistant_message: "The fix is in." }, project, data);
  const after = adapter(plugin, "Stop", { ...base, stop_hook_active: true, last_assistant_message: "Consulted." }, project, data);
  adapter(plugin, "SubagentStop", { ...base, agent_id: "adv", agent_type: "advisor:advisor-subagent", last_assistant_message: "Verdict: proceed." }, project, data);
  const state = JSON.parse(read(advisorDir, "state.json"));
  return {
    ok: pending && asking.output === null && done.stderr === "" && done.output?.decision === "block" && done.output.reason.startsWith("[Advisor] Files changed since the last advisor consult")
      && done.output.reason.includes("(model: inherit-parent)") && after.output === null && state.consults === 1 && state.conversation_id === "conv-1" && read(advisorDir, "log.md").includes("Verdict: proceed."),
    observed: { pending, askedAQuestion: asking.output, consults: state.consults, reasonStart: done.output?.reason?.slice(0, 60) },
  };
});

step("continual-learning: its cadence hook asks for a memory update once enough turns pass", () => {
  const project = tmp("project");
  const transcript = path.join(project, "t.jsonl");
  write(transcript, "{}\n");
  const plugin = path.join(KIT, "plugins", "continual-learning");
  const data = tmp("data");
  const env = { CONTINUAL_LEARNING_MIN_TURNS: "2", CONTINUAL_LEARNING_MIN_MINUTES: "60" };
  const turn = () => adapter(plugin, "Stop", { session_id: "c", stop_hook_active: false, transcript_path: transcript }, project, data, env);
  const outputs = [turn(), turn(), turn()];
  return {
    ok: outputs[0].output === null && outputs[1].stderr === "" && outputs[1].output?.reason?.startsWith("Run the `continual-learning` skill now.") && outputs[2].output === null
      && exists(project, ".agents", "hooks", "state", "continual-learning.json"),
    observed: { asked: outputs.map((o) => o.output !== null) },
  };
});

function fakeKit(skills) {
  const kit = tmp("kit");
  for (const [plugin, name] of skills) write(path.join(kit, "plugins", plugin, "skills", name, "SKILL.md"), `---\nname: ${name}\ndescription: ${plugin}\n---\n`);
  git(kit, "init", "-q");
  git(kit, "add", ".");
  git(kit, "commit", "-qm", "kit");
  return kit;
}

const installSkills = (kit, work_, ...extra) => run("/bin/bash", [INSTALL_KIT_SKILLS, "--source", kit, "--ref", "HEAD", "--checkout", path.join(work_, "kit"), "--dest", path.join(work_, "skills"), ...extra]);
const owner = (work_, name) => /description: (.*)/.exec(read(work_, "skills", name, "SKILL.md"))[1];
const head = (repo) => git(repo, "rev-parse", "HEAD").stdout.trim();
const DEFAULT_SOURCE = "https://github.com/tsnAnh/faker-kit.git";

function installFromDefaultSource(home, target) {
  const gitconfig = path.join(home, "gitconfig");
  write(gitconfig, `[url "${target}"]\n\tinsteadOf = ${DEFAULT_SOURCE}\n`);
  return run("/bin/bash", [INSTALL_KIT_SKILLS, "--ref", "HEAD"], { env: { ...process.env, HOME: home, GIT_CONFIG_GLOBAL: gitconfig, GIT_CONFIG_NOSYSTEM: "1" } });
}

step("install-kit-skills.sh links every plugin skill, lets thermos win the shared review skill, and drops skills a newer commit removed", () => {
  const kit = fakeKit([["trust-ladder", "check-agent-compatibility"], ["pstack", "faker-mode"], ["pstack", "old-skill"], ["team-workflows", "shared"], ["thermos", "shared"]]);
  const place = tmp("install");
  const first = installSkills(kit, place);
  const linked = fs.readdirSync(path.join(place, "skills")).sort();
  fs.rmSync(path.join(kit, "plugins", "pstack", "skills", "old-skill"), { recursive: true });
  git(kit, "add", "-A");
  git(kit, "commit", "-qm", "drop");
  const second = installSkills(kit, place);
  return {
    ok: first.status === 0 && linked.join() === "check-agent-compatibility,faker-mode,old-skill,shared" && owner(place, "shared") === "thermos" && second.status === 0 && second.stdout.includes("removed stale")
      && fs.readdirSync(path.join(place, "skills")).sort().join() === "check-agent-compatibility,faker-mode,shared",
    observed: { linked, afterDrop: fs.readdirSync(path.join(place, "skills")).sort() },
  };
});

step("install-kit-skills.sh keeps trust-ladder's precedence, and never replaces a skill or link the user already has", () => {
  const kit = fakeKit([["trust-ladder", "shared"], ["pstack", "shared"]]);
  const place = tmp("install");
  fs.mkdirSync(path.join(place, "skills"), { recursive: true });
  fs.symlinkSync(path.join(place, "missing-user-skill"), path.join(place, "skills", "shared"));
  const conflict = installSkills(kit, place);
  const mine = fakeKit([["trust-ladder", "check-agent-compatibility"]]);
  const own = tmp("install");
  write(path.join(own, "skills", "check-agent-compatibility", "SKILL.md"), "mine");
  const real = installSkills(mine, own);
  return {
    ok: conflict.status === 1 && conflict.stderr.includes("links elsewhere; left as is") && conflict.stdout.includes("skipped shared from pstack: trust-ladder already provides it")
      && fs.readlinkSync(path.join(place, "skills", "shared")) === path.join(place, "missing-user-skill") && real.status === 1 && real.stderr.includes("is a real directory")
      && read(own, "skills", "check-agent-compatibility", "SKILL.md") === "mine",
    observed: { conflictStatus: conflict.status, realStatus: real.status },
  };
});

step("install-kit-skills.sh defaults to the faker-kit repository, upgrades an install in place, and keeps the links when a fetch fails", () => {
  const kit = fakeKit([["trust-ladder", "check-agent-compatibility"]]);
  const home = fs.realpathSync(tmp("home"));
  const link = (name) => path.join(home, ".agents", "skills", name);
  const target = (name) => path.join(home, ".faker-kit", "plugins", "trust-ladder", "skills", name);
  const first = installFromDefaultSource(home, kit);
  const firstLinked = fs.readlinkSync(link("check-agent-compatibility")) === target("check-agent-compatibility");
  const newer = tmp("newer");
  git(newer, "clone", "-q", kit, ".");
  write(path.join(newer, "plugins", "pstack", "skills", "faker-mode", "SKILL.md"), "---\nname: faker-mode\ndescription: pstack\n---\n");
  git(newer, "add", ".");
  git(newer, "commit", "-qm", "newer");
  const upgraded = installFromDefaultSource(home, newer);
  const upgradedHead = head(path.join(home, ".faker-kit")) === head(newer);
  const before = head(path.join(home, ".faker-kit"));
  const failed = installFromDefaultSource(home, path.join(home, "missing-kit"));
  return {
    ok: first.status === 0 && firstLinked && upgraded.status === 0 && upgradedHead && fs.readdirSync(path.join(home, ".agents", "skills")).sort().join() === "check-agent-compatibility,faker-mode"
      && failed.status !== 0 && failed.stderr.includes("missing-kit") && head(path.join(home, ".faker-kit")) === before && fs.readlinkSync(link("check-agent-compatibility")) === target("check-agent-compatibility"),
    observed: { upgraded: upgradedHead, failedStatusNonZero: failed.status !== 0 },
  };
});

step("with the real claude CLI every plugin id in the settings template installs from this kit", () => {
  if (run("claude", ["--version"]).status !== 0) {
    if (process.env.CI) return { ok: false, observed: { error: "the claude CLI is not installed, and CI must run this step" } };
    return { ok: true, skipped: true, observed: { skipped: "the claude CLI is not installed" } };
  }
  const config = tmp("claude-config");
  const env = { ...process.env, CLAUDE_CONFIG_DIR: config };
  const cli = (...argv) => run("claude", ["plugin", ...argv], { env, timeout: 120_000 });
  const ids = Object.keys(JSON.parse(read(TRUST_LADDER, "templates", "claude-settings.json")).enabledPlugins);
  const added = cli("marketplace", "add", KIT);
  const known = Object.keys(JSON.parse(read(config, "plugins", "known_marketplaces.json")));
  const installs = ids.map((id) => cli("install", id).status);
  const installed = Object.keys(JSON.parse(read(config, "plugins", "installed_plugins.json")).plugins).sort();
  return {
    ok: added.status === 0 && known.includes("faker-kit") && installs.every((status) => status === 0) && installed.join() === [...ids].sort().join(),
    observed: { installed },
  };
});

const packNames = fs.readdirSync(PACKS).filter((name) => exists(PACKS, name, "pack.json")).sort();
const installPack = (...argv) => run("node", [INSTALL_PACK, ...argv]);

step("every pack has a valid manifest, a test, fixtures, a README, and installs into an empty repository", () => {
  const problems = [];
  const installs = {};
  for (const pack of packNames) {
    const manifest = JSON.parse(read(PACKS, pack, "pack.json"));
    if (manifest.name !== pack) problems.push(`${pack}: name`);
    if (!(manifest.check?.length > 0)) problems.push(`${pack}: check`);
    if (typeof manifest.baseline !== "string") problems.push(`${pack}: baseline`);
    for (const file of ["test.sh", "README.md"]) if (!exists(PACKS, pack, file)) problems.push(`${pack}: ${file}`);
    for (const kind of ["bad", "good"]) if (!exists(PACKS, pack, "fixtures", kind)) problems.push(`${pack}: fixtures/${kind}`);
    if (pack !== "agnostic" && !exists(PACKS, pack, manifest.ci)) problems.push(`${pack}: ci`);
    for (const merge of manifest.merge ?? []) if (!exists(PACKS, pack, merge.from)) problems.push(`${pack}: merge ${merge.from}`);
    const repo = tmp("repo");
    const done = installPack(pack, "--repo", repo);
    installs[pack] = done.status;
    if (done.status !== 0) problems.push(`${pack}: install exited ${done.status}`);
    else if (!read(repo, ".agents", "check-commands").includes(manifest.check[0])) problems.push(`${pack}: check not registered`);
  }
  return { ok: packNames.length > 0 && problems.length === 0, observed: { packs: packNames, problems } };
});

step("install-pack.mjs copies files, registers checks once, and keeps executable bits", () => {
  const repo = tmp("repo");
  const first = installPack("agnostic", "--repo", repo, "--with", "locale-keys");
  const commands = read(repo, ".agents", "check-commands");
  const second = installPack("agnostic", "--repo", repo, "--with", "locale-keys");
  const after = read(repo, ".agents", "check-commands");
  return {
    ok: first.status === 0 && first.stdout.includes("scripts/check.sh") && (fs.statSync(path.join(repo, "scripts", "check.sh")).mode & 0o111) !== 0
      && commands.includes("node scripts/agent-checks/ratchet.mjs check") && commands.includes("node scripts/agent-checks/locale-keys.mjs") && second.status === 0 && !second.stdout.includes("Copied:")
      && after === commands && (after.match(/ratchet\.mjs check/g) ?? []).length === 1,
    observed: { checks: commands.trim().split("\n").filter((line) => !line.startsWith("#")) },
  };
});

step("install-pack.mjs leaves an existing different file alone and says to merge it", () => {
  const repo = tmp("repo");
  write(path.join(repo, "clippy.toml"), "too-many-arguments-threshold = 9\n");
  const done = installPack("rust", "--repo", repo);
  return {
    ok: done.status === 0 && read(repo, "clippy.toml") === "too-many-arguments-threshold = 9\n" && done.stdout.includes("clippy.toml already exists") && done.stdout.includes("merge/cargo-lints.toml into Cargo.toml"),
    observed: { status: done.status },
  };
});

step("install-pack.mjs --dry-run writes nothing, and unknown packs and optional checks fail with a clear message", () => {
  const repo = tmp("repo");
  const dry = installPack("python", "--repo", repo, "--dry-run");
  const unknownPack = installPack("cobol", "--repo", tmp("repo"));
  const unknownCheck = installPack("agnostic", "--repo", tmp("repo"), "--with", "spellcheck");
  return {
    ok: dry.status === 0 && dry.stdout.includes("ruff.toml") && fs.readdirSync(repo).length === 0 && unknownPack.status === 1 && unknownPack.stderr.includes('no pack named "cobol"')
      && unknownCheck.status === 1 && unknownCheck.stderr.includes("no optional check named spellcheck"),
    observed: { unknownPack: unknownPack.stderr.trim().slice(0, 40), unknownCheck: unknownCheck.stderr.trim() },
  };
});

step("the agnostic pack's agent-checks (ratchet, import boundaries, rule fixtures, no new comments, locale keys) fail on the bad fixture repository and pass on the good one", () => {
  const done = run("bash", [path.join(PACKS, "agnostic", "test.sh")]);
  return { ok: done.status === 0 && done.stdout.trim() === "agnostic pack: bad fixture fails, good fixture passes", observed: { stdout: done.stdout.trim(), stderr: done.stderr.trim().slice(0, 300) } };
});

const failed = steps.filter((s) => !s.ok);
const digest = crypto.createHash("sha256").update(JSON.stringify(steps.map((s) => [s.step, s.ok, s.observed]))).digest("hex");
write(artifact, `${JSON.stringify({ ok: failed.length === 0, steps, digest }, null, 2)}\n`);
process.stdout.write(`transcript: ${artifact} sha256=${digest}\n`);
if (args.includes("--artifact")) fs.rmSync(work, { recursive: true, force: true });
process.exit(failed.length === 0 ? 0 : 1);
