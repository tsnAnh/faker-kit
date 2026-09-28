import { describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { installPack } from "../skills/setup-lint-pack/scripts/install-pack.mjs";

const PACKS = path.resolve(import.meta.dir, "../packs");
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "install-pack-"));
const read = (dir, file) => fs.readFileSync(path.join(dir, file), "utf8");

describe("install-pack", () => {
  test("every pack has a manifest, a test, fixtures, and a README", () => {
    for (const pack of fs.readdirSync(PACKS).filter((p) => fs.existsSync(path.join(PACKS, p, "pack.json")))) {
      const manifest = JSON.parse(read(path.join(PACKS, pack), "pack.json"));
      expect(manifest.name).toBe(pack);
      expect(manifest.check.length).toBeGreaterThan(0);
      expect(typeof manifest.baseline).toBe("string");
      for (const file of ["test.sh", "README.md"]) expect(fs.existsSync(path.join(PACKS, pack, file))).toBe(true);
      if (pack !== "agnostic") {
        expect(fs.existsSync(path.join(PACKS, pack, "fixtures", "bad"))).toBe(true);
        expect(fs.existsSync(path.join(PACKS, pack, "fixtures", "good"))).toBe(true);
        expect(fs.existsSync(path.join(PACKS, pack, manifest.ci))).toBe(true);
      }
      for (const m of manifest.merge ?? []) expect(fs.existsSync(path.join(PACKS, pack, m.from))).toBe(true);
    }
  });

  test("copies files, registers checks once, and keeps executable bits", () => {
    const repo = tmp();
    const first = installPack("agnostic", { repo, withOptional: ["locale-keys"] });
    expect(first.copied).toContain("scripts/check.sh");
    expect(fs.statSync(path.join(repo, "scripts/check.sh")).mode & 0o111).not.toBe(0);
    expect(first.checksAdded).toEqual([
      "node scripts/agent-checks/ratchet.mjs check",
      "node scripts/agent-checks/import-boundaries.mjs",
      "node scripts/agent-checks/locale-keys.mjs",
    ]);
    const second = installPack("agnostic", { repo, withOptional: ["locale-keys"] });
    expect(second.copied).toEqual([]);
    expect(second.checksAdded).toEqual([]);
    expect(read(repo, ".agents/check-commands").match(/ratchet\.mjs check/g)).toHaveLength(1);
  });

  test("leaves an existing, different file alone and asks for a manual merge", () => {
    const repo = tmp();
    fs.writeFileSync(path.join(repo, "clippy.toml"), "too-many-arguments-threshold = 9\n");
    const report = installPack("rust", { repo });
    expect(report.conflicts).toEqual(["clippy.toml"]);
    expect(read(repo, "clippy.toml")).toBe("too-many-arguments-threshold = 9\n");
    expect(report.manual.some((m) => m.startsWith("clippy.toml already exists"))).toBe(true);
    expect(report.manual.some((m) => m.includes("merge/cargo-lints.toml into Cargo.toml"))).toBe(true);
  });

  test("dry run writes nothing", () => {
    const repo = tmp();
    const report = installPack("python", { repo, dryRun: true });
    expect(report.copied).toContain("ruff.toml");
    expect(fs.readdirSync(repo)).toEqual([]);
  });

  test("rejects unknown packs and optional checks with a clear message", () => {
    expect(() => installPack("cobol", { repo: tmp() })).toThrow('no pack named "cobol"');
    expect(() => installPack("agnostic", { repo: tmp(), withOptional: ["spellcheck"] })).toThrow("no optional check named spellcheck");
  });
});
