# Plan with code

This page follows Pt. 2 of Lauren Tan's "Complete Guide to pstack": once verification works, the next question is how to decide what to build.

## Two reasons agents build the wrong thing

Agents make changing code cheap, whether or not you understand the code. That removes the old barrier of reading a system before touching it, and it also removes the understanding that came with it. Agents go wrong when:

1. They do not know enough about the system to know what to build.
2. They do not have enough context to build it correctly.

Both come down to what is in the agent's context window. Good planning is mostly putting the right context there.

## Understand first

pstack's research skills gather that context:

- `/how` traces how a subsystem works at runtime, sending explorers in parallel when it spans many directories or services.
- `/why` looks for the reasons behind the code in its history: commits, pull requests, issues, and any other sources the agent is connected to. Code shows what happens; it rarely says why.
- `/teach` runs both and explains the result so a person understands it. Pt. 2 notes that the same research makes the next agent's work better too.
- `/recall` treats past sessions as context: what you did yesterday, what was tried, what failed.

## Prototypes are planning with code

A plan written before anything runs is a guess. pstack's Prototype playbook turns open questions into experiments: build a few throwaway versions behind one switch, observe each (screenshots for a visual choice, measured behavior for a technical one), and pick with evidence. It gives the agent room to explore and to surprise you with an option you would not have thought of.

The same idea runs through `/faker-mode`: when a question can be answered by running something, it prototypes instead of asking you. Only product and preference calls go to a person.

## Competing designs

`/architect` sketches the shape of a change (types, signatures, module boundaries) before code locks it in, using several independent design runners and throwaway prototypes to settle disagreements. The point, in Pt. 2's words, is a self-contained loop where the agent synthesizes competing designs into one and is not afraid to throw its own away. Upstream spreads the runners across model families. In this kit every runner is a separate run on your own agent, since each teammate has one; see [Choose models](../how-to/models.md).

## Plans built around proof

For large work, the Multi-phase plan playbook writes a plan where every task says how it will be verified, and tests alone do not count. A script checks each plan's structure. Once you approve it, the plan executes item by item, one small pull request per step, each with its own evidence. That is what makes a large migration reviewable: nobody has to hold the whole thing in their head, only one proven step at a time.

## Why this matters for trust

Planning with code turns design from opinion into evidence, the same way verification turns "it works" from a claim into a recording. Both let you trust an agent's output without redoing its work. Combined with the [trust ladder](trust-ladder.md), engineering with agents becomes predictable and repeatable.

See [Design before building](../how-to/design-before-building.md) for the prompts.
