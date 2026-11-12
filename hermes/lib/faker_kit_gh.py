from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
from collections.abc import Callable
from pathlib import Path

PAGE_SIZE = 100
PAGE_LIMIT = 50
NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
STATUS_LINE = re.compile(r"HTTP/\S+\s+([0-9]{3})")

Runner = Callable[..., str]


class Refused(Exception):
    pass


def run_gh(args: list[str], input: str | None = None) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, input=input)
    if done.returncode != 0:
        if args[:1] == ["api"] and "--include" in args:
            status = STATUS_LINE.match(done.stdout)
            if status and status.group(1) == "304":
                return done.stdout
        raise Refused(f"gh {' '.join(args[:2])} failed: {done.stderr.strip()[:300]}")
    return done.stdout


TOKEN_SHAPES = re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:bearer|token)\s+\S+", re.IGNORECASE)
INVALID_MARKERS = ("bad credentials", "401", "invalid", "expired")
MISSING_MARKERS = ("gh auth login", "not logged in", "no token", "not authenticated", "authentication required")
CREDENTIAL_FIX = "sign in to GitHub for this machine in the Pal app"
GIT_HOST = "github.com"


def scrub(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return TOKEN_SHAPES.sub("[removed]", first)[:200]


def credential_problem(run: Runner) -> str | None:
    try:
        run(["api", "user", "--jq", ".login"])
    except Refused as err:
        raw = str(err).lower()
        if any(marker in raw for marker in INVALID_MARKERS):
            return f"GitHub sign-in expired or revoked: {CREDENTIAL_FIX}."
        if any(marker in raw for marker in MISSING_MARKERS):
            return f"GitHub is not signed in for this machine: {CREDENTIAL_FIX}."
        return f"cannot reach GitHub: {scrub(str(err).partition('failed:')[2] or str(err)) or 'no answer'}"
    return None


def fix_git_protocol(run: Runner) -> str | None:
    try:
        current = run(["config", "get", "git_protocol", "-h", GIT_HOST]).strip()
    except Refused:
        return None
    if current in ("", "https"):
        return None
    try:
        run(["config", "set", "-h", GIT_HOST, "git_protocol", "https"])
    except Refused as err:
        raise Refused(f"gh git protocol for {GIT_HOST} is {scrub(current)} and could not be set to https: {scrub(str(err).partition('failed:')[2])}") from None
    return f"{scrub(current)} -> https"


def included_response(text: str) -> tuple[int, dict[str, str], str]:
    normal = text.replace("\r\n", "\n")
    if not normal.startswith("HTTP/"):
        return 200, {}, text
    header_text, _, body = normal.partition("\n\n")
    lines = header_text.splitlines()
    match = STATUS_LINE.match(lines[0] if lines else "")
    if match is None:
        return 200, {}, body
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return int(match.group(1)), headers, body


def has_next_page(link: str | None) -> bool:
    return bool(link) and any(re.search(r'rel="?next"?', part) for part in str(link).split(","))


class EtagCache:
    def __init__(self, home: Path, bot: str) -> None:
        if not NAME.match(bot):
            raise Refused(f"bot {bot!r} is not a profile name")
        self.directory = home / "etag" / bot

    def path(self, endpoint: str) -> Path:
        return self.directory / f"{hashlib.sha256(endpoint.encode()).hexdigest()}.json"

    def read(self, endpoint: str) -> dict | None:
        try:
            data = json.loads(self.path(endpoint).read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("endpoint") == endpoint else None

    def write(self, endpoint: str, etag: str, body: str, more: bool) -> None:
        self.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        path = self.path(endpoint)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps({"endpoint": endpoint, "etag": etag, "body": body, "more": more}))
        tmp.replace(path)


class Gh:
    def __init__(self, home: Path, bot: str, repo: str, run: Runner) -> None:
        self.repo, self.run = repo, run
        self.etags = EtagCache(home, bot)

    def page(self, path: str) -> tuple[str, bool]:
        endpoint = f"GET {path}"
        cached = self.etags.read(endpoint)
        args = ["api", "--include"]
        if cached and cached.get("etag"):
            args += ["-H", f"If-None-Match: {cached['etag']}"]
        status, headers, body = included_response(self.run([*args, path]))
        if status == 304:
            if cached and isinstance(cached.get("body"), str):
                return cached["body"], bool(cached.get("more"))
            raise Refused(f"gh api failed: HTTP 304 without a cached body for {path}")
        if not 200 <= status < 300:
            raise Refused(f"gh api failed: HTTP {status}")
        more = has_next_page(headers.get("link"))
        if headers.get("etag"):
            self.etags.write(endpoint, headers["etag"], body, more)
        return body, more

    def json(self, path: str) -> dict:
        data = json.loads(self.page(path)[0])
        if not isinstance(data, dict):
            raise Refused(f"gh api returned no object for {path}")
        return data

    def pages(self, path: str, params: dict[str, str] | None = None, key: str | None = None) -> list[dict]:
        items: list[dict] = []
        for number in range(1, PAGE_LIMIT + 1):
            query = urllib.parse.urlencode({**(params or {}), "per_page": str(PAGE_SIZE), "page": str(number)})
            body, more = self.page(f"{path}?{query}")
            data = json.loads(body)
            rows = data.get(key) if key and isinstance(data, dict) else data
            if not isinstance(rows, list):
                raise Refused(f"gh api returned no list for {path}")
            items.extend(row for row in rows if isinstance(row, dict))
            if not more or not rows:
                return items
        raise Refused(f"more than {PAGE_LIMIT * PAGE_SIZE} items at {path}")

    def pull(self, number: int) -> dict:
        return self.json(f"repos/{self.repo}/pulls/{int(number)}")

    def open_pulls(self) -> list[dict]:
        return sorted(self.pages(f"repos/{self.repo}/pulls", {"state": "open"}), key=lambda p: int(p["number"]))

    def reviews(self, number: int) -> list[dict]:
        return self.pages(f"repos/{self.repo}/pulls/{int(number)}/reviews")

    def check_runs(self, head: str) -> list[dict]:
        return self.pages(f"repos/{self.repo}/commits/{head}/check-runs", key="check_runs")

    def statuses(self, head: str) -> list[dict]:
        newest_first = self.pages(f"repos/{self.repo}/commits/{head}/statuses")
        latest: dict[str, dict] = {}
        for status in newest_first:
            latest.setdefault(str(status.get("context") or ""), status)
        return list(latest.values())

    def head_pulls(self, head: str) -> list[dict]:
        return self.pages(f"repos/{self.repo}/commits/{head}/pulls")

    def branch_pulls(self, owner: str, branch: str) -> list[dict]:
        return self.pages(f"repos/{self.repo}/pulls", {"state": "all", "head": f"{owner}:{branch}"})

    def timeline(self, number: int) -> list[dict]:
        return self.pages(f"repos/{self.repo}/issues/{int(number)}/timeline")

    def head_runs(self, head: str) -> list[dict]:
        return self.pages(f"repos/{self.repo}/actions/runs", {"head_sha": head}, key="workflow_runs")

    def file_text(self, path: str, ref: str) -> str | None:
        query = urllib.parse.urlencode({"ref": ref})
        try:
            data = self.json(f"repos/{self.repo}/contents/{urllib.parse.quote(path)}?{query}")
        except Refused as err:
            if "HTTP 404" in str(err):
                return None
            raise
        try:
            return base64.b64decode(str(data.get("content") or "")).decode()
        except (binascii.Error, UnicodeDecodeError):
            raise Refused(f"{path} on {ref} is not text") from None

    def workflows_tree(self, ref: str) -> str | None:
        query = urllib.parse.urlencode({"ref": ref})
        data = json.loads(self.page(f"repos/{self.repo}/contents/.github?{query}")[0])
        if not isinstance(data, list):
            raise Refused(f"gh api returned no list for .github on {ref}")
        for entry in data:
            if isinstance(entry, dict) and entry.get("name") == "workflows" and entry.get("type") == "dir" and entry.get("sha"):
                return str(entry["sha"])
        return None

    def default_branch(self) -> str:
        return str(self.json(f"repos/{self.repo}").get("default_branch") or "")
