#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `Usage: node scripts/agent-checks/rule-fixtures.mjs [--config .agents/rule-fixtures.json]

Prove that every encoded rule still fires. Each rule names the command that runs the
check, a bad fixture that must fail with the expected text in its output, and a good
fixture that must pass. A rule someone weakens or deletes fails here, not in review.

Keep fixtures under .agents/rule-fixtures/<id>/ and exclude that directory from the
normal lint run, so the bad fixture does not fail the rest of the lint run.

Config:
  { "rules": [ { "id": "no-direct-now",
                 "run": "ruff check --no-cache {file}",
                 "bad": ".agents/rule-fixtures/no-direct-now/bad.py",
                 "good": ".agents/rule-fixtures/no-direct-now/good.py",
                 "expect": "TID251" } ] }

"{file}" in "run" is replaced with the fixture path. Commands run from the repository root.

Examples:
  node scripts/agent-checks/rule-fixtures.mjs
  node scripts/agent-checks/rule-fixtures.mjs --config .agents/rule-fixtures.json
`;

function run(repo, command, file) {
  const result = spawnSync("bash", ["-c", command.replaceAll("{file}", file)], { cwd: repo, encoding: "utf8" });
  return { status: result.status, output: `${result.stdout ?? ""}${result.stderr ?? ""}` };
}

export function checkRuleFixtures(repo, config) {
  const problems = [];
  for (const rule of config.rules ?? []) {
    const where = `rule ${rule.id}`;
    for (const key of ["run", "bad", "good", "expect"]) {
      if (!rule[key]) problems.push(`${where}: missing "${key}"`);
    }
    if (problems.some((p) => p.startsWith(where))) continue;
    for (const key of ["bad", "good"]) {
      if (!fs.existsSync(path.join(repo, rule[key]))) problems.push(`${where}: ${rule[key]} does not exist`);
    }
    if (problems.some((p) => p.startsWith(where))) continue;
    const bad = run(repo, rule.run, rule.bad);
    if (bad.status === 0) problems.push(`${where}: passed on ${rule.bad}; the rule no longer fires. Restore it, or fix the fixture if the rule moved.`);
    else if (!bad.output.includes(rule.expect)) problems.push(`${where}: failed on ${rule.bad} without "${rule.expect}" in its output, so something else failed:\n${bad.output.trim().split("\n").slice(0, 5).join("\n")}`);
    const good = run(repo, rule.run, rule.good);
    if (good.status !== 0) problems.push(`${where}: failed on ${rule.good}, which must pass:\n${good.output.trim().split("\n").slice(0, 5).join("\n")}`);
  }
  return problems;
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    return;
  }
  const configPath = args.includes("--config") ? args[args.indexOf("--config") + 1] : ".agents/rule-fixtures.json";
  const repo = spawnSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8" }).stdout.trim() || process.cwd();
  const configFile = path.join(repo, configPath);
  if (!fs.existsSync(configFile)) {
    process.stderr.write(`rule-fixtures: ${configPath} is missing. Create it with {"rules": []}, or see --help.\n`);
    process.exit(2);
  }
  const config = JSON.parse(fs.readFileSync(configFile, "utf8"));
  const problems = checkRuleFixtures(repo, config);
  if (problems.length > 0) {
    process.stderr.write(`rule-fixtures: ${problems.length} problem(s)\n${problems.map((p) => `  ${p}`).join("\n")}\n`);
    process.exit(1);
  }
  process.stdout.write(`rule-fixtures: ${(config.rules ?? []).length} rule(s) fire on their bad fixture and pass their good one\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
