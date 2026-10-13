"""Hermetic gh double for install and team-kit-admin tests; this is not GitHub."""
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

state = Path(os.environ["HOME"]) / ".fake-gh"
state.mkdir(exist_ok=True)
args = sys.argv[1:]
body = sys.stdin.read() if "--input" in args else None
with (state / "calls.log").open("a") as log:
    log.write(json.dumps({"args": args, "stdin": body}) + "\n")


def load(name, default):
    path = state / name
    return json.loads(path.read_text()) if path.exists() else default


def save(name, value):
    (state / name).write_text(json.dumps(value))


if args[:2] == ["auth", "status"]:
    # Like gh, the scopes line names each scope in quotes; FAKE_GH_LOGGED_OUT models no login.
    if os.environ.get("FAKE_GH_LOGGED_OUT"):
        print("You are not logged into any GitHub hosts.", file=sys.stderr)
        sys.exit(1)
    print("github.com\n  ✓ Logged in to github.com account Codertocat (keyring)\n  - Token scopes: 'admin:repo_hook', 'repo', 'workflow'")
elif args[:2] == ["auth", "setup-git"]:
    pass
elif args[:2] == ["auth", "token"]:
    print("gho_fake_login_token")
elif args[:2] == ["label", "create"]:
    labels = load("labels.json", {})
    repo = args[args.index("-R") + 1]
    labels.setdefault(repo, [])
    if args[2] not in labels[repo]:
        labels[repo].append(args[2])
    save("labels.json", labels)
elif args[:2] == ["repo", "clone"]:
    # Like gh, the clone has an origin remote and the default branch's history: here one empty commit
    # in a per-repository origin that this fake creates on first use.
    origin = Path(os.environ["HOME"]) / ".fake-gh" / "origins" / args[2].replace("/", "__")
    if not (origin / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "-C", str(origin), "-c", "user.email=f@k.e", "-c", "user.name=fake", "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), args[3]], check=True)
elif args[:2] == ["issue", "list"]:
    print("[]")
elif args[:2] == ["issue", "create"]:
    repo = args[args.index("-R") + 1]
    if os.environ.get("FAKE_ISSUE_CREATE_FAILS"):
        print("gh: could not add label: 'agent:andon' not found", file=sys.stderr)
        sys.exit(1)
    issues = load("issues.json", {})
    number = len(issues.get(repo, [])) + 50
    issues.setdefault(repo, []).append({"number": number, "title": args[args.index("--title") + 1], "body": args[args.index("--body") + 1],
                                        "label": args[args.index("--label") + 1], "state": "open"})
    save("issues.json", issues)
    print(f"https://github.com/{repo}/issues/{number}")
elif args[:2] == ["issue", "close"]:
    repo = args[args.index("-R") + 1]
    issues = load("issues.json", {})
    for issue in issues.get(repo, []):
        if issue["number"] == int(args[2]):
            issue["state"], issue["comment"] = "closed", args[args.index("--comment") + 1]
    save("issues.json", issues)
elif args[0] == "api":
    import base64

    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    full = next(a for a in args[1:] if a.startswith("repos/"))
    path, _, query = full.partition("?")
    repo = "/".join(path.split("/")[1:3])
    rest = path[len(f"repos/{repo}"):]

    def missing():
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)

    def record(kind, value):
        posted = load("posted.json", {})
        posted.setdefault(kind, []).append(value)
        save("posted.json", posted)

    def emit(value, link=None):
        """Print a response like `gh api`: the body, or with --include the status line, headers, and an ETag that If-None-Match can match."""
        text = json.dumps(value)
        if "--include" not in args:
            print(text)
            return
        etag = '"' + hashlib.sha256(text.encode()).hexdigest() + '"'
        given = next((args[i + 1].split(":", 1)[1].strip() for i, a in enumerate(args) if a == "-H" and args[i + 1].lower().startswith("if-none-match:")), None)
        with (state / "responses.log").open("a") as responses:
            responses.write(json.dumps({"path": full, "status": 304 if given == etag else 200}) + "\n")
        if given == etag:
            sys.stdout.write(f"HTTP/2.0 304 Not Modified\r\netag: {etag}\r\n\r\n")
            print("gh: HTTP 304", file=sys.stderr)
            sys.exit(1)
        sys.stdout.write(f"HTTP/2.0 200 OK\r\netag: {etag}\r\n" + (f"link: {link}\r\n" if link else "") + f"\r\n{text}")

    def paged(rows, key=None):
        params = urllib.parse.parse_qs(query)
        per_page = min(int((params.get("per_page") or ["30"])[0]), int(os.environ.get("FAKE_PAGE_SIZE") or 100))
        page = int((params.get("page") or ["1"])[0])
        link = f'<https://api.github.com/repositories/1/next?page={page + 1}>; rel="next"' if len(rows) > page * per_page else None
        chunk = rows[(page - 1) * per_page:page * per_page]
        emit({key: chunk, "total_count": len(rows)} if key else chunk, link)

    def issue_row(issue):
        names = issue.get("labels") or [issue["label"]]
        return {"number": issue["number"], "title": issue["title"], "body": issue["body"], "state": issue["state"], "labels": [{"name": n} for n in names],
                "user": {"login": issue.get("author", "verify-bot")}, "author_association": issue.get("association", "OWNER"),
                "created_at": issue.get("created_at", "2026-10-01T00:00:00Z"), "closed_at": issue.get("closed_at"), "state_reason": issue.get("state_reason"),
                **({"pull_request": {}} if issue.get("pull_request") else {})}

    def pull_row(number, pull):
        head = pull["head"] if isinstance(pull["head"], dict) else {"ref": pull["head"]}
        return {**pull, "number": number, "title": pull.get("title", ""), "html_url": pull.get("html_url", f"https://github.com/{repo}/pull/{number}"),
                "state": pull.get("state", "open"),
                "merged_at": pull.get("merged_at"), "body": pull.get("body", ""), "user": {"login": pull.get("author", "verify-bot")}, "labels": pull.get("labels", []),
                "head": {"sha": head.get("sha", "a" * 40), "ref": head["ref"],
                         "repo": {"full_name": repo, "name": repo.split("/")[1], "owner": {"login": repo.split("/")[0]}}},
                "base": {"repo": {"full_name": repo}}}

    if rest == "/hooks" and method == "GET":
        # Like GitHub, a listed hook never shows its secret.
        emit([{**h, "config": {k: v for k, v in h["config"].items() if k != "secret"}} for h in load("hooks.json", {}).get(repo, [])])
    elif rest == "/hooks" and method == "POST":
        hooks = load("hooks.json", {})
        hook = {"id": 1 + sum(len(v) for v in hooks.values()), **json.loads(body)}
        hooks.setdefault(repo, []).append(hook)
        save("hooks.json", hooks)
        emit(hook)
    elif rest.startswith("/hooks/") and method == "PATCH":
        hooks = load("hooks.json", {})
        hook = next((h for h in hooks.get(repo, []) if h["id"] == int(rest.rsplit("/", 1)[1])), None)
        if hook is None:
            missing()
        change = json.loads(body)
        hook.update({k: v for k, v in change.items() if k != "config"}, config={**hook["config"], **change.get("config", {})})
        save("hooks.json", hooks)
        emit(hook)
    elif rest.endswith("/protection") and method == "PUT":
        protection = load("protection.json", {})
        protection[path] = json.loads(body)
        save("protection.json", protection)
    elif rest.startswith("/labels"):
        print(json.dumps([{"name": n} for n in load("labels.json", {}).get(repo, [])]))
    elif rest.startswith("/contents/"):
        content = load("files.json", {}).get(repo, {}).get(rest[len("/contents/"):])
        if content is None:
            missing()
        print(json.dumps({"content": base64.b64encode(content.encode()).decode()}))
    elif rest == "":
        print(json.dumps({"default_branch": "main"}))
    elif rest == "/git/ref/heads/main":
        print(json.dumps({"object": {"sha": "base0"}}))
    elif rest.startswith("/git/ref/heads/") or rest.startswith("/git/refs/heads/"):
        branch = rest.split("/heads/", 1)[1]
        refs = load("refs.json", {})
        if method == "PATCH":
            refs[branch] = json.loads(body)["sha"]
            save("refs.json", refs)
            record("refs", {"branch": branch, "patched": True})
        elif branch in refs:
            print(json.dumps({"object": {"sha": refs[branch]}}))
        else:
            missing()
    elif rest == "/git/refs" and method == "POST":
        ref = json.loads(body)
        refs = load("refs.json", {})
        refs[ref["ref"].removeprefix("refs/heads/")] = ref["sha"]
        save("refs.json", refs)
        record("refs", {"branch": ref["ref"], "created": True})
    elif rest == "/git/commits/base0":
        print(json.dumps({"tree": {"sha": "tree0"}}))
    elif rest == "/git/trees/tree0":
        print(json.dumps({"tree": load("tree.json", {}).get(repo, []), "truncated": bool(os.environ.get("FAKE_TREE_TRUNCATED"))}))
    elif rest.startswith("/git/blobs/"):
        print(json.dumps({"content": base64.b64encode(load("blobs.json", {})[rest.rsplit("/", 1)[1]].encode()).decode()}))
    elif rest == "/git/trees" and method == "POST":
        tree = json.loads(body)
        record("trees", tree)
        print(json.dumps({"sha": "tree0" if os.environ.get("FAKE_TREE_UNCHANGED") else "tree1"}))
    elif rest == "/git/commits" and method == "POST":
        record("commits", json.loads(body))
        print(json.dumps({"sha": "commit1"}))
    elif rest == "/pulls" and method == "GET":
        wanted = urllib.parse.parse_qs(query)
        branch = (wanted.get("head") or [""])[0].partition(":")[2]
        listed = [pull_row(index + 7, pr) for index, pr in enumerate(load("pulls.json", {}).get(repo, []))]
        listed = [p for p in listed if (not branch or p["head"]["ref"] == branch) and p["state"] == (wanted.get("state") or [p["state"]])[0]]
        if "--include" in args or query:
            paged(listed)
        else:
            print(json.dumps([{**pr, "head": {"ref": pr["head"]} if isinstance(pr["head"], str) else pr["head"]} for pr in load("pulls.json", {}).get(repo, [])]))
    elif rest.startswith("/pulls/") and method == "GET" and rest.split("/")[2].isdigit() and len(rest.split("/")) == 3:
        number = int(rest.split("/")[2])
        listed = load("pulls.json", {}).get(repo, [])
        if not 7 <= number < 7 + len(listed):
            missing()
        emit(pull_row(number, listed[number - 7]))
    elif rest == "/issues" and method == "GET":
        wanted = urllib.parse.parse_qs(query)
        label, want = (wanted.get("labels") or [""])[0], (wanted.get("state") or ["open"])[0]
        since = (wanted.get("since") or [""])[0]
        found = [i for i in load("issues.json", {}).get(repo, []) if label in (i.get("labels") or [i["label"]]) and i["state"] == want
                 and (not since or (i.get("closed_at") or i.get("created_at", "2026-10-01T00:00:00Z")) >= since)]
        paged([issue_row(i) for i in sorted(found, key=lambda i: i["number"])])
    elif rest.startswith("/pulls/") and method == "GET" and rest.endswith("/reviews"):
        paged([])
    elif rest.startswith("/issues/") and method == "GET" and rest.endswith("/comments"):
        paged(load("comments.json", {}).get(f"{repo}#{rest.split('/')[2]}", []))
    elif rest.startswith("/issues/") and method == "GET" and rest.split("/")[2].isdigit():
        found = [i for i in load("issues.json", {}).get(repo, []) if i["number"] == int(rest.split("/")[2])]
        if not found:
            missing()
        emit(issue_row(found[0]))
    elif rest.startswith("/commits/") and rest.endswith("/check-runs"):
        paged(load("checks.json", {}).get(rest.split("/")[2], {}).get("runs", []), "check_runs")
    elif rest.startswith("/commits/") and rest.endswith("/status"):
        paged(load("checks.json", {}).get(rest.split("/")[2], {}).get("statuses", []), "statuses")
    elif rest.startswith("/commits/") and method == "GET":
        emit({"sha": load("heads.json", {}).get(rest.split("/")[2], "c" * 40), "parents": []})
    elif rest.startswith("/collaborators/") and rest.endswith("/permission"):
        emit({"permission": load("permissions.json", {}).get(rest.split("/")[2], "none")})
    elif rest == "/pulls" and method == "POST":
        pull = json.loads(body)
        pulls = load("pulls.json", {})
        pull["html_url"] = f"https://github.com/{repo}/pull/{len(pulls.get(repo, [])) + 7}"
        pulls.setdefault(repo, []).append(pull)
        save("pulls.json", pulls)
        record("pulls", pull)
        print(json.dumps(pull))
    else:
        print(f"unsupported fake gh api {method} {full}", file=sys.stderr)
        sys.exit(2)
else:
    print("unsupported fake gh command", file=sys.stderr)
    sys.exit(2)
