from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

STALE = 20 * 60
FAKE_GIT = """#!/bin/sh
case "$*" in
  *acme__shop*fetch*)
    lock="$2/.git/index.lock"
    trap 'kill $! 2>/dev/null; rm -f "$lock"; exit 143' TERM
    : > "$lock"
    sleep 30 &
    wait
    ;;
  *) exec /usr/bin/git "$@" ;;
esac
"""
LSOF_BROKEN = "#!/bin/sh\nexit 2\n"
LSOF_QUIET = "#!/bin/sh\nexit 1\n"
LSOF_HOLDING = "#!/bin/sh\nprintf 'p4242\\nfcwd\\nn%s\\n' \"$E2E_HELD_CWD\"\nexit 0\n"


def origin_commit(origin: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(origin), "-c", "user.email=f@k.e", "-c", "user.name=fake", "commit", "-q", "--allow-empty", "-m", message], check=True)
    return subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def head_of(checkout: Path) -> str:
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def script(run: Run, name: str, text: str) -> None:
    path = run.bin / name
    path.write_text(text)
    path.chmod(0o755)


def age(lock: Path, seconds: int) -> None:
    lock.write_text("")
    then = time.time() - seconds
    os.utime(lock, (then, then))


def checkout_lock_scenario(run: Run, machine_yaml: str, shop_and_crm: object, bot: str) -> None:
    machine = run.team / "machine.yaml"
    machine.write_text(machine_yaml)
    shop_and_crm(run, [])  # type: ignore[operator]
    origin = Path(run.env()["E2E_ORIGIN"])
    shop = run.team / "checkouts" / "acme__shop" / "repo"
    crm = run.team / "checkouts" / "acme__crm" / "repo"

    def install(env: dict[str, str] | None = None) -> tuple[int, dict, str]:
        code, out, err = run.kit("install", "--config", str(machine), bot=None, env=env)
        try:
            parsed = json.loads(out)
        except ValueError:
            parsed = {}
        return code, parsed, err

    code, _, err = install()
    run.step("a first install clones the launch checkout of both projects", code == 0 and (shop / ".git").is_dir() and (crm / ".git").is_dir(), err.strip()[-200:])

    lock = shop / ".git" / "index.lock"
    head = origin_commit(origin, "one")
    age(lock, STALE)
    code, out, err = install()
    removed = [line for line in err.splitlines() if line.startswith("removed stale ")]
    run.step("an old index.lock nobody holds is removed with one log line that names the file and its age, and the checkout updates",
             code == 0 and not lock.exists() and len(removed) == 1 and str(lock) in removed[0] and int(removed[0].rsplit(" ", 1)[1].rstrip("s")) >= STALE
             and head_of(shop) == head and head_of(crm) == head, f"{code} {removed} {head_of(shop)[:8]}")

    head = origin_commit(origin, "two")
    for name in ("shallow.lock", "HEAD.lock"):
        age(shop / ".git" / name, STALE)
    ref = shop / ".git" / "refs" / "remotes" / "origin" / "main.lock"
    ref.parent.mkdir(parents=True, exist_ok=True)
    age(ref, STALE)
    code, out, err = install()
    run.step("old shallow.lock, HEAD.lock, and refs/**/*.lock are removed too, one log line each",
             code == 0 and not any(p.exists() for p in (shop / ".git" / "shallow.lock", shop / ".git" / "HEAD.lock", ref)) and err.count("removed stale ") == 3 and head_of(shop) == head,
             err.strip()[-300:])

    head = origin_commit(origin, "three")
    age(lock, 30)
    code, out, err = install()
    run.step("a young lock is left, the project is refused with the file named, the other project is prepared, and the rest of the install finishes",
             code == 5 and lock.exists() and str(lock) in err and "acme/shop" in err and head_of(crm) == head and head_of(shop) != head
             and [f["project"] for f in out.get("failed_projects", [])] == ["shop"] and str(lock) in out["failed_projects"][0]["error"]
             and [p["repo"] for p in out["projects"]] == ["acme/crm"] and "refreshed" in out and "skills" in out and "removed stale" not in err,
             f"{code} {err.strip()[-300:]}")

    holder_dir = run.root / "holder"
    holder_dir.mkdir()
    fake = holder_dir / "git"
    shutil.copy(shutil.which("sleep") or "/bin/sleep", fake)
    age(lock, STALE)
    live = subprocess.Popen([str(fake), "60"], cwd=shop)
    try:
        code, out, err = install()
        run.step("an old lock a live git process holds is left alone and refused with the process named",
                 code == 5 and lock.exists() and f"git process {live.pid}" in err and head_of(shop) != head and "removed stale" not in err, f"{code} {err.strip()[-250:]}")
    finally:
        live.kill()
        live.wait()
    code, out, err = install()
    run.step("once that process is gone the same lock is removed on the next install and the checkout catches up",
             code == 0 and not lock.exists() and head_of(shop) == head and "removed stale" in err, f"{code} {err.strip()[-200:]}")

    origin_commit(origin, "four")
    age(lock, STALE)
    no_proc = {"FAKER_KIT_PROC_DIR": str(run.root / "no-proc")}
    script(run, "lsof", LSOF_BROKEN)
    code, out, err = install(no_proc)
    run.step("where neither /proc nor a working lsof can say who holds an old lock, it is left in place and the refusal names it",
             code == 5 and lock.exists() and f"{lock} is" in err and "cannot tell whether a git process holds it" in err, f"{code} {err.strip()[-250:]}")
    script(run, "lsof", LSOF_HOLDING)
    code, out, err = install({**no_proc, "E2E_HELD_CWD": str(shop)})
    run.step("on a machine with no /proc, lsof naming a git process in the checkout keeps the lock",
             code == 5 and lock.exists() and "git process 4242" in err, f"{code} {err.strip()[-250:]}")
    script(run, "lsof", LSOF_QUIET)
    code, out, err = install(no_proc)
    run.step("on a machine with no /proc, lsof naming no git process lets the old lock go", code == 0 and not lock.exists() and "removed stale" in err, f"{code} {err.strip()[-200:]}")

    origin_commit(origin, "five")
    script(run, "git", FAKE_GIT)
    code, out, err = install({"FAKER_KIT_GIT_TIMEOUT_SECONDS": "1"})
    run.step("a git step past its time limit is stopped with SIGTERM first, so git removes its own lock, and the project is reported without stopping the rest",
             code == 5 and not lock.exists() and "git fetch did not finish" in err and [f["project"] for f in out.get("failed_projects", [])] == ["shop"], f"{code} {err.strip()[-250:]}")
    (run.bin / "git").unlink()
    code, out, err = install()
    run.step("the install after the failure completes with both checkouts current", code == 0 and head_of(shop) == head_of(crm) == head_of(origin) and "failed_projects" not in out, err.strip()[-200:])
    run.step("the project bot whose checkout failed kept its profile through the failure", (run.hermes / "profiles" / bot).is_dir(), bot)
