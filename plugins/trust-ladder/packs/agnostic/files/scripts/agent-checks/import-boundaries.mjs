#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `Usage: node scripts/agent-checks/import-boundaries.mjs [--config .agents/import-boundaries.json]

Fail when a file imports something its layer must not depend on. Works for Dart,
TypeScript/JavaScript, Python, Kotlin, Java, Swift, and Rust imports.

Config (.agents/import-boundaries.json):
  { "rules": [ {
      "id": "ui-does-not-touch-data",
      "from": ["lib/features/*/ui/**"],
      "forbid": ["lib/features/*/data/**", "package:http/**"],
      "allow": [],
      "message": "UI reads state from the feature controller; data access lives in data/."
  } ] }

Relative imports are resolved to repository paths before matching, so "../data/api.dart"
from lib/features/cart/ui/ matches "lib/features/*/data/**". Package and module imports
match as written, for example "package:http/http.dart", "requests", "android.content.Context".

Example:
  node scripts/agent-checks/import-boundaries.mjs
`;

const IMPORTS = [
  { exts: ["dart"], re: /^\s*(?:import|export|part)\s+['"]([^'"]+)['"]/gm },
  { exts: ["ts", "tsx", "js", "jsx", "mjs", "cjs"], re: /(?:^|\s)(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|^\s*import\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)|import\(\s*['"]([^'"]+)['"]\s*\)/gm },
  { exts: ["py"], re: /^\s*from\s+(\.*[\w.]*)\s+import\b|^\s*import\s+([\w.]+)/gm },
  { exts: ["kt", "kts", "java"], re: /^\s*import\s+(?:static\s+)?([\w.]+)/gm },
  { exts: ["swift"], re: /^\s*(?:@testable\s+)?import\s+(?:(?:struct|class|enum|protocol|func|var|let|typealias)\s+)?([\w.]+)/gm },
  { exts: ["rs"], re: /^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([\w:]+)/gm },
];

export function globToRegExp(glob) {
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
    } else if (ch === "*") re += "[^/]*";
    else re += ch.replace(/[.+^${}()|[\]\\?]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

export function importsOf(file, text) {
  const ext = path.extname(file).slice(1).toLowerCase();
  const spec = IMPORTS.find((s) => s.exts.includes(ext));
  if (!spec) return [];
  const found = [];
  for (const match of text.matchAll(spec.re)) {
    const target = match.slice(1).find(Boolean);
    if (!target) continue;
    const at = match.index + match[0].lastIndexOf(target);
    found.push({ target, line: text.slice(0, at).split("\n").length });
  }
  return found;
}

export function resolveTarget(file, target) {
  if (target.startsWith("./") || target.startsWith("../")) {
    return path.posix.normalize(path.posix.join(path.posix.dirname(file), target));
  }
  if (path.extname(file) === ".py" && target.startsWith(".")) {
    const dots = /^\.+/.exec(target)[0].length;
    let dir = path.posix.dirname(file);
    for (let i = 1; i < dots; i++) dir = path.posix.dirname(dir);
    const rest = target.slice(dots).replace(/\./g, "/");
    return rest ? path.posix.join(dir, rest) : dir;
  }
  return target;
}

export function checkBoundaries(repo, config) {
  const files = execFileSync("git", ["-C", repo, "ls-files", "-z"], { encoding: "utf8" }).split("\0").filter(Boolean);
  const violations = [];
  for (const rule of config.rules) {
    const from = rule.from.map(globToRegExp);
    const forbid = rule.forbid.map(globToRegExp);
    const allow = (rule.allow ?? []).map(globToRegExp);
    for (const file of files.filter((f) => from.some((re) => re.test(f)))) {
      const full = path.join(repo, file);
      if (!fs.existsSync(full)) continue;
      for (const { target, line } of importsOf(file, fs.readFileSync(full, "utf8"))) {
        const resolved = resolveTarget(file, target);
        const candidates = [resolved, target];
        if (candidates.some((c) => allow.some((re) => re.test(c)))) continue;
        if (candidates.some((c) => forbid.some((re) => re.test(c)))) {
          violations.push({ rule: rule.id, file, line, target, message: rule.message ?? "" });
        }
      }
    }
  }
  return violations;
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    return;
  }
  const repo = execFileSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8" }).trim();
  const configPath = args.includes("--config") ? args[args.indexOf("--config") + 1] : ".agents/import-boundaries.json";
  const config = JSON.parse(fs.readFileSync(path.join(repo, configPath), "utf8"));
  const violations = checkBoundaries(repo, config);
  if (violations.length === 0) {
    process.stdout.write(`import-boundaries: ${config.rules.length} rule(s) hold\n`);
    return;
  }
  for (const v of violations) process.stderr.write(`${v.file}:${v.line}: imports ${v.target} [${v.rule}] ${v.message}\n`);
  process.stderr.write(`import-boundaries: ${violations.length} violation(s)\n`);
  process.exit(1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
