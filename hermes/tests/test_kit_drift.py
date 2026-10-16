import json
import subprocess

import pytest
from conftest import gh_calls, git
from test_admin import SHOP, add_shop, admin, posted, seed

CRM = "acme/crm"
OLD = "1" * 40
NEWER = "2" * 40
KIT_BRANCH = "team-kit/kit-{short}"


@pytest.fixture
def shop(ready, kit_source):
    add_shop(ready)
    root, _ = ready
    return root, kit_source[1]


def from_github(root, installed):
    """The installer fetched the kit from GitHub, so ordering comes from GitHub's compare."""
    (root / "team-kit" / "kit" / ".git" / "FETCH_HEAD").write_text(f"{installed}\t\t'{installed}' of https://github.com/tsnAnh/faker-kit.git\n")


def repo_files(kit_ref, vendored, auto_merge="low", repo=SHOP, extra=None):
    files = {}
    if kit_ref is not None:
        files[".agents/dispatch.json"] = json.dumps({"backend": "claude", "kit_ref": kit_ref, "auto_merge": auto_merge})
    if vendored is not None:
        files[".agents/team-kit/KIT.json"] = json.dumps({"version": 1, "kit_ref": vendored})
    return {repo: files, **(extra or {})}


def at(root, kit_ref, vendored, auto_merge="low"):
    seed(root, "files.json", repo_files(kit_ref, vendored, auto_merge))
    seed(root, "tree.json", {SHOP: [{"path": ".agents/dispatch.json", "mode": "100644", "type": "blob", "sha": "d"}]})


def kit_pr(root, installed, head="commit9", recorded="commit9", mergeable=True, branch=None):
    branch = branch or KIT_BRANCH.format(short=installed[:12])
    seed(root, "refs.json", {branch: head})
    seed(root, "pulls.json", {SHOP: [{"head": branch, "mergeable": mergeable}]})
    record = root / "team-kit" / "kit-commits" / "acme-shop.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"branch": branch, "commit": recorded}))
    return branch


def reconcile(ready):
    root, env = ready
    done = subprocess.run([str(root / "bin" / "team-kit-admin"), "kit-reconcile"], env={**env, "HERMES_HOME": str(root / "profiles" / "ong-xay")},
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip().splitlines()


def drift(ready, project="shop"):
    code, out = admin(ready, "kit-drift", profile="ong-xay")
    assert code == 0, out
    return next(p for p in out["projects"] if p["project"] == project)


def vendor_runs(root):
    return len(posted(root).get("trees", []))


def test_all_in_sync_is_silent_and_vendors_nothing(ready, shop):
    root, installed = shop
    at(root, installed, installed)
    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 0
    report = drift(ready)
    assert (report["state"], report["action"], report["installed"], report["kit_ref"], report["vendored"]) == ("in_sync", "none", installed, installed, installed)


def test_installed_ahead_of_kit_ref_vendors_once_then_reads_back_in_sync(ready, shop):
    root, installed = shop
    from_github(root, installed)
    seed(root, "compare.json", {f"{OLD}...{installed}": "ahead"})
    at(root, OLD, OLD)
    assert reconcile(ready) == ["[SILENT]"], "mid-repair is silent"
    assert vendor_runs(root) == 1
    [pull] = posted(root)["pulls"]
    assert pull["head"] == KIT_BRANCH.format(short=installed[:12])
    report = drift(ready)
    assert (report["state"], report["action"], report["order"]) == ("repairable", "wait", "ahead")
    assert report["pulls"][0]["trusted"] is True and report["pulls"][0]["for_installed"] is True

    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 1, "a repeat run never vendors again while its pull request is open"

    at(root, installed, installed)  # the owner's policy merged it: tick landed the pull request
    seed(root, "pulls.json", {SHOP: []})
    assert reconcile(ready) == ["[SILENT]"]
    record = json.loads((root / "team-kit" / "kit-drift" / "shop.json").read_text())
    assert record["state"] == "in_sync" and record["agreed"]["kit_ref"] == installed, "success is recorded only once the three agree"
    assert record["values"] == {"installed": installed, "kit_ref": installed, "vendored": installed}
    assert vendor_runs(root) == 1


@pytest.mark.parametrize("vendored", [OLD, None], ids=["vendored-behind", "no-vendored-kit"])
def test_a_vendored_kit_behind_kit_ref_or_missing_is_vendored(ready, shop, vendored):
    root, installed = shop
    at(root, installed, vendored)
    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 1
    assert drift(ready)["action"] == "wait"


def test_an_open_kit_pull_request_for_the_installed_kit_gets_no_second_one(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    kit_pr(root, installed)
    seed(root, "checks.json", {"commit9": {"runs": [{"status": "in_progress", "conclusion": None}]}})
    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 0 and "pulls" not in posted(root)
    report = drift(ready)
    assert (report["state"], report["action"]) == ("repairable", "wait") and report["pulls"][0]["checks"] == "pending"


def test_a_conflicting_kit_pull_request_is_rebuilt_by_vendor_in_place(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    branch = kit_pr(root, installed, head="commit9", recorded="commit9", mergeable=False)
    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 1
    assert posted(root)["refs"] == [{"branch": branch, "patched": True}]
    assert "pulls" not in posted(root), "the same pull request is reused"


def test_a_red_kit_pull_request_is_told_once(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    kit_pr(root, installed)
    seed(root, "checks.json", {"commit9": {"runs": [{"status": "completed", "conclusion": "failure"}]}})
    [notice] = reconcile(ready)
    assert notice == (f"team-kit notice: kit drift in project shop ({SHOP}) needs the owner: the kit pull request's checks failed. "
                      f"Installed kit {installed[:12]}, kit_ref {installed[:12]}, vendored kit {OLD[:12]}; kit pull request https://github.com/{SHOP}/pull/7.")
    assert reconcile(ready) == ["[SILENT]"], "the same unresolved state is not told again"
    assert vendor_runs(root) == 0
    seed(root, "checks.json", {"commit9": {"runs": [{"status": "completed", "conclusion": "success"}]}})
    assert reconcile(ready) == ["[SILENT]"], "green again is mid-repair"
    seed(root, "checks.json", {"commit9": {"runs": [{"status": "completed", "conclusion": "failure"}]}})
    assert len(reconcile(ready)) == 1, "red again after a change is a new state"


def test_installed_older_than_kit_ref_on_github_needs_the_owner_and_vendors_nothing(ready, shop):
    root, installed = shop
    from_github(root, installed)
    seed(root, "compare.json", {f"{NEWER}...{installed}": "behind"})
    at(root, NEWER, NEWER)
    [notice] = reconcile(ready)
    assert "this machine's kit is older than kit_ref 222222222222; only an install the owner approves" in notice
    assert f"Installed kit {installed[:12]}, kit_ref 222222222222, vendored kit 222222222222; kit pull request none." in notice
    assert vendor_runs(root) == 0


def test_installed_older_than_kit_ref_in_a_local_kit_source_needs_the_owner(ready, shop, kit_source):
    root, installed = shop
    src, _ = kit_source
    newer = git(src, "commit-tree", f"{installed}^{{tree}}", "-p", installed, "-m", "newer")  # a side commit; HEAD stays
    at(root, newer, newer)
    [notice] = reconcile(ready)
    assert "older than kit_ref" in notice and vendor_runs(root) == 0
    assert drift(ready)["order"] == "behind"


def test_a_kit_ref_nobody_can_place_needs_the_owner(ready, shop):
    root, installed = shop
    at(root, OLD, installed)  # OLD is in neither the local kit source nor anywhere else
    [notice] = reconcile(ready)
    assert "could not tell whether kit_ref 111111111111 is older" in notice and vendor_runs(root) == 0


def test_a_failed_vendor_run_is_told_once_and_not_retried_for_the_same_state(ready, shop):
    root, installed = shop
    seed(root, "files.json", repo_files(installed, OLD))
    seed(root, "tree.json", {SHOP: [{"path": ".claude/skills", "mode": "120000", "type": "blob", "sha": "l"}]})
    [notice] = reconcile(ready)
    assert "needs the owner: team-kit-admin vendor failed: .claude/skills in acme/shop is a symlink" in notice
    listings = sum(1 for c in gh_calls(root) if c["args"][-1].startswith(f"repos/{SHOP}/git/trees/tree0"))
    assert reconcile(ready) == ["[SILENT]"]
    assert sum(1 for c in gh_calls(root) if c["args"][-1].startswith(f"repos/{SHOP}/git/trees/tree0")) == listings, "vendor is not run again"


def test_two_projects_one_broken_the_other_still_repaired(ready, shop):
    root, installed = shop
    code, out = admin(ready, "add-project", "--bot", "sep-crm", "--project", "crm", "--repo", CRM, profile="ong-xay")
    assert code == 0, out
    seed(root, "files.json", repo_files(installed, OLD, extra={CRM: {".agents/dispatch.json": "{nope"}}))
    [notice] = reconcile(ready)
    assert notice.startswith(f"team-kit notice: kit drift in project crm ({CRM}) needs the owner: could not read {CRM}: ")
    assert vendor_runs(root) == 1 and posted(root)["pulls"][0]["head"] == KIT_BRANCH.format(short=installed[:12])
    assert drift(ready, "shop")["action"] == "wait"


def test_a_kit_pull_request_whose_head_moved_is_not_trusted(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    kit_pr(root, installed, head="someone-elses", recorded="commit9")
    seed(root, "checks.json", {"someone-elses": {"runs": [{"status": "completed", "conclusion": "success"}]}})
    [notice] = reconcile(ready)
    assert "the kit pull request's head someone-else is not the commit team-kit-admin vendor made" in notice
    assert vendor_runs(root) == 0, "never rebuilt over someone's push"
    assert drift(ready)["pulls"][0]["trusted"] is False


def test_a_kit_pull_request_for_another_commit_is_superseded_not_trusted(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    kit_pr(root, installed, branch="team-kit/kit-000000000000", head="commit9", recorded="commit9")
    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 1
    assert posted(root)["pull_edits"] == [{"pr": 7, "state": "closed"}], "the older kit update would roll the kit back"


def test_a_merge_the_policy_leaves_to_the_owner_is_told(ready, shop):
    root, installed = shop
    at(root, installed, OLD, auto_merge="none")
    kit_pr(root, installed)
    seed(root, "checks.json", {"commit9": {"runs": [{"status": "completed", "conclusion": "success"}]}})
    [notice] = reconcile(ready)
    assert "the owner merges the kit pull request: auto_merge in .agents/dispatch.json is not low" in notice
    assert f"kit pull request https://github.com/{SHOP}/pull/7." in notice


def test_a_pulled_andon_cord_holds_the_repair(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    code, out = admin(ready, "andon", "raise", "hold", "everything", profile="ong-xay")
    assert code == 0, out
    assert reconcile(ready) == ["[SILENT]"]
    assert vendor_runs(root) == 0
    assert "held while" in drift(ready)["why"]


def test_kit_drift_is_read_only(ready, shop):
    root, installed = shop
    at(root, installed, OLD)
    before = len(gh_calls(root))
    drift(ready)
    assert all(c["args"][:2] == ["api", c["args"][1]] and "-X" not in c["args"] for c in gh_calls(root)[before:])
    assert not (root / "team-kit" / "kit-drift" / "shop.json").exists()
    assert vendor_runs(root) == 0
