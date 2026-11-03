#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import itertools
import json
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
BASELINE = SKILL_DIR / "baseline.json"
VARIANTS = SKILL_DIR / "variants.json"
GUARD = Path("hermes/plugin/faker_kit_guard/policy.py")
BASH_PIN = "5.2"
CANARY = "gh pr merge 1"
RAN_MARK = "pr merge 1"
BASH_TIMEOUT_SECONDS = 10
STUB = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CANARY_LOG\"\n"
ATTACH = {"next-line": "\n", "same-line": ";"}
CORE = ("#", "\\", "\n", " ", "'", '"', "<<", "$((", "))", "((", "X", "$(", ")")
WIDE = (*CORE, "$[", "]", "${", "}", "[", "=", "a", "`", "<<<", "&&", "||", "|", "&", "-", "\t")
TIERS = (("core", CORE, {"next-line": 5, "same-line": 4}), ("wide", WIDE, {"next-line": 3, "same-line": 3}))
STEM_ATOMS = 2
SHOWN = 8
LISTED = 20
COVERED = (
    "hard_block", "terminal_problem", "shell_commands", "scan_shell", "arithmetic_closer", "shell_words", "split_words", "without_keywords", "unwrap", "shell_parts",
    "shell_tokens", "shell_inputs", "piped_shell", "command_problem", "HEREDOC_OPERATOR", "SHELL_FED_LINE", "ENV_ASSIGN", "SHELL_KEYWORDS", "SHELLS", "ASSIGNMENT_TARGET",
    "WRAPPERS", "WRAPPER_VALUE_OPTIONS", "MAX_WRAPPERS", "UNCHECKABLE", "SHELL_VALUE_OPTIONS", "FISH_VALUE_OPTIONS",
)
SYNC_LINE = re.compile(r"^Sync commit: `([0-9a-f]{40})`", re.M)

Atoms = tuple[str, ...]
Task = tuple[int, int, tuple[int, ...]]
Outcome = tuple[str, dict[str, list[int]], list[tuple[str, Atoms]], int, int]


@dataclass(frozen=True)
class Finding:
    failing: frozenset[Atoms]

    @property
    def texts(self) -> frozenset[str]:
        return frozenset("".join(atoms) for atoms in self.failing)

    @property
    def violations(self) -> int:
        return len(self.texts)

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(sorted(self.texts), ensure_ascii=True).encode()).hexdigest()

    @property
    def minimal(self) -> list[Atoms]:
        kept = sorted((atoms for atoms in self.failing if not any(atoms[:i] + atoms[i + 1 :] in self.failing for i in range(len(atoms)))), key=lambda atoms: (len(atoms), atoms))
        unique: dict[str, Atoms] = {}
        for atoms in kept:
            unique.setdefault("".join(atoms), atoms)
        return list(unique.values())

    def record(self) -> dict[str, object]:
        return {"violations": self.violations, "digest": self.digest, "minimal": [list(atoms) for atoms in self.minimal]}


@dataclass(frozen=True)
class Result:
    totals: dict[str, dict[str, int]]
    findings: dict[str, Finding]
    raised: int
    timeouts: int
    seconds: float

    def record(self) -> dict[str, object]:
        return {"totals": self.totals, "findings": {key: finding.record() for key, finding in self.findings.items()}, "raised": self.raised, "timeouts": self.timeouts, "seconds": self.seconds}


class Probe:
    def __init__(self, root: Path, scratch: Path, bash: str) -> None:
        spec = importlib.util.spec_from_file_location("guard_policy", root / GUARD)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {root / GUARD}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["guard_policy"] = module
        spec.loader.exec_module(module)
        self.sandbox = Path(tempfile.mkdtemp(dir=scratch))
        stubs = self.sandbox / "stubs"
        stubs.mkdir()
        (stubs / "gh").write_text(STUB)
        (stubs / "gh").chmod(0o755)
        self.log = self.sandbox / "ran.log"
        self.env = {"PATH": str(stubs), "CANARY_LOG": str(self.log), "HOME": str(self.sandbox), "LC_ALL": "C"}
        self.policy = module.Policy(deny=(), untrusted_readers=(), read_only_tools=frozenset(), trusted_authors=frozenset(), max_in_flight_total=1, ask_owner=False, bots={}, home=self.sandbox)
        self.hard_block = module.hard_block
        self.bash = bash
        self.raised = 0
        self.timeouts = 0

    def refused(self, text: str) -> bool:
        try:
            return self.hard_block(self.policy, "terminal", {"command": text}) is not None
        except Exception:
            self.raised += 1
            return True

    def ran_canary(self, text: str) -> bool:
        self.log.write_text("")
        try:
            subprocess.run([self.bash, "-c", text], cwd=self.sandbox, env=self.env, stdin=subprocess.DEVNULL, capture_output=True, timeout=BASH_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            self.timeouts += 1
            return False
        return RAN_MARK in self.log.read_text()


PROBE: Probe | None = None


def init_worker(root: str, scratch: str, bash: str) -> None:
    global PROBE
    PROBE = Probe(Path(root), Path(scratch), bash)


def run_task(task: Task) -> Outcome:
    assert PROBE is not None
    tier_index, length, stem = task
    name, atoms, bounds = TIERS[tier_index]
    active = [attach for attach in ATTACH if length <= bounds[attach]]
    counts = {attach: [0, 0, 0] for attach in active}
    found: list[tuple[str, Atoms]] = []
    raised, timeouts = PROBE.raised, PROBE.timeouts
    for tail in itertools.product(atoms, repeat=length - len(stem)):
        prefix = (*(atoms[i] for i in stem), *tail)
        for attach in active:
            text = "".join(prefix) + ATTACH[attach] + CANARY
            counts[attach][0] += 1
            if PROBE.refused(text):
                counts[attach][1] += 1
            elif PROBE.ran_canary(text):
                counts[attach][2] += 1
                found.append((attach, prefix))
    return name, counts, found, PROBE.raised - raised, PROBE.timeouts - timeouts


def bound(tier: str, attach: str, limit: int | None) -> int:
    bounds = next(bounds for name, _, bounds in TIERS if name == tier)
    return min(bounds[attach], limit) if limit is not None else bounds[attach]


def tasks(limit: int | None) -> Iterator[Task]:
    for tier_index, (name, atoms, _) in enumerate(TIERS):
        for length in range(max(bound(name, attach, limit) for attach in ATTACH) + 1):
            for stem in itertools.product(range(len(atoms)), repeat=min(length, STEM_ATOMS)):
                yield tier_index, length, stem


def shown(text: str, key: str) -> str:
    return repr(text + ATTACH[key.split("/")[1]] + CANARY)


def validate(root: Path, bash: str) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="scanner-check-"))
    try:
        probe = Probe(root, scratch, bash)
        if not probe.refused(CANARY):
            raise RuntimeError(f"the guard no longer refuses a plain {CANARY}, so the check would prove nothing")
        if probe.refused("echo hi"):
            raise RuntimeError("the guard refuses a harmless command, so the check cannot tell a refusal from a miss")
        if not probe.ran_canary(CANARY) or probe.ran_canary("echo hi"):
            raise RuntimeError("the stub gh did not log exactly the plain canary, so bash is not an oracle here")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def bash_version(bash: str) -> tuple[str, str]:
    script = 'printf "%s.%s\\n%s" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}" "$BASH_VERSION"'
    done = subprocess.run([bash, "-c", script], capture_output=True, text=True, check=False)
    short, _, full = done.stdout.partition("\n")
    return short, full


def run_check(root: Path, bash: str, jobs: int, limit: int | None) -> Result:
    started = time.perf_counter()
    totals: dict[str, Counter[str]] = {}
    violators: dict[tuple[str, str], set[Atoms]] = {}
    raised = timeouts = 0
    scratch = Path(tempfile.mkdtemp(prefix="scanner-check-"))
    try:
        with multiprocessing.get_context("spawn").Pool(jobs, init_worker, (str(root), str(scratch), bash)) as pool:
            for name, counts, found, task_raised, task_timeouts in pool.imap_unordered(run_task, list(tasks(limit)), chunksize=4):
                raised += task_raised
                timeouts += task_timeouts
                for attach, (texts, refused, ran) in counts.items():
                    totals.setdefault(f"{name}/{attach}", Counter()).update(texts=texts, refused=refused, ran_canary_unrefused=ran)
                for attach, atoms in found:
                    violators.setdefault((name, attach), set()).add(atoms)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    findings = {key: Finding(frozenset(violators.get((key.split("/")[0], key.split("/")[1]), set()))) for key in sorted(totals)}
    return Result({key: dict(counter) for key, counter in sorted(totals.items())}, findings, raised, timeouts, round(time.perf_counter() - started, 1))


def guard_digest(root: Path) -> str:
    return hashlib.sha256((root / GUARD).read_bytes()).hexdigest()


def shortest_first(entry: tuple[str, str]) -> tuple[int, str, str]:
    return len(entry[1]), entry[1], entry[0]


def compare(result: Result, limit: int | None) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    base = json.loads(BASELINE.read_text())["findings"] if BASELINE.is_file() else {}
    new: list[tuple[str, str]] = []
    fixed: list[tuple[str, str]] = []
    changed: list[str] = []
    for key, now in result.findings.items():
        name, attach = key.split("/")
        cap = bound(name, attach, limit)
        before = {"".join(atoms) for atoms in base.get(key, {}).get("minimal", []) if len(atoms) <= cap}
        current = {"".join(atoms) for atoms in now.minimal}
        new += [(key, text) for text in current - before]
        fixed += [(key, text) for text in before - now.texts]
        if limit is None and current == before and base.get(key, {}).get("digest") != now.digest:
            changed.append(key)
    return sorted(new, key=shortest_first), sorted(fixed, key=shortest_first), changed


def listed(label: str, entries: list[tuple[str, str]]) -> None:
    for key, text in entries[:LISTED]:
        print(f"{label} {key}: {shown(text, key)}")
    if len(entries) > LISTED:
        print(f"{label} ... and {len(entries) - LISTED} more, longer than the ones above")


def report(root: Path, bash: str, full: str, result: Result, limit: int | None) -> tuple[int, dict[str, object]]:
    new, fixed, changed = compare(result, limit)
    print(f"guard: {root / GUARD}  sha256 {guard_digest(root)[:16]}")
    print(f"bash: {full}  python: {sys.version.split()[0]}  cores: {os.cpu_count()}")
    for key, count in result.totals.items():
        print(f"{key}: {count['texts']} sequences, {count['refused']} refused, {count['ran_canary_unrefused']} ran the canary unrefused")
    for key, finding in result.findings.items():
        if finding.violations:
            print(f"{key}: {finding.violations} failing texts; shortest classes:")
            for atoms in finding.minimal[:SHOWN]:
                print(f"  {shown(''.join(atoms), key)}")
    print(f"guard raised {result.raised} times (the plugin refuses on a raise); bash timeouts {result.timeouts}; {result.seconds}s")
    identity = {"guard": guard_digest(root), "bash": bash_version(bash)[0], "findings": {key: finding.record() for key, finding in result.findings.items()}}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    status = 0
    if result.timeouts:
        print("INCONCLUSIVE: bash timed out on some texts")
        status = 2
    elif new or fixed or changed:
        listed("NEW", new)
        listed("FIXED", fixed)
        for key in changed:
            print(f"CHANGED {key}: the set of failing texts differs although its shortest members match baseline.json")
        print("FAIL: the failing texts differ from baseline.json" + ("; for FIXED only, confirm and run with --update-baseline" if not (new or changed) else ""))
        status = 1
    else:
        open_total = sum(finding.violations for finding in result.findings.values())
        print(f"UNCHANGED: {open_total} failing texts, all known findings in baseline.json (see SKILL.md); nothing new" if open_total else "PASS: no failing text")
    print(f"digest {digest}")
    return status, {"digest": digest, "limit": limit, "new": new, "fixed": fixed, "changed": changed, "result": result.record()}


def write_baseline(result: Result) -> None:
    tiers = {name: {"atoms": list(atoms), "max_atoms": bounds} for name, atoms, bounds in TIERS}
    record = {"bash": BASH_PIN, "canary": CANARY, "tiers": tiers, "findings": {key: finding.record() for key, finding in result.findings.items()}}
    BASELINE.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"wrote {BASELINE}")


def check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bash = shutil.which(args.bash or "bash") or ""
    if not bash or not (root / GUARD).is_file():
        print(f"cannot find bash or {root / GUARD}", file=sys.stderr)
        return 2
    if args.max_atoms is not None and args.max_atoms < 1:
        print("--max-atoms takes 1 or more", file=sys.stderr)
        return 2
    if args.update_baseline and args.max_atoms is not None:
        print("--update-baseline records the full bound; drop --max-atoms", file=sys.stderr)
        return 2
    short, full = bash_version(bash)
    if short != BASH_PIN:
        print(f"this check is pinned to bash {BASH_PIN}.x; {bash} is {full or 'unreadable'}. Pass --bash PATH to a bash {BASH_PIN}.", file=sys.stderr)
        return 2
    validate(root, bash)
    result = run_check(root, bash, args.jobs, args.max_atoms)
    if args.update_baseline:
        if result.timeouts:
            print("bash timed out on some texts, so the baseline would drop findings; not written", file=sys.stderr)
            return 2
        write_baseline(result)
        return 0
    status, transcript = report(root, bash, full, result, args.max_atoms)
    if args.artifact:
        Path(args.artifact).parent.mkdir(parents=True, exist_ok=True)
        Path(args.artifact).write_text(json.dumps(transcript, indent=1, sort_keys=True) + "\n")
        print(f"transcript {args.artifact}")
    return status


def variants(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    source = (root / GUARD).read_text()
    failures = 0
    for variant in json.loads(VARIANTS.read_text()):
        if source.count(variant["old"]) != 1:
            print(f"{variant['name']}: the edit no longer applies to {GUARD}; re-derive it from the current code")
            return 2
        scratch = Path(tempfile.mkdtemp(prefix="scanner-variant-"))
        try:
            (scratch / GUARD).parent.mkdir(parents=True)
            (scratch / GUARD).write_text(source.replace(variant["old"], variant["new"]))
            command = [sys.executable, str(Path(__file__).resolve()), "--root", str(scratch), "--max-atoms", str(variant["max_atoms"]), "--jobs", str(args.jobs)]
            if args.bash:
                command += ["--bash", args.bash]
            done = subprocess.run(command, capture_output=True, text=True, check=False)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        found = [line for line in done.stdout.splitlines() if line.startswith("NEW ")]
        caught = done.returncode == 1 and any(variant["expect"] in line for line in found)
        print(f"{'CAUGHT' if caught else 'MISSED'} {variant['name']}: {variant['breaks']}")
        print("\n".join(f"    {line}" for line in [*found[:3], *[line for line in done.stdout.splitlines() if line.startswith("FAIL")]]))
        failures += not caught
    print("every broken variant is caught" if not failures else f"{failures} broken variants were not caught")
    return 1 if failures else 0


def definitions(source: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            found[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign):
            found.update({target.id: ast.dump(node.value) for target in node.targets if isinstance(target, ast.Name)})
    return found


def drift(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    recorded = SYNC_LINE.search((SKILL_DIR / "SKILL.md").read_text())
    if recorded is None:
        print("SKILL.md has no 'Sync commit: `<40 hex>`' line", file=sys.stderr)
        return 2
    sync = recorded.group(1)
    old = subprocess.run(["git", "-C", str(root), "show", f"{sync}:{GUARD}"], capture_output=True, text=True, check=False)
    if old.returncode != 0:
        print(f"cannot read {GUARD} at {sync} in {root}; use a git checkout of this repository and run git fetch origin main", file=sys.stderr)
        return 2
    before, after = definitions(old.stdout), definitions((root / GUARD).read_text())
    changed = [name for name in COVERED if before.get(name) != after.get(name)]
    for name in changed:
        print(f"CHANGED {name}: {'removed' if name not in after else 'new' if name not in before else 'edited'} since {sync[:12]}")
    print(f"{len(changed)} of {len(COVERED)} covered definitions changed since the sync commit" + ("; run the check and decide whether the model still describes them" if changed else ""))
    return 1 if changed else 0


def stop(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that no shell text hides gh pr merge from the guard's hard blocks, against real bash.")
    parser.add_argument("--root", default=str(SKILL_DIR.parents[2]), help="repository whose guard to check")
    parser.add_argument("--bash", help="bash 5.2 to use as the oracle")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--max-atoms", type=int, help="shorter bound for a quick run; compares only the shortest failing texts")
    parser.add_argument("--artifact", help="write the transcript here")
    parser.add_argument("--update-baseline", action="store_true", help="record this run's failing texts as the known findings")
    parser.add_argument("--variants", action="store_true", help="apply each broken variant to a throwaway copy of the guard and require the check to fail")
    parser.add_argument("--drift", action="store_true", help="list covered definitions that differ from the sync commit recorded in SKILL.md")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    try:
        return variants(args) if args.variants else drift(args) if args.drift else check(args)
    except Exception as err:
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
