# Review a unit pull request with Claude Code's `/code-review`

After a coding unit reports `done`, anyone can review its exact pull request head with Claude Code's built-in `/code-review` command. This page gives the procedure, says what the result can and cannot attest, and says where findings go.

`/code-review <pr-number>` reviews a pull request. `/review` is its alias. `/code-review ultra` runs a cloud multi-agent review, and `/security-review` is a separate command. None of them is the Claude GitHub Action (`.github/workflows/claude-review.yml`, not used here) or the managed Code Review service, which posts a check run and never approves.

## Procedure

Run it on your machine, never inside the session that wrote the change. A fresh process in a clean checkout is the only supported way. A review inside the implementation context shares its assumptions and is not independent.

1. Read the head to review: `gh api repos/OWNER/REPO/pulls/<pr> --jq .head.sha`. Call it `<head>`.
2. Make an empty scratch directory outside any repository and fetch only that head:

   ```bash
   mkdir review-<pr> && cd review-<pr>
   git init -q . && git remote add origin https://github.com/OWNER/REPO.git
   git fetch -q --depth=50 origin <default-branch> "pull/<pr>/head"
   git checkout -q --detach <head>
   git rev-parse HEAD   # must print <head>
   ```

3. Start a new Claude Code process there with read-only tools:

   ```bash
   claude -p "/code-review <pr>" \
     --allowedTools "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(gh api:*)" \
     --output-format text
   ```

   The allowlist has no `Edit`, `Write`, `git commit`, `git push`, or `gh pr`. `gh api` can write, so the process holds a read-only token or none. The command posts nothing to GitHub by itself.
4. Read the output. Each finding names a `file`, a `line`, a `summary`, and a `failure_scenario`. It carries no severity, so judge each one against the code and drop what you cannot reproduce. Reviews cite unrelated documents and flag cosmetics, so treat every finding as a claim to verify.
5. Check the head again before you act: `gh api repos/OWNER/REPO/pulls/<pr> --jq .head.sha`. If it differs from `<head>`, the result is stale. Discard it and review the new head.

This was run against a real unit pull request head, `tsnAnh/faker-kit#154` at `b5843ad1976886b21c0a49756163979e24f6d5f3`, on Claude Code 2.1.301. It returned seven findings and changed nothing in the checkout.

## What it can and cannot attest

It produces findings text, nothing else. It is not a GitHub review object, so it has no author that GitHub verifies and no commit it is bound to. `team-kit-gh tick` reads only a review whose login is `review_app` (a `[bot]` account) on the current head (`app_verdict`), so this output cannot satisfy `check_app_review` or `review_app`.

- No findings is not an approval. It means the model found nothing it could prove, not that the change is correct.
- Nobody may post an approval, a verdict, or a review object on its behalf.
- No gate changes. `review_app`, `auto_merge`, the CI check, verification, the andon cord, and the risk score still decide exactly as before. A clean `/code-review` does not merge a pull request, and a dirty one does not gate it.
- The owner decides what to do with a finding. A real one goes back to the unit as a rework (see below).

## Head binding

A result is valid for one head. Record the head sha with every result. A push makes every earlier result stale, and a new head needs a new review. Never carry a finding or a clean result forward to a later head without rerunning.

## Recording findings

Existing code has no command that posts a comment, and `team-kit-gh` reads only the signed `<!-- team-kit:review v1 -->` results from the `review_backend` path, which is `none` here. So nothing in the kit records a `/code-review` result yet, and this page adds no code.

Until a follow-up exists, the owner records findings by hand as a comment on the unit's issue, starting with this header, with the sha you checked out:

```text
<!-- team-kit:code-review v1 -->
pr: <url>
head: <full sha>
findings: <n>
```

Then one block per finding: `file:line`, the summary, and the failure scenario. The marker is deliberately not `team-kit:review`: Hermes parses that one, and only when signed.

Follow-ups, each its own unit and not part of this change:

1. A `team-kit-gh` command that posts a bot-authored `team-kit:code-review` comment with the head sha, and a stale-head check, with tests for allowed and denied cases and for no findings never counting as an approval.
2. The review-to-rework transition, which is unit #151. A recorded finding on the current head would feed it. This page does not duplicate it, nor the guard work in #144.
