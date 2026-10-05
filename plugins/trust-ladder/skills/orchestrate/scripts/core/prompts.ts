import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { PlanValidationError } from "../errors.ts";
import { renderModelCatalog } from "../models.ts";
import type { Plan, PlanTask, TaskState } from "../schemas.ts";
import {
  mergeWorkerSlice,
  mergeWorkerSourceBranches,
  mergeWorkerTargetBranch,
} from "./branches.ts";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const templateCache = new Map<string, string>();

/** The skill folder: SKILL.md, references/, prompts/, scripts/. */
export const SKILL_DIR = resolve(SCRIPT_DIR, "../..");

function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./:@-]+$/.test(value)
    ? value
    : `'${value.replace(/'/g, "'\\''")}'`;
}

/** How an agent on this machine runs the CLI: an absolute path, since planners run in other worktrees. */
export function cliCommand(): string {
  return `bun ${shellQuote(resolve(SKILL_DIR, "scripts", "cli.ts"))}`;
}

export interface TaskRuntime {
  backend: "claude" | "codex";
  mode: "cloud" | "local";
  codexEnv?: string;
}

export interface PromptRenderContext {
  plan: Plan;
  branchForTask: (task: PlanTask | TaskState) => string;
  getTask: (name: string) => TaskState | undefined;
  readHandoff: (taskName: string) => string | null;
  runtimeForTask?: (task: PlanTask) => TaskRuntime;
}

function runtimeOf(t: PlanTask, ctx: PromptRenderContext): TaskRuntime {
  return (
    ctx.runtimeForTask?.(t) ?? {
      backend: t.backend ?? ctx.plan.backend ?? "claude",
      mode:
        t.type === "subplanner"
          ? "local"
          : (t.runtime ?? ctx.plan.runtime ?? "cloud"),
      codexEnv: t.codexEnv ?? ctx.plan.codexEnv,
    }
  );
}

function canPush(rt: TaskRuntime): boolean {
  return !(rt.backend === "codex" && rt.mode === "cloud");
}

export function renderPromptTemplate(
  name: string,
  vars: Record<string, string>
): string {
  let template = templateCache.get(name);
  if (template === undefined) {
    template = readFileSync(
      resolve(SCRIPT_DIR, `../../prompts/${name}.md`),
      "utf8"
    );
    templateCache.set(name, template);
  }

  let rendered = template;
  const withDefaults: Record<string, string> = {
    cli: cliCommand(),
    skillPath: SKILL_DIR,
    ...vars,
  };
  for (const [key, value] of Object.entries(withDefaults)) {
    rendered = rendered.replaceAll(`{{${key}}}`, value);
  }

  const unrendered = [...rendered.matchAll(/{{([^{}]+)}}/g)].map(m =>
    m[1].trim()
  );
  if (unrendered.length > 0) {
    const placeholders = [...new Set(unrendered)].join(", ");
    throw new PlanValidationError(
      `template "${name}" has unrendered placeholders: ${placeholders}`
    );
  }

  return rendered;
}

function renderVerifyPlan(t: PlanTask | undefined, heading: string): string {
  if (!t?.verify || t.verify.trim().length === 0) return "";
  return `\n\n${heading}\n\n${t.verify}\n`;
}

export function renderPrompt(args: {
  taskName: string;
  ctx: PromptRenderContext;
}): string {
  const def = planTasks(args.ctx.plan).find(t => t.name === args.taskName);
  if (!def) throw new Error(`unknown task: ${args.taskName}`);
  // CLI preview: no real agentId yet, so the slack-block leaves
  // `--agent-id` unset; the rendered prompt still parses.
  switch (def.type) {
    case "worker":
      return buildWorkerPrompt(def, undefined, args.ctx);
    case "subplanner":
      return buildSubplannerPrompt(def, undefined, args.ctx);
    case "verifier":
      return buildVerifierPrompt(def, undefined, args.ctx);
    default: {
      const _exhaustive: never = def;
      return _exhaustive;
    }
  }
}

/**
 * Paste each dep's handoff into the downstream prompt so `dependsOn` carries
 * payload, not just scheduling. Workers live on sibling branches and can't
 * read each other's work directly.
 */
function buildUpstreamHandoffsSection(
  t: PlanTask,
  ctx: PromptRenderContext
): string {
  const deps = normalizedDependsOn(t);
  if (deps.length === 0) return "";
  const chunks: string[] = [];
  for (const dep of deps) {
    const body = ctx.readHandoff(dep);
    if (!body) {
      chunks.push(
        `### Upstream: \`${dep}\`\n\n_(no handoff on disk — planner spawned this task without waiting; treat as missing context)_`
      );
      continue;
    }
    const stripped = body.replace(/^<!--[\s\S]*?-->\s*/, "").trim();
    chunks.push(`### Upstream: \`${dep}\`\n\n${stripped}`);
  }
  return `\n\nUpstream handoffs (context from tasks you depend on — you cannot see their branches directly, this is the only way you get their work):\n\n${chunks.join("\n\n---\n\n")}\n\n`;
}

export function buildWorkerPrompt(
  t: PlanTask,
  agentId: string | undefined,
  ctx: PromptRenderContext
): string {
  const allow =
    (t.pathsAllowed ?? []).map(p => `  - ${p}`).join("\n") ||
    "  - (none declared — ask before touching anything broad)";
  const forbid =
    (t.pathsForbidden ?? []).map(p => `  - ${p}`).join("\n") || "  - (none)";
  const accept =
    (t.acceptance ?? []).map(a => `  - [ ] ${a}`).join("\n") ||
    "  - (none declared)";
  const verifyPlan = renderVerifyPlan(
    t,
    "Verification plan (how the planner / a verifier will check your work):"
  );
  const upstream = buildUpstreamHandoffsSection(t, ctx);
  const slackBlock = buildSlackBlock(t, agentId, ctx);
  const branch = ctx.branchForTask(t);
  const mergeDiscipline = renderMergeDiscipline(t, ctx.plan);
  const scopedGoal = buildPromptScopedGoal(t);
  const prBase = ctx.plan.prBase ?? ctx.plan.baseBranch;
  const rt = runtimeOf(t, ctx);
  const pushDiscipline = canPush(rt)
    ? `Push exactly \`${branch}\` and report it in your handoff. The branch belongs to this task: if an earlier attempt left it, overwrite it with \`git push --force\`.`
    : `You cannot push. Your diff lands on \`${branch}\`, which the orchestrator creates from it; report that branch in your handoff.`;
  const prDiscipline = t.openPR
    ? canPush(rt)
      ? `- Keep \`.orchestrate/\` files out of the PR diff — that directory is run-local orchestrator bookkeeping, not the change you're shipping.
- After your branch is pushed, open a draft pull request with base \`${prBase}\` and head \`${branch}\`, with \`gh pr create --draft\` or your GitHub tools. Title and body should summarize your handoff. Do NOT merge, rebase, or close the PR — the planner owns integration.`
      : "- Do NOT open a PR yourself. The orchestrator opens a draft PR from your branch. Do not merge anything — the planner owns integration."
    : "- Do NOT merge, rebase, or open a PR. The planner owns integration.";
  return renderPromptTemplate("worker", {
    goal: ctx.plan.goal,
    scopedGoal,
    allow,
    forbid,
    accept,
    verifyPlan,
    upstream,
    slackBlock,
    startingRef: t.startingRef ?? ctx.plan.baseBranch,
    branch,
    pushDiscipline,
    mergeDiscipline,
    prDiscipline,
  });
}

export function buildVerifierPrompt(
  t: Extract<PlanTask, { type: "verifier" }>,
  agentId: string | undefined,
  ctx: PromptRenderContext
): string {
  const target = planTasks(ctx.plan).find(x => x.name === t.verifies);
  const targetName = t.verifies;
  const targetType = target?.type ?? "(unknown)";
  const targetBranch = target
    ? ctx.branchForTask(target)
    : `(unknown branch for ${targetName})`;
  const targetGoal =
    target?.scopedGoal ??
    "(target task missing from plan — validation should have rejected this)";
  const targetAccept =
    (target?.acceptance ?? []).map(a => `  - ${a}`).join("\n") ||
    "  - (none declared)";
  const accept =
    (t.acceptance ?? []).map(a => `  - [ ] ${a}`).join("\n") ||
    "  - (none declared)";
  const targetVerifyPlan = renderVerifyPlan(
    target,
    "Verification plan authored for the target (follow this as your recipe):"
  );
  const ownVerifyPlan = renderVerifyPlan(
    t,
    "Your own verification plan (planner-authored for this verifier task):"
  );
  const upstream = buildUpstreamHandoffsSection(t, ctx);
  const slackBlock = buildSlackBlock(t, agentId, ctx);
  const branch = ctx.branchForTask(t);
  const scopedGoal = buildPromptScopedGoal(t);
  const artifactDiscipline = canPush(runtimeOf(t, ctx))
    ? `Commit verifier artifacts (repro scripts, audit notes, log captures if useful) to \`${branch}\`, your own branch, and push it. Never push to the target branch \`${targetBranch}\`.`
    : `Leave verifier artifacts (repro scripts, audit notes, log captures if useful) as changes in the repository; the orchestrator puts them on \`${branch}\`, never on the target branch.`;
  return renderPromptTemplate("verifier", {
    artifactDiscipline,
    goal: ctx.plan.goal,
    scopedGoal,
    targetName,
    targetType,
    targetBranch,
    targetGoal,
    targetAccept,
    targetVerifyPlan,
    accept,
    ownVerifyPlan,
    upstream,
    slackBlock,
    startingRef: t.startingRef ?? targetBranch,
    branch,
  });
}

export function buildSubplannerPrompt(
  t: PlanTask,
  agentId: string | undefined,
  ctx: PromptRenderContext
): string {
  const allow =
    (t.pathsAllowed ?? []).map(p => `  - ${p}`).join("\n") ||
    "  - (none declared)";
  const forbid =
    (t.pathsForbidden ?? []).map(p => `  - ${p}`).join("\n") || "  - (none)";
  const accept =
    (t.acceptance ?? []).map(a => `  - [ ] ${a}`).join("\n") ||
    "  - (none declared)";
  const verifyPlan = renderVerifyPlan(
    t,
    "Verification plan (how the planner / a verifier will check your work):"
  );
  const upstream = buildUpstreamHandoffsSection(t, ctx);
  const slackBlock = buildSlackBlock(t, agentId, ctx);
  const scopedGoal = buildPromptScopedGoal(t);
  return renderPromptTemplate("subplanner", {
    name: t.name,
    branch: ctx.branchForTask(t),
    repoUrl: ctx.plan.repoUrl,
    goal: ctx.plan.goal,
    scopedGoal,
    allow,
    forbid,
    accept,
    verifyPlan,
    upstream,
    slackBlock,
    modelCatalog: renderModelCatalog(),
    andonStateRef: ctx.plan.andonStateRef ?? "",
    andonStatePath: ctx.plan.andonStatePath ?? "",
    slackKickoffRefJson: ctx.plan.slackKickoffRef
      ? JSON.stringify(ctx.plan.slackKickoffRef)
      : "",
    selfAgentId: agentId ?? "",
    parentAgentId: ctx.plan.selfAgentId ?? "",
    runtimeSettings: renderRuntimeSettings(ctx.plan),
    loopHygiene: renderPromptTemplate("loop-hygiene", { rootFlag: "" }),
  });
}

/** The plan's agent settings, for a child planner to copy into its own plan.json. */
export function renderRuntimeSettings(
  plan: Pick<
    Plan,
    "backend" | "runtime" | "codexEnv" | "localSandbox" | "maxDurationMinutes"
  >
): string {
  const fields: string[] = [
    `\`backend: ${JSON.stringify(plan.backend ?? "claude")}\``,
    `\`runtime: ${JSON.stringify(plan.runtime ?? "cloud")}\``,
    `\`localSandbox: ${JSON.stringify(plan.localSandbox ?? "full")}\``,
  ];
  if (plan.codexEnv)
    fields.push(`\`codexEnv: ${JSON.stringify(plan.codexEnv)}\``);
  if (plan.maxDurationMinutes)
    fields.push(`\`maxDurationMinutes: ${plan.maxDurationMinutes}\``);
  return fields.join(", ");
}

function renderMergeDiscipline(t: PlanTask, plan: Plan): string {
  const slice = mergeWorkerSlice(t.name);
  if (t.type !== "worker" || !slice) return "";
  const target = mergeWorkerTargetBranch(plan, t);
  const sources = mergeWorkerSourceBranches(plan, t);
  const sourceList =
    sources.length > 0
      ? sources.map(branch => `  - ${branch}`).join("\n")
      : "  - (none)";
  return `
- This is a merge worker for slice \`${slice}\`.
- Merge dependency branches into \`${target}\` one at a time, in \`dependsOn\` order.
- Source branches:
${sourceList}
- Push only \`${target}\` after all selected merges land.`;
}

function buildSlackBlock(
  t: PlanTask,
  agentId: string | undefined,
  ctx: PromptRenderContext
): string {
  const ref = ctx.plan.slackKickoffRef;
  // `agentIdFlag` is rendered into CLI invocations directly. Omit the flag
  // entirely when no agentId is known so a worker doesn't copy a malformed
  // `--agent-id  --workspace` into shell.
  const agentIdFlag = agentId ? ` --agent-id ${agentId}` : "";
  const slackBlock = ref
    ? renderPromptTemplate("slack-block", {
        channel: ref.channel,
        threadTs: ref.ts,
        destination: `slack:${ref.channel}:${ref.ts}`,
        taskName: t.name,
        agentIdFlag,
      })
    : "";
  const andonBlock = ref
    ? renderPromptTemplate("andon-block", { agentIdFlag })
    : "";
  return `${slackBlock}${andonBlock}`;
}

function buildPromptScopedGoal(t: PlanTask): string {
  const inlineBrief = buildInlineBriefBlock(t);
  if (!inlineBrief) return t.scopedGoal;
  return `${inlineBrief}\n\n${t.scopedGoal}`;
}

function buildInlineBriefBlock(t: PlanTask): string {
  if (!t.brief) return "";
  const brief = t.brief.trim();
  if (brief.length === 0) return "";
  return `Task brief:\n\n${brief}`;
}

function planTasks(plan: Plan): PlanTask[] {
  return plan.tasks ?? [];
}

function normalizedDependsOn(t: PlanTask): string[] {
  const deps = [...(t.dependsOn ?? [])];
  switch (t.type) {
    case "worker":
    case "subplanner":
      return deps;
    case "verifier":
      if (!deps.includes(t.verifies)) {
        deps.unshift(t.verifies);
      }
      return deps;
    default: {
      const _exhaustive: never = t;
      return _exhaustive;
    }
  }
}
