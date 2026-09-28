#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const UPSTREAM_DIR = path.join(ROOT, "upstream");
const PLUGINS_DIR = path.join(ROOT, "plugins");
const ADAPTER_FILE = "hook-adapter.mjs";
const ADAPTER_SOURCE = path.join(UPSTREAM_DIR, "agent-hooks", ADAPTER_FILE);
const AGENT_HOOKS_DIR = "agent-hooks";
const UPSTREAM_TO_AGENT_EVENT = { afterFileEdit: "PostToolUse", afterAgentResponse: "Stop", stop: "Stop", subagentStop: "SubagentStop" };
const EDIT_TOOL_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|apply_patch";
const BINARY_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".woff", ".woff2"]);
const MODEL_TOKEN = /\b(?:grok|gpt|claude-(?:opus|sonnet|haiku|fable)|composer|gemini)(?:[-.][a-z0-9.]+)*(?:\[[^\]\s]*\])?/gi;

const USAGE = `Usage: node scripts/sync-upstream.mjs [--check] [--src <dir>] [--ref <sha>]

Vendors the upstream plugin repository in upstream/source.json into plugins/<name>/
byte-for-byte, except the model-name rewrites in upstream/model-map.json, the load
fixes in upstream/compat-patches.json, and the team's renames and omissions in
upstream/renames.json. Adds generated Claude Code and Codex manifests, and hook files
that run a plugin's upstream hooks in both agents.

  --check       Rebuild in memory and fail if plugins/ differs. Writes nothing.
  --src <dir>   Use an existing checkout of the upstream repository instead of cloning.
  --ref <sha>   Sync a different upstream commit and record it in upstream/source.json.

Examples:
  node scripts/sync-upstream.mjs --check
  node scripts/sync-upstream.mjs --ref 0123abcd
`;

function parseArgs(argv) {
  const args = { check: false, src: null, ref: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--check") args.check = true;
    else if (arg === "--src") args.src = argv[++i];
    else if (arg === "--ref") args.ref = argv[++i];
    else if (arg === "-h" || arg === "--help") {
      process.stdout.write(USAGE);
      process.exit(0);
    } else fail(`unknown argument ${arg}. Run with --help for usage.`);
  }
  return args;
}

function fail(message) {
  process.stderr.write(`sync-upstream: ${message}\n`);
  process.exit(1);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function globToRegExp(glob) {
  let pattern = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === "*" && glob[i + 1] === "*") {
      pattern += ".*";
      i++;
    } else if (ch === "*") pattern += "[^/]*";
    else if (ch === "?") pattern += "[^/]";
    else pattern += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${pattern}$`);
}

function checkoutUpstream(repo, ref) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "upstream-plugins-"));
  const git = (...gitArgs) => execFileSync("git", ["-C", dir, ...gitArgs], { stdio: "pipe" });
  git("init", "-q");
  git("remote", "add", "origin", repo);
  git("fetch", "-q", "--depth", "1", "origin", ref);
  git("checkout", "-q", "FETCH_HEAD");
  return dir;
}

function headOf(dir) {
  return execFileSync("git", ["-C", dir, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
}

function listFiles(dir, base = dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === "node_modules") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFiles(full, base));
    else if (entry.isFile()) out.push(path.relative(base, full).split(path.sep).join("/"));
  }
  return out.sort();
}

function isText(rel, buf) {
  if (BINARY_EXTENSIONS.has(path.extname(rel).toLowerCase())) return false;
  if (buf.includes(0)) return false;
  return Buffer.from(buf.toString("utf8"), "utf8").equals(buf);
}

function countOccurrences(text, needle) {
  return text.split(needle).length - 1;
}

function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

function loadRules() {
  const modelMap = readJson(path.join(UPSTREAM_DIR, "model-map.json"));
  const optional = (name) => {
    const file = path.join(UPSTREAM_DIR, name);
    return fs.existsSync(file) ? readJson(file) : { rules: [] };
  };
  const compat = optional("compat-patches.json");
  const renames = optional("renames.json");
  const tag = (kind) => (rule) => ({ ...rule, kind, matchers: rule.files.map(globToRegExp) });
  return {
    rules: [...modelMap.rules.map(tag("model")), ...compat.rules.map(tag("compat")), ...renames.rules.map(tag("rename"))],
    pathRenames: renames.paths ?? [],
    omit: (renames.omit ?? []).map(globToRegExp),
    forbidden: renames.forbidden ?? null,
    authorFallback: renames.authorFallback ?? null,
    allowed: new Set(modelMap.allowedModelTokens.map((t) => t.toLowerCase())),
    exceptions: modelMap.tokenExceptions,
  };
}

function renamePath(upstreamPath, pathRenames) {
  return upstreamPath
    .split("/")
    .map((segment) => pathRenames.reduce((acc, r) => acc.split(r.from).join(r.to), segment))
    .join("/");
}

function agentHooks(plugin, upstreamHooksBuf) {
  const upstream = JSON.parse(upstreamHooksBuf.toString("utf8"));
  const hooks = {};
  for (const upstreamEvent of Object.keys(upstream.hooks ?? {})) {
    const event = UPSTREAM_TO_AGENT_EVENT[upstreamEvent];
    if (!event) fail(`${plugin}/hooks/hooks.json uses upstream hook "${upstreamEvent}", which has no Claude Code or Codex mapping. Add one to UPSTREAM_TO_AGENT_EVENT and the adapter.`);
    if (hooks[event]) continue;
    const command = `node "\${CLAUDE_PLUGIN_ROOT}/${AGENT_HOOKS_DIR}/${ADAPTER_FILE}" ${event}`;
    hooks[event] = [{ ...(event === "PostToolUse" ? { matcher: EDIT_TOOL_MATCHER } : {}), hooks: [{ type: "command", command }] }];
  }
  return Buffer.from(`${JSON.stringify({ hooks }, null, 2)}\n`, "utf8");
}

function transform(upstreamDir, pluginNames, { rules, allowed, exceptions }) {
  const outputs = new Map();
  const counts = new Map(rules.map((r) => [r.id, 0]));
  const changes = new Map(rules.map((r) => [r.id, []]));
  const tokenProblems = [];

  for (const plugin of pluginNames) {
    const pluginDir = path.join(upstreamDir, plugin);
    if (!fs.existsSync(pluginDir)) fail(`upstream has no plugin directory "${plugin}"`);
    for (const rel of listFiles(pluginDir)) {
      const upstreamPath = `${plugin}/${rel}`;
      const buf = fs.readFileSync(path.join(pluginDir, rel));
      if (!isText(upstreamPath, buf)) {
        outputs.set(upstreamPath, buf);
        continue;
      }
      let text = buf.toString("utf8");
      for (const rule of rules) {
        if (!rule.matchers.some((m) => m.test(upstreamPath))) continue;
        let index = text.indexOf(rule.from);
        while (index !== -1) {
          changes.get(rule.id).push(`${upstreamPath}:${lineOf(text, index)}`);
          index = text.indexOf(rule.from, index + rule.from.length);
        }
        const n = countOccurrences(text, rule.from);
        if (n > 0) {
          counts.set(rule.id, counts.get(rule.id) + n);
          text = text.split(rule.from).join(rule.to);
        }
      }
      for (const match of text.matchAll(MODEL_TOKEN)) {
        const token = match[0];
        const lower = token.toLowerCase();
        if (allowed.has(lower) || allowed.has(lower.replace(/[.,]$/, ""))) continue;
        if (exceptions.some((e) => e.file === upstreamPath && e.token.toLowerCase() === lower)) continue;
        tokenProblems.push(`${upstreamPath}:${lineOf(text, match.index)} unmapped model token "${token}"`);
      }
      outputs.set(upstreamPath, Buffer.from(text, "utf8"));
    }
  }

  const countProblems = rules
    .filter((r) => counts.get(r.id) !== r.count)
    .map((r) => `rule "${r.id}" expected ${r.count} replacement(s), made ${counts.get(r.id)}`);
  return { outputs, counts, changes, problems: [...countProblems, ...tokenProblems] };
}

function claudeManifest(upstreamManifestBuf, hooksPath, { pathRenames, forbidden, authorFallback }) {
  const upstream = JSON.parse(upstreamManifestBuf.toString("utf8"));
  const banned = forbidden ? new RegExp(forbidden.pattern, forbidden.flags) : null;
  const allowed = (value) => !banned || !banned.test(JSON.stringify(value));
  const manifest = { name: renamePath(upstream.name, pathRenames) };
  for (const key of ["version", "description", "author", "homepage", "repository", "license"]) {
    if (upstream[key] !== undefined && allowed(upstream[key])) manifest[key] = upstream[key];
  }
  if (!manifest.author && authorFallback) manifest.author = authorFallback;
  if (Array.isArray(upstream.keywords)) manifest.keywords = upstream.keywords.map((k) => renamePath(k, pathRenames)).filter(allowed);
  if (hooksPath) manifest.hooks = hooksPath;
  return Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

function forbiddenProblems(tree, sources, forbidden) {
  if (!forbidden) return [];
  const skip = (forbidden.skipFiles ?? []).map(globToRegExp);
  const allowedWords = new Set(forbidden.allowedWords ?? []);
  const word = new RegExp(`[A-Za-z0-9_]*${forbidden.pattern}[A-Za-z0-9_]*`, `g${forbidden.flags ?? ""}`);
  const problems = [];
  for (const [rel, buf] of tree) {
    const upstreamPath = sources.get(rel) ?? rel.replace(/^plugins\//, "");
    if (skip.some((re) => re.test(upstreamPath)) || !isText(rel, buf)) continue;
    const text = buf.toString("utf8");
    for (const match of text.matchAll(word)) {
      if (allowedWords.has(match[0])) continue;
      if ((forbidden.exceptions ?? []).some((e) => e.file === upstreamPath && e.word === match[0])) continue;
      problems.push(`${rel}:${lineOf(text, match.index)} contains "${match[0]}"; add a rule to upstream/renames.json`);
    }
  }
  return problems;
}

function buildTree(upstreamDir, pluginNames, rules, manifestDir) {
  const result = transform(upstreamDir, pluginNames, rules);
  const tree = new Map();
  const sources = new Map();
  const renamedPaths = [];
  const omitted = [];
  const generated = [];
  for (const [upstreamPath, buf] of result.outputs) {
    if (rules.omit.some((re) => re.test(upstreamPath))) {
      omitted.push(upstreamPath);
      continue;
    }
    const vendoredPath = renamePath(upstreamPath, rules.pathRenames);
    if (vendoredPath !== upstreamPath) renamedPaths.push([upstreamPath, vendoredPath]);
    tree.set(`plugins/${vendoredPath}`, buf);
    sources.set(`plugins/${vendoredPath}`, upstreamPath);
  }
  const adapter = fs.readFileSync(ADAPTER_SOURCE);
  for (const plugin of pluginNames) {
    const dir = `plugins/${renamePath(plugin, rules.pathRenames)}`;
    const upstreamManifest = result.outputs.get(`${plugin}/${manifestDir}/plugin.json`);
    if (!upstreamManifest) fail(`upstream plugin "${plugin}" has no ${manifestDir}/plugin.json`);
    const upstreamHooks = result.outputs.get(`${plugin}/hooks/hooks.json`);
    const hooksPath = upstreamHooks ? `./${AGENT_HOOKS_DIR}/hooks.json` : null;
    tree.set(`${dir}/.claude-plugin/plugin.json`, claudeManifest(upstreamManifest, hooksPath, rules));
    generated.push(`${dir}/.claude-plugin/plugin.json`);
    if (upstreamHooks) {
      for (const [rel, buf] of [[`${AGENT_HOOKS_DIR}/hooks.json`, agentHooks(plugin, upstreamHooks)], [`${AGENT_HOOKS_DIR}/${ADAPTER_FILE}`, adapter]]) {
        tree.set(`${dir}/${rel}`, buf);
        generated.push(`${dir}/${rel}`);
      }
    }
  }
  const problems = [...result.problems, ...forbiddenProblems(tree, sources, rules.forbidden)];
  return { ...result, problems, tree, sources, renamedPaths, omitted, generated };
}

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function renderChanges(rules, changes, renamedPaths, omitted, generated) {
  const lines = [
    "# Upstream changes",
    "",
    "Generated by `scripts/sync-upstream.mjs`. Every difference between `plugins/` and the upstream repository in `upstream/source.json` is listed here. Model rules rewrite LLM model names to the team's models; compat rules are the minimum edits a file needs to load in Claude Code, Codex, or Pi; rename rules apply the names the team chose. Rules quote the upstream text they replace, and occurrences use upstream paths.",
    "",
    "## Renamed paths",
    "",
    ...renamedPaths.map(([from, to]) => `- \`${from}\` → \`${to}\``),
    "",
    "## Omitted files",
    "",
    "Upstream files the kit does not ship, per `omit` in `upstream/renames.json`.",
    "",
    ...omitted.map((file) => `- \`${file}\``),
    "",
    "## Generated files",
    "",
    "Not in upstream. `.claude-plugin/plugin.json` is built from the upstream manifest named in `upstream/source.json`. `agent-hooks/` runs the plugin's upstream hooks (`hooks/hooks.json`) as Claude Code and Codex hooks, through `upstream/agent-hooks/hook-adapter.mjs`.",
    "",
    ...generated.map((file) => `- \`${file}\``),
    "",
  ];
  for (const rule of rules) {
    lines.push(`## \`${rule.id}\` (${rule.kind})`, "");
    lines.push(`- From: \`${rule.from.replace(/\n/g, "\\n")}\``);
    lines.push(`- To: \`${rule.to.replace(/\n/g, "\\n")}\``);
    lines.push(`- Occurrences: ${changes.get(rule.id).map((c) => `\`${c}\``).join(", ")}`, "");
  }
  return `${lines.join("\n").trimEnd()}\n`;
}

function vendoredFiles(pluginNames) {
  const files = [];
  for (const plugin of pluginNames) {
    const dir = path.join(PLUGINS_DIR, plugin);
    if (!fs.existsSync(dir)) continue;
    for (const rel of listFiles(dir)) files.push(`plugins/${plugin}/${rel}`);
  }
  return files;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const sourceFile = path.join(UPSTREAM_DIR, "source.json");
  const source = readJson(sourceFile);
  const ref = args.ref ?? source.ref;
  const upstreamDir = args.src ? path.resolve(args.src) : checkoutUpstream(source.repo, ref);
  const head = headOf(upstreamDir);
  if (!head.startsWith(ref) && !ref.startsWith(head)) {
    fail(`checkout at ${upstreamDir} is ${head}, expected ${ref}. Check out the ref or drop --src.`);
  }

  const rules = loadRules();
  const vendoredNames = source.plugins.map((plugin) => renamePath(plugin, rules.pathRenames));
  const { tree, sources, changes, renamedPaths, omitted, generated, problems } = buildTree(upstreamDir, source.plugins, rules, source.manifestDir);
  if (problems.length > 0) fail(`transform problems:\n  ${problems.join("\n  ")}`);

  const changesDoc = Buffer.from(renderChanges(rules.rules, changes, renamedPaths, omitted, generated), "utf8");
  const lock = {
    repo: source.repo,
    ref: head,
    files: Object.fromEntries([...tree].map(([rel, buf]) => [rel, sha256(buf)])),
  };
  const lockBuf = Buffer.from(`${JSON.stringify(lock, null, 2)}\n`, "utf8");

  if (args.check) {
    const diffs = [];
    for (const [rel, buf] of tree) {
      const full = path.join(ROOT, rel);
      if (!fs.existsSync(full)) diffs.push(`missing ${rel}`);
      else if (!fs.readFileSync(full).equals(buf)) diffs.push(`differs ${rel}`);
    }
    for (const rel of vendoredFiles(vendoredNames)) if (!tree.has(rel)) diffs.push(`extra ${rel}`);
    for (const [rel, buf] of [["upstream/CHANGES.md", changesDoc], ["upstream/lock.json", lockBuf]]) {
      const full = path.join(ROOT, rel);
      if (!fs.existsSync(full) || !fs.readFileSync(full).equals(buf)) diffs.push(`stale ${rel}`);
    }
    if (diffs.length > 0) {
      fail(`vendored plugins drifted from upstream ${head} + upstream/*.json rules:\n  ${diffs.join("\n  ")}\nRun node scripts/sync-upstream.mjs to regenerate, or move the edit into a rule.`);
    }
    process.stdout.write(`sync-upstream: ${tree.size} vendored files match upstream ${head.slice(0, 12)} + rules\n`);
    return;
  }

  for (const plugin of new Set([...source.plugins, ...vendoredNames])) fs.rmSync(path.join(PLUGINS_DIR, plugin), { recursive: true, force: true });
  for (const [rel, buf] of tree) {
    const full = path.join(ROOT, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, buf);
    const upstreamFile = sources.has(rel) ? path.join(upstreamDir, sources.get(rel)) : rel.endsWith(".mjs") ? ADAPTER_SOURCE : null;
    if (upstreamFile && fs.existsSync(upstreamFile)) fs.chmodSync(full, fs.statSync(upstreamFile).mode & 0o777);
  }
  fs.writeFileSync(path.join(UPSTREAM_DIR, "CHANGES.md"), changesDoc);
  fs.writeFileSync(path.join(UPSTREAM_DIR, "lock.json"), lockBuf);
  if (args.ref) fs.writeFileSync(sourceFile, `${JSON.stringify({ ...source, ref: head }, null, 2)}\n`);
  process.stdout.write(`sync-upstream: wrote ${tree.size} files from upstream ${head.slice(0, 12)}\n`);
}

main();
