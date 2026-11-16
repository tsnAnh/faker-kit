from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import guard_scenario

GATE = "faker-kit-babysit-gate.py"
GATES = {"faker-kit-babysit": GATE, "faker-kit-chief-news": "faker-kit-chief-news-gate.py"}
LIBRARY = "faker_kit_gate.py"
LIBRARIES = (LIBRARY, "faker_kit_gate_news.py", "faker_kit_cron_list.py")
QUIET = '{"wakeAgent": false}'
RETIRED = "faker-kit-incidents"
KIT_SCRIPT = "\nKIT_OWNED = True\nprint('gate')\n"
HERMES_PROMPT_SCAN = [
    r"ignore\s+(?:\w+\s+)*(?:previous|all|above|prior)\s+(?:\w+\s+)*instructions",
    r"do\s+not\s+tell\s+the\s+user",
    r"system\s+prompt\s+override",
    r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)",
    r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|id_rsa|id_ed25519|id_ecdsa)",
    r"authorized_keys",
    r"/etc/sudoers|visudo",
    r"rm\s+-rf\s+/",
]
SYSTEM_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


class Run(Protocol):
    def gh_now(self) -> dict: ...
    def update_gh(self, change: Callable[[dict], object]) -> None: ...
    def step(self, name: str, ok: bool, evidence: str) -> None: ...


def wakes(stdout: str) -> bool:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return True
    try:
        gate = json.loads(lines[-1].strip())
    except ValueError:
        return True
    return not isinstance(gate, dict) or gate.get("wakeAgent", True) is not False


def scan_hits(text: str, patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def gate_scenario(run: Run, hermes: Path, bot: str, bootstrap: str, install: Callable[[], subprocess.CompletedProcess[str]], env: dict[str, str], work: Path) -> None:
    profile = hermes / "profiles" / bot
    gate = profile / "scripts" / GATE
    library = profile / "scripts" / LIBRARY
    shipped = [profile / "scripts" / name for name in (*GATES.values(), *LIBRARIES)]
    jobs_file = profile / "jobs.json"
    wrapper = str(hermes / "bin" / "faker-kit")

    def jobs() -> dict[str, dict]:
        return {job["name"]: job for job in json.loads(jobs_file.read_text())}

    def edit_jobs(change: Callable[[dict[str, dict]], None]) -> None:
        by_name = jobs()
        change(by_name)
        jobs_file.write_text(json.dumps(list(by_name.values()), sort_keys=True))

    def text_of(done: subprocess.CompletedProcess[str]) -> str:
        return (done.stdout + done.stderr).strip()[-200:]

    attached = {name: job.get("script") for name, job in jobs().items()}
    texts = [path.read_text() if path.is_file() else "" for path in shipped]
    library_text = library.read_text() if library.is_file() else ""
    run.step("install writes the babysit gate and the two modules it shares into the bot's scripts folder, with the wrapper, profile, kit home, and PATH baked in, and attaches the gate to the job",
             attached == GATES and all("\nKIT_OWNED = True\n" in text and '"@' not in text for text in texts)
             and wrapper in library_text and str(profile) in library_text and json.dumps(env["FAKER_KIT_HOME"]) in library_text, json.dumps(attached, sort_keys=True))

    ids = {name: job["id"] for name, job in jobs().items()}
    edit_jobs(lambda by_name: [job.__setitem__("script", None) for job in by_name.values()])
    for path in shipped:
        path.unlink()
    again = install()
    after = jobs()
    run.step("installing over an existing job without its gate edits it in place: the gate and its modules are written back, attached to the job, and the job keeps its id",
             again.returncode == 0 and all(path.is_file() for path in shipped) and {n: j["id"] for n, j in after.items()} == ids
             and {n: j.get("script") for n, j in after.items()} == GATES and all(j["enabled"] for j in after.values()), text_of(again))

    def shipped_state() -> list[tuple[bytes, int]]:
        return [(path.read_bytes(), path.stat().st_mtime_ns) for path in shipped]

    kept = (shipped_state(), jobs_file.read_bytes())
    install()
    run.step("installing again rewrites neither the gate, its modules, nor the job", (shipped_state(), jobs_file.read_bytes()) == kept, "gates and jobs unchanged")

    edit_jobs(lambda by_name: by_name["faker-kit-babysit"].update({"prompt": "stale prompt", "schedule": "every 99m"}))
    blockers: dict[str, Callable[[], None]] = {
        "a foreign file": lambda: gate.write_text("print('not the kit')\n"),
        "a directory": lambda: gate.mkdir(),
        "a symlink": lambda: gate.symlink_to(work / "elsewhere.py"),
    }
    for label, place in blockers.items():
        gate.unlink(missing_ok=True)
        place()
        before = jobs_file.read_bytes()
        refused = install()
        blocked_text = refused.stdout + refused.stderr
        run.step(f"{label} at the gate path is refused as not written by the kit, before any cron job is created or edited",
                 refused.returncode != 0 and "was not written by the kit" in blocked_text and "Traceback" not in blocked_text and jobs_file.read_bytes() == before
                 and jobs()["faker-kit-babysit"]["prompt"] == "stale prompt", text_of(refused))
        if gate.is_dir() and not gate.is_symlink():
            gate.rmdir()
        else:
            gate.unlink()
    library.unlink(missing_ok=True)
    library.write_text("print('not the kit')\n")
    before = jobs_file.read_bytes()
    refused = install()
    blocked_text = refused.stdout + refused.stderr
    run.step("a foreign file at the shared module's path is refused as not written by the kit, before any cron job is created or edited",
             refused.returncode != 0 and "was not written by the kit" in blocked_text and "Traceback" not in blocked_text and jobs_file.read_bytes() == before
             and jobs()["faker-kit-babysit"]["prompt"] == "stale prompt" and library.read_text() == "print('not the kit')\n", text_of(refused))
    library.unlink()
    restored = install()
    run.step("with the blocker moved aside, install writes the gate and its module again and edits the jobs",
             restored.returncode == 0 and all("\nKIT_OWNED = True\n" in path.read_text() for path in (gate, library))
             and jobs()["faker-kit-babysit"]["prompt"] != "stale prompt", text_of(restored))

    spec = Path(env["FAKER_KIT_HOME"]) / "kit" / "hermes" / "cron" / "project-jobs.md"
    original = spec.read_text()

    def add_project() -> subprocess.CompletedProcess[str]:
        return subprocess.run([wrapper, "add-project", run.gh_now()["repo"], "--bot", bot, "--project", "shop"], env={**env, "HERMES_HOME": str(hermes / "profiles" / bootstrap)},
                              capture_output=True, text=True, timeout=300, check=False)

    spec.write_text(original.replace("Script: `faker-kit-babysit-gate.py`\n", ""))
    cleared = add_project()
    after = jobs()
    run.step("a job whose spec has no Script but that carries a kit gate gets the gate detached on install",
             cleared.returncode == 0 and not after["faker-kit-babysit"].get("script"),
             json.dumps({n: j.get("script") for n, j in after.items()}) + text_of(cleared)[-80:])
    kept = jobs_file.read_bytes()
    add_project()
    run.step("running add-project again after the detach changes no job", jobs_file.read_bytes() == kept, "jobs unchanged")
    spec.write_text(original)
    reattached = add_project()
    run.step("putting the Script line back attaches the babysit gate again, in place", reattached.returncode == 0 and {n: j.get("script") for n, j in jobs().items()} == GATES, text_of(reattached))

    legacy = {"id": "legacy-incidents", "name": RETIRED, "schedule": "every 10m", "prompt": "old", "skill": "dispatch", "deliver": f"bot-chat:{bot}", "script": f"{RETIRED}-gate.py", "enabled": True}
    mine = {**legacy, "id": "mine", "name": "my-own-job", "script": None}
    scripts = profile / "scripts"
    state_dir = Path(env["FAKER_KIT_HOME"]) / "gates" / bot
    foreign_script = scripts / "faker-kit-other-gate.py"
    edit_jobs(lambda by_name: by_name.update({RETIRED: legacy, "my-own-job": mine}))
    (scripts / f"{RETIRED}-gate.py").write_text(KIT_SCRIPT)
    foreign_script.write_text("print('written by someone else')\n")
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{RETIRED}.json").write_text("{}")
    foreign_job = jobs()["my-own-job"]
    removed = add_project()
    names = sorted(jobs())
    run.step("add-project removes the retired faker-kit-incidents job, its kit-owned gate script, and its gate state, and keeps the babysit job, a job the kit does not name, and a foreign script",
             removed.returncode == 0 and names == ["faker-kit-babysit", "faker-kit-chief-news", "my-own-job"] and jobs()["my-own-job"] == foreign_job
             and not (scripts / f"{RETIRED}-gate.py").exists() and not (state_dir / f"{RETIRED}.json").exists() and foreign_script.is_file()
             and (scripts / GATE).is_file(), json.dumps(names) + text_of(removed)[-80:])
    kept = (jobs_file.read_bytes(), sorted(path.name for path in scripts.iterdir()))
    settled = add_project()
    run.step("running add-project again after the removal changes no job and no script",
             settled.returncode == 0 and (jobs_file.read_bytes(), sorted(path.name for path in scripts.iterdir())) == kept, text_of(settled))
    foreign_script.unlink()
    edit_jobs(lambda by_name: by_name.pop("my-own-job"))

    prompts = " ".join(job["prompt"] for job in jobs().values())
    run.step("no scheduled job prompt names the incidents job or tells the agent to run an incidents check", "incident" not in prompts.lower(), prompts[:120])

    env_log = work / "gate-bin" / "env.log"

    def tick(extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(gate)], env={"PATH": "/usr/bin:/bin", **(extra or {})}, capture_output=True, text=True, timeout=120, check=False)

    shutil.rmtree(state_dir.parent, ignore_errors=True)
    moved = tick({"HERMES_HOME": str(work / "no-such-profile")})
    run.step("the gate runs the command in the HERMES_HOME Hermes gives it, not the one baked at install: a profile that does not exist fails the tick",
             moved.returncode != 0 and moved.stdout == "", moved.stderr.strip()[-200:])
    env_log.unlink(missing_ok=True)
    runtime = tick({"HOME": str(work / "profile-home"), "PATH": ""})
    logged = env_log.read_text().splitlines() if env_log.is_file() else []
    run.step("the gate resolves HOME from the account database, not its environment, and keeps the system directories on PATH",
             runtime.returncode == 0 and logged[:1] == [pwd.getpwuid(os.getuid()).pw_dir] and all(d in (logged[1] if len(logged) > 1 else "").split(":") for d in SYSTEM_DIRS),
             " | ".join(logged))
    shutil.rmtree(state_dir.parent, ignore_errors=True)

    job_prompts = [job["prompt"] for job in jobs().values()]
    skill = (guard_scenario.HERMES / "skills" / "dispatch" / "SKILL.md").read_text()
    prompt_hits = [hit for prompt in job_prompts for hit in scan_hits(prompt, HERMES_PROMPT_SCAN)]
    assembled_hits = scan_hits(f"{skill}\n{runtime.stdout}", HERMES_PROMPT_SCAN[:4])
    run.step("copy-checked against hermes-agent's cronjob_prompt_scan patterns: every job prompt, and the dispatch skill with the gate's output, pass",
             prompt_hits == [] and assembled_hits == [] and scan_hits("ignore all previous instructions", HERMES_PROMPT_SCAN) != [],
             f"prompt {len(prompt_hits)}, assembled {len(assembled_hits)}, a hostile phrase matches the copied patterns")
