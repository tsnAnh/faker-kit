"""Hermetic gh double for install and team-kit-admin tests; this is not GitHub."""
import json
import os
import subprocess
import sys
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


if args[:2] == ["label", "create"]:
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

    if rest.endswith("/protection") and method == "PUT":
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
        pulls = [{**pr, "head": {"ref": pr["head"]} if isinstance(pr["head"], str) else pr["head"]} for pr in load("pulls.json", {}).get(repo, [])]
        print(json.dumps(pulls))
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
