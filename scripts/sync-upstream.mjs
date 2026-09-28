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
const BINARY_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".woff", ".woff2"]);
const MODEL_TOKEN = /\b(?:cursor-)?(?:grok|gpt|claude-(?:opus|sonnet|haiku|fable)|composer|gemini)(?:[-.][a-z0-9.]+)*(?:\[[^\]\s]*\])?/gi;

const USAGE = `Usage: node scripts/sync-upstream.mjs [--check] [--src <dir>] [--ref <sha>]

Vendors cursor/plugins into plugins/<name>/ byte-for-byte, except the model-name
rewrites in upstream/model-map.json and the load fixes in upstream/compat-patches.json.

  --check       Rebuild in memory and fail if plugins/ differs. Writes nothing.
  --src <dir>   Use an existing checkout of cursor/plugins instead of cloning.
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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cursor-plugins-"));
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
  const compatFile = path.join(UPSTREAM_DIR, "compat-patches.json");
  const compat = fs.existsSync(compatFile) ? readJson(compatFile) : { rules: [] };
  const tag = (kind) => (rule) => ({ ...rule, kind, matchers: rule.files.map(globToRegExp) });
  return {
    rules: [...modelMap.rules.map(tag("model")), ...compat.rules.map(tag("compat"))],
    allowed: new Set(modelMap.allowedModelTokens.map((t) => t.toLowerCase())),
    exceptions: modelMap.tokenExceptions,
  };
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

function claudeManifest(cursorManifestBuf) {
  const cursor = JSON.parse(cursorManifestBuf.toString("utf8"));
  const manifest = { name: cursor.name };
  for (const key of ["version", "description", "author", "homepage", "repository", "license", "keywords"]) {
    if (cursor[key] !== undefined) manifest[key] = cursor[key];
  }
  return Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

function buildTree(upstreamDir, pluginNames, rules) {
  const result = transform(upstreamDir, pluginNames, rules);
  const tree = new Map();
  for (const [upstreamPath, buf] of result.outputs) tree.set(`plugins/${upstreamPath}`, buf);
  for (const plugin of pluginNames) {
    const cursorManifest = result.outputs.get(`${plugin}/.cursor-plugin/plugin.json`);
    if (!cursorManifest) fail(`upstream plugin "${plugin}" has no .cursor-plugin/plugin.json`);
    tree.set(`plugins/${plugin}/.claude-plugin/plugin.json`, claudeManifest(cursorManifest));
  }
  return { ...result, tree };
}

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function renderChanges(rules, changes) {
  const lines = [
    "# Upstream changes",
    "",
    "Generated by `scripts/sync-upstream.mjs`. Every difference between `plugins/` and upstream `cursor/plugins` is listed here. Model rules rewrite LLM model names only; compat rules are the minimum edits a file needs to load in Claude Code, Codex, or Pi.",
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
  const { tree, changes, problems } = buildTree(upstreamDir, source.plugins, rules);
  if (problems.length > 0) fail(`transform problems:\n  ${problems.join("\n  ")}`);

  const changesDoc = Buffer.from(renderChanges(rules.rules, changes), "utf8");
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
    for (const rel of vendoredFiles(source.plugins)) if (!tree.has(rel)) diffs.push(`extra ${rel}`);
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

  for (const plugin of source.plugins) fs.rmSync(path.join(PLUGINS_DIR, plugin), { recursive: true, force: true });
  for (const [rel, buf] of tree) {
    const full = path.join(ROOT, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, buf);
    const upstreamFile = path.join(upstreamDir, rel.replace(/^plugins\//, ""));
    if (fs.existsSync(upstreamFile)) fs.chmodSync(full, fs.statSync(upstreamFile).mode & 0o777);
  }
  fs.writeFileSync(path.join(UPSTREAM_DIR, "CHANGES.md"), changesDoc);
  fs.writeFileSync(path.join(UPSTREAM_DIR, "lock.json"), lockBuf);
  if (args.ref) fs.writeFileSync(sourceFile, `${JSON.stringify({ ...source, ref: head }, null, 2)}\n`);
  process.stdout.write(`sync-upstream: wrote ${tree.size} files from upstream ${head.slice(0, 12)}\n`);
}

main();
