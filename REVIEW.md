# REVIEW.md

What reviewers check that no tool can. Human reviewers and the Claude review workflow both use this file. Anything `scripts/check.sh` enforces is out of scope here.

## Blocking

Mark these `[blocking]`. Each one names what to do instead.

1. **Unproven behavior change.** A change to what an owner or agent sees or does has no evidence from running it. Ask for the `verify-faker-kit` transcript of the feature it touches, or for an e2e step that fails without the change.
2. **A gate gets bypassed in Hermes.** A merge by any route but `faker-kit merge`, a launch while the andon cord is pulled, or a unit tracked as a GitHub issue or label instead of a pull request. Ask for the kit command or the store file.
3. **A secret reaches argv, a log, a URL, or the repo.** A token, signing key, or prompt holding a key passed as a process argument, printed, logged, put in an error message, or committed. Ask for stdin, a header, or the bot profile's `.env`, and redaction.
4. **A merge gate counts something editable.** A gate in `faker-kit merge` that accepts a person's or agent's comment, a review of an older head, a missing `security-review` status, or a status whose linked review-workflow run lacks a successful `security` job on the head as approval. Ask for the review app's review or the `security` job's result on the exact head.
5. **The guard or a boundary gets weaker.** A change to `hermes/plugin/faker_kit_guard` that allows more, a new tool in a role's allowlist, or a new inbound endpoint without signature verification. Ask why, and for a guard e2e step that the old refusal still holds.
6. **A hand edit to a vendored file.** Anything under a vendored plugin changed outside `upstream/*.json` rules. Ask for a rule in `upstream/`.
7. **A second way to do something.** A new pattern for a concern AGENTS.md lists under Paved paths. Ask for the paved path.
8. **A workaround instead of a fix.** A retry, sleep, broad `except`, or special case that hides a failure whose cause is not understood. Ask for the root cause.
9. **E2E steps that prove nothing, or a unit test.** A step that would still pass if the code under test returned a default, or that imports internals and asserts on them instead of driving the real entry point. The kit has no unit tests: any new `*.test.*` or `test_*.py` file is blocking. The `test-audit` skill's authoring gate is the standard for steps.
10. **A rule that should be a check.** The same review comment has appeared before. Ask for a lint rule, type, or e2e step through `/correct` instead of repeating it.

## Nits

Mark these `[nit]`. They never block a merge.

- Naming that makes a reader stop.
- A simpler equivalent exists in the same file.
- Prose in a skill or doc that an agent reading it cold would misread.

## Style

Entries in STYLE.md are review rules too. Cite the entry id, for example `[nit] S2: say what the owner does next`.

## Out of scope

- Formatting, import order, lint findings, types, and generated docs: `scripts/check.sh` enforces them.
- Rewrites unrelated to the change.
