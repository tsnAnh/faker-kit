# Hermes test suite performance

`python3 -m pytest hermes -q -n auto -p no:cacheprovider` took a median 212 s on a 4-CPU machine. With the change it takes a median 106 s. The same 1277 tests run, and no test or assertion changed.

## What limited it

Most of the suite's time is in `hermes/tests/test_admin.py`. It collects 89 tests. 66 of them run `hermes/install.sh` in their `ready` fixture, about 2 s each, then start `team-kit-admin` and the fake `hermes` and `gh` as subprocesses. That makes them 2 to 17 s each. The other 23 are fast.

xdist's default `load` scheduler first gives each worker a chunk of consecutive tests, a quarter of its share. With 1277 tests and 4 workers that is 1277 // 4 // 4 = 79 tests. `test_admin.py` collects first, so worker 0 gets its first 79 tests and worker 1 the last 10. The other workers take fast tests and then sit idle. In a profile at `e616be5` (1191 tests, chunk 74), three workers finished in 70 to 82 s and worker 0 ran for 250 s.

## What changed

`hermes/tests/conftest.py` implements xdist's `pytest_xdist_make_scheduler` hook. When xdist would use `load`, it uses `worksteal` instead, so an idle worker takes queued tests from a busy one. That includes an explicit `--dist load`, which xdist cannot tell apart from the default. Any other `--dist`, such as `loadfile`, still wins. pytest loads this conftest at startup only when the command names `hermes`, `hermes/tests`, or a path under `hermes/tests`, or runs from `hermes/`. Every command here does. `pytest -n auto hermes/lib` keeps `load`. A bare `pytest -n auto` from the repository root keeps `load`. Without xdist the hook never runs, so `python -m pytest hermes` still works where only pytest is installed (CI's `validate` job).

No test was skipped, deleted, or given a timeout. Isolation is unchanged. Each test still gets its own `tmp_path` and its own install, and the suite passes serially and in parallel.

## Measurements

Baseline is `149ece0` (main). Candidate is `1bc4f6c`, which is `149ece0` plus the conftest hook and nothing else that runs. `pytest --collect-only -q` lists the same 1277 node IDs at both commits, and the diff of the two sorted lists is empty.

The environment was Linux 6.18 x86_64, Python 3.13.16, pytest 9.1.1, and pytest-xdist 3.8.0. `nproc` is 4. The cgroup is v1. `cpu.cfs_quota_us` is -1 (no CPU quota), the root `memory.limit_in_bytes` is unlimited, and the shell's memory cgroup is limited to 14343688192 bytes (13.4 GiB). The host has 15 GB.

Runs were sequential, alternating baseline and candidate, with nothing else running. Each ran in its own empty `TMPDIR`, removed afterwards. Wall, user, and sys time are for the pytest process and its children. Peak RSS is the largest sum over the process tree, sampled every 0.5 s. Workers is the number of xdist worker processes seen.

| Commit | Workers flag | Workers | Runs | Wall median (s) | Wall min to max (s) | User CPU median (s) | Sys CPU median (s) | Peak RSS median (MB) | Result |
|---|---|---|---|---|---|---|---|---|---|
| base `149ece0` | `-n auto` | 4 | 3 | 212.1 | 208.1 to 213.6 | 286.3 | 72.3 | 401 | 1277 passed each |
| cand `1bc4f6c` | `-n auto` | 4 | 3 | 105.6 | 105.5 to 108.7 | 287.2 | 71.6 | 416 | 1277 passed each |
| cand `1bc4f6c` | `-n 0` | 0 | 1 | 371.5 | 371.5 | 284.1 | 67.0 | 107 | 1277 passed |

The candidate saves 106.5 s of median wall time, half the run. The spread is at most 5.5 s within either side, and the ranges are about 100 s apart, so the gain is well beyond the variance. CPU time is the same on both sides, which fits the cause. The work did not shrink. It is shared across all four workers instead of queued on one. The serial run never calls the hook. It passing shows the tests do not rely on running in parallel.

There is no default worker count in the configuration. Without `psutil` installed, as here, `-n auto` follows the CPUs the process may use (`sched_getaffinity`). With `psutil` it counts physical cores. Neither follows a cgroup CPU quota, so set `PYTEST_XDIST_AUTO_NUM_WORKERS` on a quota-limited runner. On a 2-CPU machine it starts 2 workers, and work stealing still keeps both busy. The run needs about 100 MB of memory per worker.

### Earlier exploration at `e616be5`

Before main gained 86 tests (`test_rework.py`, `test_rework_recovery.py`, one in `test_guard.py`), the first measurements ran at `e616be5`, with 1191 tests at both that commit and the change. They ran in a `TMPDIR` preloaded with earlier runs, so each run also paid pytest's deletion of the run three before it. That is why their times are higher than the table above. Medians of three runs: baseline `-n auto` 283.7 s, change `-n auto` 157.8 s, baseline `-n 2` 339.6 s, baseline `-n 4` 297.4 s. A single serial baseline run took 462.4 s in an empty `TMPDIR` and 527.7 s in a preloaded one.

## Reproduce

```bash
python3 -m pytest hermes --collect-only -q -p no:cacheprovider | grep '::' | sort > tests.txt
python3 -m pytest hermes -q -n auto -p no:cacheprovider
python3 -m pytest hermes -q -n auto -p no:cacheprovider --durations=40   # where the time goes
python3 -m pytest hermes -q -n 0 -p no:cacheprovider
```

To compare, run the same commands at the baseline commit, each with a fresh `TMPDIR`.

## Not changed here

- **CI runs the suite twice.** The `check` job runs it through `scripts/check.sh` (`-n auto`), and the `validate` job runs `pip install pytest pyyaml && python -m pytest hermes` serially, without xdist. That second run is the serial row above, 371.5 s here. Removing that step from `.github/workflows/ci.yml` would save it on every push. The rest of `validate` (`sync-upstream.mjs --check`, `validate.mjs`, `gen-catalog.mjs --check`, `gen-site.mjs`, and both `bun test` lines) also repeats `check.sh`. Only the pstack and orchestrate tests are unique to it. Workflows are out of this change's scope, so this is a proposal.
- **The next limiter is the per-test install in test_admin.py.** Its `ready` fixture runs `install.sh` for each of the 66 tests that use it, about 111 s of setup in total at `e616be5`. The install writes absolute paths into wrappers and profile configs, so a cached install cannot be copied into each test's `tmp_path` as it stands. A test-only way to relocate it would be the next change to measure.
- **pytest's cleanup of earlier runs.** `tmp_path_retention_policy = "failed"` in `pyproject.toml` removes a passed test's `tmp_path` when it finishes, instead of deleting a whole run at exit. At `e616be5` it saved a further 15 s under `-n auto`. One full run with it failed once at setup: the `kit` fixture's `git commit` exited 128 in `test_gh.py::test_tick_keeps_waiting_while_a_codex_task_runs_or_its_status_cannot_be_read[unknown]`. That did not happen in 27 other full runs, and the failure was not reproduced. Under this policy pytest reuses a removed directory's numbered name for the next test with the same prefix. The default policy never reuses one. Whether the reuse caused the failure is unproven, so the setting is left out.
