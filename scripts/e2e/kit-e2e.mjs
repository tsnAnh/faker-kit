#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const USAGE = `Usage: node scripts/e2e/kit-e2e.mjs [--artifact PATH]

Runs the kit's plugin-side scripts and hooks for real in temp directories: the SessionStart and
UserPromptSubmit hooks, link-skills.sh, the ratchet and import-boundaries checks on a clean and a bad repository,
the hook adapter (a stand-in plugin and the vendored advisor and continual-learning plugins), and
install-kit-skills.sh, then installs every plugin into a clean Claude Code.

Writes a JSON transcript to --artifact (default: kit-e2e-transcript.json in the temp run folder). Its digest
is the SHA-256 of every step name, result, and observed value with temp paths normalized, so two runs of the
same code print the same digest. Exits 1 when any step fails.
`;

const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const TRUST_LADDER = path.join(KIT, "plugins", "trust-ladder");
const HOOK = path.join(TRUST_LADDER, "hooks", "faker-kit-hook.mjs");
const SETUP_SCRIPTS = path.join(TRUST_LADDER, "skills", "setup-faker-kit", "scripts");
const LINK_SKILLS = path.join(SETUP_SCRIPTS, "link-skills.sh");
const INSTALL_KIT_SKILLS = path.join(TRUST_LADDER, "skills", "setup-cloud-agents", "scripts", "install-kit-skills.sh");
const RATCHET = path.join(KIT, "scripts", "agent-checks", "ratchet.mjs");
const IMPORT_BOUNDARIES = path.join(KIT, "scripts", "agent-checks", "import-boundaries.mjs");
const ADAPTER = path.join(KIT, "upstream", "agent-hooks", "hook-adapter.mjs");
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

step("faker-agent reads the faker-mode SKILL.md itself and nothing requires a /faker-mode prompt prefix", () => {
  const agent = read(KIT, "plugins", "pstack", "agents", "faker-agent.md");
  const compat = read(TRUST_LADDER, "references", "compat.md");
  return { ok: agent.includes("`SKILL.md` in full") && !agent.includes("run `/faker-mode`") && !compat.includes("start the prompt with `/faker-mode`"), observed: null };
});

step("link-skills.sh refuses to replace a real skill directory and reports the conflict", () => {
  const repo = tmp("repo");
  for (const root of [".claude/skills", ".agents/skills"]) write(path.join(repo, root, "verify-app", "SKILL.md"), "---\nname: verify-app\ndescription: x\n---\n");
  const done = run(LINK_SKILLS, [repo]);
  return { ok: done.status === 1 && done.stderr.includes("conflict: .claude/skills/verify-app and .agents/skills/verify-app"), observed: { status: done.status, stderr: done.stderr.trim() } };
});

step("ratchet.mjs and import-boundaries.mjs pass a clean repository and fail an added suppression, an added GraphQL read, and a forbidden import", () => {
  const repo = tmp("repo");
  git(repo, "init", "-q");
  for (const config of ["ratchet.json", "import-boundaries.json"]) write(path.join(repo, ".agents", config), read(KIT, ".agents", config));
  write(path.join(repo, ".agents", "ratchet-baseline.json"), JSON.stringify({ "python-noqa": { total: 1, perFile: { "hermes/lib/faker_kit_merge.py": 1 } } }));
  const lib = "hermes/lib/faker_kit_merge.py";
  const guard = "hermes/plugin/faker_kit_guard/__init__.py";
  write(path.join(repo, lib), "import json  # noqa\n");
  write(path.join(repo, guard), "import json\n");
  write(path.join(repo, "hermes", "bin", "faker_kit.py"), "from faker_kit_merge import decide\n");
  git(repo, "add", "-A");
  git(repo, "commit", "-qm", "clean");
  const summary = (done) => ({ status: done.status, stdout: done.stdout.trim(), stderr: done.stderr.trim().split("\n") });
  const ratchet = () => summary(run("node", [RATCHET, "check"], { cwd: repo }));
  const boundaries = () => summary(run("node", [IMPORT_BOUNDARIES], { cwd: repo }));
  const withEdit = (file, text, check) => {
    fs.appendFileSync(path.join(repo, file), text);
    const found = check();
    git(repo, "checkout", "--", ".");
    return found;
  };
  const clean = { ratchet: ratchet(), boundaries: boundaries() };
  const suppression = withEdit(lib, "x = 1  # noqa\n", ratchet);
  const graphql = withEdit(lib, 'subprocess.run(["gh", "pr", "view", "1"])\n', ratchet);
  const guardImport = withEdit(guard, "import faker_kit_merge\n", boundaries);
  const libImport = withEdit(lib, "from faker_kit import main\n", boundaries);
  const again = { ratchet: ratchet(), boundaries: boundaries() };
  const silent = (found) => found.status === 0 && found.stderr.join("") === "";
  const reports = (found, text) => found.status === 1 && found.stderr.some((line) => line.includes(text));
  return {
    ok: silent(clean.ratchet) && silent(again.ratchet) && silent(clean.boundaries) && silent(again.boundaries) && clean.boundaries.stdout === "import-boundaries: 2 rule(s) hold"
      && reports(suppression, "python-noqa: 2 found, baseline allows 1.") && reports(suppression, `${lib} (1 -> 2)`)
      && reports(graphql, "gh-graphql-read: 1 found, baseline allows 0.")
      && reports(guardImport, `${guard}:2: imports faker_kit_merge [hermes-guard-stands-alone]`)
      && reports(libImport, `${lib}:2: imports faker_kit [hermes-lib-below-commands]`),
    observed: { clean, suppression, graphql, guardImport, libImport, again },
  };
});

step("check-commit-messages.mjs passes a clean message and fails a Claude Code footer line, a Co-Authored-By line naming an AI, and a Claude-Session link", () => {
  const repo = tmp("repo");
  git(repo, "init", "-q");
  git(repo, "commit", "-q", "--allow-empty", "-m", "base");
  const base = git(repo, "rev-parse", "HEAD").stdout.trim();
  const robot = String.fromCodePoint(0x1f916);
  const messages = {
    clean: "fix: handle empty input\n\nThe parser returned early on an empty string.\n",
    fullFooter: `fix: handle empty input\n\n${robot} Generated with [Claude Code](https://claude.com/claude-code)\n`,
    emojiLine: `fix: handle empty input\n\n${robot} anything at all\n`,
    emojiLineIndented: `fix: handle empty input\n\n  ${robot}\n`,
    bracketedLine: "fix: handle empty input\n\nGenerated with [Claude Code]\n",
    coAuthor: "fix: handle empty input\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
    session: "fix: handle empty input\n\nClaude-Session: https://claude.ai/code/session_1\n",
    emojiMidLine: `fix: handle empty input ${robot} quickly\n`,
  };
  const results = {};
  for (const [name, message] of Object.entries(messages)) {
    const file = path.join(tmp("message"), "message.txt");
    write(file, message);
    git(repo, "commit", "-q", "--allow-empty", "-F", file);
    const done = run("node", [path.join(KIT, "scripts", "check-commit-messages.mjs"), "--base", base], { cwd: repo });
    git(repo, "reset", "-q", "--hard", base);
    results[name] = { status: done.status, stdout: done.stdout.trim(), stderr: done.stderr.trim().replace(/^[0-9a-f]{12}/gm, "<sha>").split("\n") };
  }
  const failsWith = (name, text) => results[name].status === 1 && results[name].stderr.some((line) => line.includes(text));
  return {
    ok: results.clean.status === 0 && results.clean.stderr.join("") === "" && results.clean.stdout === "commit-messages: 1 commit(s) on this branch carry no AI references"
      && failsWith("fullFooter", "has a Claude Code footer line:") && failsWith("emojiLine", `has a Claude Code footer line: ${robot} anything at all`)
      && failsWith("emojiLineIndented", `has a Claude Code footer line: ${robot}`) && failsWith("bracketedLine", "has a Claude Code footer line: Generated with [Claude Code]")
      && failsWith("coAuthor", "has a Co-Authored-By line naming an AI:") && failsWith("session", "has a Claude-Session link:")
      && results.emojiMidLine.status === 0,
    observed: results,
  };
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

step("cloud-setup.sh --help prints its usage and exits before installing anything", () => {
  const help = run("bash", [path.join(KIT, "scripts", "cloud-setup.sh"), "--help"], { env: { PATH: "/usr/bin:/bin" } });
  return {
    ok: help.status === 0 && help.stdout.startsWith("Usage: scripts/cloud-setup.sh") && help.stdout.includes("changes nothing") && help.stderr === "",
    observed: { status: help.status, firstLine: help.stdout.split("\n")[0] },
  };
});

const SYNC_UPSTREAM = path.join(KIT, "scripts", "sync-upstream.mjs");
const FIXTURE_TOKEN = "ghs_FixtureTokenNotARealCredential0123456789";
const FIXTURE_AUTH_BASIC = Buffer.from(`x-access-token:${FIXTURE_TOKEN}`).toString("base64");
const RATE_LIMIT_STDERR = "error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429\nfatal: expected flush after ref listing";
const REAL_GIT = run("bash", ["-c", "command -v git"]).stdout.trim();
const GIT_STUB = `#!/usr/bin/env node
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const argv = process.argv.slice(2);
const dirAt = argv.indexOf("-C");
const dir = dirAt >= 0 ? argv[dirAt + 1] : process.cwd();
const sub = argv[dirAt >= 0 ? dirAt + 2 : 0];
const state = process.env.GIT_STUB_STATE;
const log = path.join(state, "calls.jsonl");
const calls = fs.existsSync(log) ? fs.readFileSync(log, "utf8").trim().split("\\n").filter(Boolean).map((line) => JSON.parse(line)) : [];
const config = path.join(dir, ".git", "config");
fs.appendFileSync(log, JSON.stringify({
  sub, argv, dir,
  gitConfigEnv: Object.fromEntries(Object.entries(process.env).filter(([name]) => name.startsWith("GIT_CONFIG_"))),
  tokenEnvNames: ["GITHUB_TOKEN", "GH_TOKEN"].filter((name) => process.env[name]),
  config: fs.existsSync(config) ? fs.readFileSync(config, "utf8") : null,
}) + "\\n");
if (sub === "fetch") {
  const fetchIndex = calls.filter((call) => call.sub === "fetch").length;
  const sleepMs = Number(process.env.GIT_STUB_FETCH_SLEEP_MS || 0);
  if (sleepMs) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, sleepMs);
  if (fetchIndex < Number(process.env.GIT_STUB_FAIL_FIRST || 0)) {
    process.stderr.write(process.env.GIT_STUB_FAIL_STDERR + "\\n");
    process.exit(128);
  }
  argv[argv.indexOf("origin")] = process.env.GIT_STUB_UPSTREAM;
}
const result = spawnSync(process.env.GIT_STUB_REAL, argv, { stdio: "inherit" });
process.exit(result.status ?? 1);
`;

function syncFixture(repoUrl) {
  const upstream = tmp("sync-upstream-source");
  write(path.join(upstream, "demo", ".example-plugin", "plugin.json"), `${JSON.stringify({ name: "demo", version: "1.0.0" })}\n`);
  write(path.join(upstream, "demo", "README.md"), "demo plugin\n");
  git(upstream, "init", "-q");
  git(upstream, "add", "-A");
  git(upstream, "commit", "-q", "-m", "upstream");
  const ref = head(upstream);
  const kit = tmp("sync-upstream-kit");
  write(path.join(kit, "scripts", "sync-upstream.mjs"), read(SYNC_UPSTREAM));
  write(path.join(kit, "upstream", "agent-hooks", "hook-adapter.mjs"), read(ADAPTER));
  write(path.join(kit, "upstream", "model-map.json"), `${JSON.stringify({ rules: [], allowedModelTokens: [], tokenExceptions: [] })}\n`);
  write(path.join(kit, "upstream", "source.json"), `${JSON.stringify({ repo: repoUrl, ref, manifestDir: ".example-plugin", plugins: ["demo"] })}\n`);
  const bin = tmp("sync-upstream-bin");
  write(path.join(bin, "git"), GIT_STUB, 0o755);
  const state = tmp("sync-upstream-state");
  const applied = run("node", [path.join(kit, "scripts", "sync-upstream.mjs"), "--src", upstream]);
  if (applied.status !== 0) throw new Error(`fixture sync failed: ${applied.stderr}`);
  const check = ({ failFirst = 0, stderr = RATE_LIMIT_STDERR, scale = 0.05, sleepMs = 0, env = {}, source = upstream } = {}) => {
    fs.rmSync(state, { recursive: true, force: true });
    fs.mkdirSync(state, { recursive: true });
    const started = Date.now();
    const result = run("node", [path.join(kit, "scripts", "sync-upstream.mjs"), "--check"], {
      env: {
        ...Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith("GIT_CONFIG_"))),
        GITHUB_TOKEN: "", GH_TOKEN: "", PATH: `${bin}:${process.env.PATH}`,
        SYNC_UPSTREAM_RETRY_SCALE: String(scale), GIT_STUB_STATE: state, GIT_STUB_REAL: REAL_GIT,
        GIT_STUB_UPSTREAM: pathToFileURL(source).href, GIT_STUB_FAIL_FIRST: String(failFirst),
        GIT_STUB_FAIL_STDERR: stderr, GIT_STUB_FETCH_SLEEP_MS: String(sleepMs), ...env,
      },
    });
    const calls = read(state, "calls.jsonl").trim().split("\n").map((line) => JSON.parse(line));
    const fetches = calls.filter((call) => call.sub === "fetch");
    const checkoutDirs = [...new Set(calls.map((call) => call.dir))];
    return { ...result, elapsedMs: Date.now() - started, calls, fetches, leftoverDirs: checkoutDirs.filter((dir) => fs.existsSync(dir)), output: `${result.stdout}${result.stderr}` };
  };
  return { kit, upstream, check, vendoredReadme: path.join(kit, "plugins", "demo", "README.md") };
}

const retryLines = (run_) => run_.stderr.split("\n").filter((line) => line.startsWith("sync-upstream: fetch attempt"));

step("sync-upstream --check retries a rate-limited fetch with backoff, then passes with the same result, and leaves no checkout behind", () => {
  const fx = syncFixture("https://github.com/example/plugins.git");
  const clean = fx.check();
  const limited = fx.check({ failFirst: 2 });
  return {
    ok: clean.status === 0 && clean.fetches.length === 1 && retryLines(clean).length === 0
      && limited.status === 0 && limited.fetches.length === 3 && retryLines(limited).length === 2
      && retryLines(limited).every((line, i) => line.includes(`attempt ${i + 1}/5`) && line.includes("HTTP 429"))
      && limited.stdout === clean.stdout && limited.leftoverDirs.length === 0 && clean.leftoverDirs.length === 0,
    observed: { cleanFetches: clean.fetches.length, limitedFetches: limited.fetches.length, retryLines: retryLines(limited).length, stdout: limited.stdout.replace(/\d+ vendored/, "N vendored").replace(/[0-9a-f]{12}/, "<sha>").trim() },
  };
});

step("sync-upstream --check stops after the bounded attempts and says the upstream was unreachable, not a mismatch", () => {
  const fx = syncFixture("https://github.com/example/plugins.git");
  const run_ = fx.check({ failFirst: 99 });
  const message = run_.stderr.trim().split("\n").at(-1);
  return {
    ok: run_.status === 1 && run_.fetches.length === 5 && retryLines(run_).length === 4 && run_.elapsedMs < 60_000
      && /^sync-upstream: could not fetch the upstream plugins \(https:\/\/github\.com\/example\/plugins\.git at [0-9a-f]{12}\) after 5 attempts over \d+\.\d seconds; last error: error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429\. This is a network or rate-limit problem, not a mismatch; rerun the job\.$/.test(message)
      && !run_.output.includes("Command failed") && run_.leftoverDirs.length === 0,
    observed: { status: run_.status, fetches: run_.fetches.length, message: message.replace(/\d+\.\d seconds/, "S seconds").replace(/[0-9a-f]{12}\)/, "<ref>)") },
  };
});

step("sync-upstream --check enforces the wall-time bound even when attempts remain", () => {
  const fx = syncFixture("https://github.com/example/plugins.git");
  const run_ = fx.check({ failFirst: 99, scale: 0.02, sleepMs: 2000 });
  return {
    ok: run_.status === 1 && run_.fetches.length >= 1 && run_.fetches.length < 5 && run_.elapsedMs < 20_000
      && run_.stderr.includes("attempts over") && run_.stderr.includes("rerun the job") && run_.leftoverDirs.length === 0,
    observed: { status: run_.status, stoppedBeforeMaxAttempts: run_.fetches.length < 5 },
  };
});

step("sync-upstream --check does not retry an unknown ref or an unreadable repository and names the permanent error", () => {
  const fx = syncFixture("https://github.com/example/plugins.git");
  const source = path.join(fx.kit, "upstream", "source.json");
  write(source, read(source).replace(/"ref":"[0-9a-f]{40}"/, `"ref":"${"1".repeat(40)}"`));
  const unknownRef = fx.check();
  const notFound = fx.check({ failFirst: 1, stderr: "fatal: unable to access 'https://github.com/example/plugins.git/': The requested URL returned error: 404" });
  const forbidden = fx.check({ failFirst: 1, stderr: "remote: Permission denied\nfatal: unable to access 'https://github.com/example/plugins.git/': The requested URL returned error: 403" });
  const rateLimited403 = syncFixture("https://github.com/example/plugins.git").check({ failFirst: 1, stderr: "fatal: unable to access 'https://github.com/example/plugins.git/': The requested URL returned error: 403 API rate limit exceeded" });
  const permanent = [unknownRef, notFound, forbidden];
  return {
    ok: permanent.every((r) => r.status === 1 && r.fetches.length === 1 && retryLines(r).length === 0 && r.stderr.includes("Not retried") && !r.output.includes("Command failed") && r.leftoverDirs.length === 0)
      && unknownRef.stderr.includes("not our ref") && rateLimited403.fetches.length === 2 && rateLimited403.status === 0,
    observed: { unknownRef: unknownRef.fetches.length, notFound: notFound.fetches.length, forbidden: forbidden.fetches.length, rateLimited403: rateLimited403.fetches.length },
  };
});

step("sync-upstream --check reports a real mismatch with the same output after a clean fetch and after a retried one", () => {
  const fx = syncFixture("https://github.com/example/plugins.git");
  write(fx.vendoredReadme, "edited by hand\n");
  const clean = fx.check();
  const retried = fx.check({ failFirst: 2 });
  const drift = (r) => r.stderr.split("\n").filter((line) => !line.startsWith("sync-upstream: fetch attempt")).join("\n");
  return {
    ok: clean.status === 1 && retried.status === 1 && clean.fetches.length === 1 && retried.fetches.length === 3
      && drift(clean) === drift(retried) && /vendored plugins drifted from upstream [0-9a-f]{40} \+ upstream\/\*\.json rules:\n {2}differs plugins\/demo\/README\.md\n/.test(clean.stderr),
    observed: { cleanStatus: clean.status, retriedStatus: retried.status, drift: drift(clean).replace(/[0-9a-f]{40}/, "<sha>").split("\n").slice(0, 2) },
  };
});

step("sync-upstream hands the token to git only through the environment, only for github.com over https, and prints it nowhere", () => {
  const withToken = { GITHUB_TOKEN: FIXTURE_TOKEN };
  const hasToken = (value) => JSON.stringify(value).includes(FIXTURE_TOKEN) || JSON.stringify(value).includes(FIXTURE_AUTH_BASIC);
  const github = syncFixture("https://github.com/example/plugins.git");
  const ok = github.check({ env: withToken });
  const failing = github.check({ env: withToken, failFirst: 99 });
  const fetch = ok.fetches[0];
  const ghTokenName = github.check({ env: { GH_TOKEN: FIXTURE_TOKEN } }).fetches[0];
  const others = ["https://github.com.evil.example/example/plugins.git", "http://github.com/example/plugins.git", "https://example.com/example/plugins.git", "https://user:pw@github.com/example/plugins.git", "https://github.com:8443/example/plugins.git"].map((repo) => {
    const fx = syncFixture(repo);
    const r = fx.check({ env: withToken, failFirst: 1 });
    return { repo, status: r.status, anyGitConfigEnv: r.calls.some((call) => Object.keys(call.gitConfigEnv).length > 0), anyTokenEnv: r.calls.some((call) => call.tokenEnvNames.length > 0), leaked: hasToken(r.output) || hasToken(r.calls.map((call) => [call.argv, call.config])) };
  });
  return {
    ok: ok.status === 0 && fetch.gitConfigEnv.GIT_CONFIG_COUNT === "1" && fetch.gitConfigEnv.GIT_CONFIG_KEY_0 === "http.https://github.com/.extraheader"
      && fetch.gitConfigEnv.GIT_CONFIG_VALUE_0 === `AUTHORIZATION: basic ${FIXTURE_AUTH_BASIC}` && fetch.tokenEnvNames.length === 0
      && ghTokenName.gitConfigEnv.GIT_CONFIG_VALUE_0 === fetch.gitConfigEnv.GIT_CONFIG_VALUE_0
      && ok.calls.filter((call) => call.sub !== "fetch").every((call) => Object.keys(call.gitConfigEnv).length === 0 && call.tokenEnvNames.length === 0)
      && !hasToken(ok.calls.map((call) => [call.argv, call.config])) && !hasToken(ok.output)
      && failing.status === 1 && failing.fetches.length === 5 && !hasToken(failing.output) && !hasToken(failing.calls.map((call) => [call.argv, call.config]))
      && others.every((o) => !o.anyGitConfigEnv && !o.anyTokenEnv && !o.leaked && o.status === 0),
    observed: { header: "environment only", argvAndConfigClean: true, outputClean: true, otherHosts: others.map((o) => [o.repo, o.anyGitConfigEnv, o.anyTokenEnv, o.leaked]) },
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

const failed = steps.filter((s) => !s.ok);
const digest = crypto.createHash("sha256").update(JSON.stringify(steps.map((s) => [s.step, s.ok, s.observed]))).digest("hex");
write(artifact, `${JSON.stringify({ ok: failed.length === 0, steps, digest }, null, 2)}\n`);
process.stdout.write(`transcript: ${artifact} sha256=${digest}\n`);
if (args.includes("--artifact")) fs.rmSync(work, { recursive: true, force: true });
process.exit(failed.length === 0 ? 0 : 1);
