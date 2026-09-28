#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseFrontmatter, skillDirs } from "./validate.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const EXPECTED_DIAGNOSTICS = [
  /^collision name "thermo-nuclear-code-quality-review" collision$/,
];
const USAGE = `Usage: node scripts/check-pi-package.mjs --sdk <dir>

Load this repository as a Pi package with Pi's own resource loader and fail unless
every skill is discovered, the extension loads, and no unexpected diagnostics appear.

  --sdk <dir>   Directory whose node_modules holds @earendil-works/pi-coding-agent.

Example:
  npm i --prefix /tmp/pi @earendil-works/pi-coding-agent && node scripts/check-pi-package.mjs --sdk /tmp/pi
`;

const sdkIndex = process.argv.indexOf("--sdk");
if (sdkIndex === -1 || !process.argv[sdkIndex + 1]) {
  process.stderr.write(USAGE);
  process.exit(2);
}
const sdkPackageDir = path.join(path.resolve(process.argv[sdkIndex + 1]), "node_modules", "@earendil-works", "pi-coding-agent");
const sdkPackage = JSON.parse(fs.readFileSync(path.join(sdkPackageDir, "package.json"), "utf8"));
const entry = sdkPackage.exports?.["."]?.import ?? sdkPackage.exports?.["."]?.default ?? sdkPackage.main;
const pi = await import(pathToFileURL(path.join(sdkPackageDir, typeof entry === "string" ? entry : entry.default)).href);

const agentDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-agent-"));
const project = fs.mkdtempSync(path.join(os.tmpdir(), "pi-project-"));
fs.mkdirSync(path.join(project, ".git"));
fs.writeFileSync(path.join(agentDir, "settings.json"), JSON.stringify({ packages: [ROOT] }));
const loader = new pi.DefaultResourceLoader({ cwd: project, agentDir });
await loader.reload();

const problems = [];
const found = new Set(loader.getSkills().skills.map((s) => s.name));
for (const plugin of fs.readdirSync(path.join(ROOT, "plugins"))) {
  for (const dir of skillDirs(path.join(ROOT, "plugins", plugin))) {
    const name = parseFrontmatter(fs.readFileSync(path.join(dir, "SKILL.md"), "utf8"))?.name;
    if (!found.has(name)) problems.push(`skill ${plugin}/${path.basename(dir)} (name "${name}") was not discovered`);
  }
}
for (const d of loader.getSkills().diagnostics) {
  const text = `${d.type} ${d.message}`.trim();
  if (!EXPECTED_DIAGNOSTICS.some((re) => re.test(text))) problems.push(`unexpected skill diagnostic: ${text}`);
}
const extensions = loader.getExtensions();
for (const e of extensions.errors ?? []) problems.push(`extension ${e.path} failed: ${e.error}`);
if ((extensions.extensions ?? []).length !== 1) problems.push(`expected 1 extension, loaded ${(extensions.extensions ?? []).length}`);

if (problems.length > 0) {
  process.stderr.write(`check-pi-package: ${problems.length} problem(s)\n  ${problems.join("\n  ")}\n`);
  process.exit(1);
}
process.stdout.write(`check-pi-package: ${found.size} skills and the team-kit extension load in Pi\n`);
