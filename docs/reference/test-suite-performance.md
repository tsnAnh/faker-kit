# Hermes test suite performance

`python3 -m pytest hermes -q -n auto -p no:cacheprovider` took about 284 s on a 4-CPU machine. It now takes about 158 s. The set of tests is the same, and no test or assertion changed.

## What limited it

The suite does about 470 worker-seconds of test work, and 320 of them are in `hermes/tests/test_admin.py`. Each of its 89 tests runs `hermes/install.sh` in its `ready` fixture (111 s of setup in total), then starts `team-kit-admin` and the fake `hermes` and `gh` as subprocesses. That makes them 2 to 17 s each.

1. **One worker got most of test_admin.py.** xdist's default `load` scheduler first gives each worker a chunk of consecutive tests, a quarter of its share. Here that is 1191 / 4 / 4 = 74 tests. `test_admin.py` collects first, so worker 0 got 74 of its 89 tests. Under `-n auto`, three workers finished in 70 to 82 s and worker 0 ran for 250 s. The gap shows in the CPU too. The run used about 450 CPU-seconds over 284 s of wall time on 4 CPUs.
2. **pytest deleted an earlier run at exit.** pytest keeps the last three runs' temporary directories and deletes the oldest when the session finishes. A full run leaves 678 MB there, mostly the kit copies the install tests make. Deleting it cold took up to 22 s at the end of a run. It took 21 s at the end of a one-test run, and 65 s at the end of a serial run (462 s with no earlier run against 528 s in steady state).

## What changed

- `hermes/tests/conftest.py` implements xdist's `pytest_xdist_make_scheduler` hook. When xdist would use `load`, it uses `worksteal` instead, so an idle worker takes queued tests from a busy one. An explicit `--dist loadfile` or similar still wins. Without xdist the hook never runs, so `python -m pytest hermes` still works where only pytest is installed (CI's `validate` job).

No test was skipped, deleted, or given a timeout. Isolation is unchanged. Each test still gets its own `tmp_path` and its own install, and the suite passes serially and in parallel.

## Measurements

Commits are `e616be5` (baseline) and the baseline plus the conftest hook (`sched`, the change as shipped). `cand` (`409ebcd`) also set `tmp_path_retention_policy = "failed"`. That setting was dropped before shipping (see Not changed here), and its rows are kept to show what it bought. The environment was Linux 6.18 x86_64, Intel Xeon at 2.80 GHz, Python 3.13.16, pytest 9.1.1, and pytest-xdist 3.8.0. `nproc` is 4 and `sched_getaffinity` gives 4 CPUs. The cgroup (v1) has no CPU quota (`cpu.cfs_quota_us` is -1) and no memory limit, and the host has 15 GB. Each configuration ran with its own `TMPDIR`, preloaded with earlier runs' directories so that every run pays pytest's cleanup the way a working machine does. The one exception is the first serial run of each commit, which ran in an empty `TMPDIR`. Runs were sequential, with nothing else running.

`--collect-only -q` collects 1191 tests at both commits. Every run passed all 1191.

| Commit | Workers flag | Runs | Wall median (s) | Wall min to max (s) | User CPU median (s) | Sys CPU median (s) | Peak RSS median (MB) |
|---|---|---|---|---|---|---|---|
| base | `-n 0` | 1 | 462.4 | 462.4 to 462.4 | 352.2 | 89.5 | 107 |
| base | `-n 0`, steady state | 1 | 527.7 | 527.7 to 527.7 | 386.2 | 100.8 | 112 |
| cand | `-n 0` | 1 | 505.6 | 505.6 to 505.6 | 354.3 | 127.5 | 111 |
| base | `-n 2` | 3 | 339.6 | 337.4 to 354.3 | 352.6 | 91.3 | 244 |
| cand | `-n 2` | 3 | 286.8 | 271.0 to 291.5 | 388.8 | 144.2 | 251 |
| base | `-n 4` | 3 | 297.4 | 276.2 to 300.1 | 371.9 | 104.5 | 401 |
| cand | `-n 4` | 3 | 139.1 | 139.1 to 142.7 | 354.6 | 124.5 | 418 |
| base | `-n auto` | 3 | 283.7 | 283.2 to 291.6 | 365.6 | 94.9 | 398 |
| sched | `-n auto` | 3 | 157.8 | 150.8 to 158.1 | 362.2 | 102.2 | 421 |
| cand | `-n auto` | 3 | 142.3 | 136.5 to 148.5 | 358.7 | 129.4 | 417 |

`-n auto` started 4 workers. Wall, user, and sys time are for the pytest process and its children. Peak RSS is the largest sampled sum over the process tree. With 4 workers the work-stealing hook saves about 126 s, and the ranges do not overlap. A serial run never calls the hook, so the shipped change leaves it as it was. `-n 2` was measured only with both settings, where it gained 53 s. The retention setting saved about 15 s more under `-n auto`.

There is no default worker count in the configuration. `-n auto` follows the CPUs the process may use. On a 2-CPU runner it starts 2 workers, and the `-n 2` row is the expected result there. The run needs about 100 MB of memory per worker.

## Reproduce

```bash
python3 -m pytest hermes --collect-only -q -p no:cacheprovider | tail -1
python3 -m pytest hermes -q -n auto -p no:cacheprovider
python3 -m pytest hermes -q -n auto -p no:cacheprovider --durations=40   # where the time goes
python3 -m pytest hermes -q -n 0 -p no:cacheprovider
```

To compare, run the same commands at `e616be5`. Run each command three times in one `TMPDIR`, because a run pays the cleanup of the run three before it.

## Not changed here

- **CI runs the suite twice.** The `check` job runs it through `scripts/check.sh` (`-n auto`), and the `validate` job runs `pip install pytest pyyaml && python -m pytest hermes` serially, without xdist. Here that second run is 506 to 528 s. Removing that step from `.github/workflows/ci.yml` would save it on every push. The rest of `validate` (`sync-upstream.mjs --check`, `validate.mjs`, `gen-catalog.mjs --check`, `gen-site.mjs`, and both `bun test` lines) also repeats `check.sh`. Only the pstack and orchestrate tests are unique to it. Workflows are out of this change's scope, so this is a proposal.
- **The next limiter is the per-test install in test_admin.py.** Its `ready` fixture runs `install.sh` for each of the 89 tests, about 2 s each and 111 s of setup in total. The install writes absolute paths into wrappers and profile configs, so a cached install cannot be copied into each test's `tmp_path` as it stands. A test-only way to relocate it would be the next change to measure.
- **pytest's cleanup of earlier runs.** `tmp_path_retention_policy = "failed"` in `pyproject.toml` removes a passed test's `tmp_path` when it finishes, instead of deleting a whole run at exit, and saved a further 15 s under `-n auto`. One full run with it failed once at setup: the `kit` fixture's `git commit` exited 128 in `test_gh.py::test_tick_keeps_waiting_while_a_codex_task_runs_or_its_status_cannot_be_read[unknown]`. That did not happen in 27 other full runs. Under this policy pytest reuses a removed directory's numbered name for the next test with the same prefix. The default policy never reuses one. Whether the reuse caused the failure is unproven, so the setting is left out until it is.
