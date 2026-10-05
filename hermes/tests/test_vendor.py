import json
import os
import shutil
import subprocess
import sys

import pytest
from conftest import KIT, git

SCRIPT = KIT / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts" / "vendor_kit.py"


def make_kit(tmp_path):
    """A small kit checkout: two plugins with a shared skill name, an agent, docs and assets to drop."""
    kit = tmp_path / "kit"
    for plugin, skills in (("trust-ladder", ("follow-brief", "shared")), ("pstack", ("faker-mode", "shared")), ("team-workflows", ("shared",))):
        for skill in skills:
            (kit / "plugins" / plugin / "skills" / skill).mkdir(parents=True)
            (kit / "plugins" / plugin / "skills" / skill / "SKILL.md").write_text(f"---\nname: {skill}\n---\nfrom {plugin}\n")
        (kit / "plugins" / plugin / "LICENSE").write_text(f"MIT {plugin}\n")
    (kit / "plugins" / "pstack" / "agents").mkdir()
    (kit / "plugins" / "pstack" / "agents" / "faker-agent.md").write_text("---\nname: faker-agent\n---\n")
    for dropped in ("docs/guide.md", "assets/logo.png", "test/hooks.test.mjs"):
        (kit / "plugins" / "trust-ladder" / dropped).parent.mkdir(parents=True, exist_ok=True)
        (kit / "plugins" / "trust-ladder" / dropped).write_text("dropped")
    (kit / "plugins" / "trust-ladder" / "packs" / "agnostic" / "test").mkdir(parents=True)
    (kit / "plugins" / "trust-ladder" / "packs" / "agnostic" / "test" / "bad.txt").write_text("fixture")
    tool = kit / "plugins" / "pstack" / "skills" / "faker-mode" / "run.sh"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    target = kit / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts"
    target.mkdir(parents=True)
    shutil.copy2(SCRIPT, target / "vendor_kit.py")
    (kit / "LICENSE").write_text("MIT kit\n")
    git(kit, "init", "-q")
    git(kit, "add", ".")
    git(kit, "commit", "-qm", "kit")
    return kit, git(kit, "rev-parse", "HEAD")


def test_an_untracked_kit_file_counts_as_a_change(tmp_path):
    kit, _ = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    (kit / "plugins" / "pstack" / "skills" / "faker-mode" / "new.md").write_text("untracked")
    done = vendor(kit, repo)
    assert done.returncode == 1 and "uncommitted changes" in done.stderr


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "skills" / "verify-shop").mkdir(parents=True)
    (repo / ".claude" / "skills" / "verify-shop" / "SKILL.md").write_text("own")
    git(repo, "init", "-q")
    return repo


def vendor(kit, repo, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--kit", str(kit), "--into", str(repo), *extra], capture_output=True, text=True)


def check(repo):
    return subprocess.run([sys.executable, str(repo / ".agents/team-kit/vendor_kit.py"), "--check", str(repo)], capture_output=True, text=True)


def test_vendors_the_plugins_links_skills_and_agents_and_keeps_the_repositorys_own(tmp_path):
    kit, ref = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    (repo / ".agents" / "skills").mkdir(parents=True)
    (repo / ".agents" / "skills" / "follow-brief").mkdir()
    done = vendor(kit, repo)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["kit_ref"] == ref and result["skipped"] == [".claude/skills/follow-brief", ".agents/skills/follow-brief"]
    vendored = repo / ".agents" / "team-kit"
    assert (vendored / "plugins/trust-ladder/LICENSE").read_text() == "MIT trust-ladder\n"
    assert (vendored / "LICENSE").read_text() == "MIT kit\n"
    assert not (vendored / "plugins/trust-ladder/docs").exists() and not (vendored / "plugins/trust-ladder/assets").exists()
    assert not (vendored / "plugins/trust-ladder/test").exists()
    assert (vendored / "plugins/trust-ladder/packs/agnostic/test/bad.txt").exists(), "lint-pack fixtures stay"
    assert os.access(vendored / "plugins/pstack/skills/faker-mode/run.sh", os.X_OK)
    assert os.readlink(repo / ".claude/skills/faker-mode") == "../../.agents/team-kit/plugins/pstack/skills/faker-mode"
    assert (repo / ".claude/skills/faker-mode/SKILL.md").read_text().startswith("---\nname: faker-mode")
    assert (repo / ".claude/skills/shared/SKILL.md").read_text().endswith("from trust-ladder\n"), "trust-ladder wins a shared name"
    assert os.readlink(repo / ".claude/agents/faker-agent.md") == "../../.agents/team-kit/plugins/pstack/agents/faker-agent.md"
    assert (repo / ".claude/skills/verify-shop/SKILL.md").read_text() == "own"
    assert (repo / ".agents/skills/follow-brief").is_dir() and not (repo / ".agents/skills/follow-brief").is_symlink()
    assert not (repo / ".claude/skills/follow-brief").exists(), "the repository's own skill of that name wins in every agent"
    manifest = json.loads((vendored / "KIT.json").read_text())
    assert manifest["kit_ref"] == ref and ".agents/skills/follow-brief" not in manifest["links"]
    assert ".claude/skills/follow-brief" not in manifest["links"]
    assert check(repo).returncode == 0


def test_check_catches_an_edit_an_extra_file_a_broken_link_and_a_pin_mismatch(tmp_path):
    kit, ref = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    assert vendor(kit, repo).returncode == 0
    vendored = repo / ".agents" / "team-kit"
    (vendored / "plugins/pstack/skills/faker-mode/SKILL.md").write_text("edited")
    (vendored / "plugins/pstack/new.md").write_text("added")
    (repo / ".claude/skills/faker-mode").unlink()
    (repo / ".agents/dispatch.json").write_text(json.dumps({"kit_ref": "0" * 40}))
    done = check(repo)
    assert done.returncode == 1
    for problem in ("faker-mode/SKILL.md differs from the kit", "plugins/pstack/new.md is not part of the vendored kit",
                    ".claude/skills/faker-mode should link to", "kit_ref 000000000000 differs from the vendored kit"):
        assert problem in done.stderr


def test_check_without_a_vendored_kit_says_so(tmp_path):
    repo = make_repo(tmp_path)
    done = subprocess.run([sys.executable, str(SCRIPT), "--check", str(repo)], capture_output=True, text=True)
    assert done.returncode == 1 and "vendor the kit first" in done.stderr


def test_a_new_kit_commit_replaces_the_copy_and_removes_stale_files_and_links(tmp_path):
    kit, _ = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    assert vendor(kit, repo).returncode == 0
    shutil.rmtree(kit / "plugins" / "pstack" / "skills" / "faker-mode")
    git(kit, "commit", "-qam", "drop faker-mode")
    new_ref = git(kit, "rev-parse", "HEAD")
    done = vendor(kit, repo)
    assert done.returncode == 0, done.stderr
    assert ".claude/skills/faker-mode" in json.loads(done.stdout)["removed"]
    assert not (repo / ".claude/skills/faker-mode").is_symlink()
    assert not (repo / ".agents/team-kit/plugins/pstack/skills/faker-mode").exists()
    assert json.loads((repo / ".agents/team-kit/KIT.json").read_text())["kit_ref"] == new_ref
    assert check(repo).returncode == 0


def test_moves_kit_ref_and_survives_the_repositorys_ignore_rules(tmp_path):
    kit, ref = make_kit(tmp_path)
    (kit / "plugins" / "pstack" / "lib").mkdir()
    (kit / "plugins" / "pstack" / "lib" / "util.py").write_text("x = 1\n")
    (kit / ".gitignore").write_text("*.local\n")
    (kit / "plugins" / "pstack" / "notes.local").write_text("ignored in the kit")
    git(kit, "add", ".")
    git(kit, "commit", "-qm", "lib")
    ref = git(kit, "rev-parse", "HEAD")
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("lib/\n*.py\n")
    (repo / ".agents").mkdir()
    (repo / ".agents/dispatch.json").write_text(json.dumps({"backend": "claude", "kit_ref": "0" * 40}))
    done = vendor(kit, repo)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["kit_ref_moved"] is True
    assert json.loads((repo / ".agents/dispatch.json").read_text()) == {"backend": "claude", "kit_ref": ref}
    assert not (repo / ".agents/team-kit/plugins/pstack/notes.local").exists(), "what the kit ignores is not vendored"
    git(repo, "add", "-A")
    assert ".agents/team-kit/plugins/pstack/lib/util.py" in git(repo, "ls-files")
    assert check(repo).returncode == 0


def test_refuses_what_the_repository_would_not_commit(tmp_path):
    kit, _ = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(".agents/\n")
    done = vendor(kit, repo)
    assert done.returncode == 1 and "ignore rules leave out" in done.stderr


def test_refuses_a_kit_with_uncommitted_changes_unless_told(tmp_path):
    kit, _ = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    (kit / "plugins" / "pstack" / "skills" / "faker-mode" / "SKILL.md").write_text("local edit")
    done = vendor(kit, repo)
    assert done.returncode == 1 and "uncommitted changes" in done.stderr
    assert vendor(kit, repo, "--allow-dirty").returncode == 0


@pytest.mark.parametrize("make", ["not git", "symlink", "linked skills folder"])
def test_refuses_what_it_cannot_vendor(tmp_path, make):
    kit, _ = make_kit(tmp_path)
    repo = make_repo(tmp_path)
    if make == "linked skills folder":
        (repo / ".agents").mkdir()
        (repo / ".agents" / "skills").symlink_to("../.claude/skills")
        assert "is a symlink; make it a real folder" in vendor(kit, repo).stderr
    elif make == "not git":
        shutil.rmtree(repo / ".git")
        assert "not a git repository" in vendor(kit, repo).stderr
    else:
        (kit / "plugins" / "pstack" / "skills" / "faker-mode" / "link.md").symlink_to("SKILL.md")
        git(kit, "add", ".")
        git(kit, "commit", "-qm", "link")
        assert "holds files only" in vendor(kit, repo).stderr


def test_the_real_kit_vendors_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    done = vendor(KIT, repo, "--allow-dirty")
    assert done.returncode == 0, done.stderr
    assert (repo / ".claude/skills/faker-mode/SKILL.md").exists()
    assert (repo / ".claude/skills/follow-brief/SKILL.md").exists()
    assert (repo / ".agents/skills/setup-dispatch/SKILL.md").exists()
    assert check(repo).returncode == 0
