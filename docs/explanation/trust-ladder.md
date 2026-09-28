# The trust ladder

This page explains the idea the kit is built on: how a team goes from watching one to five agents at a time to trusting many at once. The routing details are in [the trust ladder reference](../../plugins/trust-ladder/references/trust-ladder.md).

## The bottleneck is trust

With one to five agents, you watch every session and correct each one as it goes. It is the hardest stage to leave, because the obvious move, running a hundred agents, only multiplies the mistakes: a flood of low-quality pull requests, regressions, and bugs. You cannot run more agents until you can trust what they do without watching.

That trust does not come from better prompts. It comes from the environment the agents work in. So the question to ask each time you correct an agent is: where does this correction belong, so that no agent needs it again?

## Five layers, strongest first

1. **The codebase.** Agents extend the patterns they see, because the files they read become their context. The codebase is their memory. Make the mistake impossible to write, with types, data structures, module boundaries, and one conventional way to do each thing.
2. **Static analysis.** Lint rules, compiler diagnostics, and CI. When an agent keeps making the same mistake, a check stops it without anyone's cooperation.
3. **Rules and review bots.** AGENTS.md and automated pull request review (Claude and Codex here). This is guidance, not enforcement: an agent can skip a file, and a person can ignore a bot.
4. **Skills.** Procedures agents load when they need them, above all the verification skill that lets an agent run the app and prove its work.
5. **The style guide.** Enforced only by humans in review. At the rate agents open pull requests, nobody can remember every rule for every line, so the style guide is where rules are found, not where they should stay.

The layers get weaker as you go down: each depends more on someone choosing to follow it. So the rule is to put each correction at the strongest layer that can hold it, and not also restate it at a weaker one. `/correct` does this for you.

## Why the codebase comes first

Patterns spread both ways. A codebase that is clean and conventional teaches every agent to keep it that way. A single workaround, or a comment explaining one, gets copied, and within days or weeks it is the de facto pattern.

The answer is an agent-friendly framework. Agents take shortcuts, so the framework makes the shortcut the right path: features live in one place, what runs where is enforced through the import graph, and even comments are banned because agents used them to justify papering over problems. A codebase like that can be annoying for people. For agents, especially ones with little context, it is the environment where the right thing happens by default.

The kit's version: `paved-path` to leave one blessed way per concern, `gardener` to catch patterns while they spread, and [lint packs](../reference/lint-packs.md) that ban the escape hatches agents reach for.

## When you cannot fix it today

Tech debt does not have to be cleaned up before you can act. A lint rule or a ratchet stops the bleeding: existing instances stay for now, new ones fail CI. The pattern stops growing, and cleanup can happen later, often by an agent.

## It compounds

Each correction encoded at layer 1 or 2 is one that no agent, reviewer, or new teammate has to think about again. `.agents/corrections.tsv` records every one; `/trust-audit` scores the layers. As the share of corrections in the strong layers grows, the environment becomes something you can trust, and then you can run agents freely, including agents started by people who are not engineers. Think of a Michelin kitchen: set up so well that even a cook with little experience does good work.

Continue with [Verification is all you need](verification-is-all-you-need.md).
