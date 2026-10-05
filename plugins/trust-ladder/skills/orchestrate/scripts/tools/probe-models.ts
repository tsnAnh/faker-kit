#!/usr/bin/env bun
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MODEL_CATALOG, type ModelProfile } from "../models.ts";
import { onPath, runStatus } from "../runtime/process.ts";

// `models --check`: ask each catalog model for one short reply through the CLI that would run it,
// so a renamed or retired model fails here instead of at spawn time. Codex cloud tasks pick their
// own model, so only the local CLIs are probed. A backend whose CLI is not installed is skipped.

export type ProbeResult =
  | { profile: ModelProfile; ok: true }
  | { profile: ModelProfile; ok: false; skipped?: boolean; error: string };

export type ProbeRunner = (
  argv: string[],
  cwd: string
) => { code: number; stdout: string; stderr: string };

export function probeArgv(profile: ModelProfile): string[] {
  const effort = profile.selection.params?.find(p => p.id === "effort")?.value;
  if (profile.backend === "claude") {
    const argv = [
      "claude",
      "-p",
      "--model",
      profile.selection.id,
      "--max-turns",
      "1",
    ];
    if (effort) argv.push("--effort", effort);
    return [...argv, "Reply with the single word OK."];
  }
  const argv = [
    "codex",
    "exec",
    "--skip-git-repo-check",
    "--ephemeral",
    "-s",
    "read-only",
    "-m",
    profile.selection.id,
  ];
  if (effort) argv.push("-c", `model_reasoning_effort="${effort}"`);
  return [...argv, "Reply with the single word OK."];
}

export async function probeModelCatalog(
  opts: {
    catalog?: ModelProfile[];
    runner?: ProbeRunner;
    installed?: (binary: string) => boolean;
  } = {}
): Promise<ProbeResult[]> {
  const catalog = opts.catalog ?? MODEL_CATALOG;
  const runner: ProbeRunner =
    opts.runner ??
    ((argv, cwd) => runStatus(argv, { cwd, timeoutMs: 180_000 }));
  const installed = opts.installed ?? onPath;
  const cwd = mkdtempSync(join(tmpdir(), "orchestrate-probe-"));
  const results: ProbeResult[] = [];
  try {
    for (const profile of catalog) {
      const argv = probeArgv(profile);
      if (!installed(argv[0])) {
        results.push({
          profile,
          ok: false,
          skipped: true,
          error: `${argv[0]} is not installed`,
        });
        continue;
      }
      const out = runner(argv, cwd);
      if (out.code === 0) results.push({ profile, ok: true });
      else
        results.push({
          profile,
          ok: false,
          error:
            (out.stderr || out.stdout).trim().slice(0, 200) ||
            `exit ${out.code}`,
        });
    }
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
  return results;
}

export function printProbeResults(results: ProbeResult[]): number {
  let failures = 0;
  for (const r of results) {
    const label = `${r.profile.slug} [${r.profile.backend}] -> ${JSON.stringify(r.profile.selection)}`;
    if (r.ok) {
      console.log(`[OK]   ${label}`);
    } else if (r.skipped) {
      console.log(`[SKIP] ${label}  ${r.error}`);
    } else {
      failures += 1;
      console.log(`[FAIL] ${label}  ${r.error}`);
    }
  }
  if (failures > 0) {
    console.error(`\n${failures} catalog entries failed validation.`);
    console.error(
      "Check the model names the CLI accepts and update MODEL_CATALOG.selection."
    );
  } else {
    console.log("\nAll installed catalog entries validate.");
  }
  return failures;
}

if (import.meta.main) {
  const results = await probeModelCatalog();
  process.exit(printProbeResults(results) > 0 ? 1 : 0);
}
