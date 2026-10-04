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
    subprocess.run(["git", "init", "-q", args[3]], check=True)
elif args[:2] == ["issue", "list"]:
    print("[]")
elif args[0] == "api":
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    path = next(a for a in args[1:] if a.startswith("repos/"))
    hooks = load("hooks.json", {})
    repo = "/".join(path.split("/")[1:3])
    if path.endswith("/hooks") and method == "GET":
        print(json.dumps(hooks.get(repo, [])))
    elif path.endswith("/hooks") and method == "POST":
        hook = json.loads(body)
        hook["id"] = len(hooks.get(repo, [])) + 1
        hooks.setdefault(repo, []).append(hook)
        save("hooks.json", hooks)
        print(json.dumps(hook))
    elif "/deliveries" in path:
        print(json.dumps([{"status_code": 200}]))
    elif path.endswith("/protection") and method == "PUT":
        protection = load("protection.json", {})
        protection[path] = json.loads(body)
        save("protection.json", protection)
    elif "/labels" in path:
        print(json.dumps([{"name": n} for n in load("labels.json", {}).get(repo, [])]))
    elif "/contents/" in path:
        print("Not Found", file=sys.stderr)
        sys.exit(1)
    elif path == f"repos/{repo}":
        print(json.dumps({"default_branch": "main"}))
    else:
        print(f"unsupported fake gh api {path}", file=sys.stderr)
        sys.exit(2)
else:
    print("unsupported fake gh command", file=sys.stderr)
    sys.exit(2)
