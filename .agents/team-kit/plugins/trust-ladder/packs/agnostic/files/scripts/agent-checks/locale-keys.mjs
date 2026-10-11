#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { globToRegExp } from "./glob.mjs";

const USAGE = `Usage: node scripts/agent-checks/locale-keys.mjs [--config .agents/locales.json]

Fail when a translation is missing, extra, left in the source language, or drops a
placeholder. Supports Flutter ARB, flat or nested JSON, Android strings.xml, and
Apple .strings files.

Config (.agents/locales.json):
  { "sets": [ {
      "id": "app",
      "format": "arb",
      "source": "lib/l10n/app_en.arb",
      "targets": "lib/l10n/app_*.arb",
      "allowIdentical": ["appName", "ok"]
  } ] }

format is one of: arb, json, android, apple-strings.
targets is a glob; the source file is skipped when it matches.

Example:
  node scripts/agent-checks/locale-keys.mjs
`;

function flatten(obj, prefix = "", out = {}) {
  for (const [key, value] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) flatten(value, full, out);
    else out[full] = String(value);
  }
  return out;
}

function unescapeXml(s) {
  return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");
}

export const PARSERS = {
  arb: (text) => Object.fromEntries(Object.entries(JSON.parse(text)).filter(([k]) => !k.startsWith("@")).map(([k, v]) => [k, String(v)])),
  json: (text) => flatten(JSON.parse(text)),
  android: (text) => {
    const out = {};
    for (const m of text.matchAll(/<string\s+name="([^"]+)"(?![^>]*translatable="false")[^>]*>([\s\S]*?)<\/string>/g)) out[m[1]] = unescapeXml(m[2].trim());
    for (const m of text.matchAll(/<plurals\s+name="([^"]+)"[^>]*>([\s\S]*?)<\/plurals>/g)) {
      for (const item of m[2].matchAll(/<item\s+quantity="([^"]+)"[^>]*>([\s\S]*?)<\/item>/g)) out[`${m[1]}#${item[1]}`] = unescapeXml(item[2].trim());
    }
    return out;
  },
  "apple-strings": (text) => {
    const out = {};
    const withoutComments = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    for (const m of withoutComments.matchAll(/"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;/g)) out[m[1]] = m[2];
    return out;
  },
};

function placeholders(value) {
  return [...value.matchAll(/\{[\w.]+[^}]*\}|%(?:\d+\$)?[@dsfilu]|%%|\$\{?\w+\}?/g)].map((m) => m[0].replace(/[,\s].*\}$/, "}")).sort();
}

function hasLetters(value) {
  return /\p{L}/u.test(value.replace(/\{[^}]*\}|%(?:\d+\$)?[@dsfilu]/g, ""));
}

export function checkSet(repo, set) {
  const parse = PARSERS[set.format];
  if (!parse) throw new Error(`set "${set.id}": unknown format "${set.format}"`);
  const read = (file) => parse(fs.readFileSync(path.join(repo, file), "utf8"));
  const source = read(set.source);
  const allowIdentical = new Set(set.allowIdentical ?? []);
  const targetRe = globToRegExp(set.targets);
  const files = execFileSync("git", ["-C", repo, "ls-files", "-z"], { encoding: "utf8" }).split("\0").filter(Boolean);
  const targets = files.filter((f) => targetRe.test(f) && f !== set.source);
  const problems = [];
  for (const file of targets) {
    const target = read(file);
    for (const key of Object.keys(source)) {
      if (!(key in target)) problems.push(`${file}: missing "${key}"`);
      else if (target[key].trim() === "") problems.push(`${file}: "${key}" is empty`);
      else if (target[key] === source[key] && hasLetters(source[key]) && !allowIdentical.has(key)) problems.push(`${file}: "${key}" is still the source text "${source[key]}"`);
      else if (placeholders(target[key]).join() !== placeholders(source[key]).join()) {
        problems.push(`${file}: "${key}" has placeholders [${placeholders(target[key])}] but the source has [${placeholders(source[key])}]`);
      }
    }
    for (const key of Object.keys(target)) if (!(key in source)) problems.push(`${file}: extra "${key}" not in ${set.source}`);
  }
  return { targets: targets.length, problems };
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    return;
  }
  const repo = execFileSync("git", ["rev-parse", "--show-toplevel"], { encoding: "utf8" }).trim();
  const configPath = args.includes("--config") ? args[args.indexOf("--config") + 1] : ".agents/locales.json";
  const config = JSON.parse(fs.readFileSync(path.join(repo, configPath), "utf8"));
  let failed = 0;
  for (const set of config.sets) {
    const { targets, problems } = checkSet(repo, set);
    for (const p of problems) process.stderr.write(`[${set.id}] ${p}\n`);
    failed += problems.length;
    if (problems.length === 0) process.stdout.write(`locale-keys: [${set.id}] ${targets} locale(s) complete\n`);
  }
  if (failed > 0) {
    process.stderr.write(`locale-keys: ${failed} problem(s). Add real translations for every supported locale; do not copy the source text.\n`);
    process.exit(1);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
