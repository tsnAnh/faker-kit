#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PACKS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "packs");
const USAGE = `Usage: node install-pack.mjs <pack> [--repo DIR] [--with name,name] [--dry-run]

Copy a lint pack into a repository and register its checks.

- Copies packs/<pack>/files/ into the repository. A file that already exists with
  different content is left alone and reported, so you can merge it by hand.
- Appends the pack's check commands to .agents/check-commands, skipping duplicates.
- Prints the manual steps: config merges, the baseline, and the CI job to add.

  --repo     Target repository (default: current directory).
  --with     Also register optional checks, for example --with no-new-comments,locale-keys.
  --dry-run  Report what would change without writing.

Packs: ${fs.existsSync(PACKS) ? fs.readdirSync(PACKS).filter((p) => fs.existsSync(path.join(PACKS, p, "pack.json"))).join(", ") : "(none found)"}

Examples:
  node install-pack.mjs agnostic --with no-new-comments
  node install-pack.mjs rust --repo ../my-service --dry-run
`;

function listFiles(dir, base = dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? listFiles(full, base) : [path.relative(base, full)];
  });
}

export function installPack(packName, { repo = process.cwd(), withOptional = [], dryRun = false } = {}) {
  const packDir = path.join(PACKS, packName);
  const manifestFile = path.join(packDir, "pack.json");
  if (!fs.existsSync(manifestFile)) throw new Error(`no pack named "${packName}" in ${PACKS}`);
  const pack = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
  const report = { pack: pack.name, copied: [], unchanged: [], conflicts: [], checksAdded: [], manual: [] };

  for (const rel of listFiles(path.join(packDir, "files"))) {
    const src = path.join(packDir, "files", rel);
    const dest = path.join(repo, rel);
    if (fs.existsSync(dest)) {
      (fs.readFileSync(dest).equals(fs.readFileSync(src)) ? report.unchanged : report.conflicts).push(rel);
      continue;
    }
    report.copied.push(rel);
    if (!dryRun) {
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(src, dest);
      fs.chmodSync(dest, fs.statSync(src).mode & 0o777);
    }
  }

  const unknown = withOptional.filter((name) => !pack.optionalChecks?.[name]);
  if (unknown.length > 0) throw new Error(`pack "${pack.name}" has no optional check named ${unknown.join(", ")}`);
  const wanted = [...(pack.check ?? []), ...withOptional.map((name) => pack.optionalChecks[name])];
  const commandsFile = path.join(repo, ".agents", "check-commands");
  const existing = fs.existsSync(commandsFile) ? fs.readFileSync(commandsFile, "utf8") : "# One command per line. scripts/check.sh runs them all; CI runs scripts/check.sh.\n";
  const present = new Set(existing.split("\n").map((l) => l.trim()));
  report.checksAdded = wanted.filter((c) => !present.has(c));
  if (!dryRun && report.checksAdded.length > 0) {
    fs.mkdirSync(path.dirname(commandsFile), { recursive: true });
    fs.writeFileSync(commandsFile, `${existing.replace(/\n?$/, "\n")}${report.checksAdded.join("\n")}\n`);
  }

  for (const m of pack.merge ?? []) report.manual.push(`Merge ${path.join("packs", pack.name, m.from)} into ${m.into}: ${m.how}`);
  for (const rel of report.conflicts) report.manual.push(`${rel} already exists and differs from the pack's copy; merge ${path.join("packs", pack.name, "files", rel)} into it by hand.`);
  if (pack.baseline) report.manual.push(`Baseline: ${pack.baseline}`);
  if (pack.ci) report.manual.push(`CI: add the job in ${path.join("packs", pack.name, pack.ci)} to the repository's workflow.`);
  for (const t of pack.tools ?? []) if (!t.optional) report.manual.push(`Tool: ${t.name} ${t.version} (${t.why})`);
  return report;
}

function main() {
  const args = process.argv.slice(2);
  if (args.length === 0 || args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    process.exit(args.length === 0 ? 2 : 0);
  }
  const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
  const report = installPack(args[0], {
    repo: path.resolve(option("--repo") ?? "."),
    withOptional: (option("--with") ?? "").split(",").filter(Boolean),
    dryRun: args.includes("--dry-run"),
  });
  const section = (title, items) => items.length && process.stdout.write(`${title}\n${items.map((i) => `  ${i}`).join("\n")}\n`);
  process.stdout.write(`install-pack: ${report.pack}${args.includes("--dry-run") ? " (dry run)" : ""}\n`);
  section("Copied:", report.copied);
  section("Already present:", report.unchanged);
  section("Checks added to .agents/check-commands:", report.checksAdded);
  section("Do next:", report.manual);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`install-pack: ${error.message}\n`);
    process.exit(1);
  }
}
