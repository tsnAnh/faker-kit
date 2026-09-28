#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ROOT, parseFrontmatter, skillDirs } from "./lib/skills.mjs";
import { catalogProblems } from "./gen-catalog.mjs";

export { parseFrontmatter, skillDirs };

const OWN_PLUGIN = "trust-ladder";
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const ALLOWED_DUPLICATE_SKILLS = new Set(["thermo-nuclear-code-quality-review"]);

const problems = [];
const report = (where, message) => problems.push(`${where}: ${message}`);
const rel = (full) => path.relative(ROOT, full).split(path.sep).join("/");
const readJson = (file) => JSON.parse(fs.readFileSync(path.join(ROOT, file), "utf8"));

function markdownFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".git")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...markdownFiles(full));
    else if (entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

function checkLinks(file) {
  const text = fs.readFileSync(file, "utf8").replace(/```[\s\S]*?```/g, "");
  for (const match of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    const target = match[1];
    if (/^(https?:|mailto:|#)/.test(target)) continue;
    const clean = decodeURIComponent(target.split("#")[0]);
    if (!clean) continue;
    if (!fs.existsSync(path.resolve(path.dirname(file), clean))) report(rel(file), `broken link ${target}`);
  }
}

function checkMarketplace() {
  const marketplace = readJson(".claude-plugin/marketplace.json");
  const listed = new Set();
  for (const entry of marketplace.plugins) {
    listed.add(entry.name);
    const dir = path.join(ROOT, entry.source);
    const manifestFile = path.join(dir, ".claude-plugin/plugin.json");
    if (!fs.existsSync(manifestFile)) {
      report(".claude-plugin/marketplace.json", `plugin "${entry.name}" has no ${entry.source}/.claude-plugin/plugin.json`);
      continue;
    }
    const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
    if (manifest.name !== entry.name) report(rel(manifestFile), `name "${manifest.name}" does not match marketplace entry "${entry.name}"`);
  }
  for (const entry of fs.readdirSync(path.join(ROOT, "plugins"), { withFileTypes: true })) {
    if (entry.isDirectory() && !listed.has(entry.name)) report(`plugins/${entry.name}`, "plugin directory is missing from .claude-plugin/marketplace.json");
  }
  const own = readJson(`plugins/${OWN_PLUGIN}/.claude-plugin/plugin.json`);
  const upstream = readJson("upstream/source.json").plugins;
  const missing = upstream.filter((p) => !(own.dependencies ?? []).includes(p));
  if (missing.length > 0) report(`plugins/${OWN_PLUGIN}/.claude-plugin/plugin.json`, `dependencies miss vendored plugins: ${missing.join(", ")}`);
  return marketplace.plugins.map((p) => p.name);
}

function checkSkills(pluginNames) {
  const seen = new Map();
  for (const plugin of pluginNames) {
    for (const dir of skillDirs(path.join(ROOT, "plugins", plugin))) {
      const file = path.join(dir, "SKILL.md");
      const fields = parseFrontmatter(fs.readFileSync(file, "utf8"));
      const where = rel(file);
      if (!fields) {
        report(where, "missing YAML frontmatter");
        continue;
      }
      if (!fields.description) report(where, "frontmatter has no description");
      const dirName = path.basename(dir);
      if (plugin === OWN_PLUGIN) {
        if (fields.name !== dirName) report(where, `name "${fields.name}" must equal directory "${dirName}"`);
        if (!SKILL_NAME.test(fields.name ?? "")) report(where, `name "${fields.name}" must be lowercase letters, digits, and single hyphens`);
        if ((fields.description ?? "").length > 1024) report(where, "description is longer than 1024 characters");
      }
      const owners = seen.get(dirName) ?? [];
      owners.push(plugin);
      seen.set(dirName, owners);
    }
  }
  for (const [name, owners] of seen) {
    if (owners.length > 1 && !ALLOWED_DUPLICATE_SKILLS.has(name)) {
      report(`skill "${name}"`, `defined in ${owners.join(" and ")}; Pi keeps only the first, so rename the trust-ladder copy`);
    }
  }
}

function checkPiManifest() {
  const pkg = readJson("package.json");
  if (!pkg.keywords?.includes("pi-package")) report("package.json", 'keywords must include "pi-package"');
  for (const extension of pkg.pi?.extensions ?? []) {
    if (!fs.existsSync(path.join(ROOT, extension))) report("package.json", `pi extension ${extension} does not exist`);
  }
  if (!(pkg.pi?.skills ?? []).includes("./plugins/*/skills")) report("package.json", 'pi.skills must include "./plugins/*/skills"');
}

function checkOwnMarkdown() {
  const files = [
    ...markdownFiles(path.join(ROOT, "plugins", OWN_PLUGIN)),
    ...markdownFiles(path.join(ROOT, "docs")),
    ...["README.md", "AGENTS.md", "NOTICE.md"].map((f) => path.join(ROOT, f)).filter((f) => fs.existsSync(f)),
  ];
  for (const file of files) checkLinks(file);
}

function checkOwnPathReferences() {
  const pluginRoot = path.join(ROOT, "plugins", OWN_PLUGIN);
  const vendoredSkillDirs = fs
    .readdirSync(path.join(ROOT, "plugins"))
    .filter((p) => p !== OWN_PLUGIN)
    .flatMap((p) => skillDirs(path.join(ROOT, "plugins", p)));
  for (const dir of skillDirs(pluginRoot)) {
    const file = path.join(dir, "SKILL.md");
    const text = fs.readFileSync(file, "utf8");
    for (const match of text.matchAll(/`((?:templates|references|packs)\/[^`\s]+)`/g)) {
      const target = match[1].replace(/[<{].*$/, "");
      const bases = [dir, pluginRoot, ...vendoredSkillDirs];
      if (!bases.some((base) => fs.existsSync(path.join(base, target)))) report(rel(file), `\`${match[1]}\` does not exist`);
    }
  }
}

function main() {
  const pluginNames = checkMarketplace();
  checkSkills(pluginNames);
  checkPiManifest();
  checkOwnMarkdown();
  checkOwnPathReferences();
  for (const problem of catalogProblems()) problems.push(problem);
  if (problems.length > 0) {
    process.stderr.write(`validate: ${problems.length} problem(s)\n  ${problems.join("\n  ")}\n`);
    process.exit(1);
  }
  process.stdout.write(`validate: ${pluginNames.length} plugins OK\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main();
