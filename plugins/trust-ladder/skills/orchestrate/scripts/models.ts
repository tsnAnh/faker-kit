import type { Backend, ModelSelection } from "#agent-runtime";

import type { TaskType } from "./adapters/types.ts";

// Model catalog. Source of truth for `tasks[].model` choices; `defaultFor` entries supply the
// fallback when `tasks[].model` is omitted, per backend. Claude Code takes the aliases `opus`,
// `sonnet`, and `haiku` for the newest model of each tier; Codex takes the model id. Codex cloud
// tasks run the environment's model and ignore this choice.

export interface ModelProfile {
  /** User-facing slug for `tasks[].model` and `--model` flags. */
  slug: string;
  backend: Backend;
  /** Passed to the CLI: `--model` and `--effort` (Claude Code), `-m` and `model_reasoning_effort` (Codex). */
  selection: ModelSelection;
  summary: string;
  strengths: string[];
  speed: "fast" | "medium" | "slow";
  use: string;
  /** Task types this profile is the default for, on its backend. */
  defaultFor?: TaskType[];
}

export const MODEL_CATALOG: ModelProfile[] = [
  {
    slug: "opus",
    backend: "claude",
    selection: { id: "opus" },
    summary:
      "Claude's strongest tier; solid judgment for planning and acceptance checks.",
    strengths: [
      "judgment",
      "acceptance criteria",
      "decomposition",
      "ambiguity resolution",
      "frontend",
    ],
    speed: "slow",
    use: "Default for Claude subplanners and verifiers. Also right for workers owning judgment-heavy slices.",
    defaultFor: ["subplanner", "verifier"],
  },
  {
    slug: "opus-max",
    backend: "claude",
    selection: { id: "opus", params: [{ id: "effort", value: "max" }] },
    summary:
      "Claude's strongest tier at maximum effort; for exceptionally hard judgment.",
    strengths: ["complex judgment", "deep reasoning"],
    speed: "slow",
    use: "Reserved for tasks where `opus` produced unsatisfying results. May overthink simple work.",
  },
  {
    slug: "sonnet",
    backend: "claude",
    selection: { id: "sonnet" },
    summary: "Fast, strong Claude tier for bounded implementation work.",
    strengths: ["implementation", "throughput", "tool use", "refactoring"],
    speed: "medium",
    use: "Default for Claude workers with clear acceptance criteria.",
    defaultFor: ["worker"],
  },
  {
    slug: "haiku",
    backend: "claude",
    selection: { id: "haiku" },
    summary: "The fastest Claude tier, for mechanical edits.",
    strengths: ["throughput", "straight-line code"],
    speed: "fast",
    use: "Workers doing mechanical, well-specified edits where speed matters more than judgment.",
  },
  {
    slug: "gpt-5.5-high",
    backend: "codex",
    selection: { id: "gpt-5.5", params: [{ id: "effort", value: "high" }] },
    summary:
      "GPT-5.5 at high reasoning effort: systems design, architecture, subtle correctness.",
    strengths: [
      "systems design",
      "architecture",
      "algorithms",
      "subtle correctness",
    ],
    speed: "slow",
    use: "Default for Codex subplanners and verifiers, and workers where correctness matters more than turnaround.",
    defaultFor: ["subplanner", "verifier"],
  },
  {
    slug: "gpt-5.5",
    backend: "codex",
    selection: { id: "gpt-5.5" },
    summary: "GPT-5.5 at its default effort, for implementation work.",
    strengths: ["implementation", "refactoring", "tool calls"],
    speed: "medium",
    use: "Default for Codex workers.",
    defaultFor: ["worker"],
  },
  {
    slug: "gpt-5.5-xhigh",
    backend: "codex",
    selection: { id: "gpt-5.5", params: [{ id: "effort", value: "xhigh" }] },
    summary:
      "GPT-5.5 at extra-high reasoning effort, for exceptionally hard systems work.",
    strengths: ["complex algorithms", "deep architectural reasoning"],
    speed: "slow",
    use: "Reserved for tasks where `gpt-5.5-high` produced unsatisfying results.",
  },
];

export function defaultModelForType(
  type: TaskType,
  backend: Backend = "claude"
): string {
  const match = MODEL_CATALOG.find(
    m => m.backend === backend && m.defaultFor?.includes(type)
  );
  if (!match)
    throw new Error(
      `MODEL_CATALOG missing default for ${backend} TaskType "${type}"`
    );
  return match.slug;
}

export function isKnownModel(slug: string): boolean {
  return MODEL_CATALOG.some(m => m.slug === slug);
}

/** Unknown slugs pass through as a bare `{ id }` so planners can name any model the CLI accepts. */
export function resolveModelSelection(slug: string): ModelSelection {
  const profile = MODEL_CATALOG.find(m => m.slug === slug);
  return profile ? profile.selection : { id: slug };
}

export function renderModelCatalog(): string {
  const lines: string[] = [];
  for (const m of MODEL_CATALOG) {
    const defaults = m.defaultFor?.length
      ? ` (default ${m.backend} ${m.defaultFor.join(", ")})`
      : "";
    lines.push(`- \`${m.slug}\` [${m.backend}] — ${m.summary}${defaults}`);
    lines.push(`  speed: ${m.speed}; strengths: ${m.strengths.join(", ")}`);
    lines.push(`  use: ${m.use}`);
  }
  return lines.join("\n");
}
