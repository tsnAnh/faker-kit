import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/** Point ORCHESTRATE_HOME at a temp dir and record a session page for each agent id, as a cloud run would. */
export function seedAgentViews(agentIds: string[]): string {
  const home =
    process.env.ORCHESTRATE_TEST_HOME ??
    mkdtempSync(join(tmpdir(), "orchestrate-home-"));
  process.env.ORCHESTRATE_TEST_HOME = home;
  process.env.ORCHESTRATE_HOME = home;
  mkdirSync(join(home, "agents"), { recursive: true });
  for (const agentId of agentIds) {
    writeFileSync(
      join(home, "agents", `${agentId}.json`),
      JSON.stringify({
        version: 1,
        agentId,
        name: agentId,
        createdAt: new Date().toISOString(),
        backend: "claude",
        mode: "cloud",
        sessionId: agentId,
        sessionUrl: viewUrlFor(agentId),
        runIds: [],
        spec: {},
      })
    );
  }
  return home;
}

export function viewUrlFor(agentId: string): string {
  return `https://claude.ai/code/${agentId}`;
}
