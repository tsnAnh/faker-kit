import { describe, expect, test } from "bun:test";

import type { ModelProfile } from "../models.ts";
import {
  printProbeResults,
  probeArgv,
  probeModelCatalog,
} from "../tools/probe-models.ts";

describe("models --check", () => {
  test("probes each model through its CLI and reports failures", async () => {
    const catalog = [
      profile("good", "claude", { id: "sonnet" }),
      profile("bad", "codex", { id: "gpt-gone" }),
    ];
    const seen: string[][] = [];
    const results = await probeModelCatalog({
      catalog,
      installed: () => true,
      runner: argv => {
        seen.push(argv);
        return argv.includes("gpt-gone")
          ? { code: 1, stdout: "", stderr: "model gpt-gone does not exist" }
          : { code: 0, stdout: "OK", stderr: "" };
      },
    });
    expect(results).toEqual([
      { profile: catalog[0], ok: true },
      {
        profile: catalog[1],
        ok: false,
        error: "model gpt-gone does not exist",
      },
    ]);
    expect(seen[0].slice(0, 4)).toEqual(["claude", "-p", "--model", "sonnet"]);
    expect(seen[1]).toContain("gpt-gone");
  });

  test("skips a backend whose CLI is not installed", async () => {
    const catalog = [profile("x", "codex", { id: "gpt-5.5" })];
    const results = await probeModelCatalog({
      catalog,
      installed: () => false,
      runner: () => ({ code: 0, stdout: "", stderr: "" }),
    });
    expect(results).toEqual([
      {
        profile: catalog[0],
        ok: false,
        skipped: true,
        error: "codex is not installed",
      },
    ]);
  });

  test("passes effort to each CLI", () => {
    expect(
      probeArgv(
        profile("o", "claude", {
          id: "opus",
          params: [{ id: "effort", value: "max" }],
        })
      )
    ).toContain("--effort");
    expect(
      probeArgv(
        profile("c", "codex", {
          id: "gpt-5.5",
          params: [{ id: "effort", value: "high" }],
        })
      )
    ).toContain('model_reasoning_effort="high"');
  });

  test("printProbeResults counts failures but not skips", () => {
    const priorLog = console.log;
    const priorError = console.error;
    const lines: string[] = [];
    console.log = (line?: unknown) => lines.push(String(line ?? ""));
    console.error = (line?: unknown) => lines.push(String(line ?? ""));
    try {
      const p = profile("bad", "claude", { id: "bad" });
      expect(
        printProbeResults([
          { profile: p, ok: false, error: "nope" },
          { profile: p, ok: false, skipped: true, error: "not installed" },
        ])
      ).toBe(1);
      expect(lines.join("\n")).toContain("[SKIP]");
    } finally {
      console.log = priorLog;
      console.error = priorError;
    }
  });
});

function profile(
  slug: string,
  backend: "claude" | "codex",
  selection: ModelProfile["selection"]
): ModelProfile {
  return {
    slug,
    backend,
    selection,
    summary: slug,
    strengths: [],
    speed: "fast",
    use: slug,
  };
}
