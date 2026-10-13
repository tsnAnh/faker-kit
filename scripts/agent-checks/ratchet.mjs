#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `Usage: node scripts/agent-checks/ratchet.mjs <check|update> [--config .agents/ratchet.json] [--baseline .agents/ratchet-baseline.json]
       node scripts/agent-checks/ratchet.mjs file --rule <id> [--config .agents/ratchet.json] <path>

Stop a pattern from spreading while its existing instances are cleaned up. Each rule
counts regex matches in tracked files. "check" fails when a count rises above the
baseline. "update" lowers the baseline to the current counts; it never raises one.
A rule with "zeroAllowance": true allows no match at all: it ignores the baseline, and "update"
never records a count for it, so a rule meant to ban a pattern cannot be loosened by running update.
"file" applies one rule to one file, tracked or not, and fails when it matches: the fixture
check in rule-fixtures.mjs uses it to prove a rule fires.

Config (.agents/ratchet.json):
  { "rules": [ { "id": "rust-unwrap", "pattern": "\\\\.unwrap\\\\(\\\\)", "files": ["*.rs"],
                 "message": "Return a Result or use expect() in tests only" } ] }

Examples:
  node scripts/agent-checks/ratchet.mjs check
  node scripts/agent-checks/ratchet.mjs update
  node scripts/agent-checks/ratchet.mjs file --rule no-unwrap src/lib.rs
`;

function fail(message) {
  process.stderr.write(`ratchet: ${message}\n`);
  process.exit(2);
}

function trackedFiles(repo, globs) {
  const out = execFileSync("git", ["-C", repo, "ls-files", "-z", "--", ...globs, ":(exclude).agents/rule-fixtures/**"], { encoding: "utf8" });
  return out.split("\0").filter(Boolean);
}

export function countRule(repo, rule) {
  const re = new RegExp(rule.pattern, "g");
  const perFile = {};
  let total = 0;
  for (const file of trackedFiles(repo, rule.files ?? ["*"])) {
    const full = path.join(repo, file);
    if (!fs.existsSync(full)) continue;
    const n = (fs.readFileSync(full, "utf8").match(re) ?? []).length;
    if (n > 0) {
      perFile[file] = n;
      total += n;
    }
  }
  return { total, perFile };
}

export function runRatchet(repo, command, configPath, baselinePath) {
  const config = JSON.parse(fs.readFileSync(path.join(repo, configPath), "utf8"));
  const baselineFile = path.join(repo, baselinePath);
  const baseline = fs.existsSync(baselineFile) ? JSON.parse(fs.readFileSync(baselineFile, "utf8")) : {};
  const lines = [];
  let failed = false;
  const next = {};
  for (const rule of config.rules) {
    const { total, perFile } = countRule(repo, rule);
    const allowed = rule.zeroAllowance ? 0 : (baseline[rule.id]?.total ?? 0);
    next[rule.id] = { total, perFile };
    if (command === "check" && total > allowed) {
      failed = true;
      const grown = Object.entries(perFile)
        .filter(([file, n]) => n > (baseline[rule.id]?.perFile?.[file] ?? 0))
        .map(([file, n]) => `${file} (${baseline[rule.id]?.perFile?.[file] ?? 0} -> ${n})`);
      lines.push(`${rule.id}: ${total} found, baseline allows ${allowed}. ${rule.message ?? ""}`.trim());
      lines.push(`  grew in: ${grown.join(", ")}`);
    } else if (command === "check" && total < allowed) {
      lines.push(`${rule.id}: ${total} found, down from ${allowed}. Run "ratchet.mjs update" to lock in the progress.`);
    }
  }
  if (command === "update") {
    const merged = {};
    for (const [id, now] of Object.entries(next)) {
      const rule = config.rules.find((r) => r.id === id);
      if (rule.zeroAllowance) {
        if (now.total > 0) lines.push(`${id}: zero-allowance rule, so no baseline is written; ${now.total} found. Remove them instead.`);
        continue;
      }
      const before = baseline[id];
      merged[id] = before && now.total > before.total ? before : now;
      if (before && now.total > before.total) lines.push(`${id}: kept baseline ${before.total}; current ${now.total} is higher. Fix the new instances instead.`);
    }
    fs.mkdirSync(path.dirname(baselineFile), { recursive: true });
    fs.writeFileSync(baselineFile, `${JSON.stringify(merged, null, 2)}\n`);
    lines.push(`wrote ${baselinePath}`);
  }
  return { failed, lines };
}

export function scanFile(config, ruleId, file) {
  const rule = config.rules.find((r) => r.id === ruleId);
  if (!rule) fail(`no rule "${ruleId}" in the config`);
  const count = (fs.readFileSync(file, "utf8").match(new RegExp(rule.pattern, "g")) ?? []).length;
  return { count, line: `${rule.id}: ${count} found in ${file}. ${rule.message ?? ""}`.trim() };
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    return;
  }
  const command = args[0];
  if (command !== "check" && command !== "update" && command !== "file") fail(`expected "check", "update", or "file"\n\n${USAGE}`);
  const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
  if (command === "file") {
    const file = args.filter((a, i) => i > 0 && !a.startsWith("--") && !["--rule", "--config"].includes(args[i - 1])).at(-1);
    if (!file || !option("--rule")) fail(`file needs --rule <id> and a path\n\n${USAGE}`);
    const config = JSON.parse(fs.readFileSync(option("--config", ".agents/ratchet.json"), "utf8"));
    const { count, line } = scanFile(config, option("--rule"), file);
    (count > 0 ? process.stderr : process.stdout).write(`${line}\n`);
    if (count > 0) process.exit(1);
    return;
  }
  const repo = execFileSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8" }).trim();
  const { failed, lines } = runRatchet(repo, command, option("--config", ".agents/ratchet.json"), option("--baseline", ".agents/ratchet-baseline.json"));
  for (const line of lines) (failed ? process.stderr : process.stdout).write(`${line}\n`);
  if (failed) process.exit(1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
