import json
import re
import sys

# argv: reviews.json execution_file head_sha
reviews_path, exec_path, head = sys.argv[1], sys.argv[2], sys.argv[3]
APP = "claude[bot]"
TERMINAL = {"APPROVED", "CHANGES_REQUESTED"}


def clean(text, limit=80):
    # Model-written text: keep it to one short line and never a workflow command.
    text = re.sub(r"[\r\n]+", " ", str(text)).replace("::", "")
    return text[:limit]


denials = []
try:
    data = json.load(open(exec_path))
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and item.get("type") == "result":
            for d in item.get("permission_denials") or []:
                name = clean(d.get("tool_name", "?"), 40)
                cmd = ""
                if name == "Bash":
                    cmd = " " + clean((d.get("tool_input") or {}).get("command", ""), 60)
                denials.append(name + cmd)
except (OSError, ValueError):
    print("review postcondition: no readable execution log; cannot count tool denials")

reviews = json.load(open(reviews_path))
mine = [r for r in reviews if (r.get("user") or {}).get("login") == APP]
on_head = [r for r in mine if r.get("commit_id") == head]
terminal = [r for r in on_head if r.get("state") in TERMINAL]

if terminal:
    last = terminal[-1]
    print("review postcondition: %s submitted %s on %s" % (APP, last["state"], head[:12]))
    sys.exit(0)

print("review postcondition FAILED: no terminal %s review on head %s" % (APP, head[:12]))
print("  %s reviews on any commit: %d; on this head: %d (states: %s)" % (
    APP, len(mine), len(on_head), ",".join(sorted({r.get("state", "?") for r in on_head})) or "none"))
print("  tool permission denials: %d" % len(denials))
for d in denials[:5]:
    print("  denied: " + d)
if denials:
    print("  Fix: the allowed tools in claude_args did not cover a command the review needed.")
else:
    print("  Fix: the action finished without submitting a review; check its summary for the reason.")
sys.exit(1)
