# Choose models

The team standard is the strongest model each agent offers, at its highest effort:

| Agent | Model | How to set it |
|---|---|---|
| Claude Code | Claude Opus 5.5, effort max | `/model opus`, then `/effort max`. For one run: `claude --model opus --effort max`. |
| Codex | GPT-6 Sol, reasoning effort max | In `~/.codex/config.toml` or the project's `.codex/config.toml`: `model = "gpt-6-sol"` and `model_reasoning_effort = "max"`. |
| Pi | `openai-codex/gpt-6-sol`, thinking max | In `~/.pi/agent/settings.json`: `"defaultProvider": "openai-codex"`, `"defaultModel": "gpt-6-sol"`, `"defaultThinkingLevel": "max"`. |

## What the skills ask for

pstack's skills name a model for some roles. This kit rewrote those names (and nothing else) to the models above. You will see three names:

| Name in a skill | Meaning |
|---|---|
| `inherit-parent` | Run the role on the model you are already using. Every single role (code delegates, explorers, investigators, `/how` explainers, `/why` synthesizers, judges) uses this, so the whole run stays on your agent. |
| `claude-opus-5-5-max` | Claude Opus 5.5 at max effort. A seat in a panel. |
| `gpt-6-sol-max` | GPT-6 Sol at max effort. A seat in a panel. |

Panels are the skills that want independent opinions: `/arena`, `/architect` design runners, `/interrogate` reviewers, and the cross-judges. They list `claude-opus-5-5-max, gpt-6-sol-max, inherit-parent`, so that different model families catch different mistakes.

## Panels when you have only one agent

Most people have only one agent. A panel seat from your own family runs natively. A seat from the other family runs through the `run-model` skill, which checks whether the other family is reachable from your machine:

| You use | `claude-opus-5-5-max` seat | `gpt-6-sol-max` seat |
|---|---|---|
| Claude Code | Native: a subagent with `model: opus` | `codex exec` if Codex is installed, or `pi -p --model openai-codex/gpt-6-sol` if Pi is, else your own model |
| Codex | `claude -p --model claude-opus-5-5 --effort max` if Claude Code is installed, else your own model | Native |
| Pi | `claude -p` if Claude Code is installed, or a subagent on `anthropic/claude-opus-5-5` if you configured Pi's Anthropic provider, else your own model | Native: a subagent on `openai-codex/gpt-6-sol` |

When a seat falls back to your own model, the panel keeps its size (independent runs still disagree usefully), and the agent says the panel was single-family. Nothing breaks.

Cross-family review still happens on every pull request, because [the review bots](build-review-ship.md#review) run Claude and Codex in CI whichever agent wrote the change.

## Changing the team's models

The rewrite lives in [`upstream/model-map.json`](../../upstream/model-map.json). Every occurrence it changed is listed in [`upstream/CHANGES.md`](../../upstream/CHANGES.md), and [the models reference](../reference/models.md) summarizes it. To move to a new model, edit the rules' `to` values, run `node scripts/sync-upstream.mjs`, and review the diff. CI fails if any vendored file names a model the map does not allow.
