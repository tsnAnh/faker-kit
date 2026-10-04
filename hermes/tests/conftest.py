import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERMES = Path(__file__).resolve().parent.parent
KIT = HERMES.parent
TEMPLATES = KIT / "plugins" / "trust-ladder" / "templates" / "dispatch"
GUARD_TEMPLATE = HERMES / "templates" / "guard.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github"

for path in (HERMES / "lib", HERMES / "bin", HERMES / "plugin"):
    sys.path.insert(0, str(path))

# The recorded GitHub payloads are for Codertocat/Hello-World, so that is the shop project's home.
SHOP = ("Codertocat/Hello-World", "acme/shop-web", "acme/shop-mobile")
BOTS = {
    "version": 1,
    "bots": {
        "ong-xay": {"role": "bootstrap"},
        "meo-beo": {"role": "project", "project": "shop", "repos": list(SHOP), "home": SHOP[0]},
        "sep-crm": {"role": "project", "project": "crm", "repos": ["acme/crm"]},
    },
}


def guard_text(url="https://hermes.example.ts.net"):
    text = GUARD_TEMPLATE.read_text().replace("  - OWNER-LOGIN", "  - Codertocat\n  - meo-beo-gh")
    if url:
        text = text.replace("# webhook_base_url: https://hermes.example.ts.net", f"webhook_base_url: {url}")
    return text


@pytest.fixture
def team_kit_home(tmp_path, monkeypatch):
    """A ~/.hermes/team-kit with the sample guard.yaml and three bots: a bootstrap bot and two projects."""
    home = tmp_path / ".hermes" / "team-kit"
    home.mkdir(parents=True)
    (home / "guard.yaml").write_text(guard_text())
    (home / "bots.yaml").write_text(yaml.safe_dump(BOTS))
    monkeypatch.setenv("TEAM_KIT_HOME", str(home))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return home


@pytest.fixture
def kit(team_kit_home):
    """A git checkout at ~/.hermes/team-kit/kit. Returns its commit."""
    kit = team_kit_home / "kit"
    kit.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(kit), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True, text=True)  # noqa: E731
    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "kit")
    return git("rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def payload():
    def load(name):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    return load


def fill_brief(card="t_1a2b", standing=None, depends=None):
    text = (TEMPLATES / "brief.md").read_text().replace("<card id>", card)
    if standing is not None:
        text = text.replace("<`.agents/dispatch.md`, pasted verbatim.>", standing)
    start = text.index("## Depends on\n") + len("## Depends on\n")
    end = text.index("\n\n## Standing orders")
    return text[:start] + (depends or "none") + text[end:]


# A machine for install.sh and team-kit-admin: fake hermes, gh, and claude on PATH, and a kit
# repository holding this working tree, so the tests install what is on disk.

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True).stdout.strip()


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


FAKE_CLAUDE = "#!/bin/sh\necho \"$PWD\" > \"$HOME/.fake-claude-cwd\"\nprintf '%s\\n' \"$@\" > \"$HOME/.fake-claude-args\"\necho 'Started https://claude.ai/code/session_01BOOTstrap'\n"


@pytest.fixture(scope="session")
def kit_source(tmp_path_factory):
    import shutil

    src = tmp_path_factory.mktemp("kit")
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    shutil.copytree(KIT / "plugins" / "trust-ladder", src / "plugins" / "trust-ladder", ignore=ignore)
    shutil.copytree(HERMES, src / "hermes", ignore=ignore)
    git(src, "init", "-q")
    git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(src, "add", ".")
    git(src, "commit", "-qm", "kit")
    return src, git(src, "rev-parse", "HEAD")


@pytest.fixture
def machine(tmp_path):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tests = Path(__file__).resolve().parent
    executable(bin_dir / "hermes", f"#!{sys.executable}\n" + (tests / "fake_hermes.py").read_text())
    executable(bin_dir / "gh", f"#!{sys.executable}\n" + (tests / "fake_gh.py").read_text())
    executable(bin_dir / "claude", FAKE_CLAUDE)
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "TMPDIR": str(tmp_path), "TEAM_KIT_SOURCE_URL": "https://github.com/tsnAnh/team-kit.git"}
    return home / ".hermes", env


def install(kit_source, machine, *extra):
    src, sha = kit_source
    root, env = machine
    return subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", sha, "--source", str(src), *extra], env=env, capture_output=True, text=True)


def hermes_calls(root):
    log = root / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def gh_calls(root):
    log = root.parent / ".fake-gh" / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def fill_guard(root, url="https://hermes.example.ts.net"):
    (root / "team-kit" / "guard.yaml").write_text(guard_text(url))
