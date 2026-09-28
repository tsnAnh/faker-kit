# Verification is all you need

This page follows Pt. 1 of Lauren Tan's "Complete Guide to pstack", and the part of her talk about Control Glass.

## If an agent cannot verify its own work, nothing else matters

Without verification, every change an agent makes waits for a person to run the app and check it. That person becomes the bottleneck, and their day goes to babysitting agents. With verification, the agent runs the app itself, drives the change the way a user would, and brings back evidence: a screenshot, a recording, a trace, the rows it wrote. You review evidence instead of reproducing work.

Verification ranges from formal methods (proving invariants in something like Lean or TLA+), which few teams can use, to verification skills, which any team can build this week and which get you most of the way.

## Two parts: a CLI and a feature map

Cursor's first verification skill, Control Glass, drove the Cursor app through the Chrome DevTools protocol and took performance traces. It settled into two parts:

- **A CLI inside the skill.** Instead of each agent writing its own throwaway script, every agent uses the same small command-line tool to launch the app, check it is healthy, drive it, and capture evidence. pstack calls this [building the lever](../../plugins/pstack/skills/principle-build-the-lever/SKILL.md): give agents tools, not only instructions. The tool gets better every time someone improves it, and every agent benefits.
- **A feature map.** A bug report often arrives as a cropped screenshot and three question marks. An agent that can run the app still has to guess what the user meant. A feature map, like a sitemap for the app's features, says what each feature is, how a user reaches it, how to drive it, and what end state proves it works. It is written-down memory of how the app works.

The combination lets an agent both control the app and understand requests about it. At Cursor it became critical infrastructure, maintained by an automation and improved constantly.

## In this kit

- `/create-verification-skill` writes both parts for your repository as `verify-<app>`, and runs them once end to end before it hands over. It is the skill Pt. 1 calls `/control-app`.
- `control-ui` (web and desktop), `control-cli` (terminal apps), and `control-mobile` (Android, iOS, Flutter, KMP) are the harness recipes it builds on.
- `/cli-for-agents` reviews the CLI for the properties that make tools reliable for agents: no prompts, examples in `--help`, fast errors that say what to do.
- `/maintain-verification-skill` keeps the feature map honest as the app changes. Run it daily as a routine, and give the skill an owner. Some teams put it on an on-call rotation, because when it breaks, every agent loses its eyes.
- `/faker-mode`'s playbooks require evidence from it before a change counts as done, and `/request-a-change` shows its screenshots to non-engineers.

See [Build a verification skill](../how-to/build-a-verification-skill.md) for the steps.

## Proof standards

A verification only counts if it would catch the bug. The generated skill states these standards, and pstack's [prove-it-works](../../plugins/pstack/skills/principle-prove-it-works/SKILL.md) principle enforces them:

- Drive the real user path, not an internal setter or a test-only endpoint.
- Capture the action and the resulting state, not only the final screen.
- Check side effects (files written, rows inserted, messages sent) as well as what is visible.
- Tests are necessary but not sufficient. A change is verified when the code actually ran and did the thing.

## Verification unlocks everything else

Once agents can prove their work, you can run them in the cloud instead of on your laptop, let them run while you are away, and let people who do not read code request changes, because the evidence is something anyone can look at. Pt. 1's example: a cloud agent takes a performance trace, finds the hot spot, makes a targeted fix, and uses `/swarm` with the verification skill to confirm the win, while you do something else.

Continue with [Plan with code](plan-with-code.md).
