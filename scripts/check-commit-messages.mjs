#!/usr/bin/env node
// Owner rule (STYLE.md promotion, .agents/dispatch.md standing order 6): commit messages carry no AI or
// agent references. Checks only the commits this branch adds on top of the default branch.
import { execFileSync } from "node:child_process";

const USAGE = `Usage: node scripts/check-commit-messages.mjs [--base <ref>]

Fail when a commit on this branch credits an AI or an agent session in its message:
a Co-Authored-By line naming an AI, a "Generated with/by" line, or a Claude-Session link.
Checks <base>..HEAD, where <base> defaults to the merge base with origin/main.`;

const RULES = [
  [/^co-authored-by:.*\b(claude|anthropic|openai|codex|chatgpt|gpt-?\d|copilot|gemini)\b/im, "a Co-Authored-By line naming an AI"],
  [/^.*\bgenerated (with|by)\b.*\b(claude|chatgpt|codex|copilot|gemini|ai)\b/im, "a \"generated with\" line"],
  [/^claude-session:/im, "a Claude-Session link"],
];

function git(...args) {
  return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}

const argv = process.argv.slice(2);
if (argv.includes("-h") || argv.includes("--help")) {
  console.log(USAGE);
  process.exit(0);
}
let base = argv.includes("--base") ? argv[argv.indexOf("--base") + 1] : null;
if (!base) {
  try {
    base = git("merge-base", "HEAD", "origin/main");
  } catch {
    console.log("commit-messages: no origin/main to compare with; nothing to check");
    process.exit(0);
  }
}
const shas = git("rev-list", "--no-merges", `${base}..HEAD`).split("\n").filter(Boolean);
const problems = [];
for (const sha of shas) {
  const message = git("log", "-1", "--format=%B", sha);
  for (const [pattern, what] of RULES) {
    const hit = message.match(pattern);
    if (hit) problems.push(`${sha.slice(0, 12)} has ${what}: ${hit[0].trim()}`);
  }
}
if (problems.length) {
  console.error(problems.join("\n"));
  console.error(`commit-messages: ${problems.length} problem(s). Reword them without AI or agent references: git commit --amend for the last commit, or rebuild the branch for older ones.`);
  process.exit(1);
}
console.log(`commit-messages: ${shas.length} commit(s) on this branch carry no AI references`);
