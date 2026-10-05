<!-- team-kit:compat:start -->
## Running the upstream skills in Claude Code, Codex, and Pi

The pstack, team-workflows, thermos, advisor, continual-learning, agent-compatibility, and teaching skills were written for another editor and are kept close to word for word. When one of them uses that editor's terms, do the equivalent below in the agent you are running in. The Hermes column is for the bots of the kit's `hermes/` module (the bootstrap bot and the project bots), which run no coding skills.

| The skill says | Claude Code | Codex | Pi | Hermes |
|---|---|---|---|---|
| `Task` tool, `subagent_type: generalPurpose` | Agent tool, `general-purpose` | Run the step yourself, or run `codex exec -s read-only -` with the prompt for a fresh context | `subagent` tool with the builtin `delegate` (call `subagents_enable` first) | `delegate_task`, or a Kanban card. The dispatch guard denies `delegate_task`, so the dispatcher uses cards |
| `subagent_type: "explore"` or `"shell"` | `Explore` or `general-purpose` | Run it yourself | `subagent` with `scout` or `worker` | Not used on the Hermes side |
| `subagent_type: "faker-agent"`, `"Comment Sicko"` | `pstack:faker-agent`, `pstack:comment-sicko` | Read the agent file in pstack `agents/` and follow it | `subagent` with `worker`, prompt starting "Read pstack `agents/<file>.md` first" | Not used on the Hermes side |
| `subagent_type: "thermo-nuclear-review-subagent"` and the other named agents | `<plugin>:<name>`, for example `thermos:thermo-nuclear-review-subagent` | Read `agents/<name>.md` in that plugin and follow it | `subagent` with `reviewer`, prompt starting "Read `agents/<name>.md` first" | Not used on the Hermes side |
| `readonly: true` | An agent without edit tools, such as `Explore` | `codex exec -s read-only` | `oracle` or `scout`, which do not edit | Not used on the Hermes side |
| `run_in_background: true` | The same Agent parameter | Parallel shell jobs | `subagent` background run | Not used on the Hermes side |
| `AskQuestion` | AskUserQuestion | Ask in chat | `ask_user` | `clarify`, a question in Desktop |
| `/skill-name` | `/skill-name` or `/plugin:skill-name` | `$skill-name` | `/skill:skill-name` | `/skill-name` in Desktop |
| `.agents/skills/<name>/`, such as a generated `verify-<app>` | Also at `.claude/skills/<name>` after `setup-team-kit` links it | Read in place | Read in place | Not used on the Hermes side |
| `*.mdc` rules, "an always-applied rule" | `CLAUDE.md` or `.claude/rules/*.md` | `AGENTS.md` | `AGENTS.md` | Not used on the Hermes side |
| `~/.agents/pstack-models.md` | Read it if it exists; `/setup-pstack` writes it there | Same | Same | Not used on the Hermes side |
| `agent-transcripts/`, `~/.agents/projects/<slug>/agent-transcripts/` | `~/.claude/projects/<project>/*.jsonl` | `~/.codex/sessions/` | `~/.pi/agent/sessions/` | Hermes sessions, listed by `hermes sessions list` |
| `.agents/settings.json`, enabling a plugin for a repository | `enabledPlugins` in `.claude/settings.json` | `codex plugin add` | `packages` in `.pi/settings.json` | Not used on the Hermes side |
| The agent's built-in `create-skill` | The `skill-creator` skill, else the faker-mode "Authoring a skill" playbook | `$skill-creator` | The "Authoring a skill" playbook | Not used on the Hermes side |
| The agent's built-in `babysit` | The faker-mode Babysit playbook | Same | Same | Not used on the Hermes side |
| `/loop` | `/loop` | The faker-mode Autonomous run playbook | Same as Codex | Not used on the Hermes side |
| Cloud agents, `environment: "cloud"` | Claude Code on the web sessions | Codex cloud tasks | A pi-subagents background run in its own worktree | A Hermes project bot runs `claude --cloud` or `codex cloud exec` with `team-kit-gh dispatch` and its `tick` job, per `backend` in `.agents/dispatch.json` |
| Automations | Claude Code routines (`setup-routines`) | Codex app scheduled tasks, which run while that computer is on | A teammate's Claude Code routine or Codex scheduled task | Hermes cron jobs, which run while the gateway runs |
| Bugbot, agentic security review | A review routine with a GitHub trigger, or Claude Code Review (`setup-review-bots`) | Codex code review, automatic or `@codex review` | The team's PR review bots | Not used on the Hermes side |
| Custom Mode pin (`mode: true`) | trust-ladder's hook keeps `/faker-mode` on until you say "exit faker mode" | Same hook | trust-ladder's Pi extension does the same | Not used on the Hermes side |
| Hooks, such as `afterFileEdit` and `stop` | Plugin hooks, through each plugin's `agent-hooks/` | The same, after you trust them with `/hooks` | Upstream hooks do not run. See Upstream hooks below | Hermes plugin hooks `pre_tool_call` and `transform_tool_result`, as in the `team_kit_guard` plugin |
| Origin CLI | `gh` | `gh` | `gh` | `team-kit-gh`, in the dispatcher profile only |

When you spawn `faker-agent`, start the prompt with `/faker-mode` (Claude Code) or `$faker-mode` (Codex). The `faker-mode` skill is `disable-model-invocation`, so the subagent cannot load it through the Skill tool; a user-style command is what runs it. Any prompt that hands work to a coding agent, such as the cloud session `team-kit-gh launch` starts for a Hermes project bot, always starts with `/faker-mode` (Claude Code) or `$faker-mode` (Codex).

### Models

Each teammate runs one agent: Claude Code on Claude Opus 5.5 at max effort, or Codex on GPT-6 Sol at high effort in the standard service tier (never the fast tier, which costs 1.5x quota).

- Every pstack role and panel seat defaults to `inherit-parent`. Omit `model` and run it on your own agent. A panel keeps its size: each seat is a separate run, so the opinions stay independent.
- A seat someone configured with a model name: `claude-opus-5-5-max` is `model: opus` in Claude Code. `gpt-6-sol-high` is native in Codex, and `openai-codex/gpt-6-sol` with thinking `high` in Pi. For the other family, use the `run-model` skill. If it reports the model is unreachable, run the seat on your own model and say so.
- Agent files that say `model: inherit` run on your current model.

### Upstream hooks

advisor's end-of-turn nudge and continual-learning's cadence nudge are upstream hooks. In Claude Code and Codex they run unchanged through each plugin's `agent-hooks/`, which maps the upstream `afterFileEdit`, `afterAgentResponse`, `stop`, and `subagentStop` events to `PostToolUse`, `Stop`, and `SubagentStop`. A follow-up they post arrives as a hook message that asks you to continue: act on it like a user turn. advisor's hooks need `jq`; continual-learning's needs `bun`. Without them the hook does nothing. Codex runs plugin hooks only after the user trusts them with `/hooks`. In Pi, run `/continual-learning` yourself or schedule it with `setup-routines`, and use `/advisor ask <question>` for a consult.
<!-- team-kit:compat:end -->
