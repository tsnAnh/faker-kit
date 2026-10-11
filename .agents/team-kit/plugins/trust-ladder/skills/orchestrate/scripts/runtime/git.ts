import { createHash } from "node:crypto";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { onPath, run, runStatus } from "./process.ts";
import { orchestrateHome } from "./registry.ts";

const SHA_RE = /^[0-9a-f]{40}$/;

function git(args: string[], cwd?: string, timeoutMs?: number): string {
  return run(["git", ...args], { cwd, timeoutMs }).trim();
}

export function repoTopLevel(cwd: string = process.cwd()): string {
  return git(["rev-parse", "--show-toplevel"], cwd);
}

export function repoKey(repoUrl: string): string {
  return createHash("sha256").update(repoUrl).digest("hex").slice(0, 16);
}

/** Fetch `ref` from `repoUrl` into a private ref of `repo`, so concurrent fetches never share FETCH_HEAD. */
export function fetchInto(
  repo: string,
  repoUrl: string,
  ref: string,
  target: string,
  depth?: number
): boolean {
  const source = SHA_RE.test(ref) ? ref : `refs/heads/${ref}`;
  const args = ["fetch", "--quiet", "--no-tags"];
  if (depth) args.push(`--depth=${depth}`);
  args.push(repoUrl, `+${source}:${target}`);
  return (
    runStatus(["git", ...args], { cwd: repo, timeoutMs: 300_000 }).code === 0
  );
}

function resolveLocal(repo: string, ref: string): string | null {
  const out = runStatus(
    ["git", "rev-parse", "--verify", "--quiet", `${ref}^{commit}`],
    { cwd: repo }
  );
  return out.code === 0 ? out.stdout.trim() : null;
}

/** Remove any worktree that has `branch` checked out, so a respawn can take the branch again. */
function releaseBranch(repo: string, branch: string): void {
  const listing = runStatus(["git", "worktree", "list", "--porcelain"], {
    cwd: repo,
  }).stdout;
  let path: string | null = null;
  for (const line of listing.split("\n")) {
    if (line.startsWith("worktree ")) path = line.slice("worktree ".length);
    if (line === `branch refs/heads/${branch}` && path) {
      runStatus(["git", "worktree", "remove", "--force", path], { cwd: repo });
    }
  }
  runStatus(["git", "worktree", "prune"], { cwd: repo });
}

/**
 * A fresh worktree of `repo` at `startingRef` (from `repoUrl`, falling back to a local ref of
 * that name) with `branch` checked out. The agent commits there and pushes `branch`.
 */
export function prepareWorktree(args: {
  repo: string;
  repoUrl: string;
  startingRef: string;
  branch: string;
  path: string;
  runId: string;
}): string {
  const startRef = `refs/orchestrate/start/${args.runId}`;
  let base: string | null = null;
  if (fetchInto(args.repo, args.repoUrl, args.startingRef, startRef)) {
    base = resolveLocal(args.repo, startRef);
  }
  base ??=
    resolveLocal(args.repo, args.startingRef) ??
    resolveLocal(args.repo, `origin/${args.startingRef}`);
  if (!base) {
    throw new Error(
      `startingRef ${args.startingRef} is neither on ${args.repoUrl} nor in ${args.repo}`
    );
  }
  releaseBranch(args.repo, args.branch);
  if (existsSync(args.path))
    rmSync(args.path, { recursive: true, force: true });
  mkdirSync(join(args.path, ".."), { recursive: true });
  git(
    ["worktree", "add", "--quiet", "-B", args.branch, args.path, base],
    args.repo,
    300_000
  );
  runStatus(["git", "update-ref", "-d", startRef], { cwd: args.repo });
  return base;
}

/** A shallow clone at `startingRef`, the directory `claude --cloud` starts a session from. */
export function cloneAt(
  repoUrl: string,
  startingRef: string,
  path: string
): void {
  if (existsSync(path)) rmSync(path, { recursive: true, force: true });
  mkdirSync(join(path, ".."), { recursive: true });
  if (SHA_RE.test(startingRef)) {
    throw new Error(
      `a cloud session starts from a branch on the remote, not commit ${startingRef}; give startingRef as a branch`
    );
  }
  git(
    [
      "clone",
      "--quiet",
      "--depth=1",
      "--single-branch",
      "--branch",
      startingRef,
      repoUrl,
      path,
    ],
    undefined,
    600_000
  );
}

/** A bare cache repository per remote, used to read pushed branches without touching a worktree. */
export function cacheRepo(repoUrl: string): string {
  const path = join(orchestrateHome(), "cache", `${repoKey(repoUrl)}.git`);
  if (!existsSync(join(path, "HEAD"))) {
    mkdirSync(path, { recursive: true });
    git(["init", "--quiet", "--bare", path]);
  }
  return path;
}

export function remoteTip(repoUrl: string, branch: string): string | null {
  const out = runStatus(
    ["git", "ls-remote", "--heads", repoUrl, `refs/heads/${branch}`],
    { timeoutMs: 120_000 }
  );
  if (out.code !== 0) return null;
  const line = out.stdout.split("\n").find(l => l.trim().length > 0);
  return line ? line.split(/\s+/)[0] : null;
}

/** Commit messages on `branch`, newest first, from the cache repository. */
export function recentMessages(
  repoUrl: string,
  branch: string,
  depth = 30
): Array<{ sha: string; body: string }> {
  const cache = cacheRepo(repoUrl);
  const target = `refs/orchestrate/tip/${createHash("sha256").update(branch).digest("hex").slice(0, 16)}`;
  if (!fetchInto(cache, repoUrl, branch, target, depth)) return [];
  const out = runStatus(
    ["git", "log", `-${depth}`, "--format=%H%x00%B%x01", target],
    { cwd: cache }
  );
  if (out.code !== 0) return [];
  return out.stdout
    .split("\x01")
    .map(chunk => chunk.replace(/^\n/, ""))
    .filter(chunk => chunk.includes("\x00"))
    .map(chunk => {
      const [sha, body] = chunk.split("\x00");
      return { sha: sha.trim(), body: body ?? "" };
    });
}

export function hasUnpushedCommits(
  worktree: string,
  branch: string,
  repoUrl: string
): boolean {
  const local = resolveLocal(worktree, "HEAD");
  if (!local) return false;
  const remote = remoteTip(repoUrl, branch);
  return remote !== local;
}

export function pushBranch(
  worktree: string,
  repoUrl: string,
  branch: string
): { ok: boolean; detail: string } {
  // A URL has no remote-tracking ref, so name the expected remote tip; empty means "must not exist".
  const expected = remoteTip(repoUrl, branch) ?? "";
  const out = runStatus(
    [
      "git",
      "push",
      "--quiet",
      `--force-with-lease=refs/heads/${branch}:${expected}`,
      repoUrl,
      `HEAD:refs/heads/${branch}`,
    ],
    { cwd: worktree, timeoutMs: 300_000 }
  );
  return { ok: out.code === 0, detail: (out.stderr || out.stdout).trim() };
}

/** Open a draft pull request when the task asked for one and the handoff does not link one yet. */
export function ensureDraftPr(
  rec: {
    openPR: boolean;
    repoUrl: string;
    branch: string;
    prBase: string | null;
    startingRef: string;
    name: string;
  },
  handoff: string
): void {
  if (!rec.openPR) return;
  if (/https:\/\/github\.com\/[^\s/]+\/[^\s/]+\/pull\/\d+/.test(handoff))
    return;
  if (!onPath("gh")) return;
  const title = (
    handoff.match(/^##\s+What I did\s*\n-\s*(.+)$/m)?.[1] ?? rec.name
  ).slice(0, 120);
  runStatus(
    [
      "gh",
      "pr",
      "create",
      "--draft",
      "--repo",
      rec.repoUrl,
      "--head",
      rec.branch,
      "--base",
      rec.prBase ?? rec.startingRef,
      "--title",
      title,
      "--body-file",
      "-",
    ],
    { input: handoff, timeoutMs: 120_000 }
  );
}

export function commitsAhead(worktree: string, base: string): number {
  const out = runStatus(["git", "rev-list", "--count", `${base}..HEAD`], {
    cwd: worktree,
  });
  return out.code === 0 ? Number.parseInt(out.stdout.trim(), 10) || 0 : 0;
}

/** Apply a unified diff in `worktree` and commit it. Returns false when the patch does not apply. */
export function applyPatch(
  worktree: string,
  patch: string
): { ok: boolean; detail: string } {
  if (patch.trim().length === 0) return { ok: true, detail: "empty diff" };
  const applied = runStatus(
    ["git", "apply", "--index", "--whitespace=nowarn", "-"],
    {
      cwd: worktree,
      input: patch.endsWith("\n") ? patch : `${patch}\n`,
    }
  );
  return { ok: applied.code === 0, detail: applied.stderr.trim() };
}

export function commitAll(
  worktree: string,
  message: string
): { ok: boolean; detail: string } {
  runStatus(["git", "add", "-A"], { cwd: worktree });
  const staged = runStatus(["git", "diff", "--cached", "--quiet"], {
    cwd: worktree,
  });
  if (staged.code === 0) return { ok: true, detail: "nothing to commit" };
  const hasIdentity =
    runStatus(["git", "config", "user.email"], { cwd: worktree }).stdout.trim()
      .length > 0;
  const identity = hasIdentity
    ? []
    : ["-c", "user.name=orchestrate", "-c", "user.email=orchestrate@localhost"];
  const out = runStatus(["git", ...identity, "commit", "--quiet", "-F", "-"], {
    cwd: worktree,
    input: message,
  });
  return { ok: out.code === 0, detail: (out.stderr || out.stdout).trim() };
}
