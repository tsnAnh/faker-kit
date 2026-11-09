---
name: verify-guard-shell-scanner
description: "Verify that no shell text hides a raw `gh pr merge` from the Hermes guard's hard blocks. Runs every shell text up to 5 syntax fragments long against real bash 5.2 and the guard's hard_block, and lists each text that bash runs and the guard allows. Covers scan_shell, shell_commands and terminal_problem in hermes/plugin/faker_kit_guard/policy.py. Use after any change to the guard's shell scanning (quotes, comments, heredocs, line continuation, arithmetic, command separators), before relying on a guard hard block, or when asked whether the guard can be bypassed."
---

# Verify the guard's shell scanner

**Property.** If bash runs a plain `gh pr merge 1` that follows a shell text from the bound below, `hard_block` refuses that text.

**Harm.** A hidden `gh pr merge` lands a pull request without `faker-kit merge`'s gate: CI, the `claude[bot]` approval, the security review, and the protected paths.

**Tool.** Bounded exhaustive checking of the real code against real bash. TLA+ does not fit: the question is whether a string function reads text the way the shell does, not which order events happen in. No program verifier covers this Python scanner, so the check runs the real `hard_block` on generated text and lets bash decide what each text means. A hand model of `scan_shell` would only restate a reading of it.

## Run it

```bash
python3 .agents/skills/verify-guard-shell-scanner/scanner-check.py
```

Needs `python3` 3.10 or newer and GNU bash 5.2 on `PATH`. The run refuses any other bash minor version, because `baseline.json` was recorded with 5.2.21. Ubuntu 24.04 and Debian 12 ship bash 5.2. On another image, or a Mac whose `/bin/bash` is 3.2, pass `--bash PATH` for a bash 5.2. A full run took about 40 seconds on 4 cores and scales with cores.

This check is not in any CI job. The kit's checks also run on Macs that have no bash 5.2, a 2-core runner would take about 80 seconds (estimated), and a branch that predates the sync commit fails it until it merges.

| Last line | Exit | Meaning |
|---|---|---|
| `PASS` | 0 | No text fails. |
| `UNCHANGED` | 0 | The failing texts are exactly the known findings below. This is the state of the sync commit below. A checkout that predates it prints the arithmetic `<<` texts as `NEW`. |
| `FAIL` | 1 | `NEW` lines are failing texts that are not in `baseline.json`: a break. `FIXED` lines are known findings that stopped failing. |
| `INCONCLUSIVE` or an error | 2 | Wrong bash, a guard that does not load, a failed control, or a bash timeout. |

Before it scans, the run proves its own instruments: the guard must refuse a plain `gh pr merge 1` and allow `echo hi`, and the stub `gh` must log the first and not the second. If not, it stops with exit 2, because a pass would mean nothing.

`--drift` first: it lists the covered definitions below that changed since the sync commit. Exit 0 means none of them changed. `--max-atoms 3` gives a run of about 5 seconds that compares only the shortest failing texts. `--artifact PATH` writes a JSON transcript, and the printed `digest` is the same on every run of the same code.

## Read a result

A `NEW` line is a whole shell text, `\n` is a newline: `NEW core/next-line: '#\\\ngh pr merge 1'`. `core` or `wide` names the alphabet, and `next-line` or `same-line` says where the canary sits. Each listed text is minimal: removing any one fragment stops the failure.

Reproduce it with bash and a stub `gh`:

```bash
mkdir -p /tmp/probe && printf '#!/bin/sh\necho "RAN gh $*"\n' > /tmp/probe/gh && chmod +x /tmp/probe/gh
env PATH=/tmp/probe "$(command -v bash)" -c $'echo hi # note \\\ngh pr merge 1'
```

Bash prints `RAN gh pr merge 1`. A text that does not print it is a modeling error in `scanner-check.py`, not a guard bug: fix the runner. A text that prints it and that `hard_block` allows is a guard bug.

To see what the scanner made of a text, load the guard and print `scan_shell(text)[0]`: the segments it hands to `command_problem`. The canary is hidden when no segment starts with `gh pr merge`.

## Known findings

Open on the sync commit. One text of each class was reproduced with `bash -c`, and `decide(...)` returned `None` for it.

1. **A heredoc operator ahead of the command hides it.** `<<X gh pr merge 1`, also written with a backslash-newline after `<<X `. Bash runs `gh` with the heredoc as its input. `scan_shell` keeps `<<X gh pr merge 1` as one segment whose first word is the redirection, so `command_problem` does not see `gh`. The three comment and backslash findings that once sat here (a comment ending in a backslash, a `#` that is not at the start of a word, an escaped backslash before a newline) were fixed in `scan_shell` by applying line continuation where bash does and by tracking the start of a word.

`baseline.json` holds the shortest failing texts per alphabet and attachment: 9 in all, out of 16 failing texts. A fix belongs in the code that reads the words of a segment (`shell_words`, `split_words`, `command_problem`), not in `scan_shell`. When one lands, the run prints `FIXED`: add each class to `hermes/e2e/guard-calls.json` as a block verdict, confirm by hand, run `--update-baseline`, and move the sync commit.

## What is checked

- Alphabets. `core` has 13 fragments: `#`, `\`, newline, space, `'`, `"`, `<<`, `$((`, `))`, `((`, `X`, `$(`, `)`. `wide` adds `$[`, `]`, `${`, `}`, `[`, `=`, `a`, a backtick, `<<<`, `&&`, `||`, `|`, `&`, `-` and tab.
- Bounds. Every sequence of up to 5 `core` fragments followed by a newline and the canary, up to 4 followed by `;` and the canary, and up to 3 `wide` fragments for both. About 480,000 sequences, a few of which spell the same text, such as `))` and `)` twice. A failing text is counted once.
- Per text: `hard_block(policy, "terminal", {"command": text})`. If it allows, `bash -c text` runs in a scratch directory with no stdin and a stub `gh` as the only `PATH` entry. The text fails when the stub logs `pr merge 1`. A guard that raises counts as a refusal, because the plugin refuses on a raise (`pre_tool_call` in `hermes/plugin/faker_kit_guard/__init__.py`).
- Safety. Every text is built from the fragments above and the canary, with no `>` redirection and no real binary on `PATH`, so bash can run only its builtins and the stub.

## What is not checked

- Texts longer than the bounds, and constructs outside the alphabets: `case`, `for`, functions, `[[ ]]`, process substitution, `$'...'`, and the bodies of `sh <<EOF` and `eval` strings (the `SHELL_FED_LINE` branch).
- Indirection such as `X=gh; $X pr merge 1`, or a script written and then run. The scanner does not resolve it.
- Command wrappers such as `env`, `sudo` and `timeout`. `--drift` watches `unwrap` and its tables, but no text in the alphabets uses a wrapper.
- The other hard blocks. Secret reads, pushes to main, the merge API and wiping home use the same segments, but only the `gh pr merge 1` canary runs.
- The layers around `hard_block`: the plugin's `pre_tool_call`, the owner-turn logic, and anything Hermes does to a command before bash gets it. `hermes/e2e/guard_scenario.py` covers the plugin.
- Other shells and bash versions: zsh, dash, and the macOS `/bin/bash` 3.2.
- The merge gate. The read-then-merge order of `Kit.land` in `hermes/bin/faker_kit.py` and the owner-approval records in `hermes/lib/faker_kit_owner_turn.py` are other targets for a formal check, and none exists yet.

A pass covers the bound, not every input.

## Covered code and sync

File: `hermes/plugin/faker_kit_guard/policy.py`.

| Definition | Role |
|---|---|
| `hard_block`, `terminal_problem` | the entry the check calls, and the order of checks it runs |
| `shell_commands`, `scan_shell`, `arithmetic_closer` | split text into command segments, skip heredoc bodies, track quotes, comments and arithmetic |
| `shell_words`, `split_words`, `without_keywords`, `unwrap`, `shell_parts` | turn a segment into the words `command_problem` reads, looking through `env`, `sudo`, `timeout` and shell wrappers |
| `shell_tokens`, `shell_inputs`, `piped_shell` | refuse shells fed from a pipe or here-string |
| `command_problem` | the `gh pr merge` rule |
| `HEREDOC_OPERATOR`, `SHELL_FED_LINE`, `ENV_ASSIGN`, `SHELL_KEYWORDS`, `SHELLS`, `ASSIGNMENT_TARGET`, `WRAPPERS`, `WRAPPER_VALUE_OPTIONS`, `MAX_WRAPPERS`, `UNCHECKABLE`, `SHELL_VALUE_OPTIONS`, `FISH_VALUE_OPTIONS` | the patterns and tables those functions use |

Sync commit: `2e0bf4b99db722e8889e2b4a0d4c8f7682f0d93f`

That is the commit that fixed the comment and backslash findings in `scan_shell`, on top of main after pull request 234. Move it forward only after you confirm that this check still describes the code at the new commit:

1. Run `--drift` and read every changed definition.
2. Run the check and `--variants`.
3. Update `baseline.json` only for a finding you report or a `FIXED` text you confirmed by hand.
4. Replace the commit above.

## Prove it can fail

```bash
python3 .agents/skills/verify-guard-shell-scanner/scanner-check.py --variants
```

It applies each edit in `variants.json` to a throwaway copy of `policy.py` and requires the check to fail with a `NEW` text that contains the fragment that variant expects. Five variants: PR 228's arithmetic test removed, `;` dropped from the separators, a comment allowed inside a word, a double quote that never closes, and the `<<<` and `<<` branches swapped. If a variant reports that its edit no longer applies, the code moved. Re-derive the edit from the current code and do not delete it to make the run pass.

The alphabets, bounds and covered list sit at the top of `scanner-check.py`. After changing one, review the findings and run `--update-baseline`.
