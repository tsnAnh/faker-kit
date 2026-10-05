import json
import subprocess

import pytest
import yaml
from conftest import fill_guard, gh_calls, hermes_calls, install

SHOP = "acme/shop"
LABELS = ["agent-task", "agent:running", "agent:verifying", "agent:review", "agent:needs-human", "agent:security", "agent:incident", "risk:low", "risk:medium", "risk:high", "agent:andon"]


@pytest.fixture
def ready(kit_source, machine):
    """install.sh with a bootstrap bot, and guard.yaml filled in."""
    root, env = machine
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay")
    assert done.returncode == 0, done.stderr
    fill_guard(root)
    return root, env


def admin(ready, *args, profile=None):
    root, env = ready
    env = dict(env)
    if profile:
        env["HERMES_HOME"] = str(root / "profiles" / profile)
    done = subprocess.run([str(root / "bin" / "team-kit-admin"), *args], env=env, capture_output=True, text=True)
    return done.returncode, (json.loads(done.stdout) if done.returncode == 0 else done.stderr)


def add_shop(ready):
    code, out = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP, profile="ong-xay")
    assert code == 0, out
    return out


def bots(root):
    return yaml.safe_load((root / "team-kit" / "bots.yaml").read_text())["bots"]


def jobs(root, bot):
    return json.loads((root / "profiles" / bot / "jobs.json").read_text())


def seed(root, name, value):
    """Fake GitHub state the fake gh reads: files.json, tree.json, blobs.json, refs.json, pulls.json."""
    state = root.parent / ".fake-gh"
    state.mkdir(exist_ok=True)
    (state / name).write_text(json.dumps(value))


def posted(root):
    path = root.parent / ".fake-gh" / "posted.json"
    return json.loads(path.read_text()) if path.exists() else {}


def vendored(root, kit_ref, extra=None):
    seed(root, "files.json", {SHOP: {".agents/team-kit/KIT.json": json.dumps({"version": 1, "kit_ref": kit_ref}), **(extra or {})}})


def labels(root):
    return json.loads((root.parent / ".fake-gh" / "labels.json").read_text())


def test_add_project_creates_the_project_bot_its_labels_and_paused_jobs(ready):
    root, _ = ready
    out = add_shop(ready)
    assert sorted(out["jobs_created_paused"]) == ["team-kit-incidents", "team-kit-security", "team-kit-status", "team-kit-tick"]
    assert out["labels"] == LABELS
    assert bots(root)["meo-beo"] == {"role": "project", "project": "shop", "repo": SHOP}
    assert labels(root) == {SHOP: LABELS}

    home = root / "profiles" / "meo-beo"
    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["toolsets"] == ["terminal", "file", "clarify", "skills"]
    assert config["plugins"]["enabled"] == ["team_kit_guard"]
    assert config["approvals"] == {"mode": "manual", "cron_mode": "deny"}
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["dispatch"]
    assert (home / "SOUL.md").read_text().startswith("You are a team-kit project bot.")
    assert (root / "team-kit" / "briefs" / "meo-beo").is_dir()
    assert not (home / ".env").exists(), "team-kit writes no .env; the owner puts the token there"

    created = {c[4]: c for c in hermes_calls(root) if c[2:4] == ["cron", "create"]}
    assert sorted(created) == ["0 7 * * *", "0 9,13,17 * * *", "every 10m", "every 30m"]
    assert created["0 7 * * *"][5].startswith("Run `team-kit-gh security` in the terminal.")
    tick = created["every 30m"]
    assert tick[:2] == ["-p", "meo-beo"]
    assert tick[5].startswith("Run `team-kit-gh tick` in the terminal.")
    assert tick[tick.index("--name") + 1] == "team-kit-tick"
    assert tick[tick.index("--deliver") + 1] == "bot-chat:meo-beo" and tick[tick.index("--skill") + 1] == "dispatch"
    assert all(not job["enabled"] and job["paused_reason"] for job in jobs(root, "meo-beo"))
    assert not (root / "config.yaml").exists(), "the host profile's config is never touched"


def test_a_second_project_gets_its_own_bot_and_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-project", "--bot", "sep-crm", "--project", "crm", "--repo", "acme/crm", profile="ong-xay")
    assert code == 0, out
    assert bots(root)["sep-crm"]["repo"] == "acme/crm"
    assert len(jobs(root, "sep-crm")) == 4 and len(jobs(root, "meo-beo")) == 4


def test_add_project_can_set_custom_tick_and_incident_intervals(ready):
    root, _ = ready
    code, out = admin(
        ready,
        "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP,
        "--tick-every", "10m", "--incidents-every", "5m",
        profile="ong-xay",
    )
    assert code == 0, out
    assert out["tick_every"] == "10m" and out["incidents_every"] == "5m"
    assert bots(root)["meo-beo"] == {"role": "project", "project": "shop", "repo": SHOP, "tick_every": "10m", "incidents_every": "5m"}
    created = {c[c.index("--name") + 1]: c for c in hermes_calls(root) if c[2:4] == ["cron", "create"]}
    assert created["team-kit-tick"][4] == "every 10m"
    assert created["team-kit-incidents"][4] == "every 5m"


def test_add_project_reschedules_existing_tick_and_incident_jobs(ready):
    root, _ = ready
    add_shop(ready)
    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=x\n")
    admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    code, out = admin(
        ready,
        "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP,
        "--tick-every", "10m", "--incidents-every", "5m",
        profile="ong-xay",
    )
    assert code == 0, out
    assert sorted(out["jobs_rescheduled"]) == ["team-kit-incidents", "team-kit-tick"]
    calls = hermes_calls(root)
    assert not [c for c in calls if c[2:4] in (["cron", "delete"], ["cron", "remove"])], "a reschedule never deletes a job"
    assert sorted(c[4] for c in calls if c[2:4] == ["cron", "edit"]) == ["job-0", "job-3"]
    by_name = {job["name"]: job for job in jobs(root, "meo-beo")}
    assert len(by_name) == 4
    assert by_name["team-kit-tick"]["schedule"] == "every 10m" and by_name["team-kit-tick"]["id"] == "job-0"
    assert by_name["team-kit-incidents"]["schedule"] == "every 5m" and by_name["team-kit-incidents"]["id"] == "job-3"
    assert by_name["team-kit-tick"]["enabled"] and by_name["team-kit-incidents"]["enabled"]


def test_rescheduling_a_paused_job_keeps_it_paused_with_its_reason(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(
        ready,
        "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP, "--tick-every", "15m",
        profile="ong-xay",
    )
    assert code == 0, out
    assert out["jobs_rescheduled"] == ["team-kit-tick"]
    tick = next(j for j in jobs(root, "meo-beo") if j["name"] == "team-kit-tick")
    assert tick["schedule"] == "every 15m" and not tick["enabled"]
    assert tick["paused_reason"] == "Awaiting the bot's GitHub login"
    # Nothing changes when the schedule already matches.
    before = len(hermes_calls(root))
    admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP, "--tick-every", "15m", profile="ong-xay")
    assert not [c for c in hermes_calls(root)[before:] if c[2:4] in (["cron", "edit"], ["cron", "create"], ["cron", "delete"])]


def test_add_project_again_refreshes_the_bot_without_duplicating_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP, profile="ong-xay")
    assert code == 0, out
    assert out["jobs_created_paused"] == []
    assert len(jobs(root, "meo-beo")) == 4


def test_a_project_has_one_repository(ready):
    root, _ = ready
    add_shop(ready)
    code, err = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", "acme/shop-admin", profile="ong-xay")
    assert code == 1 and "a project has one repository" in err
    assert bots(root)["meo-beo"]["repo"] == SHOP
    code, err = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repos", "acme/a,acme/b", profile="ong-xay")
    assert code != 0


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (["--bot", "sep-crm", "--project", "crm", "--repo", "acme/shop"], "belongs to both"),
        (["--bot", "meo-beo", "--project", "crm", "--repo", "acme/crm"], "already the project bot of shop"),
        (["--bot", "ong-xay", "--project", "crm", "--repo", "acme/crm"], "already the bootstrap bot"),
        (["--bot", "sep-crm", "--project", "shop", "--repo", "acme/crm"], "project shop belongs to both"),
        (["--bot", "Sep Crm", "--project", "crm", "--repo", "acme/crm"], "must be lowercase letters"),
        (["--bot", "default", "--project", "crm", "--repo", "acme/crm"], "the owner's own Hermes profile"),
        (["--bot", "sep-crm", "--project", "crm", "--repo", "acme/crm;id"], "is not OWNER/REPO"),
        (["--bot", "sep-crm", "--project", "crm", "--repo", "acme/crm", "--tick-every", "0m"], "--tick-every must be"),
    ],
)
def test_add_project_refuses_before_changing_anything(ready, args, why):
    root, _ = ready
    add_shop(ready)
    before = (root / "team-kit" / "bots.yaml").read_text(), labels(root)
    code, err = admin(ready, "add-project", *args, profile="ong-xay")
    assert code == 1 and why in err
    assert ((root / "team-kit" / "bots.yaml").read_text(), labels(root)) == before
    assert not (root / "profiles" / "sep-crm").exists()


def test_setup_repo_starts_one_cloud_session_on_the_vendored_kit(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    vendored(root, kit_source[1])
    code, out = admin(ready, "setup-repo", "--project", "shop", "--backend", "claude", profile="ong-xay")
    assert code == 0, out
    assert out == {"project": "shop", "repo": "acme/shop", "backend": "claude", "kit_ref": kit_source[1], "session": "https://claude.ai/code/session_01BOOTstrap"}
    home = root.parent
    assert (home / ".fake-claude-cwd").read_text().strip() == str(root / "team-kit" / "checkouts" / "acme__shop" / "setup")
    args = (home / ".fake-claude-args").read_text()
    assert args.startswith("--cloud\nSet up acme/shop for team-kit dispatch")
    assert f"vendored in this repository at .agents/team-kit/, at commit {kit_source[1]}" in args
    assert ".agents/team-kit/plugins/trust-ladder/skills/setup-dispatch/SKILL.md" in args
    assert "clone" not in args.lower(), "the setup session never clones the kit, which may be private"
    assert "trusted_dispatchers: codertocat, meo-beo-gh" in args


@pytest.mark.parametrize(("kit", "why"), [(None, "has no vendored kit"), ("0" * 40, "has the kit at 000000000000")])
def test_setup_repo_waits_for_the_vendored_kit(ready, kit_source, kit, why):
    root, _ = ready
    add_shop(ready)
    if kit:
        vendored(root, kit)
    code, err = admin(ready, "setup-repo", "--project", "shop", "--backend", "claude", profile="ong-xay")
    assert code == 1 and why in err and "team-kit-admin vendor --project shop" in err
    assert not (root.parent / ".fake-claude-args").exists()


def test_setup_repo_refuses_codex_without_an_environment_and_unknown_projects(ready):
    add_shop(ready)
    code, err = admin(ready, "setup-repo", "--project", "shop", "--backend", "codex", profile="ong-xay")
    assert code == 1 and "--codex-env" in err
    code, err = admin(ready, "setup-repo", "--project", "crm", "--backend", "claude", profile="ong-xay")
    assert code == 1 and "no project crm" in err


def tree_entries(root):
    [tree] = posted(root)["trees"]
    assert tree["base_tree"] == "tree0"
    return {e["path"]: e for e in tree["tree"]}


def test_vendor_commits_the_pinned_kit_through_the_api_and_opens_a_pull_request(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 0, out
    short = kit_source[1][:12]
    assert out["pr"] == "https://github.com/acme/shop/pull/7" and out["branch"] == f"team-kit/kit-{short}"
    entries = tree_entries(root)
    skill = entries[".agents/team-kit/plugins/trust-ladder/skills/follow-brief/SKILL.md"]
    assert skill["mode"] == "100644" and skill["content"].startswith("---\nname: follow-brief")
    assert entries[".agents/team-kit/vendor_kit.py"]["mode"] == "100755"
    assert entries[".claude/skills/follow-brief"] == {"path": ".claude/skills/follow-brief", "mode": "120000", "type": "blob",
                                                      "content": "../../.agents/team-kit/plugins/trust-ladder/skills/follow-brief"}
    assert entries[".agents/skills/follow-brief"]["content"] == "../../.agents/team-kit/plugins/trust-ladder/skills/follow-brief"
    manifest = json.loads(entries[".agents/team-kit/KIT.json"]["content"])
    assert manifest["kit_ref"] == kit_source[1]
    assert "plugins/trust-ladder/skills/follow-brief/SKILL.md" in manifest["files"]
    assert not any(path.startswith("plugins/trust-ladder/test/") for path in manifest["files"]), "the kit's own tests stay out"
    assert any(path.startswith("plugins/trust-ladder/packs/agnostic/test/") for path in manifest["files"]), "lint-pack fixtures stay in"
    [commit] = posted(root)["commits"]
    assert commit == {"message": f"chore(agents): vendor team-kit {short}", "tree": "tree1", "parents": ["base0"]}
    [pull] = posted(root)["pulls"]
    assert (pull["head"], pull["base"]) == (f"team-kit/kit-{short}", "main")
    assert all("git" != c["args"][0] for c in gh_calls(root)), "no git push: everything goes through the API"


def test_vendor_keeps_the_repositorys_own_skills_and_removes_stale_kit_files(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    seed(root, "tree.json", {SHOP: [
        {"path": ".claude/skills/follow-brief", "mode": "040000", "type": "tree", "sha": "own"},
        {"path": ".agents/skills/old-skill", "mode": "120000", "type": "blob", "sha": "link-old"},
        {"path": ".agents/skills/verify-shop", "mode": "120000", "type": "blob", "sha": "link-own"},
        {"path": ".agents/team-kit/plugins/trust-ladder/skills/gone/SKILL.md", "mode": "100644", "type": "blob", "sha": "x"},
        {"path": ".agents/dispatch.json", "mode": "100644", "type": "blob", "sha": "d"},
    ]})
    seed(root, "blobs.json", {"link-old": "../../.agents/team-kit/plugins/pstack/skills/old-skill", "link-own": "../../.claude/skills/verify-shop"})
    seed(root, "files.json", {SHOP: {".agents/dispatch.json": json.dumps({"backend": "claude", "kit_ref": "0" * 40})}})
    code, out = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 0, out
    assert out["kept_repository_own"] == [".claude/skills/follow-brief", ".agents/skills/follow-brief"]
    assert out["removed"] == [".agents/skills/old-skill", ".agents/team-kit/plugins/trust-ladder/skills/gone/SKILL.md"]
    entries = tree_entries(root)
    assert ".claude/skills/follow-brief" not in entries, "a real folder of the repository is never replaced"
    assert ".agents/skills/follow-brief" not in entries, "nor shadowed for Codex and Pi by the kit's skill of that name"
    assert entries[".agents/skills/old-skill"]["sha"] is None
    assert ".agents/skills/verify-shop" not in entries, "a link that points elsewhere is the repository's own"
    assert json.loads(entries[".agents/dispatch.json"]["content"])["kit_ref"] == kit_source[1], "the same pull request moves kit_ref"
    assert ".claude/skills/follow-brief" not in json.loads(entries[".agents/team-kit/KIT.json"]["content"])["links"]


def test_vendor_with_nothing_to_change_opens_no_pull_request(ready, kit_source):
    root, env = ready
    add_shop(ready)
    env["FAKE_TREE_UNCHANGED"] = "1"
    code, out = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 0 and out["pr"] is None and "already has the kit" in out["note"]
    assert "commits" not in posted(root)


def test_vendor_again_updates_its_branch_and_reuses_the_open_pull_request(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    branch = f"team-kit/kit-{kit_source[1][:12]}"
    seed(root, "refs.json", {branch: "old"})
    seed(root, "pulls.json", {SHOP: [{"html_url": "https://github.com/acme/shop/pull/3", "head": branch}]})
    code, out = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 0 and out["pr"] == "https://github.com/acme/shop/pull/3"
    assert posted(root)["refs"] == [{"branch": branch, "patched": True}]
    assert "pulls" not in posted(root)


def test_vendor_lists_older_kit_pull_requests_it_supersedes(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    seed(root, "pulls.json", {SHOP: [{"html_url": "https://github.com/acme/shop/pull/2", "head": "team-kit/kit-000000000000"},
                                     {"html_url": "https://github.com/acme/shop/pull/4", "head": "feature/other"}]})
    code, out = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 0 and out["superseded"] == ["https://github.com/acme/shop/pull/2"]


@pytest.mark.parametrize("problem", ["symlinked skills folder", "invalid dispatch.json", "dirty kit"])
def test_vendor_refuses_before_writing_anything(ready, problem):
    root, _ = ready
    add_shop(ready)
    if problem == "symlinked skills folder":
        seed(root, "tree.json", {SHOP: [{"path": ".claude/skills", "mode": "120000", "type": "blob", "sha": "l"}]})
        why = ".claude/skills in acme/shop is a symlink"
    elif problem == "invalid dispatch.json":
        seed(root, "tree.json", {SHOP: [{"path": ".agents/dispatch.json", "mode": "100644", "type": "blob", "sha": "d"}]})
        seed(root, "files.json", {SHOP: {".agents/dispatch.json": "{not json"}})
        why = "is not valid JSON"
    else:
        (root / "team-kit" / "kit" / "plugins" / "trust-ladder" / "stray.md").write_text("local")
        why = "uncommitted changes"
    code, err = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 1 and why in err
    assert "trees" not in posted(root)


def test_vendor_refuses_a_repository_too_large_to_list(ready):
    root, env = ready
    add_shop(ready)
    env["FAKE_TREE_TRUNCATED"] = "1"
    code, err = admin(ready, "vendor", "--project", "shop", profile="ong-xay")
    assert code == 1 and "vendor_kit.py --into" in err


def test_protect_requires_the_checks_for_admins_too(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "protect", "--project", "shop", "--check", "check", profile="ong-xay")
    assert code == 0, out
    protection = json.loads((root.parent / ".fake-gh" / "protection.json").read_text())
    body = protection["repos/acme/shop/branches/main/protection"]
    assert body["required_status_checks"] == {"strict": False, "contexts": ["check"]}
    assert body["enforce_admins"] is True
    code, err = admin(ready, "protect", "--project", "shop", profile="ong-xay")
    assert code == 1 and "at least one --check" in err


def test_activate_waits_for_the_bots_github_token_then_resumes_its_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, err = admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    assert code == 1 and "no GH_TOKEN" in err
    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=github_pat_secret\n")
    code, out = admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    assert code == 0, out
    assert sorted(out["resumed"]) == ["team-kit-incidents", "team-kit-security", "team-kit-status", "team-kit-tick"]
    assert all(job["enabled"] for job in jobs(root, "meo-beo"))
    assert "github_pat_secret" not in json.dumps(out)


def test_status_reports_every_bot_and_repository_and_refreshes_counts(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "status", profile="ong-xay")
    assert code == 0, out
    assert out["trusted_authors"] == ["codertocat", "meo-beo-gh"]
    shop = next(b for b in out["bots"] if b["bot"] == "meo-beo")
    assert shop["github_login"] is False
    assert shop["jobs"] == {"team-kit-tick": False, "team-kit-status": False, "team-kit-security": False, "team-kit-incidents": False}
    assert shop["repo"] == {"repo": "acme/shop", "labels_missing": [], "running": 0, "dispatch_json": False}
    assert {"bot": "ong-xay", "role": "bootstrap"} in out["bots"]
    counts = json.loads((root / "team-kit" / "in-flight" / "meo-beo.json").read_text())["repos"]
    assert counts == {"acme/shop": 0}


@pytest.mark.parametrize(("profile", "args", "why"), [
    ("meo-beo", ["status"], "not a bootstrap bot"),
    ("stranger", ["status"], "not a bootstrap bot"),
    ("ong-xay", ["sync"], "runs only from hermes/install.sh"),
    ("ong-xay", ["add-bootstrap", "--bot", "x"], "runs only from hermes/install.sh"),
])
def test_only_the_bootstrap_bot_or_the_owner_runs_it(ready, profile, args, why):
    add_shop(ready)
    code, err = admin(ready, *args, profile=profile)
    assert code == 1 and why in err


def test_reinstalling_refreshes_project_bots_and_keeps_their_job_state(ready, kit_source, machine):
    root, _ = ready
    add_shop(ready)
    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=x\n")
    admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    (root / "profiles" / "meo-beo" / "SOUL.md").write_text("tampered")
    stale = root / "profiles" / "meo-beo" / "scripts" / "team-kit"
    stale.mkdir(parents=True)
    (stale / "github-triage.py").write_text("old webhook triage")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    assert (root / "profiles" / "meo-beo" / "SOUL.md").read_text().startswith("You are a team-kit project bot.")
    assert not stale.exists(), "the webhook version's scripts are removed"
    assert all(job["enabled"] for job in jobs(root, "meo-beo")) and len(jobs(root, "meo-beo")) == 4
    assert (root / "profiles" / "meo-beo" / ".env").read_text() == "GH_TOKEN=x\n"


def test_the_gh_calls_never_carry_a_token(ready):
    root, _ = ready
    add_shop(ready)
    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=github_pat_secret\n")
    admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    admin(ready, "status", profile="ong-xay")
    assert all("github_pat_secret" not in json.dumps(c) for c in gh_calls(root))
    assert all("github_pat_secret" not in " ".join(c) for c in hermes_calls(root))


def issues(root):
    path = root.parent / ".fake-gh" / "issues.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_the_machine_cord_opens_an_andon_issue_in_every_project_and_stops_setup(ready, kit_source):
    root, env = ready
    add_shop(ready)
    code, out = admin(ready, "andon", "raise", "cloud", "bill", "spike", profile="ong-xay")
    assert code == 0, out
    assert out == {"stopped": True, "reason": "cloud bill spike", "issues": {SHOP: 50}}
    [issue] = issues(root)[SHOP]
    assert issue["label"] == "agent:andon" and issue["title"] == "Andon: cloud bill spike"
    assert issue["body"].startswith("<!-- team-kit:andon v1 scope=machine -->") and "team-kit-admin andon clear" in issue["body"]
    saved = json.loads((root / "team-kit" / "andon" / "_machine.json").read_text())
    assert saved["by"] == "ong-xay" and saved["issues"] == {SHOP: 50}
    code, err = admin(ready, "andon", "raise", "again", profile="ong-xay")
    assert code == 1 and "clear it before" in err
    vendored(root, subprocess.run(["git", "-C", str(root / "team-kit" / "kit"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())
    code, err = admin(ready, "setup-repo", "--project", "shop", "--backend", "claude", profile="ong-xay")
    assert code == 1 and "andon pulled for this machine by ong-xay: cloud bill spike" in err
    code, out = admin(ready, "status", profile="ong-xay")
    assert out["andon"]["machine"]["reason"] == "cloud bill spike"
    code, out = admin(ready, "andon", "clear", profile="ong-xay")
    assert code == 0, out
    assert out == {"stopped": False, "closed": [f"{SHOP}#50"]}
    assert issues(root)[SHOP][0]["state"] == "closed"
    assert not (root / "team-kit" / "andon" / "_machine.json").exists()
    code, err = admin(ready, "andon", "clear", profile="ong-xay")
    assert code == 1 and "not pulled" in err


def test_the_machine_cord_holds_when_github_will_not_open_its_issues(ready):
    root, env = ready
    add_shop(ready)
    env["FAKE_ISSUE_CREATE_FAILS"] = "1"
    code, out = admin(ready, "andon", "raise", "stop", profile="ong-xay")
    assert code == 0, out
    assert out["issues"] == {} and "not found" in out["no_issue"][SHOP]
    assert json.loads((root / "team-kit" / "andon" / "_machine.json").read_text())["reason"] == "stop"


def test_andon_shows_project_cords_and_refuses_a_bare_raise(ready):
    root, _ = ready
    add_shop(ready)
    (root / "team-kit" / "andon").mkdir(exist_ok=True)
    (root / "team-kit" / "andon" / "meo-beo.json").write_text(json.dumps({"reason": "flaky deploy", "by": "meo-beo", "at": 0}))
    code, out = admin(ready, "andon", profile="ong-xay")
    assert out == {"machine": None, "projects": {"meo-beo": "flaky deploy"}}
    code, err = admin(ready, "andon", "raise", profile="ong-xay")
    assert code == 1 and "say why" in err


def test_add_chief_creates_a_read_only_bot_with_the_chief_skill_and_no_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-chief", "--bot", "chanh-van", profile="ong-xay")
    assert code == 0, out
    assert out["role"] == "chief" and "acme/shop" in out["next"][0] and "read" in out["next"][0]
    assert bots(root)["chanh-van"] == {"role": "chief"}
    home = root / "profiles" / "chanh-van"
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["chief"]
    assert (home / "SOUL.md").read_text().startswith("You are this machine's team-kit chief of staff.")
    assert (home / "plugins" / "team_kit_guard" / "plugin.yaml").exists()
    assert not any(c[:2] == ["-p", "chanh-van"] and c[2:4] == ["cron", "create"] for c in hermes_calls(root)), "the chief has no jobs"
    code, err = admin(ready, "add-chief", "--bot", "meo-beo", profile="ong-xay")
    assert code == 1 and "already a project bot" in err
