import { execFileSync, spawn } from "node:child_process";
import { closeSync, existsSync, openSync, readFileSync } from "node:fs";

// Local agents run as detached processes, so they outlive the `run` loop that started them and a
// later loop can reattach. A tiny `sh` wrapper records the exit code in a file, because the
// process that reads it is usually not the parent. No shell parses the agent's arguments: they
// reach `sh` as positional parameters and run as "$@".

export interface DetachedProcess {
  pid: number;
}

export function spawnDetached(args: {
  argv: string[];
  cwd: string;
  stdinFile?: string;
  stdoutFile: string;
  stderrFile: string;
  exitFile: string;
  env?: Record<string, string | undefined>;
}): DetachedProcess {
  if (args.argv.length === 0) throw new Error("spawnDetached: empty argv");
  const stdin = args.stdinFile ? openSync(args.stdinFile, "r") : "ignore";
  const stdout = openSync(args.stdoutFile, "a");
  const stderr = openSync(args.stderrFile, "a");
  try {
    const child = spawn(
      "/bin/sh",
      ["-c", '"$@"; echo $? > "$0"', args.exitFile, ...args.argv],
      {
        cwd: args.cwd,
        detached: true,
        stdio: [stdin, stdout, stderr],
        env: { ...process.env, ...args.env },
      }
    );
    if (child.pid === undefined) {
      throw new Error(`could not start ${args.argv[0]}`);
    }
    child.unref();
    return { pid: child.pid };
  } finally {
    if (typeof stdin === "number") closeSync(stdin);
    closeSync(stdout);
    closeSync(stderr);
  }
}

export function readExitCode(exitFile: string): number | null {
  if (!existsSync(exitFile)) return null;
  const raw = readFileSync(exitFile, "utf8").trim();
  if (!raw) return null;
  const code = Number.parseInt(raw, 10);
  return Number.isFinite(code) ? code : null;
}

export function isAlive(pid: number | null): boolean {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

/** When a process started, as `ps` prints it; with the pid, it tells a process apart from a later one that reused the pid. */
export function processStartTime(pid: number | null): string | null {
  if (!pid) return null;
  try {
    const out = execFileSync("ps", ["-o", "lstart=", "-p", String(pid)], {
      stdio: "pipe",
      env: { ...process.env },
    })
      .toString()
      .trim();
    return out || null;
  } catch {
    return null;
  }
}

/** Alive, and the same process that was started: a pid reused after a reboot does not count. */
export function isSameProcess(
  pid: number | null,
  startedAs: string | undefined
): boolean {
  if (!isAlive(pid)) return false;
  if (!startedAs) return true;
  return processStartTime(pid) === startedAs;
}

export function killGroup(
  pid: number | null,
  signal: NodeJS.Signals = "SIGTERM"
): void {
  if (!pid) return;
  try {
    process.kill(-pid, signal);
  } catch {
    try {
      process.kill(pid, signal);
    } catch {
      // Already gone.
    }
  }
}

export function onPath(binary: string): boolean {
  try {
    execFileSync("sh", ["-c", 'command -v "$0"', binary], {
      stdio: "pipe",
      env: { ...process.env },
    });
    return true;
  } catch {
    return false;
  }
}

export function run(
  argv: string[],
  opts: {
    cwd?: string;
    input?: string;
    timeoutMs?: number;
    env?: Record<string, string | undefined>;
  } = {}
): string {
  const [cmd, ...rest] = argv;
  return execFileSync(cmd, rest, {
    cwd: opts.cwd,
    input: opts.input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
    timeout: opts.timeoutMs ?? 120_000,
    maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, ...opts.env },
  });
}

export interface RunOutcome {
  code: number;
  stdout: string;
  stderr: string;
}

/** Like `run`, but returns the exit code instead of throwing on a non-zero one. */
export function runStatus(
  argv: string[],
  opts: { cwd?: string; input?: string; timeoutMs?: number } = {}
): RunOutcome {
  try {
    return { code: 0, stdout: run(argv, opts), stderr: "" };
  } catch (err) {
    const e = err as {
      status?: number | null;
      stdout?: string | Buffer;
      stderr?: string | Buffer;
      message?: string;
    };
    return {
      code: typeof e.status === "number" ? e.status : 1,
      stdout: String(e.stdout ?? ""),
      stderr: String(e.stderr ?? e.message ?? ""),
    };
  }
}
