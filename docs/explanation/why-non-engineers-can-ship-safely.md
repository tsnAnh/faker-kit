# Why non-engineers can ship safely

Not every contributor to a codebase is an engineer anymore. Designers, product managers, and CEOs are shipping features. The codebase has to be set up so that agents piloted by busy people with little context still do a good job by default.

This page explains why that is safe with the kit, and where the limits are.

## Safety comes from gates, not from the person

The same gates apply to every change, whoever asked for it and whichever agent wrote it:

1. **The change is a pull request.** Nothing reaches the default branch directly. Branch protection blocks direct pushes and force pushes for everyone, including administrators.
2. **The checks run.** `scripts/check.sh` runs the lint packs, the compiler, the tests, and the agnostic checks the team turned on: import boundaries always, and optionally complete translations and no new comments. A change that breaks a rule fails, with a message that tells the agent how to fix it.
3. **Two review bots read it.** Claude and Codex review every pull request against REVIEW.md, AGENTS.md, and STYLE.md. Every blocking finding must be fixed or answered before merge.
4. **A code owner approves it.** CODEOWNERS names an engineer for every path. Their approval is required, and a new commit dismisses an earlier approval.

A designer's pull request and a senior engineer's pull request pass through exactly the same gates. That is what makes the first one safe. The [branch protection checklist](../../plugins/trust-ladder/templates/branch-protection.md) turns them on.

## Evidence anyone can read

`/request-a-change` is built around proof the requester can check without reading code:

- It restates the request in plain words and asks "Is that right?" before it starts.
- It proves the change with before and after screenshots or a recording from the repository's verification skill.
- It opens a pull request whose first section says, in plain words, what changes for users, followed by the evidence, then technical notes for the reviewing engineer.

The requester judges the outcome; the engineer judges the code. Each looks at what they are best placed to judge.

## What the agent will not do

- **It never merges.** Not even with every check green. An engineer merges.
- **It stops on sensitive areas.** Requests that touch money, permissions, personal data, or deleting things are handed to an engineer instead.
- **It keeps every language in step.** Any user-facing text change updates every supported language with a real translation, and the agnostic pack's `locale-keys` check, when enabled, fails if one is missing.
- **It works on a branch**, usually in a cloud session, so the requester needs no local setup and nothing on their machine can break.

## The environment does the rest

Everything on the [trust ladder](trust-ladder.md) makes these changes better, not only safer. A codebase with one paved path per concern gives the agent one obvious way to do things. Lint rules catch the mistakes a newcomer's agent is most likely to make. The verification skill lets the agent see what it built. Every correction an engineer encodes with `/correct` protects every future request, including the next one from marketing.

## Where the limits are

- Without a verification skill, the agent can only show test results. `/start-here` and `/request-a-change` check for one and say so plainly. Build one first ([how](../how-to/build-a-verification-skill.md)).
- Branch protection has to actually be on. The kit writes the checklist; someone with admin rights has to apply it.
- Review load moves to engineers. Keep the first pull requests small, and review them quickly; fast feedback is what makes the habit stick.
