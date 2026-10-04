import json
import subprocess

import pytest
import yaml
from conftest import FIXTURES, fill_guard, gh_calls, hermes_calls, install
from team_kit_contracts import pr_marker

SHOP = "acme/shop-api,acme/shop-web,acme/shop-mobile"


@pytest.fixture
def ready(kit_source, machine):
    """install.sh with a bootstrap bot, and guard.yaml filled in."""
    root, env = machine
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay")
    assert done.returncode == 0, done.stderr
    fill_guard(root)
    return root, env


def admin(ready, *args, profile=None, stdin=None):
    root, env = ready
    env = dict(env)
    if profile:
        env["HERMES_HOME"] = str(root / "profiles" / profile)
    done = subprocess.run([str(root / "bin" / "team-kit-admin"), *args], env=env, capture_output=True, text=True, input=stdin)
    return done.returncode, (json.loads(done.stdout) if done.returncode == 0 else done.stderr)


def add_shop(ready):
    code, out = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repos", SHOP, "--home", "acme/shop-api", profile="ong-xay")
    assert code == 0, out
    return out


def bots(root):
    return yaml.safe_load((root / "team-kit" / "bots.yaml").read_text())["bots"]


def routes(root):
    return yaml.safe_load((root / "config.yaml").read_text())["platforms"]["webhook"]["extra"]["routes"]


def jobs(root, bot):
    return json.loads((root / "profiles" / bot / "jobs.json").read_text())


def test_add_project_creates_an_isolated_project_bot(ready):
    root, _ = ready
    out = add_shop(ready)
    assert out["route"] == "/p/meo-beo/webhooks/team-kit-shop"
    assert sorted(out["jobs_created_paused"]) == ["team-kit-drain", "team-kit-status"]
    assert bots(root)["meo-beo"] == {"role": "project", "project": "shop", "repos": SHOP.split(","), "home": "acme/shop-api"}

    home = root / "profiles" / "meo-beo"
    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["toolsets"] == ["kanban", "terminal", "file", "clarify", "skills"]
    assert config["plugins"]["enabled"] == ["team_kit_guard"]
    assert "HERMES_KANBAN_BOARD=shop\n" in (home / ".env").read_text()
    assert oct((home / ".env").stat().st_mode & 0o777) == "0o600"
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["dispatch"]
    assert (home / "SOUL.md").read_text().startswith("You are a team-kit project bot.")
    assert (root / "team-kit" / "briefs" / "meo-beo").is_dir()
    entry = (home / "scripts" / "team-kit" / "github-triage.py").read_text()
    assert 'BOT = "meo-beo"' in entry and "@TEAM_KIT_BOT@" not in entry

    assert {"slug": "shop", "name": "shop"} in json.loads((root / "boards.json").read_text())
    route = routes(root)["team-kit-shop"]
    secret = (root / "team-kit" / "webhook-secret").read_text().strip()
    assert route["profile"] == "meo-beo" and route["secret"] == secret
    assert route["script"] == "team-kit/github-triage.py" and route["cron_job"] == "team-kit-drain"
    assert "toolsets" not in route and "coalesce" not in route
    assert out["gateway"] == "restarted"
    assert all(secret not in " ".join(c) for c in hermes_calls(root)), "the secret never goes on a command line"
    assert oct((root / "config.yaml").stat().st_mode & 0o777) == "0o600"

    created = {c[4]: c for c in hermes_calls(root) if c[2:4] == ["cron", "create"]}
    assert sorted(created) == ["0 9,13,17 * * *", "every 2h"]
    drain = created["every 2h"]
    assert drain[:2] == ["-p", "meo-beo"]
    assert drain[drain.index("--deliver") + 1] == "bot-chat:meo-beo" and drain[drain.index("--skill") + 1] == "dispatch"
    assert all(not job["enabled"] and job["paused_reason"] for job in jobs(root, "meo-beo"))


def test_a_second_project_gets_its_own_board_route_and_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-project", "--bot", "sep-crm", "--project", "crm", "--repos", "acme/crm", profile="ong-xay")
    assert code == 0, out
    assert bots(root)["sep-crm"]["home"] == "acme/crm"
    assert routes(root)["team-kit-crm"]["profile"] == "sep-crm"
    assert routes(root)["team-kit-shop"]["profile"] == "meo-beo"
    assert "HERMES_KANBAN_BOARD=crm\n" in (root / "profiles" / "sep-crm" / ".env").read_text()
    assert len(jobs(root, "sep-crm")) == 2 and len(jobs(root, "meo-beo")) == 2


def test_add_project_again_extends_the_project_without_duplicating_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repos", "acme/shop-admin", profile="ong-xay")
    assert code == 0, out
    assert out["jobs_created_paused"] == []
    assert bots(root)["meo-beo"]["repos"][-1] == "acme/shop-admin"
    assert bots(root)["meo-beo"]["home"] == "acme/shop-api"
    assert len(jobs(root, "meo-beo")) == 2


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (["--bot", "sep-crm", "--project", "crm", "--repos", "acme/shop-web"], "belongs to both"),
        (["--bot", "meo-beo", "--project", "crm", "--repos", "acme/crm"], "already the project bot of shop"),
        (["--bot", "ong-xay", "--project", "crm", "--repos", "acme/crm"], "already the bootstrap bot"),
        (["--bot", "sep-crm", "--project", "shop", "--repos", "acme/crm"], "project shop belongs to both"),
        (["--bot", "Sep Crm", "--project", "crm", "--repos", "acme/crm"], "must be lowercase letters"),
        (["--bot", "default", "--project", "crm", "--repos", "acme/crm"], "the owner's own Hermes profile"),
        (["--bot", "sep-crm", "--project", "crm", "--repos", ","], "at least one OWNER/REPO"),
        (["--bot", "sep-crm", "--project", "crm", "--repos", "acme/crm;id"], "is not OWNER/REPO"),
        (["--bot", "sep-crm", "--project", "crm", "--repos", "acme/crm", "--home", "acme/other"], "--home must be one of --repos"),
    ],
)
def test_add_project_refuses_before_changing_anything(ready, args, why):
    root, _ = ready
    add_shop(ready)
    before = (root / "team-kit" / "bots.yaml").read_text(), (root / "config.yaml").read_text()
    code, err = admin(ready, "add-project", *args, profile="ong-xay")
    assert code == 1 and why in err
    assert ((root / "team-kit" / "bots.yaml").read_text(), (root / "config.yaml").read_text()) == before
    assert not (root / "profiles" / "sep-crm").exists()


def test_add_project_refuses_a_route_someone_else_owns_and_never_echoes_its_secret(ready):
    root, _ = ready
    host = {"platforms": {"webhook": {"extra": {"routes": {"team-kit-shop": {"profile": "other", "script": "x.py", "secret": "do-not-leak"}}}}}}
    (root / "config.yaml").write_text(yaml.safe_dump(host))
    code, err = admin(ready, "add-project", "--bot", "meo-beo", "--project", "shop", "--repos", "acme/shop-api", profile="ong-xay")
    assert code == 1 and "conflicting host webhook route team-kit-shop" in err
    assert "do-not-leak" not in err
    assert yaml.safe_load((root / "config.yaml").read_text()) == host
    assert not (root / "profiles" / "meo-beo").exists()
    assert "meo-beo" not in bots(root)


def test_add_repo_labels_it_and_points_its_webhook_here_with_the_secret_on_stdin(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-repo", "--project", "shop", "--repo", "acme/shop-web", profile="ong-xay")
    assert code == 0, out
    url = "https://hermes.example.ts.net/p/meo-beo/webhooks/team-kit-shop"
    assert out == {"project": "shop", "repo": "acme/shop-web", "labels": ["agent-task", "agent:running", "agent:needs-human"], "webhook": f"created: {url}"}
    secret = (root / "team-kit" / "webhook-secret").read_text().strip()
    calls = gh_calls(root)
    assert all(secret not in " ".join(c["args"]) for c in calls), "the secret never goes on a command line"
    post = next(c for c in calls if "POST" in c["args"])
    hook = json.loads(post["stdin"])
    assert hook["config"] == {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"}
    assert hook["events"] == ["pull_request", "issue_comment", "check_suite"]
    code, out = admin(ready, "add-repo", "--project", "shop", "--repo", "acme/shop-web", profile="ong-xay")
    assert out["webhook"] == f"exists: {url}"
    assert secret not in json.dumps(out)


def test_add_repo_skips_the_webhook_without_a_public_url(ready):
    root, _ = ready
    add_shop(ready)
    fill_guard(root, url=None)
    code, out = admin(ready, "add-repo", "--project", "shop", "--repo", "acme/shop-web", profile="ong-xay")
    assert code == 0 and out["webhook"].startswith("skipped: set webhook_base_url")


def test_add_repo_refuses_another_projects_repository(ready):
    add_shop(ready)
    admin(ready, "add-project", "--bot", "sep-crm", "--project", "crm", "--repos", "acme/crm", profile="ong-xay")
    code, err = admin(ready, "add-repo", "--project", "crm", "--repo", "acme/shop-web", profile="ong-xay")
    assert code == 1 and "already belongs to project shop" in err


def test_bootstrap_repo_starts_one_cloud_session_from_a_clone_pinned_to_the_kit(ready, kit_source):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "bootstrap-repo", "--project", "shop", "--repo", "acme/shop-api", "--backend", "claude", profile="ong-xay")
    assert code == 0, out
    assert out == {"project": "shop", "repo": "acme/shop-api", "backend": "claude", "kit_ref": kit_source[1], "session": "https://claude.ai/code/session_01BOOTstrap"}
    home = root.parent
    assert (home / ".fake-claude-cwd").read_text().strip() == str(root / "team-kit" / "checkouts" / "acme__shop-api" / "bootstrap")
    args = (home / ".fake-claude-args").read_text()
    assert args.startswith("--cloud\nSet up acme/shop-api for team-kit dispatch")
    assert f"https://github.com/tsnAnh/team-kit.git at commit {kit_source[1]}" in args
    assert "also write PROJECT.md" in args
    assert "trusted_dispatchers: codertocat, meo-beo-gh" in args


def test_bootstrap_repo_refuses_codex_without_an_environment_and_unknown_repositories(ready):
    add_shop(ready)
    code, err = admin(ready, "bootstrap-repo", "--project", "shop", "--repo", "acme/shop-api", "--backend", "codex", profile="ong-xay")
    assert code == 1 and "--codex-env" in err
    code, err = admin(ready, "bootstrap-repo", "--project", "shop", "--repo", "acme/crm", "--backend", "claude", profile="ong-xay")
    assert code == 1 and "not a repository of project shop" in err


def test_protect_requires_the_checks_for_admins_too(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "protect", "--repo", "acme/shop-web", "--check", "check", profile="ong-xay")
    assert code == 0, out
    protection = json.loads((root.parent / ".fake-gh" / "protection.json").read_text())
    body = protection["repos/acme/shop-web/branches/main/protection"]
    assert body["required_status_checks"] == {"strict": False, "contexts": ["check"]}
    assert body["enforce_admins"] is True
    code, err = admin(ready, "protect", "--repo", "acme/shop-web", profile="ong-xay")
    assert code == 1 and "at least one --check" in err


def test_activate_waits_for_the_bots_github_token_then_resumes_its_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, err = admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    assert code == 1 and "no GH_TOKEN" in err
    env = root / "profiles" / "meo-beo" / ".env"
    env.write_text(env.read_text() + "GH_TOKEN=github_pat_secret\n")
    code, out = admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    assert code == 0, out
    assert sorted(out["resumed"]) == ["team-kit-drain", "team-kit-status"]
    assert all(job["enabled"] for job in jobs(root, "meo-beo"))
    assert "github_pat_secret" not in json.dumps(out)


def test_status_reports_every_bot_and_repository(ready):
    root, _ = ready
    add_shop(ready)
    admin(ready, "add-repo", "--project", "shop", "--repo", "acme/shop-web", profile="ong-xay")
    code, out = admin(ready, "status", profile="ong-xay")
    assert code == 0, out
    assert out["trusted_authors"] == ["codertocat", "meo-beo-gh"]
    shop = next(b for b in out["bots"] if b["bot"] == "meo-beo")
    assert shop["github_login"] is False
    assert shop["jobs"] == {"team-kit-drain": False, "team-kit-status": False}
    web = next(r for r in shop["repos"] if r["repo"] == "acme/shop-web")
    assert web == {"repo": "acme/shop-web", "labels_missing": [], "running": 0, "dispatch_json": False, "webhook": "last delivery 200"}
    api = next(r for r in shop["repos"] if r["repo"] == "acme/shop-api")
    assert api["labels_missing"] == ["agent-task", "agent:running", "agent:needs-human"] and api["webhook"] == "missing"
    assert {"bot": "ong-xay", "role": "bootstrap"} in out["bots"]
    counts = json.loads((root / "team-kit" / "in-flight" / "meo-beo.json").read_text())["repos"]
    assert counts == {"acme/shop-api": 0, "acme/shop-web": 0, "acme/shop-mobile": 0}


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
    env = root / "profiles" / "meo-beo" / ".env"
    env.write_text(env.read_text() + "GH_TOKEN=x\n")
    admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    (root / "profiles" / "meo-beo" / "scripts" / "team-kit" / "github-triage.py").write_text("tampered")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    assert 'BOT = "meo-beo"' in (root / "profiles" / "meo-beo" / "scripts" / "team-kit" / "github-triage.py").read_text()
    assert all(job["enabled"] for job in jobs(root, "meo-beo")) and len(jobs(root, "meo-beo")) == 2
    assert "GH_TOKEN=x" in env.read_text()


def test_the_installed_triage_script_wakes_only_its_own_bot(ready):
    root, env = ready
    add_shop(ready)
    admin(ready, "add-project", "--bot", "sep-crm", "--project", "crm", "--repos", "acme/crm", profile="ong-xay")
    guard = root / "team-kit" / "guard.yaml"
    guard.write_text(guard.read_text().replace("  - Codertocat\n", "  - Codertocat\n  - octocat\n"))
    event = json.loads((FIXTURES / "pull_request.opened.json").read_text())
    event["repository"]["full_name"] = event["pull_request"]["head"]["repo"]["full_name"] = "acme/shop-web"
    event["pull_request"]["body"] = f"Closes #1\n\n{pr_marker('t_1', 1)}"

    def run(bot):
        script = root / "profiles" / bot / "scripts" / "team-kit" / "github-triage.py"
        done = subprocess.run(["python3", str(script)], input=json.dumps(event), capture_output=True, text=True,
                              env={**env, "TEAM_KIT_HOME": str(root / "team-kit")})
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    assert json.loads(run("meo-beo"))["team_kit"]["repo"] == "acme/shop-web"
    assert run("sep-crm") == "[SILENT]"
