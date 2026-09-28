# Prompts

Prompts to copy, by role. Many are adapted from Lauren Tan's talk and pstack guide posts. They are written for Claude Code; in Codex, start a skill with `$` instead of `/`, and in Pi with `/skill:`.

The pattern behind all of them: say what you want and how you will know it is done. You rarely need to name a playbook or list skills.

## Anyone

```text
/start-here
/start-here I want to change the text on the checkout button
/request-a-change the empty cart screen should suggest popular items
/request-a-change after switching to dark mode, the chart labels are unreadable
/how does the free trial end?
/why do we ask for a phone number at sign-up?
/teach me how notifications get to a user's phone
/bro
/weekly-review for every author on main
/what-did-i-get-done this week
/create-learning-path I want to review pull requests with confidence
```

## Designers

```text
/poteto-mode try three layouts for the empty state. show me a screenshot of each side by side.
/poteto-mode make the settings screen match this mockup exactly. <image>
/request-a-change the primary buttons should use 12px corner radius everywhere
/request-a-change on small phones the pay button is cut off at the bottom
```

## Product and marketing

```text
/request-a-change the onboarding headline should say "Plan your week in two minutes" in every language
/technical-writing draft release notes for everything merged this week
can we unslop this announcement?
/why did conversions drop after the pricing page change?
```

## Engineers

Understand:

```text
/how is virtualization implemented?
/why are we still stuck on an old version of node?
/teach me why you implemented it this way and not with a cache. what were the tradeoffs you made and why?
/recall the work i did yesterday on virtualization and then read this bug report
/blast-radius this change to the session cache
```

Design:

```text
/poteto-mode we need to add rate limiting for external webhooks. /architect this first, and answer any open questions with prototypes. let me review before proceeding.
/poteto-mode create a plan to migrate our entire UI library to the new design tokens. break the migration into small, verifiable PRs. each PR must have its visual regression tests and live verification steps.
/arena take my prompt to the arena verbatim. i want to compare their proposals with yours.
```

Build and prove:

```text
/poteto-mode the export writes duplicate rows when a retry lands mid-run. repro first, then fix and verify.
/poteto-mode build this behind a feature flag. verify it really works.
apply prove it works. run the real import flow and show me the written records.
take a trace of the status quo, make a targeted fix, and use /swarm to confirm the win
/verify-this the list no longer jitters when scrolling
```

Clean, review, and ship:

```text
/deslop
/no-comments
/interrogate review this pr.
/thermos review this branch
/poteto-mode babysit this PR until it is merge-ready
/poteto-mode ship this stack
```

Correct and garden:

```text
/correct the agent keeps catching Exception and swallowing it
/encode-lint-rule ban android.util.Log; use AppLog
/gardener what spread in the codebase this week?
/paved-path we have three ways to format dates
/reflect that took too long. capture what we learned so the next run doesn't repeat it.
```

Run while away:

```text
/poteto-mode i'm stepping away. migrate every caller to the new async store; i want to trust it when i'm back.
/poteto-mode orchestrate the store migration. own it until every package is converted and merged. i'll check in twice a day.
/show-me-your-work keep a decision trail i can review when i'm back
```

Steer mid-task with a principle's name:

```text
attack the premise: two fixes for the flaky sync failed the same way
subtract before you add. delete the obsolete adapters first, then design what's left.
redesign from first principles as if offline mode was always a requirement
```

The full list of principles is in [Playbooks and principles](playbooks.md). pstack's own [recipes and pitfalls](../../plugins/pstack/docs/guide/10-recipes-and-pitfalls.md) has more.

## Leads

```text
/trust-audit how many agents can this repo take?
/style-guide-from-reviews last 90 days
/setup-routines run the gardener and the verification-skill upkeep every night
/check-agent-compatibility
```
