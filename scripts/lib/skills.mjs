import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

export function parseFrontmatter(text) {
  const match = /^---\n([\s\S]*?)\n---\n/.exec(text);
  if (!match) return null;
  const fields = {};
  const lines = match[1].split("\n");
  for (let i = 0; i < lines.length; i++) {
    const kv = /^([A-Za-z][\w-]*):\s*(.*)$/.exec(lines[i]);
    if (!kv) continue;
    let value = kv[2];
    if (/^[>|][-+]?$/.test(value)) {
      const block = [];
      while (i + 1 < lines.length && /^\s+\S/.test(lines[i + 1])) block.push(lines[++i].trim());
      value = block.join(" ");
    }
    fields[kv[1]] = value.replace(/^["'](.*)["']$/, "$1");
  }
  return fields;
}

export function skillDirs(pluginDir) {
  const skillsDir = path.join(pluginDir, "skills");
  if (!fs.existsSync(skillsDir)) return [];
  const found = [];
  const walk = (dir) => {
    if (fs.existsSync(path.join(dir, "SKILL.md"))) {
      found.push(dir);
      return;
    }
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(path.join(dir, entry.name));
    }
  };
  walk(skillsDir);
  return found.sort();
}

export function marketplacePlugins(root = ROOT) {
  const marketplace = JSON.parse(fs.readFileSync(path.join(root, ".claude-plugin/marketplace.json"), "utf8"));
  return marketplace.plugins.map((p) => p.name);
}

export function listSkills(root = ROOT) {
  const skills = [];
  for (const plugin of marketplacePlugins(root)) {
    for (const dir of skillDirs(path.join(root, "plugins", plugin))) {
      const fields = parseFrontmatter(fs.readFileSync(path.join(dir, "SKILL.md"), "utf8")) ?? {};
      const name = path.basename(dir);
      skills.push({ key: `${plugin}/${name}`, plugin, name, dir, fields });
    }
  }
  return skills;
}
