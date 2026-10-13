#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const OLD_REPO = "tsnAnh/team-kit"; // legacy team-kit repository, the one this script moves away from
const NEW_REPO = "tsnAnh/faker-kit";

const USAGE = `Usage: node migrate-from-team-kit.mjs [--check] [REPO]

Move a repository that an older /setup-team-kit set up from the team-kit marketplace to faker-kit.
Claude Code registers a marketplace under the name in its manifest, which is now faker-kit, so a
teammate with no earlier registration cannot install the team-kit ids an older setup wrote.

REPO defaults to the current directory. It rewrites, keeping every other setting:
  .claude/settings.json   the team-kit marketplace entry and every <plugin>@team-kit id
  .pi/settings.json       a git:github.com/${OLD_REPO} package source, keeping its @<commit>
  .codex/config.toml      the install commands in its comments

--check  Change nothing; exit 1 if a file still needs moving.

Exit codes: 0 done or nothing to move, 1 --check found work, 2 a conflict it will not resolve.`;

const OLD = "team-kit";
const NEW = "faker-kit";
const KIT_REPO = /^tsnAnh\/(team|faker)-kit$/i;
const KIT_GIT_URL = /^(https:\/\/|git@)github\.com[/:]tsnAnh\/(team|faker)-kit(\.git)?$/i;
const OLD_PI_SOURCE = /^(git:github\.com\/|https:\/\/github\.com\/)tsnAnh\/team-kit(\.git)?(?=@|$)/i;
const NEW_PI_SOURCE = /^(git:github\.com\/|https:\/\/github\.com\/)tsnAnh\/faker-kit(\.git)?(?=@|$)/i;

class Conflict extends Error {}

const renameKeys = (object, rename) => Object.fromEntries(Object.entries(object).map(([key, value]) => [rename(key), value]));

function isKitSource(source) {
  if (source?.source === "github") return KIT_REPO.test(source.repo ?? "");
  if (source?.source === "git") return KIT_GIT_URL.test(source.url ?? "");
  return false;
}

function claudeSettings(text, file) {
  const settings = JSON.parse(text);
  const notes = [];
  const marketplaces = settings.extraKnownMarketplaces;
  if (marketplaces?.[OLD]) {
    for (const name of [OLD, NEW]) {
      if (marketplaces[name] && !isKitSource(marketplaces[name].source)) {
        throw new Conflict(`${file}: the ${name} marketplace is not tsnAnh's kit (${JSON.stringify(marketplaces[name].source)}); move it by hand`);
      }
    }
    if (marketplaces[NEW]) {
      delete marketplaces[OLD];
      notes.push(`dropped the ${OLD} marketplace; ${NEW} was already there`);
    } else {
      const source = marketplaces[OLD].source;
      if (source.source === "github") source.repo = NEW_REPO;
      else source.url = source.url.replace(/team-kit(\.git)?$/i, (_, git) => `${NEW}${git ?? ""}`);
      settings.extraKnownMarketplaces = renameKeys(marketplaces, (key) => (key === OLD ? NEW : key));
      notes.push(`renamed the ${OLD} marketplace to ${NEW}`);
    }
  }
  const plugins = settings.enabledPlugins;
  if (plugins) {
    const next = {};
    let renamed = 0;
    for (const [id, value] of Object.entries(plugins)) {
      if (!id.endsWith(`@${OLD}`)) {
        next[id] = value;
        continue;
      }
      const target = `${id.slice(0, -OLD.length)}${NEW}`;
      if (target in plugins) {
        notes.push(`kept ${target} as it was and dropped ${id}`);
        continue;
      }
      next[target] = value;
      renamed += 1;
    }
    settings.enabledPlugins = next;
    if (renamed) notes.push(`renamed ${renamed} plugin id${renamed === 1 ? "" : "s"} to @${NEW}`);
  }
  return { text: notes.length ? `${JSON.stringify(settings, null, 2)}\n` : text, notes };
}

function piSettings(text) {
  const settings = JSON.parse(text);
  if (!Array.isArray(settings.packages)) return { text, notes: [] };
  const sourceOf = (entry) => (typeof entry === "string" ? entry : entry?.source ?? "");
  const hasNew = settings.packages.some((entry) => NEW_PI_SOURCE.test(sourceOf(entry)));
  const notes = [];
  const packages = [];
  for (const entry of settings.packages) {
    const source = sourceOf(entry);
    if (!OLD_PI_SOURCE.test(source)) {
      packages.push(entry);
    } else if (hasNew) {
      notes.push(`dropped ${source}; a faker-kit package was already there`);
    } else {
      const moved = source.replace(OLD_PI_SOURCE, (_, prefix, git) => `${prefix}${NEW_REPO}${git ?? ""}`);
      packages.push(typeof entry === "string" ? moved : { ...entry, source: moved });
      notes.push(`moved ${source} to ${moved}`);
    }
  }
  if (!notes.length) return { text, notes };
  settings.packages = packages;
  return { text: `${JSON.stringify(settings, null, 2)}\n`, notes };
}

function codexConfig(text) {
  const lines = text.split("\n").map((line) =>
    line.startsWith("#") ? line.replaceAll(OLD_REPO, NEW_REPO).replaceAll(`@${OLD}"`, `@${NEW}"`) : line,
  );
  const moved = lines.join("\n");
  return { text: moved, notes: moved === text ? [] : ["moved the install commands in its comments to faker-kit"] };
}

const FILES = [
  [".claude/settings.json", claudeSettings],
  [".pi/settings.json", piSettings],
  [".codex/config.toml", codexConfig],
];

function writeAtomically(file, text) {
  const temp = `${file}.migrate.${process.pid}`;
  fs.writeFileSync(temp, text, { mode: fs.statSync(file).mode & 0o777 });
  fs.renameSync(temp, file);
}

function main(argv) {
  if (argv.includes("-h") || argv.includes("--help")) {
    console.log(USAGE);
    return 0;
  }
  const check = argv.includes("--check");
  const rest = argv.filter((arg) => arg !== "--check");
  if (rest.length > 1 || rest.some((arg) => arg.startsWith("-"))) {
    console.error(`migrate-from-team-kit: unexpected arguments ${rest.join(" ")}\n\n${USAGE}`);
    return 2;
  }
  const repo = path.resolve(rest[0] ?? ".");
  const plans = [];
  try {
    for (const [relative, migrate] of FILES) {
      const file = path.join(repo, relative);
      if (!fs.existsSync(file)) continue;
      const before = fs.readFileSync(file, "utf8");
      const { text, notes } = migrate(before, relative);
      if (notes.length) plans.push({ file, relative, text, notes });
    }
  } catch (error) {
    if (!(error instanceof Conflict) && !(error instanceof SyntaxError)) throw error;
    console.error(`migrate-from-team-kit: ${error.message}. Nothing was changed.`);
    return 2;
  }
  if (!plans.length) {
    console.log(`migrate-from-team-kit: nothing names the ${OLD} marketplace in ${repo}`);
    return 0;
  }
  for (const { file, relative, text, notes } of plans) {
    if (!check) writeAtomically(file, text);
    console.log(`${check ? "would change" : "changed"} ${relative}: ${notes.join("; ")}`);
  }
  if (check) return 1;
  console.log("Commit these files. Teammates who registered team-kit should then run: claude plugin marketplace remove team-kit");
  return 0;
}

process.exitCode = main(process.argv.slice(2));
