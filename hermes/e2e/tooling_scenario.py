from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

HERMES = Path(__file__).resolve().parent.parent
KIT = HERMES.parent
SCRIPTS = KIT / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts"
LINKS = SCRIPTS / "kit_links.py"
ENVIRONMENT_SETUP = SCRIPTS / "environment-setup.sh"
COMPAT = KIT / "plugins" / "trust-ladder" / "references" / "compat.md"
RATCHET = KIT / "scripts" / "agent-checks" / "ratchet.mjs"
PLAYBOOKS = KIT / "plugins" / "pstack" / "skills" / "faker-mode" / "playbooks"
SHIPPED_TEXT = ("skills", "templates", "profiles")
PLAYBOOK_PATH = re.compile(r"playbooks/([A-Za-z0-9_-]+\.md)")
RULE = "gh-graphql-read"
GUARDED = ["hermes/bin/faker_kit.py", "hermes/lib/faker_kit_gh.py"]
PRODUCTION = GUARDED[0]
COVERED = ["hermes/*.py", ":(exclude)hermes/e2e/*", ":(exclude)hermes/vendor/*"]

Step = Callable[[str, bool, str], None]


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def make_kit(root: Path) -> Path:
    kit = root / "kit"
    for plugin, skills in (("trust-ladder", ("check-agent-compatibility", "shared")), ("pstack", ("faker-mode", "shared")), ("team-workflows", ("shared",))):
        for skill in skills:
            folder = kit / "plugins" / plugin / "skills" / skill
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"---\nname: {skill}\n---\nfrom {plugin}\n")
    (kit / "plugins" / "pstack" / "agents").mkdir()
    (kit / "plugins" / "pstack" / "agents" / "faker-agent.md").write_text("---\nname: faker-agent\n---\n")
    (kit / "plugins" / "pstack" / "agents" / "notes.txt").write_text("not an agent")
    (kit / "plugins" / "pstack" / "skills" / "no-skill-file").mkdir()
    references = kit / "plugins" / "trust-ladder" / "references"
    references.mkdir()
    shutil.copy2(COMPAT, references / "compat.md")
    scripts = kit / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(LINKS, scripts / "kit_links.py")
    subprocess.run(["git", "init", "-q", "-b", "main", str(kit)], check=True, capture_output=True)
    git(kit, "add", ".")
    git(kit, "commit", "-qm", "kit")
    return kit


def hook_registrations(config: Path) -> list[str]:
    settings = json.loads((config / "settings.json").read_text())
    return [hook["command"] for group in settings["hooks"]["SessionStart"] for hook in group["hooks"]]


def environment_scenario(root: Path, step: Step) -> None:
    kit = make_kit(root)
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    config = home / ".claude"
    token = "e2e-read-token-" + "q" * 24
    base = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CONFIG_DIR", "FAKER_KIT_READ_TOKEN", "FAKER_KIT_REF", "FAKER_KIT_URL", "config_dir")}
    env = {**base, "HOME": str(home)}
    session = {**env, "FAKER_KIT_READ_TOKEN": token, "FAKER_KIT_URL": str(kit)}
    installer = config / "faker-kit-install.sh"
    kit_dir = home / ".cache" / "faker-kit"
    compat = COMPAT.read_text()

    def hook(extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(installer)], env={**session, **(extra or {})}, capture_output=True, text=True, timeout=120)

    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"model": "sonnet", "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    first = subprocess.run(["bash", str(ENVIRONMENT_SETUP)], env=env, capture_output=True, text=True, timeout=120)
    second = subprocess.run(["bash", "-c", f"set -euo pipefail; source {ENVIRONMENT_SETUP}; source {ENVIRONMENT_SETUP}"], env=env, capture_output=True, text=True, timeout=120)
    commands = hook_registrations(config)
    settings = json.loads((config / "settings.json").read_text())
    step("environment-setup.sh without the token installs no kit, registers the session hook once beside the user's own, and sourcing it again twice adds no second hook",
         first.returncode == 0 and second.returncode == 0 and not kit_dir.exists() and not (config / "skills").exists()
         and commands == ["echo mine", f"bash {installer}"] and settings["model"] == "sonnet" and os.access(installer, os.X_OK)
         and "FAKER_KIT_READ_TOKEN is not set" in (config / "faker-kit-setup.log").read_text(), (first.stderr + second.stderr).strip()[-300:] or str(commands))

    started = hook()
    skills = sorted(p.name for p in (config / "skills").iterdir())
    agents = sorted(p.name for p in (config / "agents").iterdir())
    log = (config / "faker-kit-setup.log").read_text()
    owner = (config / "skills" / "shared").readlink() if (config / "skills" / "shared").is_symlink() else None
    leaked = [str(p) for p in home.rglob("*") if p.is_file() and not p.is_symlink() and token.encode() in p.read_bytes()]
    step("the session hook with the token fetches the kit, links its skills and agents with trust-ladder winning a shared name, prints exactly the compat block, and leaks no token",
         started.returncode == 0 and skills == ["check-agent-compatibility", "faker-mode", "shared"] and agents == ["faker-agent.md"]
         and owner == kit_dir.resolve() / "plugins" / "trust-ladder" / "skills" / "shared" and (config / "skills" / "faker-mode" / "SKILL.md").is_file()
         and started.stdout == compat and started.stderr == "" and " installed" in log and "3 skills, 1 agents" in log and (kit_dir / ".faker-kit-installed").is_file()
         and not leaked and token not in started.stdout + started.stderr, started.stderr.strip()[-300:] or str(skills))

    shutil.rmtree(kit)
    again = hook()
    step("a second hook run is a no-op that fetches nothing, changes nothing, and still prints exactly the compat block",
         again.returncode == 0 and again.stdout == compat and again.stderr == "" and sorted(p.name for p in (config / "skills").iterdir()) == skills,
         again.stderr.strip()[-300:])

    kit = make_kit(root / "second")
    shutil.rmtree(kit_dir)
    for link in (config / "skills").iterdir():
        link.unlink()
    own = config / "skills" / "faker-mode"
    own.mkdir()
    (own / "SKILL.md").write_text("mine")
    stale = config / "skills" / "removed-upstream"
    stale.symlink_to(kit_dir.resolve() / "plugins" / "pstack" / "skills" / "removed-upstream")
    rerun = hook({"FAKER_KIT_URL": str(kit)})
    step("a skill folder the user already has is kept as it is, a link to a skill the kit dropped is removed, and the rest still link",
         rerun.returncode == 0 and (own / "SKILL.md").read_text() == "mine" and not own.is_symlink() and not stale.is_symlink()
         and (config / "skills" / "shared").is_symlink() and "kept 1 of your own, removed 1 stale" in (config / "faker-kit-setup.log").read_text(), rerun.stderr.strip()[-300:])

    shutil.rmtree(kit_dir)
    failed = hook({"FAKER_KIT_URL": str(root / "no-such-kit")})
    step("a hook whose fetch fails exits non-zero with one line pointing at the log, prints nothing to stdout, and leaves no marker so the next start tries again",
         failed.returncode != 0 and failed.stdout == "" and "see " in failed.stderr and "faker-kit-setup.log" in failed.stderr and len(failed.stderr.strip().splitlines()) == 1
         and not (kit_dir / ".faker-kit-installed").exists() and hook({"FAKER_KIT_URL": str(kit)}).stdout == compat, failed.stderr.strip()[-300:])

    elsewhere = root / "elsewhere"
    shutil.rmtree(kit_dir)
    surrounding = {**env, "ELSEWHERE": str(elsewhere), "SETUP": str(ENVIRONMENT_SETUP)}
    chosen = subprocess.run(["bash", "-c", 'config_dir=$ELSEWHERE; source "$SETUP"'], env=surrounding, capture_output=True, text=True, timeout=120)
    moved = subprocess.run(["bash", str(elsewhere / "faker-kit-install.sh")], env={**session, "FAKER_KIT_URL": str(kit)}, capture_output=True, text=True, timeout=120)
    via_env = subprocess.run(["bash", str(ENVIRONMENT_SETUP)], env={**env, "CLAUDE_CONFIG_DIR": str(root / "from-env")}, capture_output=True, text=True, timeout=120)
    step("the config folder is the surrounding script's config_dir, else CLAUDE_CONFIG_DIR, and the hook installs into the folder it was registered in",
         chosen.returncode == 0 and moved.returncode == 0 and (elsewhere / "skills" / "faker-mode").is_symlink() and via_env.returncode == 0
         and (root / "from-env" / "settings.json").is_file(), (chosen.stderr + moved.stderr + via_env.stderr).strip()[-300:])


def ratchet(cwd: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run(["node", str(RATCHET), *args], cwd=cwd, capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def ratchet_repo(root: Path, source: str) -> Path:
    repo = root / "ratchet-repo"
    shutil.rmtree(repo, ignore_errors=True)
    (repo / "hermes" / "bin").mkdir(parents=True)
    (repo / ".agents").mkdir()
    shutil.copy(KIT / ".agents" / "ratchet.json", repo / ".agents" / "ratchet.json")
    (repo / PRODUCTION).write_text(source)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    return repo


def ratchet_scenario(root: Path, step: Step) -> None:
    config = json.loads((KIT / ".agents" / "ratchet.json").read_text())
    rule = next(r for r in config["rules"] if r["id"] == RULE)
    baseline = json.loads((KIT / ".agents" / "ratchet-baseline.json").read_text())
    codes = [ratchet(KIT, "file", "--rule", RULE, guarded)[0] for guarded in GUARDED]
    step("the gh-graphql-read rule finds nothing in the faker-kit GitHub files and the baseline grandfathers nothing",
         codes == [0, 0] and RULE not in baseline and rule["files"] == COVERED and rule["zeroAllowance"] is True, str(codes))
    clean = 'def read(gh):\n    return gh.api_json("repos/acme/shop/pulls/7")\n'
    banned = clean + 'def regress(run):\n    return run(["pr", "view", "7", "--json", "state"])\n'
    repo = ratchet_repo(root, clean)
    first = ratchet(repo, "check")
    (repo / PRODUCTION).write_text(banned)
    regressed = ratchet(repo, "check")
    (repo / PRODUCTION).write_text(clean)
    restored = ratchet(repo, "check")
    step("the ratchet check fails on a reintroduced GraphQL read and passes once it is restored",
         first[0] == 0 and regressed[0] == 1 and f"{RULE}: 1 found, baseline allows 0" in regressed[1] and f"{PRODUCTION} (0 -> 1)" in regressed[1] and restored[0] == 0, regressed[1][-200:])
    repo = ratchet_repo(root, 'def regress(run):\n    return run(["issue", "list", "-R", "a/b"])\n')
    updated = ratchet(repo, "update")
    recorded = json.loads((repo / ".agents" / "ratchet-baseline.json").read_text())
    after = ratchet(repo, "check")
    step("ratchet update cannot grandfather a prohibited read", updated[0] == 0 and "no baseline is written" in updated[1] and RULE not in recorded and after[0] == 1
         and f"{RULE}: 1 found, baseline allows 0" in after[1], updated[1][-200:])
    repo = ratchet_repo(root, "x = 1\n")
    (repo / "hermes" / "lib").mkdir(parents=True, exist_ok=True)
    (repo / "hermes" / "e2e").mkdir(parents=True, exist_ok=True)
    (repo / "hermes" / "bin" / "other.py").write_text('run(["pr", "view", "7"])\n')
    (repo / "hermes" / "e2e" / "scenario.py").write_text('run(["pr", "view", "7"])\n')
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    other = ratchet(repo, "check")
    (repo / "hermes" / "bin" / "other.py").write_text("x = 1\n")
    (repo / "hermes" / "lib" / "shared.py").write_text('run(["issue", "list"])\n')
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    shared = ratchet(repo, "check")
    (repo / "hermes" / "lib" / "shared.py").write_text("x = 1\n")
    quiet = ratchet(repo, "check")
    step("the rule covers every Python file under hermes except the e2e scenarios",
         other[0] == 1 and "hermes/bin/other.py (0 -> 1)" in other[1] and "scenario.py" not in other[1]
         and shared[0] == 1 and "hermes/lib/shared.py (0 -> 1)" in shared[1] and quiet[0] == 0, other[1][-200:])


def named_playbooks(hermes: Path) -> dict[str, list[str]]:
    named: dict[str, list[str]] = {}
    for folder in SHIPPED_TEXT:
        for path in sorted((hermes / folder).rglob("*")):
            if path.is_file() and not path.is_symlink():
                for name in PLAYBOOK_PATH.findall(path.read_text(errors="replace")):
                    named.setdefault(name, []).append(str(path.relative_to(hermes.parent)))
    return named


def playbook_paths_scenario(root: Path, step: Step) -> None:
    named = named_playbooks(HERMES)
    missing = {name: files for name, files in named.items() if not (PLAYBOOKS / name).is_file()}
    step("every playbooks/<name>.md a kit skill, template or profile names exists in the vendored faker-mode playbooks",
         len(named) > 0 and not missing, f"named {sorted(named)}; missing {missing}")
    fixture = root / "hermes"
    (fixture / "skills" / "dispatch").mkdir(parents=True)
    (fixture / "templates").mkdir()
    (fixture / "profiles").mkdir()
    (fixture / "skills" / "dispatch" / "SKILL.md").write_text("read `playbooks/babysit.md` and `playbooks/ghost.md`\n")
    (fixture / "templates" / "brief.md").write_text("see playbooks/shipping.md\n")
    found = named_playbooks(fixture)
    gone = sorted(name for name in found if not (PLAYBOOKS / name).is_file())
    step("a skill that names a playbook the vendored folder lacks is found, and one that names an existing playbook is not",
         gone == ["ghost.md"] and sorted(found) == ["babysit.md", "ghost.md", "shipping.md"], str(found))
