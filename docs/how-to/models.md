# Choose models

Each person on the team uses one coding agent, Claude Code or Codex, so the kit is set up to run everything on that one agent.

| Agent | Model | How to set it |
|---|---|---|
| Claude Code | Claude Opus 5.5, effort max | `/model opus`, then `/effort max`. For one run: `claude --model opus --effort max`. |
| Codex | GPT-6 Sol, reasoning effort high, standard service tier | In `~/.codex/config.toml` or the project's `.codex/config.toml`: `model = "gpt-6-sol"`, `model_reasoning_effort = "high"`, and `service_tier = "default"`. |
| Pi | `openai-codex/gpt-6-sol`, thinking high | In `~/.pi/agent/settings.json`: `"defaultProvider": "openai-codex"`, `"defaultModel": "gpt-6-sol"`, `"defaultThinkingLevel": "high"`. |

Never turn on Codex's Fast mode (its toggle in the Codex TUI, or `service_tier = "fast"`): it uses 1.5x the quota. `service_tier = "default"` pins the standard tier so a model's own default cannot switch it on.

## What the skills ask for

pstack's skills name a model for each role. This kit rewrote every one of them to `inherit-parent`, which means "omit the model and use the one you are already running":

- Single roles: code delegates, explorers, investigators, `/how` explainers, `/why` synthesizers, reflect reviewers, judges.
- Panels: `/arena` runners and its cross-judge, `/architect` design runners, `/interrogate` reviewers. A panel still spawns one run per seat, so its opinions stay independent; they just all run on your agent.
- The advisor: `/advisor` consults your own model in a fresh context.
- Agent files (`ci-watcher`, the agent-compatibility reviewers, `advisor-subagent`) say `model: inherit`.

Nothing in a default run calls a model you do not have, so nothing falls back and nothing waits on another tool.

Cross-family review still happens where it costs no one's quota: [the review bots](build-review-ship.md#review) run Claude and Codex on every pull request in CI, on repository secrets, whichever agent wrote the change.

## When someone names a model

You can still pin a role to a model, with `/setup-pstack` (which writes `~/.agents/pstack-models.md`) or with `/advisor <model>`. Use the team's names: `claude-opus-5-5-max` or `gpt-6-sol-high`. A seat from your own family runs natively. A seat from the other family goes through the `run-model` skill, which runs it through the other agent's CLI if you happen to have it, and otherwise tells your agent to run the seat on its own model and say so. `run-model` always runs GPT at high effort in the standard tier: it drops a `-fast` suffix and lowers `max` or `xhigh` to `high`.

## Changing the team's models

The rewrite lives in [`upstream/model-map.json`](../../upstream/model-map.json). Every occurrence it changed is listed in [`upstream/CHANGES.md`](../../upstream/CHANGES.md), and [the models reference](../reference/models.md) summarizes it. To change it, edit the rules, run `node scripts/sync-upstream.mjs`, and review the diff. CI fails if a vendored file names a model the map does not allow.
