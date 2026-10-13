import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const TRUST_LADDER = path.resolve(import.meta.dir, "..");
const SCRIPT = path.join(TRUST_LADDER, "skills", "setup-team-kit", "scripts", "migrate-from-team-kit.mjs");
const TEMPLATES = path.join(TRUST_LADDER, "templates");
const KIT = path.resolve(TRUST_LADDER, "..", "..");

const template = (name) => fs.readFileSync(path.join(TEMPLATES, name), "utf8");
// What an older /setup-team-kit wrote: today's templates under the legacy team-kit name.
const older = (text) =>
  text
    .replaceAll("tsnAnh/faker-kit", "tsnAnh/team-kit") // legacy team-kit repository
    .replaceAll("@faker-kit", "@team-kit")
    .replace('"faker-kit": {', '"team-kit": {');

function repo(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "faker-kit-migrate-"));
  for (const [file, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true });
    fs.writeFileSync(path.join(dir, file), text);
  }
  return dir;
}

const read = (dir, file) => fs.readFileSync(path.join(dir, file), "utf8");
const migrate = (dir, ...args) => spawnSync("node", [SCRIPT, ...args, dir], { encoding: "utf8" });

const OLD_FILES = {
  ".claude/settings.json": older(template("claude-settings.json")),
  ".pi/settings.json": older(template("pi-settings.json")),
  ".codex/config.toml": older(template("codex-config.toml")),
};

describe("migrate-from-team-kit", () => {
  test("a repository set up from the older templates ends up with today's templates", () => {
    expect(Object.values(OLD_FILES).join("")).not.toContain("faker-kit");
    const dir = repo(OLD_FILES);
    const done = migrate(dir);
    expect(done.status).toBe(0);
    expect(read(dir, ".claude/settings.json")).toBe(template("claude-settings.json"));
    expect(read(dir, ".pi/settings.json")).toBe(template("pi-settings.json"));
    expect(read(dir, ".codex/config.toml")).toBe(template("codex-config.toml"));
    expect(done.stdout).toContain("changed .claude/settings.json: renamed the team-kit marketplace to faker-kit; renamed 9 plugin ids to @faker-kit\n");
  });

  test("a second run changes nothing", () => {
    const dir = repo(OLD_FILES);
    expect(migrate(dir).status).toBe(0);
    const once = Object.keys(OLD_FILES).map((file) => read(dir, file));
    const again = migrate(dir);
    expect(again.status).toBe(0);
    expect(again.stdout).toBe(`migrate-from-team-kit: nothing names the team-kit marketplace in ${fs.realpathSync(dir)}\n`);
    expect(Object.keys(OLD_FILES).map((file) => read(dir, file))).toEqual(once);
  });

  test("--check reports the work and writes nothing", () => {
    const dir = repo(OLD_FILES);
    const checked = migrate(dir, "--check");
    expect(checked.status).toBe(1);
    expect(checked.stdout.split("\n").filter(Boolean).map((line) => line.split(":")[0])).toEqual([
      "would change .claude/settings.json",
      "would change .pi/settings.json",
      "would change .codex/config.toml",
    ]);
    expect(Object.keys(OLD_FILES).map((file) => read(dir, file))).toEqual(Object.values(OLD_FILES));
    expect(migrate(repo({ ".claude/settings.json": template("claude-settings.json") }), "--check").status).toBe(0);
  });

  test("every other setting, a pinned ref, and a teammate's own plugin stay as they were", () => {
    const settings = {
      model: "opus",
      extraKnownMarketplaces: {
        mine: { source: { source: "github", repo: "acme/plugins" } },
        "team-kit": { source: { source: "github", repo: "tsnAnh/team-kit", ref: "v2" } }, // legacy team-kit marketplace
      },
      enabledPlugins: { "lint@mine": true, "trust-ladder@team-kit": true, "thermos@team-kit": false },
      permissions: { deny: ["Read(./.env)"] },
    };
    const dir = repo({
      ".claude/settings.json": JSON.stringify(settings),
      ".pi/settings.json": JSON.stringify({ packages: ["npm:pi-ask-user", { source: "git:github.com/tsnAnh/team-kit@abc123", skills: [] }] }), // legacy team-kit source
    });
    expect(migrate(dir).status).toBe(0);
    expect(JSON.parse(read(dir, ".claude/settings.json"))).toEqual({
      model: "opus",
      extraKnownMarketplaces: {
        mine: { source: { source: "github", repo: "acme/plugins" } },
        "faker-kit": { source: { source: "github", repo: "tsnAnh/faker-kit", ref: "v2" } },
      },
      enabledPlugins: { "lint@mine": true, "trust-ladder@faker-kit": true, "thermos@faker-kit": false },
      permissions: { deny: ["Read(./.env)"] },
    });
    expect(JSON.parse(read(dir, ".pi/settings.json"))).toEqual({
      packages: ["npm:pi-ask-user", { source: "git:github.com/tsnAnh/faker-kit@abc123", skills: [] }],
    });
  });

  test("a half-moved repository keeps its faker-kit choices and loses only the duplicates", () => {
    const dir = repo({
      ".claude/settings.json": JSON.stringify({
        extraKnownMarketplaces: {
          "team-kit": { source: { source: "github", repo: "tsnAnh/team-kit" } }, // legacy team-kit marketplace
          "faker-kit": { source: { source: "github", repo: "tsnAnh/faker-kit" } },
        },
        enabledPlugins: { "trust-ladder@team-kit": true, "trust-ladder@faker-kit": false, "pstack@team-kit": true },
      }),
      ".pi/settings.json": JSON.stringify({ packages: ["git:github.com/tsnAnh/team-kit@abc", "git:github.com/tsnAnh/faker-kit@def"] }), // legacy team-kit source
    });
    const done = migrate(dir);
    expect(done.status).toBe(0);
    expect(JSON.parse(read(dir, ".claude/settings.json"))).toEqual({
      extraKnownMarketplaces: { "faker-kit": { source: { source: "github", repo: "tsnAnh/faker-kit" } } },
      enabledPlugins: { "trust-ladder@faker-kit": false, "pstack@faker-kit": true },
    });
    expect(JSON.parse(read(dir, ".pi/settings.json"))).toEqual({ packages: ["git:github.com/tsnAnh/faker-kit@def"] });
    expect(done.stdout).toContain("kept trust-ladder@faker-kit as it was and dropped trust-ladder@team-kit");
  });

  test("a team-kit marketplace that is not this kit stops the run before any file changes", () => {
    const foreign = JSON.stringify({
      extraKnownMarketplaces: { "team-kit": { source: { source: "github", repo: "someone/team-kit" } } },
      enabledPlugins: { "trust-ladder@team-kit": true },
    });
    const dir = repo({ ".claude/settings.json": foreign, ".pi/settings.json": OLD_FILES[".pi/settings.json"] });
    const done = migrate(dir);
    expect(done.status).toBe(2);
    expect(done.stderr).toBe(
      'migrate-from-team-kit: .claude/settings.json: the team-kit marketplace is not tsnAnh\'s kit ({"source":"github","repo":"someone/team-kit"}); move it by hand. Nothing was changed.\n',
    );
    expect(read(dir, ".claude/settings.json")).toBe(foreign);
    expect(read(dir, ".pi/settings.json")).toBe(OLD_FILES[".pi/settings.json"]);
  });

  test("a settings file that is not JSON stops the run", () => {
    const dir = repo({ ".claude/settings.json": "{ not json" });
    const done = migrate(dir);
    expect(done.status).toBe(2);
    expect(done.stderr).toStartWith("migrate-from-team-kit: ");
    expect(read(dir, ".claude/settings.json")).toBe("{ not json");
  });

  // The defect this script exists for, against the real Claude Code CLI: a marketplace is registered
  // under its manifest's name, so only the migrated ids install for a teammate with no old registration.
  const claude = spawnSync("claude", ["--version"], { encoding: "utf8" }).status === 0;
  test.skipIf(!claude)("with the real claude CLI, only the migrated plugin ids install from this kit", () => {
    const config = fs.mkdtempSync(path.join(os.tmpdir(), "faker-kit-claude-"));
    const env = { ...process.env, CLAUDE_CONFIG_DIR: config };
    const cli = (...args) => spawnSync("claude", ["plugin", ...args], { encoding: "utf8", env, timeout: 120_000 });
    expect(cli("marketplace", "add", KIT).status).toBe(0);
    expect(Object.keys(JSON.parse(fs.readFileSync(path.join(config, "plugins", "known_marketplaces.json"), "utf8")))).toContain("faker-kit");

    const dir = repo(OLD_FILES);
    const oldId = Object.keys(JSON.parse(read(dir, ".claude/settings.json")).enabledPlugins)[0];
    const before = cli("install", oldId);
    expect(before.status).not.toBe(0);
    expect(before.stdout + before.stderr).toContain('Plugin "trust-ladder" not found in marketplace "team-kit"');

    expect(migrate(dir).status).toBe(0);
    const ids = Object.keys(JSON.parse(read(dir, ".claude/settings.json")).enabledPlugins);
    const after = cli("install", ids[0]);
    expect(after.status).toBe(0);
    const installed = JSON.parse(fs.readFileSync(path.join(config, "plugins", "installed_plugins.json"), "utf8")).plugins;
    expect(Object.keys(installed).sort()).toEqual([...ids].sort());
  }, 120_000);
});
