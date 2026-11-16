#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

VERSION = 1
STATE_DIR = ".faker-kit-import"
BRANCH = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
REPO = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}\Z")
LOGIN = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
SHA40 = re.compile(r"\A[0-9a-f]{40}\Z")
SHA64 = re.compile(r"\A[0-9a-f]{64}\Z")
ID16 = re.compile(r"\A[0-9a-f]{16}\Z")
SOURCE_PATH = re.compile(r"\A/[A-Za-z0-9 ._+@/-]{1,1000}\Z")
B64URL = re.compile(r"\A[A-Za-z0-9_-]{1,8000}\Z")
FIELDS = ("version", "descriptor_id", "project", "repo", "repo_id", "branch", "source_path", "expected_login", "kit_version", "helper_sha256", "commit_time")
IMPORT_NAME = "faker-kit import"
IMPORT_EMAIL = "faker-kit-import@users.noreply.github.com"
KIT_FILE = "hermes/lib/faker_kit_import.py"
MAX_FILES = 20_000
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_DEPTH = 40
GIT_SECONDS = 300
LISTED_EXCLUSIONS = 200
SIGN_IN = "sign in to GitHub for this machine in the Pal app, then run the same command again"
PROTECTED_HOME = ("Library", "Applications", "Desktop", "Documents", "Downloads")

EXCLUDED_DIRS = {
    ".gradle": "machine_local", ".idea": "machine_local", ".vscode": "machine_local", ".fleet": "machine_local", ".kotlin": "machine_local",
    ".externalnativebuild": "build_artifact", ".cxx": "build_artifact", "build": "build_artifact", "captures": "build_artifact",
    "node_modules": "build_artifact", "pods": "build_artifact", "deriveddata": "build_artifact", "__macosx": "os_artifact",
}
EXCLUDED_FILES = (
    (re.compile(r"\A(?:local\.properties|\.ds_store|thumbs\.db|desktop\.ini)\Z"), "machine_local"),
    (re.compile(r"\A\.env(?:\..*)?\Z|\.env\Z"), "secret_or_signing"),
    (re.compile(r".*\.(?:jks|keystore|p12|pfx|pem|key|p8|mobileprovision|cer|crt|der)\Z"), "secret_or_signing"),
    (re.compile(r"\A(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)(?:\..*)?\Z|\A\.(?:npmrc|netrc|pgpass|htpasswd)\Z"), "secret_or_signing"),
    (re.compile(r".*(?:keystore|signing|secret|credential|upload-key|release-key)[^/]*\.properties\Z|\A(?:key|keys|secrets?|credentials?)\.(?:properties|json|ya?ml|txt)\Z"), "secret_or_signing"),
    (re.compile(r"\Agoogle-services\.json\Z|\A.*service[-_]?account.*\.json\Z"), "secret_or_signing"),
    (re.compile(r".*\.(?:apk|aab|ap_|hprof|log|iml|swp)\Z"), "build_artifact"),
)
AUTOMATION_FILES = (".github/workflows", ".github/actions", ".github/dependabot.yml", ".github/dependabot.yaml")
AMBIGUOUS_NAME = re.compile(r"(?:secret|credential|token|passw(?:or)?d|private[-_]?key)", re.I)
AMBIGUOUS_SUFFIX = (".json", ".properties", ".txt", ".yaml", ".yml", ".xml", ".cfg", ".conf", ".ini", ".env", ".pem", ".toml", ".plist")
CONTENT_SECRETS = (
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private_key_block"),
    (re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "github_token_shape"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "aws_key_shape"),
    (re.compile(rb"xox[abprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"), "slack_token_shape"),
    (re.compile(rb"sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}"), "api_key_shape"),
    (re.compile(rb"(?:storePassword|keyPassword)\s*[=:]\s*[\"'][^\"'$]{3,}[\"']"), "signing_password_literal"),
)
SAFE_IGNORE = (
    "local.properties", "keystore.properties", "*.jks", "*.keystore", "*.p12", "*.pfx", "*.pem", "*.key", "*.p8", "*.mobileprovision",
    ".env", ".env.*", ".gradle/", ".idea/", "build/", ".externalNativeBuild/", ".cxx/", "*.apk", "*.aab", ".DS_Store", "google-services.json",
)
GIT_CONFIG_SAFE = {
    "core": {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "precomposeunicode", "symlinks"},
    "remote": {"url", "fetch"},
    "branch": {"remote", "merge"},
}
GITHUB_REPO = re.compile(r"github\.com[/:]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?\Z")
REMOTE_URL = re.compile(r"\A(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?\Z")


class ImportRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Descriptor:
    version: int
    descriptor_id: str
    project: str
    repo: str
    repo_id: int
    branch: str
    source_path: str
    expected_login: str
    kit_version: str
    helper_sha256: str
    commit_time: int

    def fields(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in FIELDS}


def descriptor_id_of(fields: dict[str, object]) -> str:
    bound = {key: value for key, value in fields.items() if key != "descriptor_id"}
    return hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def source_path_problem(path: str) -> str | None:
    if not SOURCE_PATH.match(path) or "//" in path or path.endswith("/") or any(part in ("", ".", "..") for part in path.split("/")[1:]):
        return "the source path must be an absolute path of plain characters with no .., no //, and no trailing /"
    parts = path.split("/")[1:]
    if len(parts) < 3 or any(part.startswith(".") for part in parts):
        return "the source path must be a project folder at least three levels deep with no hidden folder in it"
    if parts[0] in ("System", "usr", "etc", "private", "bin", "sbin", "dev", "proc", "sys", "root", "Library"):
        return "the source path is a system folder"
    if parts[0] in ("Users", "home") and len(parts) >= 3 and parts[2] in PROTECTED_HOME:
        return "the source path is inside a protected folder of the home directory"
    return None


def make_descriptor(fields: dict[str, object]) -> Descriptor:
    given = {key: value for key, value in fields.items() if key != "descriptor_id"}
    return parse_descriptor({**given, "descriptor_id": descriptor_id_of(given)})


def parse_descriptor(data: object) -> Descriptor:
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        raise ImportRefused("descriptor_invalid", "the import descriptor does not have exactly the expected fields")
    text = {key: data[key] for key in FIELDS if key not in ("version", "repo_id", "commit_time")}
    numbers = (data["version"], data["repo_id"], data["commit_time"])
    if any(isinstance(value, bool) or not isinstance(value, int) for value in numbers) or any(not isinstance(value, str) for value in text.values()):
        raise ImportRefused("descriptor_invalid", "the import descriptor has a field of the wrong type")
    checks = (
        (data["version"] == VERSION, "version"), (bool(ID16.match(str(data["descriptor_id"]))), "descriptor_id"), (bool(NAME.match(str(data["project"]))), "project"),
        (bool(REPO.match(str(data["repo"]))), "repo"), (isinstance(data["repo_id"], int) and data["repo_id"] > 0, "repo_id"), (bool(BRANCH.match(str(data["branch"]))), "branch"),
        (bool(LOGIN.match(str(data["expected_login"]))), "expected_login"), (bool(SHA40.match(str(data["kit_version"]))), "kit_version"),
        (bool(SHA64.match(str(data["helper_sha256"]))), "helper_sha256"), (isinstance(data["commit_time"], int) and 0 < data["commit_time"] < 4_000_000_000, "commit_time"),
        (source_path_problem(str(data["source_path"])) is None, "source_path"),
    )
    bad = [name for ok, name in checks if not ok]
    if bad:
        raise ImportRefused("descriptor_invalid", f"the import descriptor has an invalid {', '.join(bad)}")
    if data["descriptor_id"] != descriptor_id_of(dict(data)):
        raise ImportRefused("descriptor_invalid", "the import descriptor does not match its id; prepare it again")
    return Descriptor(int(data["version"]), str(data["descriptor_id"]), str(data["project"]), str(data["repo"]), int(data["repo_id"]), str(data["branch"]), str(data["source_path"]),
                      str(data["expected_login"]), str(data["kit_version"]), str(data["helper_sha256"]), int(data["commit_time"]))


def encode(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def decode(text: str) -> object:
    if not B64URL.match(text):
        raise ImportRefused("descriptor_invalid", "the import descriptor is not base64url text")
    try:
        return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except (binascii.Error, ValueError):
        raise ImportRefused("descriptor_invalid", "the import descriptor is not valid JSON") from None


def mac_command(desc: Descriptor, kit_repo: str) -> str:
    if not REPO.match(kit_repo):
        raise ImportRefused("kit_source_invalid", "the recorded kit source is not a GitHub OWNER/REPO")
    helper = '"$HOME/' + f'{STATE_DIR}/helper/{desc.kit_version}/faker_kit_import.py"'
    return (
        f'mkdir -p "$HOME/{STATE_DIR}/helper/{desc.kit_version}" && '
        f"gh api -H 'Accept: application/vnd.github.raw' '/repos/{kit_repo}/contents/{KIT_FILE}?ref={desc.kit_version}' > {helper} && "
        f"echo '{desc.helper_sha256}  '{helper} | shasum -a 256 -c - && "
        f"python3 {helper} run --descriptor {encode(desc.fields())}"
    )


GhRun = Callable[[list[str]], str]


def gh_json(run: GhRun, path: str) -> object:
    try:
        return json.loads(run(["api", path]))
    except ValueError:
        raise ImportRefused("github_unreadable", f"GitHub answered {path} with something that is not JSON") from None


def read_repo(run: GhRun, repo: str) -> dict[str, object]:
    data = gh_json(run, f"repos/{repo}")
    if not isinstance(data, dict):
        raise ImportRefused("github_unreadable", f"GitHub answered repos/{repo} with no object")
    return data


def repo_problem(data: dict[str, object], repo: str, repo_id: int, branch: str | None) -> str | None:
    permissions = data.get("permissions")
    if not isinstance(data.get("full_name"), str) or str(data["full_name"]).lower() != repo.lower():
        return f"the repository is {data.get('full_name')!r}, not {repo}"
    if data.get("id") != repo_id or isinstance(data.get("id"), bool):
        return "the repository's numeric id is not the one the owner chose"
    if data.get("private") is not True:
        return "the repository is not private; an initial import only goes to a private repository"
    if data.get("archived") is True or data.get("disabled") is True:
        return "the repository is archived or disabled"
    if not isinstance(permissions, dict) or permissions.get("push") is not True:
        return "this GitHub login cannot push to the repository"
    if branch is not None and data.get("default_branch") != branch:
        return f"the repository's default branch is {data.get('default_branch')!r}, not {branch}"
    return None


def api_refs(run: GhRun, repo: str) -> list[str]:
    found: list[str] = []
    for kind in ("branches", "tags"):
        rows = gh_json(run, f"repos/{repo}/{kind}?per_page=100")
        if not isinstance(rows, list):
            raise ImportRefused("github_unreadable", f"GitHub answered {kind} with no list")
        found.extend(f"{kind}:{row.get('name')}" if isinstance(row, dict) else f"{kind}:?" for row in rows)
    try:
        commits = json.loads(run(["api", f"repos/{repo}/commits?per_page=1"]))
    except ImportRefused as err:
        if "409" not in str(err):
            raise
        commits = []
    if not isinstance(commits, list):
        raise ImportRefused("github_unreadable", "GitHub answered the commit list with no list")
    return [*found, *(["commits"] if commits else [])]


def remote_commit_problem(run: GhRun, repo: str, branch: str, commit: str, tree: str) -> str | None:
    branches = gh_json(run, f"repos/{repo}/branches?per_page=100")
    tags = gh_json(run, f"repos/{repo}/tags?per_page=100")
    ref = gh_json(run, f"repos/{repo}/git/ref/heads/{branch}")
    detail = gh_json(run, f"repos/{repo}/git/commits/{commit}")
    if not (isinstance(branches, list) and isinstance(tags, list) and isinstance(ref, dict) and isinstance(detail, dict)):
        return "GitHub answered the remote check with an unexpected shape"
    if [row.get("name") for row in branches if isinstance(row, dict)] != [branch] or tags:
        return "the repository has refs other than the one initial branch"
    if (ref.get("object") or {}).get("sha") != commit:
        return f"{branch} is not at the receipt's commit"
    if detail.get("parents") != [] or (detail.get("tree") or {}).get("sha") != tree:
        return "the commit is not a root commit of the receipt's tree"
    return None


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        temp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def helper_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def guarded_source_path(source: str) -> str:
    problem = source_path_problem(source)
    if problem:
        raise ImportRefused("source_path_invalid", problem)
    return source


def prepare(run: GhRun, home: Path, bot: str, project: str, repo: str, repo_id: int, source: str, kit_version: str, kit_repo: str, now: float) -> dict[str, object]:
    guarded_source_path(source)
    login = str(run(["api", "user", "--jq", ".login"])).strip()
    if not LOGIN.match(login):
        raise ImportRefused("identity_unreadable", "cannot read the signed-in GitHub login on this machine")
    data = read_repo(run, repo)
    problem = repo_problem(data, repo, repo_id, None)
    if problem:
        raise ImportRefused("repo_refused", problem)
    found = api_refs(run, repo)
    if found:
        raise ImportRefused("remote_not_empty", f"{repo} already has history or refs ({', '.join(found[:5])}); an initial import needs an empty repository")
    folder = home / "imports" / bot
    path = folder / f"{project}.json"
    fields: dict[str, object] = {
        "version": VERSION, "project": project, "repo": str(data["full_name"]), "repo_id": repo_id, "branch": str(data.get("default_branch") or ""), "source_path": source,
        "expected_login": login, "kit_version": kit_version, "helper_sha256": helper_sha256(), "commit_time": int(now),
    }
    try:
        stored = json.loads(path.read_text())
        same = isinstance(stored, dict) and all(stored.get(key) == fields[key] for key in fields if key != "commit_time")
        if same:
            fields["commit_time"] = stored["commit_time"]
    except (OSError, ValueError):
        pass
    desc = make_descriptor(fields)
    atomic_write(path, json.dumps(desc.fields(), sort_keys=True))
    return {"descriptor_id": desc.descriptor_id, "repo": desc.repo, "repo_id": desc.repo_id, "branch": desc.branch, "source_path": desc.source_path, "expected_login": desc.expected_login,
            "kit_version": desc.kit_version, "helper_sha256": desc.helper_sha256, "mac_command": mac_command(desc, kit_repo)}


def verify(run: GhRun, home: Path, bot: str, project: str, receipt_text: str) -> dict[str, object]:
    receipt = decode(receipt_text)
    path = home / "imports" / bot / f"{project}.json"
    try:
        stored = parse_descriptor(json.loads(path.read_text()))
    except (OSError, ValueError):
        raise ImportRefused("no_prepared_import", f"no prepared import for {project}; run faker-kit import prepare first") from None
    if not isinstance(receipt, dict) or receipt.get("ok") is not True or receipt.get("status") not in ("imported", "already_imported"):
        raise ImportRefused("receipt_invalid", "the receipt is not a successful import receipt")
    commit, tree = receipt.get("commit"), receipt.get("tree")
    if not (isinstance(commit, str) and SHA40.match(commit) and isinstance(tree, str) and SHA40.match(tree)):
        raise ImportRefused("receipt_invalid", "the receipt has no commit and tree")
    for key, wanted in (("descriptor_id", stored.descriptor_id), ("repo", stored.repo), ("repo_id", stored.repo_id), ("branch", stored.branch), ("kit_version", stored.kit_version)):
        if receipt.get(key) != wanted:
            raise ImportRefused("receipt_mismatch", f"the receipt's {key} is not the prepared import's")
    problem = repo_problem(read_repo(run, stored.repo), stored.repo, stored.repo_id, stored.branch)
    if problem:
        raise ImportRefused("repo_refused", problem)
    problem = remote_commit_problem(run, stored.repo, stored.branch, commit, tree)
    if problem:
        raise ImportRefused("remote_mismatch", problem)
    done = {"verified": True, "project": project, "repo": stored.repo, "branch": stored.branch, "commit": commit, "tree": tree, "files": receipt.get("files"), "excluded": receipt.get("excluded")}
    atomic_write(home / "imports" / bot / f"{project}.verified.json", json.dumps(done, sort_keys=True))
    return done


Process = Callable[[list[str], dict[str, str], Path | None], "subprocess.CompletedProcess[str]"]


def run_process(argv: list[str], env: dict[str, str], cwd: Path | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=GIT_SECONDS, check=False)


def clean_env(home: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LANG": "C", "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()), "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_ATTR_NOSYSTEM": "1"}
    return {**base, **(extra or {})}


@dataclass
class Snapshot:
    files: list[tuple[str, int, int]] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    excluded_paths: list[str] = field(default_factory=list)
    total: int = 0

    def skip(self, reason: str, rel: str) -> None:
        self.excluded[reason] = self.excluded.get(reason, 0) + 1
        if len(self.excluded_paths) < LISTED_EXCLUSIONS:
            self.excluded_paths.append(f"{rel} [{reason}]")


def exclusion(rel: str, name: str, is_dir: bool) -> str | None:
    lowered = name.lower()
    if rel in AUTOMATION_FILES:
        return "automation_not_activated"
    if is_dir:
        return EXCLUDED_DIRS.get(lowered)
    for pattern, reason in EXCLUDED_FILES:
        if pattern.match(lowered):
            return reason
    return None


def safe_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and len(name.encode()) <= 255 and not re.search(r"[\x00-\x1f\x7f\\]", name)


def origin_problem(source: Path, repo: str) -> str | None:
    git = source / ".git"
    try:
        info = git.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return "the source's .git is not a plain folder (a worktree file or a link)"
    config = git / "config"
    try:
        cinfo = config.lstat()
        if not stat.S_ISREG(cinfo.st_mode) or cinfo.st_size > 65536:
            return "the source's .git/config is not a small regular file"
        text = config.read_text(errors="replace")
    except OSError:
        return "the source's .git/config cannot be read"
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            head = re.match(r'\[\s*([A-Za-z0-9.-]+)(?:\s+"[^"]*")?\s*\]\s*\Z', line)
            if head is None or head.group(1).lower() not in GIT_CONFIG_SAFE:
                return f"the source's git configuration has an unsupported section {line[:40]!r}"
            section = head.group(1).lower()
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key not in GIT_CONFIG_SAFE.get(section, set()):
            return f"the source's git configuration sets {section}.{key}, which an import never trusts"
        if section == "remote" and key == "url":
            found = REMOTE_URL.match(value.strip())
            if found is None or found.group(1).lower() != repo.lower():
                return f"the source's git remote is not {repo}"
    return None


def collect(root: Path, snap: Snapshot, copy_to: Path | None) -> None:
    def walk(folder: Path, rel: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise ImportRefused("tree_too_deep", f"the source is nested more than {MAX_DEPTH} folders deep")
        with os.scandir(folder) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            if not safe_name(name):
                raise ImportRefused("unsafe_name", f"a file name under {rel or '.'} has control characters or a backslash")
            path = rel + name
            info = (folder / name).lstat()
            is_dir = stat.S_ISDIR(info.st_mode)
            if path == ".git" and is_dir:
                continue
            reason = exclusion(path, name, is_dir)
            if reason:
                snap.skip(reason, path)
                continue
            if name == ".git":
                raise ImportRefused("nested_git", f"{path} is a git folder or worktree file inside the source")
            if stat.S_ISLNK(info.st_mode):
                raise ImportRefused("symlink", f"{path} is a symbolic link")
            if is_dir:
                if (folder / name / ".git").exists() or (folder / name / ".git").is_symlink():
                    raise ImportRefused("nested_git", f"{path}/.git is a nested git folder")
                walk(folder / name, path + "/", depth + 1)
            elif stat.S_ISREG(info.st_mode):
                take(folder / name, path, info)
            else:
                raise ImportRefused("special_file", f"{path} is not a regular file or folder")

    def take(file: Path, path: str, info: os.stat_result) -> None:
        if len(snap.files) >= MAX_FILES or info.st_size > MAX_FILE_BYTES or snap.total + info.st_size > MAX_TOTAL_BYTES:
            raise ImportRefused("source_too_large", f"the source passes the import limit at {path}")
        lowered = path.lower()
        if AMBIGUOUS_NAME.search(file.name) and lowered.endswith(AMBIGUOUS_SUFFIX):
            raise ImportRefused("ambiguous_sensitive_name", f"{path} may hold a secret; move or exclude it before importing")
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                raise ImportRefused("source_changed", f"{path} changed while the snapshot was read")
            body = handle.read(MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
        if len(body) != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise ImportRefused("source_changed", f"{path} changed while the snapshot was read")
        for pattern, code in CONTENT_SECRETS:
            if pattern.search(body):
                raise ImportRefused("suspicious_content", f"{path} looks like it holds a secret ({code}); the value is not shown")
        snap.total += len(body)
        snap.files.append((path, info.st_size, info.st_mtime_ns))
        if copy_to is not None:
            target = copy_to / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if path == ".gitignore":
                body = body.rstrip(b"\n") + b"\n"
            target.write_bytes(body)
            target.chmod(0o755 if info.st_mode & stat.S_IXUSR else 0o644)

    walk(root, "", 0)
    if copy_to is not None:
        ignore = copy_to / ".gitignore"
        existing = ignore.read_bytes() if ignore.exists() else b""
        ignore.write_bytes(existing + ("# faker-kit import: fixed safety rules\n" + "\n".join(SAFE_IGNORE) + "\n").encode())


def resolved_source(desc: Descriptor, home: Path) -> Path:
    source = Path(desc.source_path)
    try:
        real = source.resolve(strict=True)
    except OSError:
        raise ImportRefused("source_missing", f"{desc.source_path} does not exist on this machine") from None
    if str(real) != desc.source_path or not real.is_dir() or real == home or (home / STATE_DIR) in (real, *real.parents):
        raise ImportRefused("source_path_invalid", "the source path must be a real folder, with no link in its path, outside the import state folder")
    return real


class Importer:
    def __init__(self, desc: Descriptor, home: Path, process: Process = run_process) -> None:
        self.desc, self.home, self.process = desc, home, process
        self.state_root = home / STATE_DIR
        self.gh = shutil.which("gh") or "gh"
        self.url = f"https://github.com/{desc.repo}.git"
        self.ref = f"refs/heads/{desc.branch}"

    @staticmethod
    def gh_env() -> dict[str, str]:
        kept = {key: os.environ[key] for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "XDG_CONFIG_HOME") if os.environ.get(key)}
        return {**kept, "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}

    def gh_run(self, args: list[str]) -> str:
        try:
            done = self.process([self.gh, *args], clean_env(str(self.home), self.gh_env()), None)
        except (OSError, subprocess.TimeoutExpired):
            raise ImportRefused("github_unreachable", "cannot reach GitHub from this machine; run the same command again when it is online") from None
        if done.returncode != 0:
            raw = done.stderr.lower()
            if any(mark in raw for mark in ("gh auth login", "not logged in", "bad credentials", "401", "authentication")):
                raise ImportRefused("not_signed_in", f"GitHub is not signed in on this machine: {SIGN_IN}")
            if "409" in raw:
                raise ImportRefused("github_conflict", "HTTP 409 " + done.stderr.strip()[:120])
            raise ImportRefused("github_unreachable", f"GitHub did not answer {' '.join(args[:2])}: {done.stderr.strip()[:160]}; run the same command again when it is online")
        return done.stdout

    def git(self, args: list[str], cwd: Path | None, extra: dict[str, str] | None = None, remote: bool = False) -> str:
        prefix = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
        if remote:
            prefix += ["-c", "credential.helper=", "-c", f"credential.helper=!{self.gh} auth git-credential"]
        try:
            done = self.process([*prefix, *args], clean_env(str(self.home), {**(self.gh_env() if remote else {}), **(extra or {})}), cwd)
        except subprocess.TimeoutExpired:
            raise ImportRefused("git_timeout", "git did not finish in time; the remote is read again before anything is repeated") from None
        except OSError:
            raise ImportRefused("git_missing", "git is not available on this machine") from None
        if done.returncode != 0:
            raise ImportRefused("git_failed", f"git {args[0]} failed: {done.stderr.strip()[:200]}")
        return done.stdout

    def remote_refs(self) -> dict[str, str]:
        try:
            out = self.git(["ls-remote", self.url], None, remote=True)
        except ImportRefused as err:
            raw = str(err).lower()
            if any(mark in raw for mark in ("authentication", "could not read username", "403", "401")):
                raise ImportRefused("not_signed_in", f"GitHub is not signed in on this machine: {SIGN_IN}") from None
            raise ImportRefused("github_unreachable", f"cannot read the remote's refs: {str(err)[:160]}; run the same command again when it is online") from None
        refs: dict[str, str] = {}
        for line in out.splitlines():
            sha, _, name = line.partition("\t")
            if SHA40.match(sha) and name and name != "HEAD":
                refs[name] = sha
        return refs

    def checks(self, final: bool) -> None:
        login = self.gh_run(["api", "user", "--jq", ".login"]).strip()
        if login != self.desc.expected_login:
            raise ImportRefused("identity_mismatch", f"this machine is signed in to GitHub as {login or 'nobody'}, not the expected {self.desc.expected_login}")
        data = json.loads(self.gh_run(["api", f"repos/{self.desc.repo}"]))
        problem = repo_problem(data if isinstance(data, dict) else {}, self.desc.repo, self.desc.repo_id, self.desc.branch)
        if problem:
            raise ImportRefused("repo_refused", problem)
        if final:
            if self.remote_refs():
                raise ImportRefused("remote_not_empty", "the remote gained refs; nothing was written")
            found = api_refs(self.gh_run, self.desc.repo)
            if found:
                raise ImportRefused("remote_not_empty", f"GitHub lists history or refs on the remote ({', '.join(found[:5])}); nothing was written")

    def state_path(self) -> Path:
        return self.state_root / "state" / f"{self.desc.descriptor_id}.json"

    def read_state(self) -> dict[str, object]:
        try:
            data = json.loads(self.state_path().read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) and data.get("descriptor_id") == self.desc.descriptor_id else {}

    def write_state(self, status: str, commit: str, tree: str, snap: Snapshot | None) -> None:
        extra = {"files": len(snap.files), "excluded": snap.excluded, "excluded_paths": snap.excluded_paths} if snap else {}
        previous = self.read_state()
        keep = {key: previous[key] for key in ("files", "excluded", "excluded_paths") if key in previous and not extra}
        atomic_write(self.state_path(), json.dumps({"descriptor_id": self.desc.descriptor_id, "status": status, "commit": commit, "tree": tree, **keep, **extra}, sort_keys=True))

    def receipt(self, status: str, commit: str, tree: str, state: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "status": status, "descriptor_id": self.desc.descriptor_id, "repo": self.desc.repo, "repo_id": self.desc.repo_id,
                "branch": self.desc.branch, "kit_version": self.desc.kit_version,
                "commit": commit, "tree": tree, "files": state.get("files"), "excluded": state.get("excluded"), "excluded_paths": state.get("excluded_paths"),
                "next": "workflows and other automation were left out; restore them in a pull request the owner reviews"}

    def build(self, source: Path, stage: Path) -> tuple[Snapshot, str, str]:
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, mode=0o700)
        origin = origin_problem(source, self.desc.repo)
        if origin:
            raise ImportRefused("origin_mismatch", origin)
        snap = Snapshot()
        collect(source, snap, stage)
        self.git(["init", "-q", "-b", self.desc.branch, str(stage)], None)
        self.git(["add", "--force", "--all"], stage)
        again = Snapshot()
        collect(source, again, None)
        if again.files != snap.files or again.excluded != snap.excluded:
            raise ImportRefused("source_changed", "the source changed while the snapshot was built; nothing was written")
        listed = {line for line in self.git(["ls-files", "-z"], stage).split("\0") if line}
        copied = {path for path, _size, _time in snap.files}
        if not copied <= listed or not listed <= {*copied}:
            raise ImportRefused("snapshot_mismatch", "the staged tree is not the snapshot that was read")
        tree = self.git(["write-tree"], stage).strip()
        stamp = f"{self.desc.commit_time} +0000"
        who = {"GIT_AUTHOR_NAME": IMPORT_NAME, "GIT_AUTHOR_EMAIL": IMPORT_EMAIL, "GIT_COMMITTER_NAME": IMPORT_NAME, "GIT_COMMITTER_EMAIL": IMPORT_EMAIL,
               "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        commit = self.git(["commit-tree", tree, "-m", f"Initial import of {self.desc.project}", "-m", f"faker-kit import {self.desc.descriptor_id}"], stage, who).strip()
        return snap, tree, commit

    def adopt(self, stage: Path, existing: str, tree: str, known: set[str]) -> bool:
        if existing not in known:
            return False
        self.git(["fetch", "-q", "--no-tags", "--depth=1", self.url, f"{self.ref}:refs/faker-kit/remote"], stage, remote=True)
        parents = self.git(["rev-list", "--parents", "-n1", existing], stage).split()
        who = self.git(["log", "-1", "--format=%ae", existing], stage).strip()
        return parents == [existing] and self.git(["rev-parse", f"{existing}^{{tree}}"], stage).strip() == tree and who == IMPORT_EMAIL

    def run(self) -> dict[str, object]:
        source = resolved_source(self.desc, self.home)
        lock = self.state_root / "state" / f"{self.desc.descriptor_id}.lock"
        lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock_file(lock):
            return self.locked(source)

    def locked(self, source: Path) -> dict[str, object]:
        state = self.read_state()
        self.checks(final=False)
        if state.get("status") == "done":
            refs = self.remote_refs()
            if refs.get(self.ref) == str(state.get("commit")):
                return self.receipt("already_imported", str(state["commit"]), str(state["tree"]), state)
            raise ImportRefused("remote_changed", "the remote no longer holds the commit this import recorded; nothing was written")
        stage = self.state_root / "stage" / self.desc.descriptor_id
        try:
            snap, tree, commit = self.build(source, stage)
            refs = self.remote_refs()
            if refs:
                known = {value for value in (commit, str(state.get("commit") or "")) if value}
                if set(refs) == {self.ref} and self.adopt(stage, refs[self.ref], tree, known):
                    self.write_state("done", refs[self.ref], tree, snap)
                    return self.receipt("already_imported", refs[self.ref], tree, self.read_state())
                raise ImportRefused("remote_not_empty", "the remote already has history that is not this import's recorded commit; nothing was written")
            self.write_state("pending", commit, tree, snap)
            self.checks(final=True)
            recheck = Snapshot()
            collect(source, recheck, None)
            if recheck.files != snap.files:
                raise ImportRefused("source_changed", "the source changed after the snapshot; nothing was written")
            self.push(stage, commit)
            self.write_state("done", commit, tree, snap)
            return self.receipt("imported", commit, tree, self.read_state())
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def push(self, stage: Path, commit: str) -> None:
        failure: ImportRefused | None = None
        try:
            self.git(["push", "-q", "--no-verify", self.url, f"{commit}:{self.ref}"], stage, remote=True)
        except ImportRefused as err:
            failure = err
        refs = self.remote_refs()
        if refs == {self.ref: commit}:
            return
        if refs:
            raise ImportRefused("remote_changed", "another writer created the remote's history first; nothing of it was changed and nothing more was written")
        if failure:
            raise ImportRefused("outcome_uncertain", f"the push did not land and the remote is still empty ({str(failure)[:120]}); running the same command again is safe")
        raise ImportRefused("outcome_uncertain", "the push finished but the remote shows no ref; running the same command again is safe")


@contextlib.contextmanager
def lock_file(path: Path) -> Iterator[None]:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def failure(err: ImportRefused) -> dict[str, object]:
    return {"ok": False, "code": err.code, "message": str(err)}


def execute(descriptor: str) -> dict[str, object]:
    desc = parse_descriptor(decode(descriptor))
    if helper_sha256() != desc.helper_sha256:
        raise ImportRefused("helper_not_pinned", "this helper is not the file the descriptor pins; deliver it again from the pinned kit commit")
    return Importer(desc, Path(os.environ.get("HOME") or str(Path.home()))).run()


def main(argv: list[str] | None = None) -> int:
    top = argparse.ArgumentParser(prog="faker_kit_import.py")
    sub = top.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("run")
    cmd.add_argument("--descriptor", required=True)
    args = top.parse_args(argv)
    try:
        out = execute(args.descriptor)
    except ImportRefused as err:
        sys.stdout.write(json.dumps(failure(err)) + "\n")
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as err:
        sys.stdout.write(json.dumps({"ok": False, "code": "unexpected", "message": f"unexpected {type(err).__name__}; nothing more was done"}) + "\n")
        return 1
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
