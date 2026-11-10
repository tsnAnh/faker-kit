# STYLE.md

Rules the team enforces in review because no tool enforces them yet. Each entry names where it should move next (`Promotion`), so this file shrinks as rules become checks. Run `/correct` on an entry to promote it.

This repository's pull requests come from agents, and it has no human review comments yet, so these entries come from the owner's own corrections while building the kit. Each source names one. Add an entry when the same correction comes up a second time.

## Entry format

```markdown
### S<n>. <The rule in one sentence>

- Why: <what goes wrong without it>
- Bad: <smallest example>
- Good: <the same example done right>
- Promotion: type | lint:<rule-id> | review | skill:<name> | style-only
- Source: <PR or review comment where it came up>
```

`style-only` means the rule is a matter of judgment and stays here.

## Entries

### S1. A refusal or notice tells the owner what to do next

- Why: the owner reads these in Bot Chat or a terminal, often hours later. A line that only says what failed leaves them guessing which command or setting fixes it.
- Bad: `faker-kit: refused: no passing CI run`
- Good: `faker-kit: refused: no passing push or pull_request CI run on the head; add a workflow that builds and tests every pull request`
- Promotion: review
- Source: the owner's setup sessions for the routine and the claude --cloud launch (#35, #46), where every stuck step was a refusal that did not name its fix.

### S2. Use the gh CLI for GitHub work; reach for a session's GitHub tools only where gh is missing

- Why: `gh` behaves the same in every agent and on the owner's machine, and its calls are easy to read in a transcript and to replay. Cloud sessions block GitHub's GraphQL, so there the form is `gh api` REST.
- Bad: a skill step that says "post the comment with the GitHub tools".
- Good: `gh api repos/OWNER/REPO/issues/<n>/comments -F body=@report.md`, with "Where `gh` is missing, use the session's GitHub tools."
- Promotion: review
- Source: the owner's "prefer gh cli" when the cloud skills moved to REST calls (#36).

### S3. Write a skill or doc step as one instruction a cold agent can follow

- Why: skills are read mid-task by an agent with no chat history, often with no human watching. A step that hedges ("you might want to"), bundles several actions, or relies on context the reader lacks gets skipped or guessed.
- Bad: "You may want to check the status of every project before you try launching, if that seems needed."
- Good: "Check every project: `faker-kit status --all`, then launch only for a project whose line shows no `NOT SET UP` warning."
- Promotion: style-only
- Source: AGENTS.md "Write for an agent reading the file cold"; the owner's setup sessions, where each unclear step became a question.

### S4. Prefer the upstream skill when one covers the capability

- Why: two skills for one job split the team's habits, and a project links skills by bare name, so a repeated name collides. A wrapper keeps upstream fixes flowing.
- Bad: keeping trust-ladder's own `correct` next to pstack's `correct`.
- Good: use pstack's `/correct`; put this repository's extra steps in AGENTS.md or a trust-ladder skill that calls it.
- Promotion: review (AGENTS.md, Writing trust-ladder skills)
- Source: the owner's "prefer upstream skill" (#43).

## Promoted

- **No AI or agent references in commit messages** moved to a check: `scripts/check-commit-messages.mjs`, run by CI's `commit-messages` job (owner rule; `.agents/dispatch.md` standing order 6, #42).
