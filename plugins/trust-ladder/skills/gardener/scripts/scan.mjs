#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `Usage: node scan.mjs [--since <git date>] [--baseline <git date>] [--min-files N] [--json] [REPO]

Measure how fast agent-copied smells are spreading in a git repository.

  --since       Start of the window to inspect (default "7 days ago").
  --baseline    Earlier point to compare counts against (default "30 days ago").
  --min-files   Report helper names defined in at least N files (default 3).
  --json        Print JSON instead of a table.

Examples:
  node scan.mjs
  node scan.mjs --since "14 days ago" --json ../app
`;

const CODE = ["ts", "tsx", "js", "jsx", "mjs", "kt", "kts", "swift", "rs", "dart", "py", "java", "go"];
const ext = (...exts) => exts.flatMap((e) => [`*.${e}`]);

export const SMELLS = [
  { id: "suppression", label: "Lint or type suppressions", pattern: String.raw`eslint-disable|@ts-ignore|@ts-expect-error|#\s*noqa|#\s*type:\s*ignore|@Suppress(Lint|Warnings)?\(|@file:Suppress|swiftlint:disable|#!?\[allow\(|//\s*ignore(_for_file)?:`, files: ext(...CODE) },
  { id: "workaround-comment", label: "Workaround comments", pattern: String.raw`(//|#|/\*|\*)\s*.*\b(TODO|FIXME|HACK|XXX|WORKAROUND|[Ww]orkaround|for now|[Tt]emporar(y|ily)|[Dd]o not remove|[Dd]on't remove|IMPORTANT:)`, files: ext(...CODE) },
  { id: "ts-any", label: "TypeScript any and double casts", pattern: String.raw`\bas any\b|:\s*any\b|as unknown as`, files: ext("ts", "tsx") },
  { id: "kotlin-not-null-assertion", label: "Kotlin !! assertions", pattern: String.raw`[A-Za-z0-9_)\]]!!`, files: ext("kt", "kts") },
  { id: "swift-force", label: "Swift try! and as!", pattern: String.raw`\btry!|\bas!`, files: ext("swift") },
  { id: "rust-unwrap", label: "Rust unwrap and expect", pattern: String.raw`\.unwrap\(\)|\.expect\(`, files: ext("rs") },
  { id: "dart-dynamic", label: "Dart dynamic", pattern: String.raw`\bdynamic\b`, files: ext("dart") },
  { id: "python-any-cast", label: "Python Any and cast", pattern: String.raw`\bAny\b|\bcast\(`, files: ext("py") },
];

const DEFINITIONS = [
  { files: ext("ts", "tsx", "js", "jsx", "mjs"), pattern: String.raw`^\s*(export\s+)?(async\s+)?function\s+[A-Za-z_]\w*|^\s*(export\s+)?const\s+[A-Za-z_]\w*\s*=\s*(async\s*)?\(` },
  { files: ext("kt", "kts"), pattern: String.raw`^\s*((private|internal|public|inline|suspend)\s+)*fun\s+(<[^>]+>\s*)?([A-Za-z_][\w.]*\.)?[A-Za-z_]\w*\s*\(` },
  { files: ext("swift"), pattern: String.raw`^\s*((private|fileprivate|internal|public|static)\s+)*func\s+[A-Za-z_]\w*` },
  { files: ext("rs"), pattern: String.raw`^\s*(pub(\([a-z]+\))?\s+)?(async\s+)?fn\s+[A-Za-z_]\w*` },
  { files: ext("py"), pattern: String.raw`^def\s+[A-Za-z_]\w*` },
  { files: ext("dart"), pattern: String.raw`^[A-Za-z_<>?, ]+\s+[a-z_]\w*\s*\([^)]*\)\s*(async\s*)?(\{|=>)` },
];

const COMMON_NAMES = new Set([
  "main", "build", "init", "setup", "setUp", "tearDown", "toString", "equals", "hashCode", "invoke", "get", "set",
  "render", "update", "handle", "handler", "run", "create", "apply", "copy", "from", "new", "default", "fmt", "drop",
  "clone", "eq", "hash", "next", "call", "execute", "dispose", "initState", "onCreate", "onDestroy", "body", "load",
  "save", "parse", "validate", "reset", "start", "stop", "close", "open", "test", "it", "describe",
]);
const TEST_PATH = /(^|\/)(test|tests|__tests__|spec|androidTest|testFixtures)(\/|$)|[._-](test|spec)\.[a-z]+$|Tests?\.(kt|swift)$|_test\.(dart|py|go|rs)$/;

function git(repo, args) {
  try {
    return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  } catch (error) {
    if (error.status === 1) return "";
    throw error;
  }
}

const grepModeCache = new Map();
function grepMode(repo) {
  if (!grepModeCache.has(repo)) {
    let mode = "-P";
    try {
      execFileSync("git", ["-C", repo, "grep", "-P", "-q", "x", "HEAD"], { stdio: "ignore" });
    } catch (error) {
      if (error.status !== 1) mode = "-E";
    }
    grepModeCache.set(repo, mode);
  }
  return grepModeCache.get(repo);
}

function revAt(repo, date) {
  return git(repo, ["rev-list", "-1", `--before=${date}`, "HEAD"]).trim() || null;
}

function countAt(repo, rev, smell) {
  if (!rev) return 0;
  const out = git(repo, ["grep", "-I", "-c", grepMode(repo), smell.pattern, rev, "--", ...smell.files]);
  return out.split("\n").filter(Boolean).reduce((sum, line) => sum + Number(line.split(":").pop()), 0);
}

function topFiles(repo, smell) {
  const out = git(repo, ["grep", "-I", "-c", grepMode(repo), smell.pattern, "HEAD", "--", ...smell.files]);
  return out
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(":");
      return { file: parts.slice(1, -1).join(":"), count: Number(parts.at(-1)) };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);
}

function addedLines(repo, since) {
  const out = git(repo, ["log", `--since=${since}`, "--first-parent", "-p", "--unified=0", "--no-color", "--format=", "HEAD"]);
  return out.split("\n").filter((l) => l.startsWith("+") && !l.startsWith("+++")).map((l) => l.slice(1));
}

function definedName(line) {
  const match = /(?:function|fun|func|fn|def|const)\s+(?:<[^>]+>\s*)?(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)/.exec(line) ?? /([a-z_]\w*)\s*\(/.exec(line);
  return match?.[1] ?? null;
}

function duplicateDefinitions(repo, added, minFiles) {
  const byName = new Map();
  for (const def of DEFINITIONS) {
    const out = git(repo, ["grep", "-I", "-n", grepMode(repo), def.pattern, "HEAD", "--", ...def.files]);
    for (const line of out.split("\n").filter(Boolean)) {
      const [, file, , ...rest] = line.split(":");
      if (TEST_PATH.test(file)) continue;
      const name = definedName(rest.join(":"));
      if (!name || COMMON_NAMES.has(name) || name.startsWith("test") || name.startsWith("_")) continue;
      if (!byName.has(name)) byName.set(name, new Set());
      byName.get(name).add(file);
    }
  }
  const addedNames = new Set(added.map(definedName).filter(Boolean));
  return [...byName]
    .filter(([, files]) => files.size >= minFiles)
    .map(([name, files]) => ({ name, files: [...files].sort(), newInWindow: addedNames.has(name) }))
    .sort((a, b) => Number(b.newInWindow) - Number(a.newInWindow) || b.files.length - a.files.length);
}

export function scan(repo, { since = "7 days ago", baseline = "30 days ago", minFiles = 3 } = {}) {
  const baselineRev = revAt(repo, baseline);
  const added = addedLines(repo, since);
  const smells = SMELLS.map((smell) => {
    const re = new RegExp(smell.pattern);
    const now = countAt(repo, "HEAD", smell);
    const before = countAt(repo, baselineRev, smell);
    return {
      id: smell.id,
      label: smell.label,
      now,
      before,
      growth: now - before,
      addedInWindow: added.filter((l) => re.test(l)).length,
      topFiles: now > 0 ? topFiles(repo, smell) : [],
    };
  })
    .filter((s) => s.now > 0 || s.before > 0)
    .sort((a, b) => b.addedInWindow - a.addedInWindow || b.growth - a.growth);
  return { since, baseline, baselineRev, smells, duplicates: duplicateDefinitions(repo, added, minFiles) };
}

function renderTable(result) {
  const lines = [`Smells (window: since ${result.since}; baseline: ${result.baseline})`, ""];
  lines.push("added  now  before  growth  smell");
  for (const s of result.smells) {
    lines.push(`${String(s.addedInWindow).padStart(5)}  ${String(s.now).padStart(3)}  ${String(s.before).padStart(6)}  ${String(s.growth > 0 ? `+${s.growth}` : s.growth).padStart(6)}  ${s.label}`);
  }
  lines.push("", "Helpers defined in several files (new in window first):");
  for (const d of result.duplicates.slice(0, 20)) {
    lines.push(`${d.newInWindow ? "NEW " : "    "}${d.name}: ${d.files.join(", ")}`);
  }
  return `${lines.join("\n")}\n`;
}

function main() {
  const args = process.argv.slice(2);
  const options = {};
  let json = false;
  let repo = ".";
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--since") options.since = args[++i];
    else if (arg === "--baseline") options.baseline = args[++i];
    else if (arg === "--min-files") options.minFiles = Number(args[++i]);
    else if (arg === "--json") json = true;
    else if (arg === "-h" || arg === "--help") {
      process.stdout.write(USAGE);
      return;
    } else if (arg.startsWith("-")) {
      process.stderr.write(`scan: unknown option ${arg}\n\n${USAGE}`);
      process.exit(2);
    } else repo = arg;
  }
  const result = scan(path.resolve(repo), options);
  process.stdout.write(json ? `${JSON.stringify(result, null, 2)}\n` : renderTable(result));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
