from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

HERMES = Path(__file__).resolve().parent.parent
KIT = HERMES.parent
SCRIPT = KIT / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts" / "vendor_kit.py"
RATCHET = KIT / "scripts" / "agent-checks" / "ratchet.mjs"
RULE = "gh-graphql-read"
GUARDED = ["hermes/bin/faker_kit.py", "hermes/lib/faker_kit_gh.py"]
PRODUCTION = GUARDED[0]
COVERED = ["hermes/*.py", ":(exclude)hermes/e2e/*", ":(exclude)hermes/vendor/*"]

Step = Callable[[str, bool, str], None]


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def make_kit(root: Path) -> tuple[Path, str]:
    kit = root / "kit"
    for plugin, skills in (("trust-ladder", ("check-agent-compatibility", "shared")), ("pstack", ("faker-mode", "shared")), ("team-workflows", ("shared",))):
        for skill in skills:
            folder = kit / "plugins" / plugin / "skills" / skill
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"---\nname: {skill}\n---\nfrom {plugin}\n")
        (kit / "plugins" / plugin / "LICENSE").write_text(f"MIT {plugin}\n")
    (kit / "plugins" / "pstack" / "agents").mkdir()
    (kit / "plugins" / "pstack" / "agents" / "faker-agent.md").write_text("---\nname: faker-agent\n---\n")
    for dropped in ("docs/guide.md", "assets/logo.png", "test/hooks.test.mjs"):
        target = kit / "plugins" / "trust-ladder" / dropped
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("dropped")
    fixture = kit / "plugins" / "trust-ladder" / "packs" / "agnostic" / "test"
    fixture.mkdir(parents=True)
    (fixture / "bad.txt").write_text("fixture")
    tool = kit / "plugins" / "pstack" / "skills" / "faker-mode" / "run.sh"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    scripts = kit / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / "vendor_kit.py")
    (kit / "LICENSE").write_text("MIT kit\n")
    git(kit, "init", "-q")
    git(kit, "add", ".")
    git(kit, "commit", "-qm", "kit")
    return kit, git(kit, "rev-parse", "HEAD")


def make_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    skill = repo / ".claude" / "skills" / "verify-shop"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("own")
    git(repo, "init", "-q")
    return repo


def vendor(kit: Path, repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--kit", str(kit), "--into", str(repo), *extra], capture_output=True, text=True)


def check(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(repo / ".agents/faker-kit/vendor_kit.py"), "--check", str(repo)], capture_output=True, text=True)


def vendor_scenario(root: Path, step: Step) -> None:
    kit, ref = make_kit(root)
    repo = make_repo(root)
    (repo / ".agents" / "skills" / "check-agent-compatibility").mkdir(parents=True)
    done = vendor(kit, repo)
    result = json.loads(done.stdout) if done.returncode == 0 else {}
    vendored = repo / ".agents" / "faker-kit"
    link = repo / ".claude/skills/faker-mode"
    manifest = json.loads((vendored / "KIT.json").read_text()) if (vendored / "KIT.json").exists() else {}
    step("vendor_kit.py vendors the plugins, links skills and agents, and keeps the repository's own skills",
         done.returncode == 0 and result["kit_ref"] == ref and result["skipped"] == [".claude/skills/check-agent-compatibility", ".agents/skills/check-agent-compatibility"]
         and (vendored / "plugins/trust-ladder/LICENSE").read_text() == "MIT trust-ladder\n" and (vendored / "LICENSE").read_text() == "MIT kit\n"
         and not (vendored / "plugins/trust-ladder/docs").exists() and not (vendored / "plugins/trust-ladder/assets").exists() and not (vendored / "plugins/trust-ladder/test").exists()
         and (vendored / "plugins/trust-ladder/packs/agnostic/test/bad.txt").exists() and os.access(vendored / "plugins/pstack/skills/faker-mode/run.sh", os.X_OK)
         and link.is_symlink() and link.readlink().as_posix() == "../../.agents/faker-kit/plugins/pstack/skills/faker-mode"
         and (repo / ".claude/skills/shared/SKILL.md").read_text().endswith("from trust-ladder\n")
         and (repo / ".claude/agents/faker-agent.md").readlink().as_posix() == "../../.agents/faker-kit/plugins/pstack/agents/faker-agent.md"
         and (repo / ".claude/skills/verify-shop/SKILL.md").read_text() == "own" and (repo / ".agents/skills/check-agent-compatibility").is_dir()
         and not (repo / ".agents/skills/check-agent-compatibility").is_symlink() and not (repo / ".claude/skills/check-agent-compatibility").exists()
         and manifest["kit_ref"] == ref and ".claude/skills/check-agent-compatibility" not in manifest["links"] and check(repo).returncode == 0, (done.stderr or done.stdout)[-300:])

    (vendored / "plugins/pstack/skills/faker-mode/SKILL.md").write_text("edited")
    (vendored / "plugins/pstack/new.md").write_text("added")
    link.unlink()
    (repo / ".agents").mkdir(exist_ok=True)
    (repo / ".agents/dispatch.json").write_text(json.dumps({"kit_ref": "0" * 40}))
    bad = check(repo)
    problems = (
        "faker-mode/SKILL.md differs from the kit", "plugins/pstack/new.md is not part of the vendored kit",
        ".claude/skills/faker-mode should link to", "kit_ref 000000000000 differs from the vendored kit",
    )
    step("vendor_kit.py --check catches an edit, an extra file, a broken link, and a pin mismatch", bad.returncode == 1 and all(p in bad.stderr for p in problems), bad.stderr[-300:])

    bare = make_repo(root, "bare")
    none = subprocess.run([sys.executable, str(SCRIPT), "--check", str(bare)], capture_output=True, text=True)
    step("vendor_kit.py --check without a vendored kit says to vendor first", none.returncode == 1 and "vendor the kit first" in none.stderr, none.stderr.strip())

    stale = make_repo(root, "stale")
    (stale / ".agents" / "old-kit" / "plugins" / "pstack" / "skills" / "faker-mode").mkdir(parents=True)
    (stale / ".claude" / "skills" / "faker-mode").symlink_to("../../.agents/old-kit/plugins/pstack/skills/faker-mode")
    refused = vendor(kit, stale)
    step("vendor_kit.py refuses a repository whose skill links point into another vendored kit folder, and changes nothing",
         refused.returncode != 0 and "another vendored kit folder" in refused.stderr and ".claude/skills/faker-mode" in refused.stderr
         and not (stale / ".agents" / "faker-kit").exists(), refused.stderr.strip()[-300:])

    fresh = make_repo(root, "fresh")
    assert vendor(kit, fresh).returncode == 0
    shutil.rmtree(kit / "plugins" / "pstack" / "skills" / "faker-mode")
    git(kit, "commit", "-qam", "drop faker-mode")
    new_ref = git(kit, "rev-parse", "HEAD")
    again = vendor(kit, fresh)
    step("vendor_kit.py at a newer kit commit replaces the copy and removes stale files and links",
         again.returncode == 0 and ".claude/skills/faker-mode" in json.loads(again.stdout)["removed"] and not (fresh / ".claude/skills/faker-mode").is_symlink()
         and not (fresh / ".agents/faker-kit/plugins/pstack/skills/faker-mode").exists() and json.loads((fresh / ".agents/faker-kit/KIT.json").read_text())["kit_ref"] == new_ref
         and check(fresh).returncode == 0, again.stderr[-300:])

    kit3, _ = make_kit(root / "third")
    (kit3 / "plugins" / "pstack" / "lib").mkdir()
    (kit3 / "plugins" / "pstack" / "lib" / "util.py").write_text("x = 1\n")
    (kit3 / ".gitignore").write_text("*.local\n")
    (kit3 / "plugins" / "pstack" / "notes.local").write_text("ignored in the kit")
    git(kit3, "add", ".")
    git(kit3, "commit", "-qm", "lib")
    ref3 = git(kit3, "rev-parse", "HEAD")
    ignoring = make_repo(root, "ignoring")
    (ignoring / ".gitignore").write_text("lib/\n*.py\n")
    (ignoring / ".agents").mkdir()
    (ignoring / ".agents/dispatch.json").write_text(json.dumps({"backend": "claude", "kit_ref": "0" * 40}))
    moved = vendor(kit3, ignoring)
    git(ignoring, "add", "-A")
    step("vendor_kit.py moves kit_ref, vendors files the repository's ignore rules would drop, and leaves out what the kit ignores",
         moved.returncode == 0 and json.loads(moved.stdout)["kit_ref_moved"] is True and json.loads((ignoring / ".agents/dispatch.json").read_text()) == {"backend": "claude", "kit_ref": ref3}
         and not (ignoring / ".agents/faker-kit/plugins/pstack/notes.local").exists() and ".agents/faker-kit/plugins/pstack/lib/util.py" in git(ignoring, "ls-files")
         and check(ignoring).returncode == 0,
         moved.stderr[-300:])

    hidden = make_repo(root, "hidden")
    (hidden / ".gitignore").write_text(".agents/\n")
    refused = vendor(kit, hidden)
    step("vendor_kit.py refuses what the repository would not commit", refused.returncode == 1 and "ignore rules leave out" in refused.stderr, refused.stderr.strip()[-200:])

    kit4, _ = make_kit(root / "fourth")
    dirty = make_repo(root, "dirty")
    (kit4 / "plugins" / "pstack" / "skills" / "faker-mode" / "SKILL.md").write_text("local edit")
    refused = vendor(kit4, dirty)
    allowed = vendor(kit4, dirty, "--allow-dirty")
    kit5, _ = make_kit(root / "fifth")
    (kit5 / "plugins" / "pstack" / "skills" / "faker-mode" / "new.md").write_text("untracked")
    untracked = vendor(kit5, make_repo(root, "untracked"))
    step("vendor_kit.py refuses a kit with uncommitted or untracked changes unless told", refused.returncode == 1 and "uncommitted changes" in refused.stderr and allowed.returncode == 0
         and untracked.returncode == 1 and "uncommitted changes" in untracked.stderr, refused.stderr.strip()[-200:])

    kit6, _ = make_kit(root / "sixth")
    linked = make_repo(root, "linked")
    (linked / ".agents").mkdir()
    (linked / ".agents" / "skills").symlink_to("../.claude/skills")
    folder = vendor(kit6, linked)
    plain = make_repo(root, "plain")
    shutil.rmtree(plain / ".git")
    not_git = vendor(kit6, plain)
    (kit6 / "plugins" / "pstack" / "skills" / "faker-mode" / "link.md").symlink_to("SKILL.md")
    git(kit6, "add", ".")
    git(kit6, "commit", "-qm", "link")
    symlink = vendor(kit6, make_repo(root, "symlinked"))
    step("vendor_kit.py refuses a linked skills folder, a folder that is not a git repository, and a symlink in the kit",
         "is a symlink; make it a real folder" in folder.stderr and "not a git repository" in not_git.stderr and "holds files only" in symlink.stderr
         and all(p.returncode == 1 for p in (folder, not_git, symlink)),
         folder.stderr.strip()[-120:])

    real = make_repo(root, "real")
    live = vendor(KIT, real, "--allow-dirty")
    step("vendor_kit.py vendors this kit cleanly and the copy checks", live.returncode == 0 and (real / ".claude/skills/faker-mode/SKILL.md").exists()
         and (real / ".claude/skills/check-agent-compatibility/SKILL.md").exists() and check(real).returncode == 0, (live.stderr or live.stdout)[-300:])


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
