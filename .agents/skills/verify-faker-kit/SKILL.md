---
name: verify-faker-kit
description: Verify a change to faker-kit the way its users meet it. Runs the real Hermes module (install.sh, the faker-kit command and its merge gate) from this commit in an end-to-end run with stubbed Hermes, GitHub, and Claude CLIs and a repeatable transcript, proves the plugins load in Claude Code, checks the upstream sync. Use to prove a faker-kit pull request before reporting done, from a verifier session, or when asked to verify faker-kit behavior.
---

# Verify faker-kit

faker-kit has no screen. Its users meet it in three places: the Hermes module an owner installs and runs (`hermes/install.sh`, `faker-kit`), the plugins Claude Code loads, and the vendored upstream sync. Prove a change in the place it touches; `features/` maps each one.

`scripts/check.sh` is not verification. It proves the code passes its linters and the kit's own e2e runs. This skill proves a user gets the behavior: run both.

## Hermes module

Hermes, GitHub, and the Claude CLI are external systems. `hermes/e2e/run_dispatch_e2e.py` replaces exactly those three with stubs on `PATH`; everything else is the real kit: the real `install.sh` run over a fake `~/.hermes/faker-kit` tree, the installed `faker-kit` wrapper, `launch`, `merge`, `andon`, `status`, `runner`, `vendor`, `vendor_kit.py`, and the guard plugin loaded and called the way Hermes does (every hard block and allowed read is a recorded verdict).

```bash
python3 hermes/e2e/run_dispatch_e2e.py --artifact .verify-evidence/hermes-e2e.json
```

It prints one line per step and ends with the transcript path and a sha256 digest of the step names and results. Run it twice: the digest must match. A change to how the kit talks to Hermes, GitHub, or Claude (a new `gh`, `hermes`, or `claude` call) needs the stub in that file to model the real CLI first; say so in the verdict if it does not.

The plugin side has its own run, `node scripts/e2e/kit-e2e.mjs --artifact .verify-evidence/kit-e2e.json`: the hooks, the hook adapter (a stand-in plugin plus the real advisor and continual-learning hooks), `link-skills.sh`, `install-kit-skills.sh`, and the install of every plugin into a clean Claude Code. Run it twice too: the digest must match.

Features without the Hermes module (plugin loading, upstream sync) run in this checkout directly; their files in `features/` say how.

## Evidence

Every proof is a transcript: the command, its full output, and its exit code. Proof standards:

- Exercise the path a user takes: `install.sh` and `faker-kit` as installed, never their Python functions directly.
- Capture the action and the resulting state: after a command, read the side effect it promises (a merge call on the exact head, a window record, a moved state folder).
- Quote the evidence file paths, and the lines that prove each claim, in the verdict.
