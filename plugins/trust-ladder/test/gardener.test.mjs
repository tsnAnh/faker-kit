import { describe, expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { scan } from "../skills/gardener/scripts/scan.mjs";

function repoWithHistory(commits) {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "gardener-"));
  const git = (args, env = {}) => execFileSync("git", ["-C", repo, ...args], { env: { ...process.env, ...env } });
  git(["init", "-q"]);
  git(["config", "user.email", "t@example.com"]);
  git(["config", "user.name", "t"]);
  for (const { daysAgo, files } of commits) {
    for (const [file, content] of Object.entries(files)) {
      fs.mkdirSync(path.dirname(path.join(repo, file)), { recursive: true });
      fs.writeFileSync(path.join(repo, file), content);
    }
    const date = new Date(Date.now() - daysAgo * 86400000).toISOString();
    git(["add", "-A"]);
    git(["commit", "-q", "-m", `commit ${daysAgo}`], { GIT_AUTHOR_DATE: date, GIT_COMMITTER_DATE: date });
  }
  return repo;
}

describe("gardener scan", () => {
  const repo = repoWithHistory([
    { daysAgo: 40, files: { "a/util.ts": "export function isRecord(v: unknown) { return true }\nconst x = 1 as any\n" } },
    { daysAgo: 20, files: { "b/guards.ts": "export function isRecord(v: unknown) { return false }\n// TODO: for now\n" } },
    {
      daysAgo: 2,
      files: {
        "c/check.ts": "export function isRecord(v: unknown) { return !!v }\n// eslint-disable-next-line\nconst y = z as any\n",
        "c/check.test.ts": "function isRecord() {}\n",
        "android/Screen.kt": "fun render(x: String?) = x!!.length\n",
      },
    },
  ]);
  const result = scan(repo, { since: "7 days ago", baseline: "30 days ago" });
  const smell = (id) => result.smells.find((s) => s.id === id);

  test("counts a smell now, at the baseline, and in the window", () => {
    expect(smell("ts-any")).toMatchObject({ now: 2, before: 1, growth: 1, addedInWindow: 1 });
    expect(smell("suppression")).toMatchObject({ now: 1, before: 0, addedInWindow: 1 });
    expect(smell("kotlin-not-null-assertion")).toMatchObject({ now: 1, addedInWindow: 1 });
    expect(smell("workaround-comment")).toMatchObject({ now: 1, addedInWindow: 0 });
  });

  test("ranks smells that spread in the window first", () => {
    expect(result.smells.at(-1).id).toBe("workaround-comment");
  });

  test("finds the third copy of a helper and ignores test files", () => {
    expect(result.duplicates).toEqual([{ name: "isRecord", files: ["a/util.ts", "b/guards.ts", "c/check.ts"], newInWindow: true }]);
  });
});
