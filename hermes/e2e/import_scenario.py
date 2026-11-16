from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from guard_scenario import Host, blocked

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

REPO = "TitoniumLab/SmileWallpaper"
REPO_ID = 1457556081
LOGIN = "tsnAnh"
PROJECT = "smilewallpaper"
PROJECT_BOT = "smile-bot"
BOOTSTRAP = "ong-xay"
CHIEF = "chief-bot"
KIT_REPO = "tsnAnh/faker-kit"
LIB = Path(__file__).resolve().parent.parent / "lib" / "faker_kit_import.py"
REAL_GIT = shutil.which("git") or "/usr/bin/git"
SENTINEL = "SENTINEL-5f3a9c-not-for-github"
BOTS = f"version: 1\nbots:\n  {BOOTSTRAP}: {{role: bootstrap}}\n  {CHIEF}: {{role: chief}}\n  {PROJECT_BOT}: {{role: project, project: {PROJECT}, repo: {REPO}}}\n"
META = {"id": REPO_ID, "full_name": REPO, "private": True, "archived": False, "default_branch": "main", "permissions": {"push": True, "admin": True}}

FAKE_GH = r"""
import json, os, subprocess, sys
def pointer():
    if os.environ.get("E2E_IMPORT_STATE"):
        return os.environ["E2E_IMPORT_STATE"], os.environ["E2E_REMOTE"], os.environ.get("E2E_GIT_LOG", os.devnull)
    return tuple(json.load(open(os.path.join(os.environ["HOME"], ".e2e-pointer"))))
STATE_PATH, REMOTE, GIT_LOG = pointer()
args = sys.argv[1:]
state = json.load(open(STATE_PATH))
remote = REMOTE
def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)
def git(*a):
    return subprocess.run([state["git"], "--git-dir", remote, *a], capture_output=True, text=True)
def show(value):
    print(json.dumps(value))
    sys.exit(0)
if args[:2] == ["auth", "git-credential"]:
    sys.exit(0)
if args[0] != "api":
    fail("fake gh: unhandled " + " ".join(args))
rest, skip, path = args[1:], False, None
for i, word in enumerate(rest):
    if skip:
        skip = False
    elif word in ("-H", "--jq"):
        skip = True
    elif not word.startswith("-"):
        path = word
path = path.lstrip("/")
if path == "user" and state.get("net_down"):
    fail("error connecting to api.github.com: dial tcp: lookup api.github.com: no such host")
if path == "user":
    if state.get("signed_out"):
        fail("To use GitHub CLI, run: gh auth login")
    print(state["login"])
    sys.exit(0)
if path.startswith("repos/tsnAnh/faker-kit/contents/"):
    sys.stdout.write(open(state["kit_file"]).read())
    sys.exit(0)
if state.get("net_down"):
    fail("error connecting to api.github.com: dial tcp: lookup api.github.com: no such host")
prefix = "repos/" + state["meta"]["full_name"]
if path == prefix:
    show(state["meta"])
refs = git("for-each-ref", "--format=%(refname) %(objectname)").stdout.split("\n")
refs = [line.split(" ") for line in refs if line]
if path.startswith(prefix + "/branches"):
    names = [r[0][len("refs/heads/"):] for r in refs if r[0].startswith("refs/heads/")] + state.get("ghost_branches", [])
    show([{"name": n} for n in names])
if path.startswith(prefix + "/tags"):
    show([{"name": r[0][len("refs/tags/"):]} for r in refs if r[0].startswith("refs/tags/")])
if path.startswith(prefix + "/commits"):
    if not refs:
        fail("Git Repository is empty. (HTTP 409)")
    show([{"sha": refs[0][1]}])
if path.startswith(prefix + "/git/ref/heads/"):
    found = [r for r in refs if r[0] == "refs/heads/" + path.rsplit("/", 1)[-1]]
    if not found:
        fail("Not Found (HTTP 404)")
    show({"object": {"sha": found[0][1]}})
if path.startswith(prefix + "/git/commits/"):
    body = git("cat-file", "-p", path.rsplit("/", 1)[-1]).stdout.split("\n\n")[0].split("\n")
    show({"tree": {"sha": body[0].split()[1]}, "parents": [line.split()[1] for line in body if line.startswith("parent ")]})
fail("Not Found (HTTP 404) " + path)
"""

FAKE_GIT = r"""
import json, os, subprocess, sys
def pointer():
    if os.environ.get("E2E_IMPORT_STATE"):
        return os.environ["E2E_IMPORT_STATE"], os.environ["E2E_REMOTE"], os.environ.get("E2E_GIT_LOG", os.devnull)
    return tuple(json.load(open(os.path.join(os.environ["HOME"], ".e2e-pointer"))))
STATE_PATH, REMOTE, GIT_LOG = pointer()
args = sys.argv[1:]
state_path = STATE_PATH
state = json.load(open(state_path))
remote = REMOTE
with open(GIT_LOG, "a") as log:
    log.write(json.dumps(args) + "\n")
words, i = [], 0
while i < len(args):
    if args[i] in ("-c", "-C"):
        i += 2
    else:
        words.append(args[i])
        i += 1
sub = words[0] if words else ""
rewritten = [a.replace("https://github.com/" + state["meta"]["full_name"] + ".git", remote) for a in args]
real = [state["git"], "-c", "protocol.file.allow=always", *rewritten]
def consume(key):
    value = state.pop(key, None)
    json.dump(state, open(state_path, "w"))
    return value
if sub in ("ls-remote", "push", "fetch") and state.get("git_down"):
    sys.stderr.write("fatal: unable to access 'https://github.com/x.git/': Could not resolve host: github.com\n")
    sys.exit(128)
if sub == "push":
    race = consume("race")
    if race:
        subprocess.run([state["git"], "-C", race["repo"], "push", "-q", remote, race["sha"] + ":refs/heads/main"], check=True, capture_output=True)
    mode = consume("push_mode")
    if mode == "fail":
        sys.stderr.write("fatal: unable to access: Connection reset by peer\n")
        sys.exit(128)
    done = subprocess.run(real)
    if mode == "lost" and done.returncode == 0:
        sys.stderr.write("fatal: the remote end hung up unexpectedly\n")
        sys.exit(128)
    sys.exit(done.returncode)
if sub == "add":
    target = consume("mutate")
    if target:
        with open(target, "ab") as handle:
            handle.write(b"x")
sys.exit(subprocess.run(real).returncode)
"""


def run_git(*argv: str, cwd: Path | None = None) -> str:
    done = subprocess.run([REAL_GIT, *argv], capture_output=True, text=True, cwd=cwd, check=True, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_GLOBAL": "/dev/null"})
    return done.stdout


class Case:
    def __init__(self, harness: Harness, name: str, remote: Path | None = None, source_name: str = "Smile Wallpaper") -> None:
        self.h = harness
        self.root = harness.root / "cases" / name
        self.root.mkdir(parents=True)
        self.remote = remote or self.root / "remote.git"
        if remote is None:
            run_git("init", "-q", "--bare", "-b", "main", str(self.remote))
        self.state_file = self.root / "state.json"
        self.home = self.root / "machome"
        self.home.mkdir()
        self.git_log = self.root / "git.log"
        self.git_log.write_text("")
        (self.home / ".e2e-pointer").write_text(json.dumps([str(self.root / "state.json"), str(self.remote), str(self.git_log)]))
        self.source = self.root / "Projects" / source_name
        self.write_state({"login": LOGIN, "meta": META, "git": REAL_GIT, "kit_file": str(LIB)})
        make_source(self.source)

    def write_state(self, data: dict) -> None:
        self.state_file.write_text(json.dumps(data))

    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def set(self, **change: object) -> None:
        self.write_state({**self.state(), **change})

    def meta(self, **change: object) -> None:
        self.set(meta={**META, **change})

    def env(self) -> dict[str, str]:
        return {"E2E_IMPORT_STATE": str(self.state_file), "E2E_REMOTE": str(self.remote), "E2E_GIT_LOG": str(self.git_log)}

    def prepare(self, source: Path | None = None, repo_id: int = REPO_ID, bot: str = BOOTSTRAP, project: str = PROJECT) -> tuple[int, str, str]:
        return self.h.run.kit("import", "prepare", "--project", project, "--source", str(source or self.source), "--repo-id", str(repo_id), bot=bot, env=self.env())

    def prepared(self) -> dict:
        code, out, err = self.prepare()
        if code != 0:
            raise AssertionError(f"prepare failed: {err}")
        return json.loads(out)

    def mac(self, command: str, timeout: int = 120) -> tuple[int, dict, str]:
        env = {"HOME": str(self.home), "PATH": f"{self.h.macbin}:/usr/bin:/bin", "TMPDIR": str(self.root), "LANG": "C.UTF-8", **self.env()}
        done = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=env, timeout=timeout, check=False)
        last = [line for line in done.stdout.splitlines() if line.startswith("{")]
        return done.returncode, (json.loads(last[-1]) if last else {}), done.stdout + done.stderr

    def pushes(self) -> list[list[str]]:
        calls = [json.loads(line) for line in self.git_log.read_text().splitlines()]
        return [c for c in calls if "push" in c and "-C" not in c[:1]]

    def seed(self, branch: str = "main", tag: str | None = None) -> str:
        scratch = self.root / f"scratch-{branch}-{tag}"
        run_git("init", "-q", "-b", branch, str(scratch))
        (scratch / "other.txt").write_text("another writer")
        run_git("-C", str(scratch), "add", ".")
        run_git("-C", str(scratch), "-c", "user.name=o", "-c", "user.email=o@o", "commit", "-q", "-m", "other")
        sha = run_git("-C", str(scratch), "rev-parse", "HEAD").strip()
        run_git("-C", str(scratch), "push", "-q", str(self.remote), f"HEAD:refs/tags/{tag}" if tag else f"HEAD:refs/heads/{branch}")
        return sha

    def refs(self) -> list[str]:
        return [line.split(" ")[0] for line in run_git("--git-dir", str(self.remote), "for-each-ref", "--format=%(refname) %(objectname)").splitlines()]

    def main_sha(self) -> str:
        return run_git("--git-dir", str(self.remote), "rev-parse", "refs/heads/main").strip()


def make_source(src: Path) -> None:
    files = {
        "app/src/main/java/com/smile/App.kt": "package com.smile\nclass App\n",
        "app/build.gradle.kts": 'plugins { id("com.android.application") }\n',
        "build.gradle.kts": "// root\n",
        "settings.gradle.kts": 'include(":app")\n',
        "gradle.properties": "org.gradle.jvmargs=-Xmx2g\n",
        "gradle/wrapper/gradle-wrapper.properties": "distributionUrl=https://services.gradle.org/distributions/gradle-8.zip\n",
        ".gitignore": "*.iml\n",
        "gradlew": "#!/bin/sh\necho gradle\n",
        "README.txt": "Smile\n",
        ".github/ISSUE_TEMPLATE/bug.md": "bug\n",
        "local.properties": f"sdk.dir=/Users/x/Library/Android/sdk\nstorePassword={SENTINEL}\n",
        "keystore.properties": f"storePassword={SENTINEL}\nkeyPassword={SENTINEL}\n",
        "app/upload.jks": SENTINEL,
        "app/google-services.json": SENTINEL,
        ".env": f"TOKEN={SENTINEL}\n",
        ".env.production": SENTINEL,
        ".DS_Store": SENTINEL,
        ".gradle/caches/x.bin": SENTINEL,
        ".idea/workspace.xml": SENTINEL,
        "app/build/outputs/apk/release/app-release.apk": SENTINEL,
        "app/build/intermediates/x.class": SENTINEL,
        ".github/workflows/ci.yml": f"on: push\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo {SENTINEL}\n",
        ".github/dependabot.yml": SENTINEL,
    }
    for name, text in files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (src / "gradlew").chmod(0o755)
    os.mkfifo(src / "app" / "release.keystore")
    os.mkfifo(src / ".gradle" / "lock")


def tree_state(root: Path) -> dict[str, str]:
    seen: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else "-"
        seen[str(path.relative_to(root))] = f"{info.st_mode:o} {info.st_size} {info.st_mtime_ns} {digest}"
    return seen


def remote_text(remote: Path) -> bytes:
    return subprocess.run([REAL_GIT, "--git-dir", str(remote), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True).stdout


def remote_files(remote: Path) -> list[str]:
    return run_git("--git-dir", str(remote), "ls-tree", "-r", "--name-only", "refs/heads/main").splitlines()


class Harness:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.root = run.root
        self.macbin = run.root / "macbin"

    def build(self) -> None:
        self.macbin.mkdir()
        (self.run.team / "bots.yaml").write_text(BOTS)
        for bot in (BOOTSTRAP, CHIEF, PROJECT_BOT):
            (self.run.hermes / "profiles" / bot).mkdir(parents=True, exist_ok=True)
        for place in (self.run.bin, self.macbin):
            for name, text in (("gh", FAKE_GH),):
                path = place / name
                path.write_text(f"#!{sys.executable}\n{text}")
                path.chmod(0o755)
        wrapper = self.macbin / "git"
        wrapper.write_text(f"#!{sys.executable}\n{FAKE_GIT}")
        wrapper.chmod(0o755)
        (self.macbin / "python3").symlink_to(sys.executable)
        (self.run.team / "kit-source").write_text(f"https://github.com/{KIT_REPO}.git\n")


Check = Callable[[Case], None]


def import_scenario(run: Run) -> None:
    h = Harness(run)
    h.build()
    step = run.step
    gate_cases(h)
    outcome = success_case(h)
    retry_cases(h, outcome)
    refusal_cases(h)
    uncertainty_cases(h)
    concurrency_cases(h)
    guard_cases(h)
    step("import: the helper and the descriptor ran on fixture GitHub responses and local bare remotes only, never a live GitHub repository", True, "fixtures")


def gate_cases(h: Harness) -> None:
    step = h.run.step
    case = Case(h, "gate")
    refused = {}
    for label, bot, project, source, repo_id in (
        ("project bot", PROJECT_BOT, PROJECT, None, REPO_ID),
        ("chief", CHIEF, PROJECT, None, REPO_ID),
        ("unregistered project", BOOTSTRAP, "shop", None, REPO_ID),
        ("wrong repo id", BOOTSTRAP, PROJECT, None, 42),
    ):
        refused[label] = case.prepare(source, repo_id, bot, project)
    for label, change in (
        ("public", {"private": False}),
        ("no push", {"permissions": {"push": False}}),
        ("archived", {"archived": True}),
        ("renamed", {"full_name": "TitoniumLab/Other"}),
        ("other id", {"id": 7}),
    ):
        case.meta(**change)
        refused[label] = case.prepare()
    case.meta()
    case.set(signed_out=True)
    refused["signed out"] = case.prepare()
    case.set(signed_out=False, net_down=True)
    refused["offline"] = case.prepare()
    case.set(net_down=False)
    for label, path in (
        ("relative", "Projects/Smile"),
        ("traversal", "/Users/o/../x/y/z"),
        ("root", "/"),
        ("home", "/Users/o"),
        ("hidden", "/Users/o/.ssh/proj"),
        ("library", "/Users/o/Library/Application Support/x"),
        ("system", "/System/Library/x/y"),
        ("injection", "/Users/o/p;touch /tmp/x"),
        ("substitution", "/Users/o/p$(id)/x"),
        ("newline", "/Users/o/p\nx/y"),
    ):
        refused[label] = case.prepare(Path(path))
    bad = [label for label, (code, _out, err) in refused.items() if code == 0 or "refused" not in err]
    step(
        "import: only the bootstrap bot prepares an import, and a wrong project, repo id, name, visibility, permission, sign-in, or source path is refused before anything is written",
        not bad and not (h.run.team / "imports").exists(),
        f"{len(refused)} refusals" if not bad else f"not refused: {bad}",
    )
    case.seed("dev")
    code, _out, err = case.prepare()
    other = Case(h, "gate-tag")
    other.seed(tag="v1")
    code2, _o2, err2 = other.prepare()
    step(
        "import: prepare refuses a remote that already has another branch or only a tag, and refuses when GitHub cannot be read",
        code != 0 and code2 != 0 and "remote_not_empty" in err and "remote_not_empty" in err2 and not (h.run.team / "imports").exists(),
        err[:80],
    )


def success_case(h: Harness) -> dict:
    step = h.run.step
    case = Case(h, "success")
    (case.source / ".git").mkdir()
    (case.source / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = git@github.com:titoniumlab/smilewallpaper.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )
    (case.source / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    before = tree_state(case.source)
    prepared = case.prepared()
    again = case.prepared()
    command = prepared["mac_command"]
    shape = {"descriptor_id", "repo", "repo_id", "branch", "source_path", "expected_login", "kit_version", "helper_sha256", "mac_command"}
    descriptor_arg = command.rsplit("--descriptor ", 1)[1]
    step(
        "import: prepare binds project, canonical repo and numeric id, source path, signed-in login, pinned kit commit, and helper hash, and the printed Mac command carries only safe text",
        set(prepared) == shape
        and prepared["repo"] == REPO
        and prepared["repo_id"] == REPO_ID
        and prepared["expected_login"] == LOGIN
        and prepared["source_path"] == str(case.source)
        and again == prepared
        and re.fullmatch(r"[A-Za-z0-9_-]+", descriptor_arg) is not None
        and not re.search(r"[;`\n]|\$\(", command)
        and str(case.source) not in command,
        f"id {prepared['descriptor_id']}",
    )
    code, out, raw = case.mac(command)
    commit = out.get("commit", "")
    files = remote_files(case.remote) if commit else []
    objects = remote_text(case.remote) if commit else b""
    ignore = run_git("--git-dir", str(case.remote), "show", "refs/heads/main:.gitignore") if commit else ""
    parents = run_git("--git-dir", str(case.remote), "rev-list", "--parents", "-n1", "refs/heads/main").split() if commit else []
    expected = sorted(
        [
            "app/build.gradle.kts",
            "app/src/main/java/com/smile/App.kt",
            "build.gradle.kts",
            ".gitignore",
            "gradle.properties",
            "gradle/wrapper/gradle-wrapper.properties",
            "gradlew",
            "settings.gradle.kts",
            "README.txt",
            ".github/ISSUE_TEMPLATE/bug.md",
        ]
    )
    step(
        "import: an empty matching remote gets one root commit of the snapshot on main through the helper delivered and pinned by the descriptor",
        code == 0
        and out.get("status") == "imported"
        and case.refs() == ["refs/heads/main"]
        and parents == [commit]
        and sorted(files) == expected
        and case.main_sha() == commit
        and len(case.pushes()) == 1
        and case.pushes()[0][-1] == f"{commit}:refs/heads/main",
        f"{len(files)} files",
    )
    step(
        "import: excluded secrets, signing files, local and build artifacts, and workflows are absent from the tree and from every git object, and nothing secret is printed",
        bool(commit)
        and SENTINEL.encode() not in objects
        and SENTINEL not in raw
        and b"ci.yml" not in objects
        and not any(f.startswith((".gradle/", ".idea/", "app/build/")) for f in files)
        and "automation_not_activated" in out.get("excluded", {})
        and "secret_or_signing" in out.get("excluded", {}),
        json.dumps(out.get("excluded", {}), sort_keys=True),
    )
    step(
        "import: the staged .gitignore keeps the owner's rules and adds the fixed safety rules, the executable bit survives, and the commit has the import author",
        "*.iml" in ignore
        and "local.properties" in ignore
        and "*.keystore" in ignore
        and run_git("--git-dir", str(case.remote), "ls-tree", "refs/heads/main", "gradlew").startswith("100755")
        and run_git("--git-dir", str(case.remote), "log", "-1", "--format=%ae", "refs/heads/main").strip() == "faker-kit-import@users.noreply.github.com",
        "",
    )
    step(
        "import: the owner's source is untouched: same files, modes, sizes, times and bytes, no git init, no rewritten .gitignore, and the excluded FIFOs were never opened",
        tree_state(case.source) == before
        and (case.source / ".git" / "HEAD").read_text() == "ref: refs/heads/main\n"
        and (case.source / ".gitignore").read_text() == "*.iml\n"
        and not list((case.home / ".faker-kit-import" / "stage").glob("*")),
        "",
    )
    receipt = base64.urlsafe_b64encode(json.dumps(out).encode()).decode().rstrip("=")
    ok = h.run.kit("import", "verify", "--project", PROJECT, "--receipt", receipt, bot=BOOTSTRAP, env=case.env())
    verified = json.loads(ok[1]) if ok[0] == 0 else {}
    tampered = {}
    for label, change in (
        ("commit", {"commit": "0" * 40}),
        ("repo", {"repo": "TitoniumLab/Other"}),
        ("tree", {"tree": "1" * 40}),
        ("descriptor", {"descriptor_id": "0" * 16}),
        ("status", {"ok": False}),
    ):
        text = base64.urlsafe_b64encode(json.dumps({**out, **change}).encode()).decode().rstrip("=")
        tampered[label] = h.run.kit("import", "verify", "--project", PROJECT, "--receipt", text, bot=BOOTSTRAP, env=case.env())
    other_tip = case.seed("dev")
    tampered["extra branch"] = h.run.kit("import", "verify", "--project", PROJECT, "--receipt", receipt, bot=BOOTSTRAP, env=case.env())
    step(
        "import: the Pi verifies the remote commit as a root commit of the receipt's tree, on the only branch, with no tags, and refuses a forged or stale receipt",
        ok[0] == 0 and verified.get("verified") is True and verified.get("commit") == commit and all(code != 0 for code, _o, _e in tampered.values()) and other_tip != "",
        f"{len(tampered)} forged receipts refused",
    )
    return {"case": case, "prepared": prepared, "out": out, "receipt": receipt, "before": before}


def retry_cases(h: Harness, done: dict) -> None:
    step = h.run.step
    case: Case = done["case"]
    commit = done["out"]["commit"]
    code, again, _raw = case.mac(done["prepared"]["mac_command"])
    (case.source / "README.txt").write_text("changed after the import\n")
    code2, changed, _raw2 = case.mac(done["prepared"]["mac_command"])
    shutil.rmtree(case.home / ".faker-kit-import" / "state")
    code3, recovered, _raw3 = case.mac(done["prepared"]["mac_command"])
    (case.source / "README.txt").write_text("Smile\n")
    step(
        "import: an unchanged retry, a retry after the source changed, and a retry after the local record was lost all return the recorded commit with no second push or root commit",
        code == code2 == 0
        and again.get("status") == changed.get("status") == "already_imported"
        and again.get("commit") == changed.get("commit") == commit
        and code3 != 0
        and recovered.get("code") == "remote_not_empty"
        and len(case.pushes()) == 1
        and case.main_sha() == commit,
        f"{again.get('status')} {recovered.get('code')}",
    )
    lost = Case(h, "lost-record")
    first = lost.prepared()
    c1, o1, _ = lost.mac(first["mac_command"])
    shutil.rmtree(lost.home / ".faker-kit-import" / "state")
    c2, o2, _ = lost.mac(first["mac_command"])
    step(
        "import: when the reply and the local record were lost, the exact recorded tree and commit on the remote are recognised and reported without a second write",
        c1 == c2 == 0 and o1.get("status") == "imported" and o2.get("status") == "already_imported" and o2.get("commit") == o1.get("commit") and len(lost.pushes()) == 1,
        o2.get("status", ""),
    )
    shutil.rmtree(case.home / ".faker-kit-import" / "state", ignore_errors=True)
    run_git("--git-dir", str(case.remote), "update-ref", "refs/heads/main", case.seed("elsewhere") or commit)
    code4, moved, _raw4 = case.mac(done["prepared"]["mac_command"])
    step(
        "import: a remote whose main is no longer the recorded import is never adopted, overwritten, or appended to",
        code4 != 0 and moved.get("code") in ("remote_not_empty", "remote_changed") and len(case.pushes()) == 1,
        moved.get("code", ""),
    )


def refusal_cases(h: Harness) -> None:
    step = h.run.step
    table: list[tuple[str, Callable[[Case], None], set[str]]] = []

    def case_of(label: str, setup: Callable[[Case], None], codes: set[str]) -> None:
        table.append((label, setup, codes))

    case_of("another login", lambda c: c.set(login="someone-else"), {"identity_mismatch"})
    case_of("signed out", lambda c: c.set(signed_out=True), {"not_signed_in"})
    case_of("public", lambda c: c.meta(private=False), {"repo_refused"})
    case_of("read-only", lambda c: c.meta(permissions={"push": False}), {"repo_refused"})
    case_of("archived", lambda c: c.meta(archived=True), {"repo_refused"})
    case_of("other repo id", lambda c: c.meta(id=99), {"repo_refused"})
    case_of("another branch", lambda c: c.seed("dev"), {"remote_not_empty"})
    case_of("tag only", lambda c: c.seed(tag="v1"), {"remote_not_empty"})
    case_of("unrelated main", lambda c: c.seed("main"), {"remote_not_empty"})
    case_of("api sees a ref git does not", lambda c: c.set(ghost_branches=["ghost"]), {"remote_not_empty"})
    case_of("GitHub unreachable", lambda c: c.set(net_down=True), {"github_unreachable"})
    case_of("git transport unreachable", lambda c: c.set(git_down=True), {"github_unreachable"})
    case_of("source changes during the snapshot", lambda c: c.set(mutate=str(c.source / "README.txt")), {"source_changed"})
    case_of("a competing writer wins the push", lambda c: c.set(race={"repo": str(prepare_rival(c)), "sha": rival_sha(c)}), {"remote_changed"})
    case_of("symlink", lambda c: (c.source / "app" / "link").symlink_to("/etc/passwd"), {"symlink"})
    case_of("nested git folder", lambda c: (c.source / "vendor" / "lib" / ".git").mkdir(parents=True), {"nested_git"})
    case_of("special file", lambda c: os.mkfifo(c.source / "app" / "pipe"), {"special_file"})
    case_of("secret in an ordinary file", lambda c: (c.source / "app" / "notes.txt").write_text(f"token ghp_{'a' * 30} {SENTINEL}\n"), {"suspicious_content"})
    case_of("ambiguous sensitive name", lambda c: (c.source / "app" / "token_cache.json").write_text("{}"), {"ambiguous_sensitive_name"})
    case_of("source origin is another repository", lambda c: write_git_config(c, "https://github.com/acme/other.git"), {"origin_mismatch"})
    case_of("git config with a credential helper", lambda c: write_git_config(c, f"https://github.com/{REPO}.git", "[credential]\n\thelper = !x\n"), {"origin_mismatch"})
    case_of("git config with a filter", lambda c: write_git_config(c, f"https://github.com/{REPO}.git", '[filter "x"]\n\tclean = x\n'), {"origin_mismatch"})
    case_of("a .git file", lambda c: (c.source / ".git").write_text("gitdir: /elsewhere\n"), {"origin_mismatch"})
    case_of("too many files", lambda _c: None, set())
    table.pop()
    wrong = []
    for label, setup, codes in table:
        case = Case(h, re.sub(r"\W+", "-", label))
        prepared = case.prepared()
        setup(case)
        before, refs_before = tree_state(case.source), case.refs()
        code, out, raw = case.mac(prepared["mac_command"])
        raced = label == "a competing writer wins the push"
        problems = [
            code == 0 or out.get("code") not in codes,
            SENTINEL in raw or "ghp_" in raw,
            len(case.pushes()) != (1 if raced else 0),
            case.refs() != (["refs/heads/main"] if raced else refs_before),
            tree_state(case.source) != before and "source changes" not in label,
        ]
        if any(problems):
            wrong.append(f"{label}: {out.get('code')} {problems}")
    step(
        "import: every unsafe remote, identity, repository, source, and race case is refused with its own code, nothing is pushed, and no secret is printed",
        not wrong,
        f"{len(table)} cases" if not wrong else wrong[0],
    )
    rival = Case(h, "rival-intact")
    prepared = rival.prepared()
    rival.set(race={"repo": str(prepare_rival(rival)), "sha": rival_sha(rival)})
    code, out, _raw = rival.mac(prepared["mac_command"])
    step(
        "import: when another writer creates main first, the push is rejected by the remote without force, the winner stays, and the helper reports a conflict",
        code != 0 and out.get("code") == "remote_changed" and rival.main_sha() == rival_sha(rival) and len(rival.refs()) == 1,
        out.get("code", ""),
    )
    descriptor = Case(h, "descriptor")
    prepared = descriptor.prepared()
    arg = prepared["mac_command"].rsplit("--descriptor ", 1)[1]
    original = json.loads(base64.urlsafe_b64decode(arg + "=" * (-len(arg) % 4)))
    bad = []
    helper = descriptor.home / ".faker-kit-import" / "helper" / prepared["kit_version"] / "faker_kit_import.py"
    descriptor.mac(prepared["mac_command"].rsplit(" && python3", 1)[0])
    marker = descriptor.root / "pwned"
    for label, change in (
        ("another repo", {"repo": "acme/other"}),
        ("extra field", {"extra": 1}),
        ("injected path", {"source_path": f"/Users/o/p;touch {marker}"}),
        ("substituted login", {"expected_login": "$(touch x)"}),
        ("string id", {"repo_id": "1"}),
        ("kit version", {"kit_version": "z" * 40}),
    ):
        text = base64.urlsafe_b64encode(json.dumps({**original, **change}).encode()).decode().rstrip("=")
        code, out, _raw = descriptor.mac(f"python3 {shell_quote(str(helper))} run --descriptor {text}")
        if code == 0 or out.get("code") != "descriptor_invalid":
            bad.append(label)
    code, out, _raw = descriptor.mac(f"python3 {shell_quote(str(helper))} run --descriptor 'a;b'")
    descriptor.set(kit_file=str(descriptor.root / "other-helper.py"))
    (descriptor.root / "other-helper.py").write_text("print('not the pinned helper')\n")
    elsewhere = Case(h, "delivery")
    elsewhere_prepared = elsewhere.prepared()
    elsewhere.set(kit_file=str(elsewhere.root / "other-helper.py"))
    (elsewhere.root / "other-helper.py").write_text("print('not the pinned helper')\n")
    dcode, dout, draw = elsewhere.mac(elsewhere_prepared["mac_command"])
    helper.write_text(helper.read_text() + "\n")
    pcode, pout, _praw = descriptor.mac(f"python3 {shell_quote(str(helper))} run --descriptor {arg}")
    step(
        "import: a tampered, extra, mistyped, or shell-shaped descriptor field, and a helper that does not match its pinned hash are refused before any GitHub or source access",
        not bad
        and code != 0
        and pcode != 0
        and pout.get("code") == "helper_not_pinned"
        and dcode != 0
        and "print('not the pinned" not in dout
        and not marker.exists()
        and not (elsewhere.home / ".faker-kit-import" / "state").exists()
        and "FAILED" in draw,
        f"{len(bad)} accepted",
    )


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def write_git_config(case: Case, url: str, extra: str = "") -> None:
    folder = case.source / ".git"
    folder.mkdir(exist_ok=True)
    (folder / "config").write_text(f'[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = {url}\n{extra}')


def prepare_rival(case: Case) -> Path:
    scratch = case.root / "rival"
    if not scratch.exists():
        run_git("init", "-q", "-b", "main", str(scratch))
        (scratch / "rival.txt").write_text("someone else's first commit")
        run_git("-C", str(scratch), "add", ".")
        run_git("-C", str(scratch), "-c", "user.name=r", "-c", "user.email=r@r", "commit", "-q", "-m", "rival")
    return scratch


def rival_sha(case: Case) -> str:
    return run_git("-C", str(prepare_rival(case)), "rev-parse", "HEAD").strip()


def uncertainty_cases(h: Harness) -> None:
    step = h.run.step
    lost = Case(h, "push-lost")
    prepared = lost.prepared()
    lost.set(push_mode="lost")
    code, out, _raw = lost.mac(prepared["mac_command"])
    failed = Case(h, "push-failed")
    prepared2 = failed.prepared()
    failed.set(push_mode="fail")
    code2, out2, _raw2 = failed.mac(prepared2["mac_command"])
    nothing = failed.refs()
    code3, out3, _raw3 = failed.mac(prepared2["mac_command"])
    code4, out4, _raw4 = failed.mac(prepared2["mac_command"])
    step(
        "import: a push whose reply is lost is settled by reading the exact remote, a push that did not land reports an uncertain outcome, and the retry writes one root commit once",
        code == 0
        and out.get("status") == "imported"
        and code2 != 0
        and out2.get("code") == "outcome_uncertain"
        and nothing == []
        and code3 == 0
        and out3.get("status") == "imported"
        and code4 == 0
        and out4.get("status") == "already_imported"
        and out3.get("commit") == out4.get("commit")
        and len(failed.refs()) == 1,
        f"{out2.get('code')} {out3.get('status')}",
    )


def concurrency_cases(h: Harness) -> None:
    step = h.run.step
    same = Case(h, "same-descriptor")
    prepared = same.prepared()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _i: same.mac(prepared["mac_command"]), range(2)))
    statuses = sorted(out.get("status", "") for _c, out, _r in results)
    commits = {out.get("commit") for _c, out, _r in results}
    step(
        "import: two simultaneous calls with one descriptor are serialised, create one root commit, and return the same commit",
        all(code == 0 for code, _o, _r in results) and statuses == ["already_imported", "imported"] and len(commits) == 1 and len(same.pushes()) == 1 and len(same.refs()) == 1,
        " ".join(statuses),
    )
    first = Case(h, "race-a", source_name="Smile A")
    second = Case(h, "race-b", remote=first.remote, source_name="Smile B")
    (second.source / "README.txt").write_text("a different source\n")
    command_a = first.prepared()["mac_command"]
    code, out, _err = second.prepare()
    command_b = json.loads(out)["mac_command"]
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        run_a = pool.submit(first.mac, command_a)
        run_b = pool.submit(second.mac, command_b)
        results = [run_a.result(), run_b.result()]
    wins = [out for code, out, _raw in results if code == 0 and out.get("status") == "imported"]
    losses = [out for code, out, _raw in results if code != 0]
    count = run_git("--git-dir", str(first.remote), "rev-list", "--count", "refs/heads/main").strip()
    step(
        "import: two different imports racing for the initial main produce one winner, the loser is refused with a conflict, and main holds exactly one root commit that is the winner's",
        code == 0 and len(wins) == 1 and len(losses) == 1 and losses[0].get("code") in ("remote_not_empty", "remote_changed") and count == "1" and first.main_sha() == wins[0]["commit"],
        f"{len(wins)} winner",
    )


def guard_cases(h: Harness) -> None:
    run = h.run
    case = Case(h, "guard")
    command = case.prepared()["mac_command"]
    host = Host(run.root / "guard-host", run.step, lambda _verdict: None)
    host.policy(ask=False)
    host.skills(False)
    host.load()

    def envelope(text: str, tool: str = "device") -> tuple[str, dict]:
        return tool, {"device": "Mac", "action": "shell.run", "args": {"command": text, "cwd": "/Users/owner/Projects/Smile", "timeout_seconds": 600}}

    shapes = tuple(envelope(command, name) for name in ("device", "functions.device", "mcp__hermes__device"))
    allowed = [host.tool(tool, args, BOOTSTRAP, host.sid()) for tool, args in shapes]
    appended = [
        host.tool(*envelope(command + suffix, name), BOOTSTRAP, host.sid())
        for name, suffix in (("device", "; git push origin main"), ("functions.device", " && git push -f origin HEAD:main"), ("device", "\ngh api -X PUT repos/o/r/contents/x -f branch=main"))
    ]
    readonly = [host.tool("device", {"device": "Mac", "action": action, "args": {}}, BOOTSTRAP, host.sid()) for action in ("screen.capture", "files.list", "status")]
    ordinary = [
        host.terminal(c, BOOTSTRAP, host.sid()) for c in ("git push origin main", "git push --force origin x", f"gh api -X PUT repos/{REPO}/contents/a.txt -f branch=main -f message=m -f content=YQ==")
    ]
    direct = host.terminal(f"faker-kit import prepare --project {PROJECT} --source '{case.source}' --repo-id {REPO_ID}", BOOTSTRAP, host.sid())
    run.step(
        "import: the guard lets the printed Mac command through the native device tool, blocks it with a raw main push, force-push, or ref write added, and leaves other device actions alone",
        all(v is None for v in allowed)
        and all(blocked(v, "blocked for every bot") for v in appended)
        and all(blocked(v, "blocked for every bot") for v in ordinary)
        and direct is None
        and all(v is None for v in readonly),
        "",
    )
    run.step("import: the guard has no empty-repository exception, so a raw main push stays blocked in the terminal and in the device tool's shell.run alike", all(blocked(v) for v in ordinary), "")
