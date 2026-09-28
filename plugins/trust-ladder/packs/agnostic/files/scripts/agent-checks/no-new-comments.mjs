#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `Usage: node scripts/agent-checks/no-new-comments.mjs [--base <ref>] [--config .agents/no-new-comments.json]

Fail when a change adds a comment. Agents copy comments that justify workarounds, so
this repository explains code with names, types, and structure instead.

Allowed: license headers, public API doc comments (///, /**, //!), lint suppressions,
lines linking an issue or URL, shebangs and pragmas, and anything matching the
config's "allow" patterns. Paths matching "exclude" globs are skipped.

  --base     Compare against this ref (default: merge-base with origin/main or main).

Examples:
  node scripts/agent-checks/no-new-comments.mjs
  node scripts/agent-checks/no-new-comments.mjs --base origin/develop
`;

const SLASH = ["ts", "tsx", "js", "jsx", "mjs", "cjs", "kt", "kts", "swift", "rs", "dart", "java", "go", "c", "cc", "cpp", "h", "hpp", "m", "mm", "scss", "gradle"];
const HASH = ["py", "sh", "bash", "zsh", "rb", "yml", "yaml", "toml", "tf", "cmake"];
const DOC = /^\s*(\/\/\/|\/\/!|\/\*\*|\/\*!)/;
const ALWAYS_ALLOWED = [
  /copyright|spdx-license-identifier|licensed under/i,
  /https?:\/\//,
  /(^|\s)#\d+\b|\b[A-Z][A-Z0-9]+-\d+\b/,
  /eslint-disable|eslint-enable|@ts-(ignore|expect-error|nocheck|check)|prettier-ignore|biome-ignore|noqa|type:\s*ignore|pyright:|swiftlint:|swift-format-ignore|ktlint-disable|detekt|ignore(_for_file)?:|NOLINT|istanbul ignore|c8 ignore/,
  /^\s*#!/,
  /^\s*#\s*-\*-/,
  /^\s*\/\/\s*(swift-tools-version|go:|\+build|@flow|@jsx|#region|#endregion)/,
];

function git(repo, args) {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
}

function defaultBase(repo) {
  for (const ref of ["origin/main", "main", "origin/master", "master"]) {
    try {
      return git(repo, ["merge-base", "HEAD", ref]).trim();
    } catch {}
  }
  return "HEAD";
}

function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (glob.slice(i) === "/**") {
      re += "(?:/.*)?";
      break;
    }
    if (ch === "*" && glob[i + 1] === "*") {
      re += ".*";
      i++;
      if (glob[i + 1] === "/") i++;
    } else if (ch === "*") re += "[^/]*";
    else re += ch.replace(/[.+^${}()|[\]\\?]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

function stripStrings(line) {
  return line.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`/g, (s) => `${s[0]}${" ".repeat(s.length - 2)}${s[0]}`);
}

export function commentIn(line, ext) {
  const code = stripStrings(line);
  if (SLASH.includes(ext)) {
    const at = code.search(/\/\/|\/\*|^\s*\*(?!\*)/);
    return at === -1 ? null : line.slice(Math.max(at, 0)).trim();
  }
  if (HASH.includes(ext)) {
    const at = code.search(/(^|\s)#/);
    return at === -1 ? null : line.slice(at).trim();
  }
  if (ext === "sql" || ext === "lua") {
    const at = code.indexOf("--");
    return at === -1 ? null : line.slice(at).trim();
  }
  if (["xml", "html", "svg", "md"].includes(ext)) {
    const at = code.indexOf("<!--");
    return at === -1 ? null : line.slice(at).trim();
  }
  return null;
}

export function findNewComments(repo, base, config = {}) {
  const allow = [...ALWAYS_ALLOWED, ...(config.allow ?? []).map((p) => new RegExp(p))];
  const exclude = (config.exclude ?? []).map(globToRegExp);
  const diff = git(repo, ["diff", "--unified=0", "--no-color", "--no-ext-diff", base, "--"]);
  const untracked = git(repo, ["ls-files", "--others", "--exclude-standard"]).split("\n").filter(Boolean);
  const added = [];
  let file = null;
  let lineNo = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++ ")) file = line.startsWith("+++ b/") ? line.slice(6) : null;
    else if (line.startsWith("@@")) lineNo = Number(/\+(\d+)/.exec(line)[1]);
    else if (line.startsWith("+") && file) added.push({ file, line: lineNo++, text: line.slice(1) });
  }
  const known = new Set([...SLASH, ...HASH, "sql", "lua", "xml", "html", "svg", "md"]);
  for (const f of untracked.filter((u) => known.has(path.extname(u).slice(1).toLowerCase()))) {
    fs.readFileSync(path.join(repo, f), "utf8").split("\n").forEach((text, i) => added.push({ file: f, line: i + 1, text }));
  }
  const found = [];
  let inDocBlock = false;
  let lastFile = null;
  for (const { file: f, line, text } of added) {
    if (f !== lastFile) {
      inDocBlock = false;
      lastFile = f;
    }
    if (exclude.some((re) => re.test(f))) continue;
    const ext = path.extname(f).slice(1).toLowerCase();
    if (DOC.test(text)) {
      inDocBlock = /\/\*\*|\/\*!/.test(text) && !text.includes("*/");
      continue;
    }
    if (inDocBlock) {
      if (text.includes("*/")) inDocBlock = false;
      continue;
    }
    const comment = commentIn(text, ext);
    if (!comment || comment.length < 3) continue;
    if (allow.some((re) => re.test(comment))) continue;
    found.push({ file: f, line, comment });
  }
  return found;
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    return;
  }
  const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
  const repo = git(process.cwd(), ["rev-parse", "--show-toplevel"]).trim();
  const configFile = path.join(repo, option("--config", ".agents/no-new-comments.json"));
  const config = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, "utf8")) : {};
  const found = findNewComments(repo, option("--base", defaultBase(repo)), config);
  if (found.length === 0) {
    process.stdout.write("no-new-comments: no new comments\n");
    return;
  }
  for (const f of found) process.stderr.write(`${f.file}:${f.line}: ${f.comment}\n`);
  process.stderr.write(
    `no-new-comments: ${found.length} new comment(s). Say it with names, types, or structure instead, then delete the comment. ` +
      "Keep only license headers, public API docs, lint suppressions, and links to an external constraint. Run /no-comments for help.\n",
  );
  process.exit(config.mode === "warn" ? 0 : 1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
