# Design before building

Agents make code cheap, so the expensive mistake is building the wrong thing well. pstack's answer is to plan with code: understand the system, try the options for real, then write a plan whose every step carries proof. The pstack guide's [Understand](../../plugins/pstack/docs/guide/03-understand.md) and [Design](../../plugins/pstack/docs/guide/04-design.md) chapters cover this in depth; this page is the short version.

## 1. Understand what is there

```text
/how is virtualization implemented?
/why are we still stuck on an old version of node?
/teach me why you implemented it this way and not with a cache. what were the tradeoffs?
/recall the work i did yesterday on virtualization and then read this bug report
```

- `/how` traces how the code works today. For a large subsystem it sends explorers in parallel.
- `/why` searches the history (commits, pull requests, issues, and whatever else your agent is connected to) for the reasons behind a decision.
- `/teach` combines the two into an explanation for you, and is just as useful as context for the next agent.
- `/recall` finds earlier sessions. In Claude Code they are under `~/.claude/projects/`, in Codex `~/.codex/sessions/`, in Pi `~/.pi/agent/sessions/`.

## 2. Try the options instead of arguing about them

```text
/faker-mode prototype two approaches to offline sync: a queue of operations, and last-write-wins on whole records. show me how each behaves when two devices edit the same note.
```

The Prototype playbook builds throwaway versions behind one switch, observes each one (screenshots for a visual choice, measured behavior for a technical one), and recommends. Nothing from a prototype ships; the chosen direction gets built properly afterwards.

When a question can be settled by running something, `/faker-mode` prototypes instead of asking you.

## 3. Sketch the shape with competing designs

```text
/faker-mode we need rate limiting for external webhooks. /architect this first, and answer open questions with prototypes. let me review before proceeding.
```

`/architect` sketches types, signatures, and module boundaries before code, using several independent design runners, each a separate run on your own agent (see [models](models.md)). `/arena` runs several candidates at the same task, picks the strongest as a base, and grafts in the best parts of the rest.

## 4. Write a plan for large changes

```text
/faker-mode create a plan to migrate our UI library to the new design tokens. break it into small, verifiable PRs. each PR needs visual regression tests and live verification steps.
```

The Multi-phase plan playbook writes a checklist, one section per pull request, where each step names the evidence that proves it. A script ([`check-plan.mjs`](../../plugins/pstack/skills/faker-mode/scripts/check-plan.mjs)) checks the plan's structure. Once you approve it, the plan executes item by item, and each small pull request is easy to review.

## For designers

The same tools answer design questions: "try three layouts for the empty state" runs the Prototype playbook with a screenshot per variant, and "make it match this mockup" runs Visual parity. See [Start here: designers](../start/designers.md).
