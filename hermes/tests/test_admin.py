import json
import subprocess

import pytest
import yaml
from conftest import fill_guard, gh_calls, hermes_calls, install

SHOP = "acme/shop"
LABELS = ["agent-task", "agent:running", "agent:review", "agent:needs-human"]


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


def labels(root):
    return json.loads((root.parent / ".fake-gh" / "labels.json").read_text())


def test_add_project_creates_the_project_bot_its_labels_and_paused_jobs(ready):
    root, _ = ready
    out = add_shop(ready)
    assert sorted(out["jobs_created_paused"]) == ["team-kit-status", "team-kit-tick"]
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
    assert sorted(created) == ["0 9,13,17 * * *", "every 30m"]
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
    assert len(jobs(root, "sep-crm")) == 2 and len(jobs(root, "meo-beo")) == 2


def test_add_project_again_refreshes_the_bot_without_duplicating_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP, profile="ong-xay")
    assert code == 0, out
    assert out["jobs_created_paused"] == []
    assert len(jobs(root, "meo-beo")) == 2


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


def test_setup_repo_starts_one_cloud_session_from_a_clone_pinned_to_the_kit(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "setup-repo", "--project", "shop", "--backend", "claude", profile="ong-xay")
    assert code == 0, out
    assert out == {"project": "shop", "repo": "acme/shop", "backend": "claude", "kit_ref": kit_source[1], "session": "https://claude.ai/code/session_01BOOTstrap"}
    home = root.parent
    assert (home / ".fake-claude-cwd").read_text().strip() == str(root / "team-kit" / "checkouts" / "acme__shop" / "setup")
    args = (home / ".fake-claude-args").read_text()
    assert args.startswith("--cloud\nSet up acme/shop for team-kit dispatch")
    assert f"https://github.com/tsnAnh/team-kit.git at commit {kit_source[1]}" in args
    assert "PROJECT.md" not in args
    assert "trusted_dispatchers: codertocat, meo-beo-gh" in args


def test_setup_repo_refuses_codex_without_an_environment_and_unknown_projects(ready):
    add_shop(ready)
    code, err = admin(ready, "setup-repo", "--project", "shop", "--backend", "codex", profile="ong-xay")
    assert code == 1 and "--codex-env" in err
    code, err = admin(ready, "setup-repo", "--project", "crm", "--backend", "claude", profile="ong-xay")
    assert code == 1 and "no project crm" in err


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
    assert sorted(out["resumed"]) == ["team-kit-status", "team-kit-tick"]
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
    assert shop["jobs"] == {"team-kit-tick": False, "team-kit-status": False}
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
    assert all(job["enabled"] for job in jobs(root, "meo-beo")) and len(jobs(root, "meo-beo")) == 2
    assert (root / "profiles" / "meo-beo" / ".env").read_text() == "GH_TOKEN=x\n"


def test_the_gh_calls_never_carry_a_token(ready):
    root, _ = ready
    add_shop(ready)
    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=github_pat_secret\n")
    admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    admin(ready, "status", profile="ong-xay")
    assert all("github_pat_secret" not in json.dumps(c) for c in gh_calls(root))
    assert all("github_pat_secret" not in " ".join(c) for c in hermes_calls(root))
