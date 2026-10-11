---
name: correct
description: Turn a correction you gave an agent into a mechanism so no agent repeats the mistake. Picks the strongest layer that can hold the rule (types and architecture, then lint/CI, then AGENTS.md and review bots, then skills, then the style guide), proves it fires, and logs it. Use right after correcting an agent, when a reviewer flags the same issue twice, or when someone says "don't do that again", "encode this", or /correct.
---

# Correct

A correction typed into a chat helps one agent once. This skill puts it where every future agent meets it. Read [the trust ladder](../../references/trust-ladder.md) first; its routing table has worked examples.

## 1. Pin down the mistake

1. Find the evidence: the diff, the transcript lines, or the review comment. Quote it.
2. Write the **bad example**: the smallest code or behavior that shows the mistake.
3. Write the **good example**: what the agent should have done.
4. State the **rule** in one sentence that a new teammate would understand.
5. Decide whether it recurs. It recurs when it happened before, when any other agent would plausibly do it again, or when a human asked to encode it. A one-off misunderstanding of this task gets fixed and stops here, with no mechanism.

## 2. Climb the ladder, strongest layer first

Take the first layer that can hold the rule. Say in your report why each stronger layer could not.

1. **Codebase: make the bad state impossible.** A type that cannot represent it, one blessed API with the alternatives deleted, a module boundary the build enforces. If callers must move to a new pattern, run the `paved-path` skill.
2. **Static analysis: make it fail CI.** A lint rule, a compiler flag, or a CI check. Run the `encode-lint-rule` skill. The failure message must say what to do instead, because that message is what the next agent reads.
3. **Rules and review bots: make it visible.** Use this for judgment calls no tool can check. Add one line to the right AGENTS.md: the root file, or a nested one in the directory the rule governs. Add the same rule under `## Review guidelines` so Claude and Codex PR reviews flag it. For Claude Code path-scoped rules, use `.claude/rules/<topic>.md` with `paths:` frontmatter.
4. **Skills: teach the procedure.** When the fix is a sequence of steps, such as how to verify a screen, add it to the project skill that owns the procedure. For verification, that is the `verify-<app>` skill from `/create-verification-skill`.
5. **Style guide: write it down.** Only when nothing above can hold it yet. Add a STYLE.md entry with the bad and good examples and a `promotion:` field naming the layer it should reach next.

Encode the rule once, at the strongest layer. Do not also add prose at a weaker layer ("the instruction is the symptom", from `principle-encode-lessons-in-structure`).

## 3. Prove the mechanism fires

- **Layers 1 and 2:** run the check on the bad example and show it fails with the "do this instead" message. Run it on the good example and show it passes. Commit both so the proof keeps running: as a test when the rule is a type or a test, otherwise as `.agents/rule-fixtures/<id>/bad.<ext>` and `good.<ext>` with an entry in `.agents/rule-fixtures.json` (see `encode-lint-rule`). `scripts/check.sh` then fails if anyone later weakens the rule.
- **Existing violations:** run the check on the whole repository. Fix them in this change when there are few. Otherwise record them in the ratchet baseline so CI blocks only new ones, and file one cleanup task. Never weaken the rule to get green.
- **Layer 3:** give a fresh subagent with no chat history the bad example and the repository's instructions, and ask for a review. It must flag the mistake. If it does not, the rule is unclear or in the wrong file.
- **Layers 4 and 5:** run the skill step or read the entry cold, as a new teammate would.

## 4. Record it

Append one tab-separated row to `.agents/corrections.tsv`. Create the file with this header if it is missing:

```text
date	rule	layer	mechanism	proof	source
```

- `layer`: 1 to 5.
- `mechanism`: the path of the type, lint rule, instruction line, skill, or STYLE.md entry.
- `proof`: the command that shows it failing on the bad example.
- `source`: the PR, issue, or chat where the correction happened.

## 5. Ship it

Open one small PR with the mechanism, its fixtures, the baseline if any, and the ledger row. Title it `chore(agents): encode <rule>`. Keep it separate from feature work so reviewers can see the rule change on its own.

## Report

End with:

- The rule, and the layer chosen with the reason stronger layers did not fit.
- The mechanism's path.
- The proof: the failing output on the bad example and the passing output on the good one.
- How many existing violations were fixed or baselined, and the cleanup task.
- The PR link.
