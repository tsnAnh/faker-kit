"""The gh-graphql-read ratchet rule bans GraphQL-backed `gh issue` / `gh pr` reads in team-kit-gh and nothing else."""
import json
import shutil
import subprocess

import pytest
from conftest import KIT

NODE = shutil.which("node")
RATCHET = KIT / "scripts" / "agent-checks" / "ratchet.mjs"
RULE = "gh-graphql-read"
PRODUCTION = "hermes/bin/team_kit_gh.py"
pytestmark = pytest.mark.skipif(NODE is None, reason="the ratchet needs node")

BANNED = {
    "argv list": 'run(["issue", "list", "-R", repo, "--json", "number"])',
    "single quotes": "run(['pr', 'view', str(pr), '-R', repo])",
    "spaces around the commas": 'run([ "pr" ,   "list" , "-R", repo ])',
    "line breaks": 'run([\n    "issue",\n    "view", str(number),\n])',
    "mixed quotes": "run([\"issue\", 'status'])",
    "gh as the first word": 'subprocess.run(["gh", "pr", "checks", str(pr)])',
    "spread prefix": 'run([*base, "pr", "status"])',
    "shell string": 'subprocess.run("gh issue list --label x", shell=True)',
    "shell string, extra spaces": "os.system('gh   pr   view 7')",
}
ALLOWED = {
    "issue create": 'run(["issue", "create", "-R", repo, "--title", title])',
    "issue comment": 'run(["issue", "comment", str(n), "-R", repo, "--body", text])',
    "issue close": 'run(["issue", "close", str(n), "-R", repo])',
    "issue edit": 'run(["issue", "edit", str(n), "-R", repo, "--add-label", name])',
    "pr edit": 'run(["pr", "edit", str(pr), "-R", repo, "--add-label", name])',
    "pr comment": 'run(["pr", "comment", str(pr), "-R", repo, "--body", text])',
    "pr merge": 'run(["pr", "merge", str(pr), "-R", repo, "--merge"])',
    "REST read": 'run(["api", f"repos/{repo}/issues/{n}"])',
    "paved helper": 'gh.api_pages(f"repos/{gh.repo}/issues", {"labels": label})',
    "prose": "# comment on the issue, then view the list of pr checks in the browser",
}


def node(*args, cwd):
    done = subprocess.run([NODE, str(RATCHET), *args], cwd=cwd, capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def scan(tmp_path, text):
    sample = tmp_path / "sample.py"
    sample.write_text(text + "\n")
    return node("file", "--rule", RULE, "--config", str(KIT / ".agents" / "ratchet.json"), str(sample), cwd=KIT)


@pytest.mark.parametrize("text", BANNED.values(), ids=BANNED.keys())
def test_every_prohibited_read_form_fires_the_rule(tmp_path, text):
    code, out = scan(tmp_path, text)
    assert code == 1 and f"{RULE}: 1 found" in out and "Gh.api_page / Gh.api_json" in out


@pytest.mark.parametrize("text", ALLOWED.values(), ids=ALLOWED.keys())
def test_writes_rest_reads_and_prose_pass(tmp_path, text):
    code, out = scan(tmp_path, text)
    assert code == 0, out


def test_the_production_file_has_none_and_the_baseline_grandfathers_nothing():
    assert node("file", "--rule", RULE, PRODUCTION, cwd=KIT)[0] == 0
    baseline = json.loads((KIT / ".agents" / "ratchet-baseline.json").read_text())
    assert RULE not in baseline
    rule = next(r for r in json.loads((KIT / ".agents" / "ratchet.json").read_text())["rules"] if r["id"] == RULE)
    assert rule["files"] == [PRODUCTION] and rule["zeroAllowance"] is True


def git_repo(tmp_path, source):
    repo = tmp_path / "repo"
    (repo / "hermes" / "bin").mkdir(parents=True)
    (repo / ".agents").mkdir()
    shutil.copy(KIT / ".agents" / "ratchet.json", repo / ".agents" / "ratchet.json")
    (repo / PRODUCTION).write_text(source)
    for args in (["init", "-q"], ["add", "."]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


def test_the_normal_ratchet_check_fails_on_a_reintroduced_read_and_passes_once_restored(tmp_path):
    clean = 'def read(gh):\n    return gh.api_json("repos/acme/shop/pulls/7")\n'
    banned = clean + 'def regress(run):\n    return run(["pr", "view", "7", "--json", "state"])\n'
    repo = git_repo(tmp_path, clean)
    assert node("check", cwd=repo)[0] == 0
    (repo / PRODUCTION).write_text(banned)
    code, out = node("check", cwd=repo)
    assert code == 1 and f"{RULE}: 1 found, baseline allows 0" in out and f"{PRODUCTION} (0 -> 1)" in out
    (repo / PRODUCTION).write_text(clean)
    assert node("check", cwd=repo)[0] == 0


def test_update_cannot_grandfather_a_prohibited_read(tmp_path):
    repo = git_repo(tmp_path, 'def regress(run):\n    return run(["issue", "list", "-R", "a/b"])\n')
    code, out = node("update", cwd=repo)
    assert code == 0 and "no baseline is written" in out
    assert RULE not in json.loads((repo / ".agents" / "ratchet-baseline.json").read_text())
    code, out = node("check", cwd=repo)
    assert code == 1 and f"{RULE}: 1 found, baseline allows 0" in out


def test_other_production_files_and_other_rules_are_untouched(tmp_path):
    repo = git_repo(tmp_path, "x = 1\n")
    (repo / "hermes" / "bin" / "other.py").write_text('run(["pr", "view", "7"])\n')
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    assert node("check", cwd=repo)[0] == 0, "the rule covers team-kit-gh only"
