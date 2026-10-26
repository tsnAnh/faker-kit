# Using faker-kit

faker-kit gives Claude Code Lauren Tan's pstack working methods and their companion plugins, and a way to make the agent more trustworthy every time you correct it. The kit's own pages are below; the pstack guide and the plugin READMEs cover the vendored parts, so this index links to them instead of restating them.

## The kit's own pages

| Page | Read it to |
|---|---|
| [Engineers: your first hour](start/engineers.md) | Install, set up a repository, build a verification skill, ship a proven change, encode your first correction |
| [Install the kit](how-to/install.md) | Install in Claude Code, enable it for a whole team, choose the model |
| [Set up a repository](how-to/set-up-a-repo.md) | Run `/setup-faker-kit`: AGENTS.md, review, team settings |
| [Roll the kit out to a team](how-to/roll-out-to-a-team.md) | Sequence a rollout and name the roles |
| [Correct an agent so it stays corrected](how-to/correct-an-agent.md) | Use `/correct` to move a fix to the strongest layer |
| [Dispatch work with Hermes](how-to/dispatch-with-hermes.md) | Let cloud agents turn what you ask for into pull requests you only merge |
| [The trust ladder](explanation/trust-ladder.md) | Understand why the kit routes every correction down five layers |
| [Every skill](reference/skills.md) | Look up a skill, who it is for, and a prompt to try |
| [Models](reference/models.md) | See how upstream model names map to ours |
| [Claude Code terms](../plugins/trust-ladder/references/compat.md) | Read the table that maps the upstream skills' terms to Claude Code |

## The pstack guide

Written by pstack's author. It is the best tour of `/faker-mode` and its skills. [Start at the guide's index](../plugins/pstack/docs/guide/README.md).

1. [Set up pstack](../plugins/pstack/docs/guide/01-setup.md)
2. [Route work through `/faker-mode`](../plugins/pstack/docs/guide/02-faker-mode.md)
3. [Understand the code before changing it](../plugins/pstack/docs/guide/03-understand.md)
4. [Design before you write code](../plugins/pstack/docs/guide/04-design.md)
5. [Build the change and clean the diff](../plugins/pstack/docs/guide/05-build-and-clean.md)
6. [Verify the result and open a PR](../plugins/pstack/docs/guide/06-verify-and-ship.md)
7. [Run work while you sleep](../plugins/pstack/docs/guide/07-overnight.md)
8. [Steer with principle names](../plugins/pstack/docs/guide/08-principles.md)
9. [Make it yours](../plugins/pstack/docs/guide/09-make-it-yours.md)
10. [Recipes and pitfalls](../plugins/pstack/docs/guide/10-recipes-and-pitfalls.md)

## The vendored plugins

[pstack](../plugins/pstack/README.md), [team-workflows](../plugins/team-workflows/README.md), [thermos](../plugins/thermos/README.md), [continual-learning](../plugins/continual-learning/README.md), [cli-for-agent](../plugins/cli-for-agent/README.md), [agent-compatibility](../plugins/agent-compatibility/README.md), [advisor](../plugins/advisor/README.md), and [teaching](../plugins/teaching/README.md).
