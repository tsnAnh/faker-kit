import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { compatBlock, projectHasCompat } from "../lib/compat.mjs";
import { nextActiveModes, reminderText } from "../lib/modes.mjs";

const COMPAT_SECTION = "team-kit-compat";
const MODE_SECTION = "team-kit-mode";

export default function teamKit(pi: ExtensionAPI) {
  let compat: string | null = null;
  let active = new Set<string>();
  let enteredThisTurn = false;

  pi.on("session_start", async (_event, ctx) => {
    compat = projectHasCompat(ctx.cwd) ? null : compatBlock();
    active = new Set();
  });

  pi.on("input", async (event) => {
    const next = nextActiveModes(event.text, active);
    enteredThisTurn = [...next].some((skill) => !active.has(skill));
    active = next;
    return { action: "continue" };
  });

  pi.on("before_agent_start", async (event) => {
    const sections = event.systemPromptOptions.sections;
    if (compat) sections[COMPAT_SECTION] = compat;
    const reminder = enteredThisTurn ? null : reminderText(active);
    if (reminder) sections[MODE_SECTION] = reminder;
    else delete sections[MODE_SECTION];
  });
}
