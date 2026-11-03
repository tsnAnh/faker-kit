# Start here: engineers

Your first hour with the kit. By the end, a repository is set up for agents, an agent has shipped a change with proof that it works, and one of your corrections is enforced by CI instead of living in a chat.

## 1. Install (5 minutes)

Follow [Install the kit](../how-to/install.md), then [choose the model](../how-to/install.md#choose-the-model).

## 2. Set up a repository (20 minutes)

In the repository, on a new branch:

```text
/setup-faker-kit
```

It writes AGENTS.md (with the repository's own build, test, and lint commands), and links project skills. It adds no check script or lint configuration, and one CI step that checks the kit's links and vendored copy. It opens one pull request. Details: [Set up a repository](../how-to/set-up-a-repo.md).

## 3. Build a verification skill (20 minutes, then ongoing)

```text
/create-verification-skill
```

This is the most valuable thing you can give an agent: a small CLI that starts your app, drives it, and captures screenshots, logs, and traces, plus a feature map of how to reach every screen. Without it, agents can only guess whether their change works, and you stay the bottleneck. For Android, iOS, Flutter, and KMP apps, it builds on `control-mobile`. Details: the pstack guide's [Verify the result and open a PR](../../plugins/pstack/docs/guide/06-verify-and-ship.md).

## 4. Ship one change with proof (15 minutes)

```text
/faker-mode add an empty state to the search screen. verify it really works.
```

`/faker-mode` picks a playbook (here, Feature), delegates the work to subagents, runs your verification skill, and opens a pull request with the evidence. It stays on for the rest of the session; say "exit faker mode" to turn it off. Read the pstack guide's [Route work through `/faker-mode`](../../plugins/pstack/docs/guide/02-faker-mode.md) for how to phrase tasks.

## 5. Correct the agent once, for good

The next time you catch an agent doing something you do not want repeated:

```text
/correct the agent keeps catching Exception and swallowing it
```

It finds the mistakes agents repeat, fixes each at the highest level that works (architecture, then types, a lint or CI check, a test, and docs last), proves each new check fails on a real past mistake, and keeps the rule table in AGENTS.md. Details: [Correct an agent](../how-to/correct-an-agent.md).

## What to learn next

| When you want to | Use | Guide |
|---|---|---|
| Understand code before changing it | `/how`, `/why`, `/teach`, `/recall` | [pstack guide: understand](../../plugins/pstack/docs/guide/03-understand.md) |
| Decide on a design with evidence | `/architect`, `/arena`, the Prototype playbook | [pstack guide: design](../../plugins/pstack/docs/guide/04-design.md) |
| Review and land work | `/interrogate`, `/thermos`, `/deslop`, `/no-comments`, the Babysit playbook | [pstack guide: verify and ship](../../plugins/pstack/docs/guide/06-verify-and-ship.md) |
| Run agents while you are away | the Autonomous run, Orchestrate, and Autopilot playbooks, or a Hermes project bot | [pstack guide: overnight](../../plugins/pstack/docs/guide/07-overnight.md), [Dispatch work with Hermes](../how-to/dispatch-with-hermes.md) |
| Steer with principle names | the `principle-*` skills | [pstack guide: principles](../../plugins/pstack/docs/guide/08-principles.md) |
| See how ready a repo is for agents | `/check-agent-compatibility` | [The trust ladder](../explanation/trust-ladder.md) |

[Every skill](../reference/skills.md) is in the reference.
