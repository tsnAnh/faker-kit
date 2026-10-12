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

for path in (HERMES / "lib", HERMES / "bin", HERMES / "plugin"):
    sys.path.insert(0, str(path))

# A project is one repository; shop keeps its API, web, and mobile apps side by side in acme/shop.
SHOP = "acme/shop"
BOTS = {
    "version": 1,
    "bots": {
        "ong-xay": {"role": "bootstrap"},
        "chanh-van": {"role": "chief"},
        "meo-beo": {"role": "project", "project": "shop", "repo": SHOP},
        "sep-crm": {"role": "project", "project": "crm", "repo": "acme/crm"},
    },
}


def guard_text():
    text = GUARD_TEMPLATE.read_text().replace("  - OWNER-LOGIN", "  - Codertocat\n  - meo-beo-gh")
    return text + "\nalert_hosts:\n  - grafana.example.com\n"


@pytest.fixture
def team_kit_home(tmp_path, monkeypatch):
    """A ~/.hermes/team-kit with the sample guard.yaml and four bots: a bootstrap bot, a chief of staff, and two projects."""
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


# Mirrors the real CLI: `--cloud "<message>"` creates a session only with a terminal on stdout, and
# `--cloud <session> -p` reads the message on stdin, needs no terminal, and prints "Sent to cloud session.".
FAKE_CLAUDE = """import json, os, sys, time
args = sys.argv[1:]
def record(**entry):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as out:
        out.write(json.dumps(entry) + "\\n")
if len(args) == 3 and args[0] == "--cloud" and args[2] == "-p":
    record(kind="send", argv=args, stdin=sys.stdin.read(), cwd=os.getcwd())
    if os.environ.get("FAKE_CLAUDE_SEND_FAIL"):
        print("Error: session " + args[1] + " is not running")
        sys.exit(1)
    print("Sent to cloud session.")
    print("Session ID: " + args[1])
elif len(args) == 2 and args[0] == "--cloud":
    if not os.isatty(1):
        print("Error: --cloud requires an interactive terminal.")
        sys.exit(1)
    record(kind="create", argv=args, cwd=os.getcwd())
    if os.environ.get("FAKE_CLAUDE_TRUST"):
        print("Do you trust the files in this folder?\\n No, exit\\n Yes, I trust this folder", flush=True)
        time.sleep(30)
    print("Created cloud session: Task", flush=True)
    print("View: https://claude.ai/code/session_01FAKEabc?from=cli&m=0", flush=True)
else:
    record(kind="other", argv=args)
    sys.exit(2)
"""


@pytest.fixture(scope="session")
def kit_source(tmp_path_factory):
    import shutil  # noqa: PLC0415

    src = tmp_path_factory.mktemp("kit")
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "node_modules")
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
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "TMPDIR": str(tmp_path), "TEAM_KIT_SOURCE_URL": "https://github.com/tsnAnh/team-kit.git"}
    return home / ".hermes", env


def install(kit_source, machine, *extra):
    src, sha = kit_source
    root, env = machine  # noqa: RUF059
    return subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", sha, "--source", str(src), *extra], env=env, capture_output=True, text=True)


def hermes_calls(root):
    log = root / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def gh_calls(root):
    log = root.parent / ".fake-gh" / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def fill_guard(root):
    (root / "team-kit" / "guard.yaml").write_text(guard_text())
