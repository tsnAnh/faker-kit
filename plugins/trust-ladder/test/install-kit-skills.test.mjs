import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SCRIPT = path.resolve(import.meta.dir, "..", "skills", "setup-cloud-agents", "scripts", "install-kit-skills.sh");

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "team-kit-skills-"));
const git = (cwd, ...args) => spawnSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd, encoding: "utf8" });

function fakeKit(skills) {
  const kit = tmp();
  for (const [plugin, name] of skills) {
    fs.mkdirSync(path.join(kit, "plugins", plugin, "skills", name), { recursive: true });
    fs.writeFileSync(path.join(kit, "plugins", plugin, "skills", name, "SKILL.md"), `---\nname: ${name}\ndescription: ${plugin}\n---\n`);
  }
  git(kit, "init", "-q");
  git(kit, "add", ".");
  git(kit, "commit", "-qm", "kit");
  return kit;
}

function install(kit, work, ...extra) {
  // Exercise Apple's Bash 3 on macOS, even when Homebrew Bash is first on PATH.
  return spawnSync("/bin/bash", [SCRIPT, "--source", kit, "--ref", "HEAD", "--checkout", path.join(work, "kit"), "--dest", path.join(work, "skills"), ...extra], { encoding: "utf8" });
}

const DEFAULT_SOURCE = "https://github.com/tsnAnh/faker-kit.git";

// Runs with the script's default --source, checkout, and dest under a throwaway HOME. Git rewrites
// the default URL to `target`, so no network is used and a wrong default fails the fetch.
function installDefault(home, target) {
  const gitconfig = path.join(home, "gitconfig");
  fs.writeFileSync(gitconfig, `[url "${target}"]\n\tinsteadOf = ${DEFAULT_SOURCE}\n`);
  const env = { ...process.env, HOME: home, GIT_CONFIG_GLOBAL: gitconfig, GIT_CONFIG_NOSYSTEM: "1" };
  return spawnSync("/bin/bash", [SCRIPT, "--ref", "HEAD"], { encoding: "utf8", env });
}

const head = (repo) => git(repo, "rev-parse", "HEAD").stdout.trim();

const owner = (work, name) => /description: (.*)/.exec(fs.readFileSync(path.join(work, "skills", name, "SKILL.md"), "utf8"))[1];

describe("install-kit-skills", () => {
  test("links every plugin skill, and thermos wins the shared review skill", () => {
    const kit = fakeKit([["trust-ladder", "start-here"], ["pstack", "faker-mode"], ["team-workflows", "shared"], ["thermos", "shared"]]);
    const work = tmp();
    const result = install(kit, work);
    expect(result.status).toBe(0);
    expect(fs.readdirSync(path.join(work, "skills")).sort()).toEqual(["faker-mode", "shared", "start-here"]);
    expect(owner(work, "shared")).toBe("thermos");
  });

  test("a second run at a newer commit moves the links and drops removed skills", () => {
    const kit = fakeKit([["trust-ladder", "start-here"], ["pstack", "old-skill"]]);
    const work = tmp();
    expect(install(kit, work).status).toBe(0);
    fs.rmSync(path.join(kit, "plugins", "pstack"), { recursive: true });
    git(kit, "commit", "-qam", "drop");
    const result = install(kit, work);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("removed stale");
    expect(fs.readdirSync(path.join(work, "skills"))).toEqual(["start-here"]);
  });

  test("trust-ladder keeps precedence even when its destination conflicts", () => {
    const kit = fakeKit([["trust-ladder", "shared"], ["pstack", "shared"]]);
    const work = tmp();
    fs.mkdirSync(path.join(work, "skills"), { recursive: true });
    fs.symlinkSync(path.join(work, "missing-user-skill"), path.join(work, "skills", "shared"));
    const result = install(kit, work);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("links elsewhere; left as is");
    expect(result.stdout).toContain("skipped shared from pstack: trust-ladder already provides it");
    expect(fs.readlinkSync(path.join(work, "skills", "shared"))).toBe(path.join(work, "missing-user-skill"));
  });

  test("the default source is the faker-kit repository", () => {
    const kit = fakeKit([["trust-ladder", "start-here"]]);
    const home = fs.realpathSync(tmp());
    const result = installDefault(home, kit);
    expect(result.status).toBe(0);
    expect(fs.readlinkSync(path.join(home, ".agents", "skills", "start-here"))).toBe(path.join(home, ".team-kit", "plugins", "trust-ladder", "skills", "start-here"));
  });

  test("an install from the old source upgrades in place from the default source", () => {
    const kit = fakeKit([["trust-ladder", "start-here"]]);
    const home = fs.realpathSync(tmp());
    const env = { ...process.env, HOME: home };
    expect(spawnSync("/bin/bash", [SCRIPT, "--source", kit, "--ref", "HEAD"], { encoding: "utf8", env }).status).toBe(0);
    const newer = tmp();
    git(newer, "clone", "-q", kit, ".");
    fs.mkdirSync(path.join(newer, "plugins", "pstack", "skills", "faker-mode"), { recursive: true });
    fs.writeFileSync(path.join(newer, "plugins", "pstack", "skills", "faker-mode", "SKILL.md"), "---\nname: faker-mode\ndescription: pstack\n---\n");
    git(newer, "add", ".");
    git(newer, "commit", "-qm", "newer");
    const result = installDefault(home, newer);
    expect(result.status).toBe(0);
    expect(head(path.join(home, ".team-kit"))).toBe(head(newer));
    expect(fs.readdirSync(path.join(home, ".agents", "skills")).sort()).toEqual(["faker-mode", "start-here"]);
    expect(fs.readlinkSync(path.join(home, ".agents", "skills", "start-here"))).toBe(path.join(home, ".team-kit", "plugins", "trust-ladder", "skills", "start-here"));
  });

  test("a source that cannot be fetched fails and leaves the existing links", () => {
    const kit = fakeKit([["trust-ladder", "start-here"]]);
    const home = fs.realpathSync(tmp());
    expect(installDefault(home, kit).status).toBe(0);
    const before = head(path.join(home, ".team-kit"));
    const result = installDefault(home, path.join(home, "missing-kit"));
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("missing-kit");
    expect(head(path.join(home, ".team-kit"))).toBe(before);
    expect(fs.readlinkSync(path.join(home, ".agents", "skills", "start-here"))).toBe(path.join(home, ".team-kit", "plugins", "trust-ladder", "skills", "start-here"));
  });

  test("never replaces a skill the user already has", () => {
    const kit = fakeKit([["trust-ladder", "start-here"]]);
    const work = tmp();
    fs.mkdirSync(path.join(work, "skills", "start-here"), { recursive: true });
    fs.writeFileSync(path.join(work, "skills", "start-here", "SKILL.md"), "mine");
    const result = install(kit, work);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("is a real directory");
    expect(fs.readFileSync(path.join(work, "skills", "start-here", "SKILL.md"), "utf8")).toBe("mine");
  });
});
