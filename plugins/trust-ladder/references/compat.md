<!-- team-kit:compat:start -->
## Running the Cursor team's skills in Claude Code, Codex, and Pi

The pstack, cursor-team-kit, thermos, orchestrate, advisor, continual-learning, agent-compatibility, and teaching skills were written for Cursor and are kept word for word. When one of them uses a Cursor term, do the equivalent below in the agent you are running in.

| The skill says | Claude Code | Codex | Pi |
|---|---|---|---|
| `Task` tool, `subagent_type: generalPurpose` | Agent tool, `general-purpose` | Run the step yourself, or run `codex exec -s read-only -` with the prompt for a fresh context | `subagent` tool with the builtin `delegate` (call `subagents_enable` first) |
| `subagent_type: "explore"` or `"shell"` | `Explore` or `general-purpose` | Run it yourself | `subagent` with `scout` or `worker` |
| `subagent_type: "poteto-agent"`, `"Comment Sicko"` | `pstack:poteto-agent`, `pstack:comment-sicko` | Read the agent file in pstack `agents/` and follow it | `subagent` with `worker`, prompt starting "Read pstack `agents/<file>.md` first" |
| `subagent_type: "thermo-nuclear-review-subagent"` and the other named agents | `<plugin>:<name>`, for example `thermos:thermo-nuclear-review-subagent` | Read `agents/<name>.md` in that plugin and follow it | `subagent` with `reviewer`, prompt starting "Read `agents/<name>.md` first" |
| `readonly: true` | An agent without edit tools, such as `Explore` | `codex exec -s read-only` | `oracle` or `scout`, which do not edit |
| `run_in_background: true` | The same Agent parameter | Parallel shell jobs | `subagent` background run |
| `AskQuestion` | AskUserQuestion | Ask in chat | `ask_user` |
| `/skill-name` | `/skill-name` or `/plugin:skill-name` | `$skill-name` | `/skill:skill-name` |
| `.cursor/skills/<name>/`, such as a generated `verify-<app>` | Also at `.claude/skills/<name>` after `setup-team-kit` links it | `.agents/skills/<name>` | `.agents/skills/<name>` |
| `.cursor/rules/*.mdc`, "an always-applied rule" | `CLAUDE.md` or `.claude/rules/*.md` | `AGENTS.md` | `AGENTS.md` |
| `~/.cursor/rules/pstack-models.mdc` | Read it if it exists; `/setup-pstack` writes it there | Same | Same |
| `agent-transcripts/` | `~/.claude/projects/<project>/*.jsonl` | `~/.codex/sessions/` | `~/.pi/agent/sessions/` |
| Cursor's built-in `create-skill` | The `skill-creator` skill, else the poteto-mode "Authoring a skill" playbook | `$skill-creator` | The "Authoring a skill" playbook |
| Cursor's built-in `babysit` | The poteto-mode Babysit playbook | Same | Same |
| `/loop` | `/loop` | The poteto-mode Autonomous run playbook | Same as Codex |
| Cloud agents, `environment: "cloud"` | Claude Code on the web sessions | Codex cloud tasks | A pi-subagents background run in its own worktree |
| Cursor Automations, Grok Bot routines | Claude Code routines or GitHub Actions (`setup-routines`) | GitHub Actions | GitHub Actions |
| Grok Bot webhook routine and its sender key (`make-bot-ui`) | A routine with an API trigger; its bearer token is the sender key | A GitHub `repository_dispatch` workflow | Same as Codex |
| Bugbot, agentic security review | Claude Code Review or `claude-code-action` | Codex review or `codex-action` | Same PR bots |
| Custom Mode pin (`mode: true`) | trust-ladder's hook keeps `/poteto-mode` on until you say "exit poteto mode" | Same hook | trust-ladder's Pi extension does the same |
| Origin CLI | `gh` | `gh` | `gh` |

### Models

- `inherit-parent` or `auto`: omit `model`. The role runs on your current agent's model.
- `claude-opus-5-5-max`: in Claude Code use `model: opus`. In Codex or Pi, run the seat with the `run-model` skill.
- `gpt-6-sol-max`: native in Codex. In Pi use `openai-codex/gpt-6-sol` with thinking `max`. In Claude Code, run the seat with the `run-model` skill.
- If `run-model` reports that the model is unreachable, run that seat on your own model, keep the panel size, and say the panel was single-family.

### Cursor hooks that do not fire here

continual-learning's cadence nudge and advisor's end-of-turn nudge are Cursor-format hooks. They are ignored. Run `/continual-learning` yourself or schedule it with `setup-routines`, and use `/advisor ask <question>` when you want a consult.
<!-- team-kit:compat:end -->
