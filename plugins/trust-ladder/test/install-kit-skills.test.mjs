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
  return spawnSync(SCRIPT, ["--source", kit, "--ref", "HEAD", "--checkout", path.join(work, "kit"), "--dest", path.join(work, "skills"), ...extra], { encoding: "utf8" });
}

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
