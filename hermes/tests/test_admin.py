import json
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import team_kit_admin
import yaml
from conftest import FAKE_CLAUDE, fill_guard, gh_calls, guard_text, hermes_calls, install
from install_config import InstallError, cron_spec
from team_kit_gh import IN_FLIGHT_FRESH_SECONDS, InFlight
from team_kit_guard.policy import load_policy
from team_kit_machine import MachineError, cloudflared_config, edit_guard, parse_machine

SHOP = "acme/shop"
LABELS = ["agent-task", "agent:proposal", "agent:running", "agent:verifying", "agent:review", "agent:needs-human", "agent:security", "agent:incident", "risk:low", "risk:medium", "risk:high", "agent:andon"]  # noqa: E501


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
    assert sorted(out["jobs_created_paused"]) == ["team-kit-incidents", "team-kit-security", "team-kit-status", "team-kit-tick", "team-kit-upstream"]
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
    assert sorted(created) == ["0 9 * * 1", "0 9,13,17 * * *", "every 10m", "every 15m", "every 30m"]
    tick = created["every 30m"]
    assert tick[:2] == ["-p", "meo-beo"]
    assert tick[tick.index("--name") + 1] == "team-kit-tick"
    assert tick[tick.index("--deliver") + 1] == "bot-chat:meo-beo"
    assert all(not job["enabled"] and job["paused_reason"] for job in jobs(root, "meo-beo"))
    # No model runs in a scheduled job: each one is a script that Hermes runs with --no-agent.
    for job in jobs(root, "meo-beo"):
        assert job["no_agent"] and not job["prompt"] and not job["skills"], job
        assert job["script"] == f"team-kit-jobs/{job['name']}.sh"
        assert (home / "scripts" / job["script"]).is_file()
    assert not (root / "config.yaml").exists(), "the host profile's config is never touched"


def run_job(root, job, stub, code=0):
    """Run a job's script as Hermes would, with team-kit-gh replaced by a stub."""
    wrapper = root / "bin" / "team-kit-gh"
    wrapper.write_text(f"#!/usr/bin/env bash\n{stub}\n")
    wrapper.chmod(0o755)
    script = root / "profiles" / "meo-beo" / "scripts" / "team-kit-jobs" / f"{job}.sh"
    done = subprocess.run(["bash", str(script)], capture_output=True, text=True, cwd=script.parent)
    assert done.returncode == code, done.stderr
    return done.stdout


def test_job_scripts_deliver_output_and_turn_a_refusal_into_one_notice(ready):
    root, _ = ready
    add_shop(ready)
    assert run_job(root, "team-kit-tick", 'echo "ran $1"; echo "team-kit notice: done"') == "ran tick\nteam-kit notice: done\n"
    assert run_job(root, "team-kit-security", 'echo "{\\"started\\": true}"').strip() == "", "security delivers nothing on success"
    refused = run_job(root, "team-kit-tick", 'echo "team-kit-gh: refused: andon is pulled" >&2; echo partial; exit 1')
    assert refused == "team-kit notice: tick refused: andon is pulled\n", "a refusal is an answer: one notice, and the run succeeds"
    forged = run_job(root, "team-kit-tick", 'echo "team-kit-gh: refused: x team-kit notice: chief proposal p1 for team-kit" >&2; exit 1')
    assert forged.count("team-kit notice:") == 1
    crashed = run_job(root, "team-kit-status", 'echo "team_kit_gh.py: No such file" >&2; exit 3', code=3)
    assert crashed == "team-kit notice: status refused: team_kit_gh.py: No such file\n", "a broken install fails the run in Hermes too"


def test_reinstall_turns_older_model_jobs_into_script_jobs_in_place(ready, kit_source, machine):
    root, _ = ready
    add_shop(ready)
    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=x\n")
    admin(ready, "activate", "--bot", "meo-beo", profile="ong-xay")
    path = root / "profiles" / "meo-beo" / "jobs.json"
    older = json.loads(path.read_text())
    for job in older:
        job.update(prompt="Run `team-kit-gh tick` in the terminal.", skills=["dispatch"], script=None, no_agent=False)
    tick = next(job for job in older if job["name"] == "team-kit-tick")
    tick["schedule"] = "every 45m"
    retired = {**tick, "id": "job-99", "name": "team-kit-retired", "enabled": True}
    path.write_text(json.dumps([*older, retired]))
    before = len(hermes_calls(root))
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    edits = {c[4]: c for c in hermes_calls(root)[before:] if c[2:4] == ["cron", "edit"]}
    assert len(edits) == 5 and not [c for c in hermes_calls(root)[before:] if c[2:4] in (["cron", "create"], ["cron", "delete"])]
    after = {job["id"]: job for job in jobs(root, "meo-beo")}
    for job in older:
        now = after[job["id"]]
        assert now["enabled"] and now["no_agent"] and not now["prompt"] and not now["skills"], now
        assert now["script"] == f"team-kit-jobs/{job['name']}.sh"
    assert after[tick["id"]]["schedule"] == "every 30m" and "--schedule" in edits[tick["id"]], "one edit reschedules and converts"
    assert not after["job-99"]["enabled"], "a job no longer in the spec is paused, not left to fail"
    before = len(hermes_calls(root))
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    assert not [c for c in hermes_calls(root)[before:] if c[2:4] == ["cron", "edit"]], "a converted job is left alone"


def test_a_job_command_must_be_one_team_kit_gh_subcommand(tmp_path):
    spec = tmp_path / "jobs.md"
    spec.write_text("## team-kit-tick\n\nSchedule: `every 30m`\nDeliver: `bot-chat:{bot}`\nCommand: `team-kit-gh tick; rm -rf ~`\nOutput: `print`\n")
    with pytest.raises(InstallError, match="must be `team-kit-gh <subcommand>`"):
        cron_spec(spec, "meo-beo")
    spec.write_text("## team-kit-../../x\n\nSchedule: `every 30m`\nDeliver: `bot-chat:{bot}`\nCommand: `team-kit-gh tick`\nOutput: `print`\n")
    with pytest.raises(InstallError, match="names the job's script"):
        cron_spec(spec, "meo-beo")


def test_a_second_project_gets_its_own_bot_and_jobs(ready):
    root, _ = ready
    add_shop(ready)
    code, out = admin(ready, "add-project", "--bot", "sep-crm", "--project", "crm", "--repo", "acme/crm", profile="ong-xay")
    assert code == 0, out
    assert bots(root)["sep-crm"]["repo"] == "acme/crm"
    assert len(jobs(root, "sep-crm")) == 5 and len(jobs(root, "meo-beo")) == 5


def test_add_project_can_set_custom_tick_and_incident_intervals(ready):
    root, _ = ready
    code, out = admin(
        ready,
        "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP,
        "--tick-every", "10m", "--incidents-every", "5m", "--security-every", "1h",
        profile="ong-xay",
    )
    assert code == 0, out
    assert (out["tick_every"], out["incidents_every"], out["security_every"]) == ("10m", "5m", "1h")
    assert bots(root)["meo-beo"] == {"role": "project", "project": "shop", "repo": SHOP, "tick_every": "10m", "incidents_every": "5m", "security_every": "1h"}
    created = {c[c.index("--name") + 1]: c for c in hermes_calls(root) if c[2:4] == ["cron", "create"]}
    assert created["team-kit-tick"][4] == "every 10m"
    assert created["team-kit-incidents"][4] == "every 5m"
    assert created["team-kit-security"][4] == "every 1h"


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
    assert len(by_name) == 5
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
    assert len(jobs(root, "meo-beo")) == 5


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


@pytest.mark.parametrize(("ask_owner", "mode"), [(True, "manual"), (False, "off")])
def test_hermes_approvals_follow_the_guards_ask_owner(ready, ask_owner, mode):
    root, _ = ready
    guard = root / "team-kit" / "guard.yaml"
    text = guard.read_text()
    assert "\nask_owner: true\n" in text, "the template asks by default"
    guard.write_text(text.replace("\nask_owner: true\n", f"\nask_owner: {str(ask_owner).lower()}\n"))
    add_shop(ready)
    assert yaml.safe_load((root / "profiles" / "meo-beo" / "config.yaml").read_text())["approvals"]["mode"] == mode


def test_add_project_prepares_the_launch_checkout_and_keeps_the_cli_config_dir(ready):
    root, _ = ready
    args = ("add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP)
    code, out = admin(with_env(ready, CLAUDE_CONFIG_DIR="/owner/.claude"), *args, profile="ong-xay")
    assert code == 0, out
    checkout = root / "team-kit" / "checkouts" / "acme__shop" / "repo"
    assert out["launch_checkout"] == str(checkout) and (checkout / ".git").is_dir()
    assert out["next"][0] == f"For Claude Code sessions, the owner trusts the launch checkout once: cd {checkout} && claude, choose yes, then /exit"
    env_file = root / "profiles" / "meo-beo" / ".env"
    assert env_file.read_text() == "CLAUDE_CONFIG_DIR=/owner/.claude\n"
    code, out = admin(with_env(ready), *args, profile="ong-xay")
    assert code == 0, out
    assert env_file.read_text() == "CLAUDE_CONFIG_DIR=/owner/.claude\n", "a shell without CLAUDE_CONFIG_DIR never removes it"


def test_setup_repo_starts_the_session_from_the_launch_checkout_with_its_prompt_as_the_first_message(ready, kit_source, tmp_path):
    root, env = ready
    add_shop(ready)
    vendored(root, kit_source[1])
    cli = Path(env["PATH"].split(":")[0]) / "claude"
    cli.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE)
    cli.chmod(0o755)
    calls = tmp_path / "claude-calls.jsonl"
    code, out = admin(with_env(ready, FAKE_CLAUDE_LOG=str(calls)), "setup-repo", "--project", "shop", "--backend", "claude", profile="ong-xay")
    assert code == 0, out
    assert out == {"project": "shop", "repo": "acme/shop", "backend": "claude", "kit_ref": kit_source[1], "session": "https://claude.ai/code/session_01FAKEabc"}
    [create] = [json.loads(line) for line in calls.read_text().splitlines()]
    checkout = str(root / "team-kit" / "checkouts" / "acme__shop" / "repo")
    assert create["cwd"] == checkout and create["argv"][0] == "--cloud"
    prompt = create["argv"][1]
    assert prompt.startswith("Set up acme/shop for team-kit dispatch")
    assert f"vendored in this repository at .agents/team-kit/, at commit {kit_source[1]}" in prompt
    assert ".agents/team-kit/plugins/trust-ladder/skills/setup-dispatch/SKILL.md" in prompt
    assert "clone" not in prompt.lower(), "the setup session never clones the kit, which may be private"
    assert "trusted_dispatchers: codertocat, meo-beo-gh" in prompt


@pytest.mark.parametrize(("kit", "why"), [(None, "has no vendored kit"), ("0" * 40, "has the kit at 000000000000")])
def test_setup_repo_waits_for_the_vendored_kit(ready, kit_source, kit, why):
    root, _ = ready
    add_shop(ready)
    if kit:
        vendored(root, kit)
    code, err = admin(ready, "setup-repo", "--project", "shop", "--backend", "claude", profile="ong-xay")
    assert code == 1 and why in err and "team-kit-admin vendor --project shop" in err


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
    assert all(c["args"][0] != "git" for c in gh_calls(root)), "no git push: everything goes through the API"


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
    root, env = ready  # noqa: RUF059
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
    assert sorted(out["resumed"]) == ["team-kit-incidents", "team-kit-security", "team-kit-status", "team-kit-tick", "team-kit-upstream"]
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
    assert shop["jobs"] == {"team-kit-tick": False, "team-kit-status": False, "team-kit-security": False, "team-kit-incidents": False, "team-kit-upstream": False}
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
    assert all(job["enabled"] for job in jobs(root, "meo-beo")) and len(jobs(root, "meo-beo")) == 5
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
    root, env = ready  # noqa: RUF059
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
    drafts = root / "team-kit" / "drafts" / "chanh-van"
    assert drafts.is_dir() and (drafts.stat().st_mode & 0o777) == 0o700, "the chief's drafts folder exists and is private"
    assert not any(c[:2] == ["-p", "chanh-van"] and c[2:4] == ["cron", "create"] for c in hermes_calls(root)), "the chief has no jobs"
    code, err = admin(ready, "add-chief", "--bot", "meo-beo", profile="ong-xay")
    assert code == 1 and "already a project bot" in err


FAKE_CLAUDE_CLI = """import json, sys
if sys.argv[1:] == ["--version"]:
    print("{version} (Claude Code)")
elif sys.argv[1:] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": {logged_in}, "authMethod": "claude.ai"}}))
else:
    sys.exit(2)
"""
PROVIDER = "claude-subscription-directsdk-experimental"


def fake_claude(ready, version="2.1.296", logged_in=True):
    _, env = ready
    path = Path(env["PATH"].split(":")[0]) / "claude"
    path.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE_CLI.format(version=version, logged_in=bool(logged_in)))
    path.chmod(0o755)


def with_env(ready, **extra):
    root, env = ready
    return root, {**{k: v for k, v in env.items() if k != "CLAUDE_CONFIG_DIR"}, **extra}


def config(root, bot):
    return yaml.safe_load((root / "profiles" / bot / "config.yaml").read_text())


def test_claude_subscription_switches_the_bots_model_and_points_the_plugin_at_the_login(ready):
    root, _ = ready
    fake_claude(ready)
    add_shop(ready)
    home = root / "profiles" / "meo-beo"
    current = config(root, "meo-beo")
    current["model"] = {"default": "gpt-x", "provider": "openai-codex", "base_url": "https://example.invalid", "api_mode": "codex_responses"}
    (home / "config.yaml").write_text(yaml.safe_dump(current))
    (home / ".env").write_text("GH_TOKEN=keep\n")
    (root / ".env").write_text("OWNER_KEY=keep\n")
    args = ("add-project", "--bot", "meo-beo", "--project", "shop", "--repo", SHOP, "--claude-subscription")
    code, out = admin(with_env(ready, CLAUDE_CONFIG_DIR="/owner/.claude"), *args, profile="ong-xay")
    assert code == 0, out
    after = config(root, "meo-beo")
    assert after["model"] == {"default": "sonnet", "provider": PROVIDER}
    assert set(after["plugins"]["enabled"]) >= {"team_kit_guard", PROVIDER}
    assert (home / ".env").read_text() == "GH_TOKEN=keep\nCLAUDE_CONFIG_DIR=/owner/.claude\n"
    assert stat.S_IMODE((home / ".env").stat().st_mode) == 0o600
    # The gateway that runs the bot's jobs reads the root .env, not the profile's.
    assert (root / ".env").read_text() == "OWNER_KEY=keep\nCLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR=/owner/.claude\n"
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    enables = [c for c in hermes_calls(root) if c[2:4] == ["plugins", "enable"]]
    assert enables and all("--no-allow-tool-override" in c for c in enables), "the plugin never replaces built-in tools"

    installs = len([c for c in hermes_calls(root) if c[2:4] == ["plugins", "install"]])
    code, out = admin(with_env(ready), *args, "--model", "opus", profile="ong-xay")
    assert code == 0, out
    assert len([c for c in hermes_calls(root) if c[2:4] == ["plugins", "install"]]) == installs, "an installed plugin is not installed again"
    assert config(root, "meo-beo")["model"] == {"default": "opus", "provider": PROVIDER}
    assert (home / ".env").read_text() == "GH_TOKEN=keep\nCLAUDE_CONFIG_DIR=/owner/.claude\n", "a shell without CLAUDE_CONFIG_DIR keeps it"
    assert "CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR=/owner/.claude" in (root / ".env").read_text()


@pytest.mark.parametrize(("cli", "why"), [
    (None, "Claude Code is not installed"),
    ({"version": "2.1.200"}, "2.1.284 or later is needed"),
    ({"logged_in": False}, "Claude Code is not logged in here"),
])
def test_claude_subscription_refuses_before_changing_anything(ready, cli, why):
    root, _ = ready
    if cli is not None:
        fake_claude(ready, **cli)
    add_shop(ready)
    before = (root / "team-kit" / "bots.yaml").read_text(), labels(root), config(root, "meo-beo"), len(hermes_calls(root))
    code, err = admin(with_env(ready), "add-project", "--bot", "sep-crm", "--project", "crm", "--repo", "acme/crm", "--claude-subscription", profile="ong-xay")
    assert code == 1 and why in err
    assert ((root / "team-kit" / "bots.yaml").read_text(), labels(root), config(root, "meo-beo"), len(hermes_calls(root))) == before
    assert not (root / "profiles" / "sep-crm").exists()


def in_process(ready, monkeypatch, *argv, now=None, profile="ong-xay"):
    """team-kit-admin's main in this process against the installed machine, so a test supplies the clock."""
    root, env = ready
    for key, value in {**env, "TEAM_KIT_HOME": str(root / "team-kit"), "HERMES_HOME": str(root / "profiles" / profile)}.items():
        monkeypatch.setenv(key, value)
    return team_kit_admin.main(list(argv), who=profile, **({} if now is None else {"now": now}))


def test_andon_raise_stamps_the_cord_with_the_supplied_clock(ready, monkeypatch, capsys):
    root, _ = ready
    add_shop(ready)
    assert in_process(ready, monkeypatch, "andon", "raise", "cloud", "bill", now=lambda: 1234.5) == 0
    assert json.loads(capsys.readouterr().out)["stopped"] is True
    assert json.loads((root / "team-kit" / "andon" / "_machine.json").read_text())["at"] == 1234.5


def test_status_stamps_the_in_flight_count_with_the_supplied_clock(ready, monkeypatch, capsys):
    root, _ = ready
    add_shop(ready)
    assert in_process(ready, monkeypatch, "status", now=lambda: 4242.0) == 0
    capsys.readouterr()
    assert json.loads((root / "team-kit" / "in-flight" / "meo-beo.json").read_text()) == {"at": 4242.0, "repos": {SHOP: 0}}


def test_without_a_supplied_clock_both_stamps_are_wall_time(ready, monkeypatch, capsys):
    root, _ = ready
    add_shop(ready)
    before = time.time()
    assert in_process(ready, monkeypatch, "andon", "raise", "stop") == 0
    assert in_process(ready, monkeypatch, "status") == 0
    after = time.time()
    capsys.readouterr()
    cord = json.loads((root / "team-kit" / "andon" / "_machine.json").read_text())["at"]
    counted = json.loads((root / "team-kit" / "in-flight" / "meo-beo.json").read_text())["at"]
    assert before <= cord <= after and before <= counted <= after


def test_an_admin_count_is_fresh_for_the_other_bots_until_a_day_after_the_supplied_clock(team_kit_home):
    stamped = 1_800_000_000.0
    team_kit_admin.write_in_flight(team_kit_home, "sep-crm", {"acme/crm": 2}, lambda: stamped)
    assert json.loads((team_kit_home / "in-flight" / "sep-crm.json").read_text()) == {"at": stamped, "repos": {"acme/crm": 2}}
    policy = load_policy()
    assert InFlight(policy, policy.bot("meo-beo"), lambda *_: "", lambda: stamped + IN_FLIGHT_FRESH_SECONDS - 1).others() == 2
    assert InFlight(policy, policy.bot("meo-beo"), lambda *_: "", lambda: stamped + IN_FLIGHT_FRESH_SECONDS + 1).others() == 0


def test_write_in_flight_called_without_a_clock_stamps_wall_time(team_kit_home):
    before = time.time()
    team_kit_admin.write_in_flight(team_kit_home, "sep-crm", {"acme/crm": 1})
    stamped = json.loads((team_kit_home / "in-flight" / "sep-crm.json").read_text())["at"]
    assert before <= stamped <= time.time()


# machine config -------------------------------------------------------------------------------------

MACHINE = {
    "version": 1, "bootstrap_bot": "ong-xay", "claude_subscription": False,
    "guard": {"trusted_authors": ["Codertocat"], "ask_owner": False, "max_in_flight_total": 4},
    "projects": [{"bot": "meo-beo", "project": "shop", "repo": SHOP, "tick_every": "10m"}],
    "chief": {"bot": "chanh-van"},
    "webhooks": {"url": "https://hermes.example.com", "tunnel": "hermes"},
    "slack": {"bot": "meo-beo", "channel_id": "C0ABCDEF12", "channel_name": "shop"},
}


def machine_config(root, **changes):
    path = root.parent / "hermes-machine.yaml"
    path.write_text(yaml.safe_dump({**MACHINE, **changes}))
    return path


@pytest.mark.parametrize(("change", "why"), [
    ({"webhooks": {"url": "https://hermes.CHANGE-ME.example", "tunnel": "hermes"}}, "fill it in first"),
    ({"webhooks": {"url": "https://hermes.example.com"}}, "both url and tunnel"),
    ({"webhooks": {"url": "http://hermes.example.com", "tunnel": "hermes"}}, "is not valid"),
    ({"chief": {"bot": "meo-beo"}}, "its own profile name"),
    ({"slack": {"bot": "chanh-van", "channel_id": "C0ABCDEF12", "channel_name": "shop"}}, "one of the project bots"),
    ({"guard": {"trusted_authors": [], "ask_owner": False}}, "trusted_authors"),
    ({"version": 2}, "version must be 1"),
    ({"guard": {"trusted_authors": ["CHANGE-ME"]}}, "still lists CHANGE-ME"),
    ({"projects": [{"bot": "a-b", "project": "a", "repo": "acme/a"}, {"bot": "a_b", "project": "b", "repo": "acme/b"}]}, "share a webhook secret"),
])
def test_a_machine_config_is_refused_before_anything_changes(change, why):
    with pytest.raises(MachineError, match=why):
        parse_machine({**MACHINE, **change})


def test_machine_settings_go_into_guard_yaml_and_keep_its_comments():
    template = guard_text()
    edited = edit_guard(template, parse_machine(MACHINE))
    policy = yaml.safe_load(edited)
    assert (policy["ask_owner"], policy["max_in_flight_total"], policy["trusted_authors"]) == (False, 4, ["Codertocat"])
    assert policy["slack"] | {"enabled": True, "bot": "meo-beo", "channel_id": "C0ABCDEF12", "channel_name": "shop"} == policy["slack"]
    template_comments = [line for line in template.splitlines() if line.lstrip().startswith("#")]
    assert template_comments and all(line in edited.splitlines() for line in template_comments)
    assert edit_guard(edited, parse_machine(MACHINE)) == edited, "running it again changes nothing"


def test_guard_edits_keep_the_owners_indentation():
    template = guard_text().replace("\n  enabled: false", "\n    enabled: false").replace("\n  bot: meo-beo", "\n    bot: meo-beo")
    template = template.replace("\n  channel_id:", "\n    channel_id:").replace("\n  channel_name:", "\n    channel_name:")
    template = template.replace("\n  app_token_env:", "\n    app_token_env:").replace("\n  bot_token_env:", "\n    bot_token_env:")
    template = template.replace("\n  mirror_notices:", "\n    mirror_notices:")
    assert yaml.safe_load(template)["slack"]["enabled"] is False
    assert yaml.safe_load(edit_guard(template, parse_machine(MACHINE)))["slack"]["channel_id"] == "C0ABCDEF12"


def test_the_tunnel_publishes_only_this_machines_routes_by_exact_path():
    text = cloudflared_config("abc-123", Path("/home/pi/.cloudflared/abc-123.json"), "hermes.example.com", parse_machine(MACHINE))
    ingress = yaml.safe_load(text)["ingress"]
    assert [rule.get("path") for rule in ingress] == ["^/p/meo\\-beo/webhooks/meo\\-beo\\-tick$", "^/p/meo\\-beo/webhooks/meo\\-beo\\-security$", None]
    assert all(rule["service"] == "http://127.0.0.1:8644" and rule["hostname"] == "hermes.example.com" for rule in ingress[:-1])
    assert ingress[-1] == {"service": "http_status:404"}, "anything else, /health and other routes included, is 404"


def test_machine_sets_up_bots_and_signed_webhooks_and_keeps_secrets_out_of_argv_and_config(ready):
    root, _ = ready
    path = machine_config(root)
    code, out = admin(ready, "machine", "--config", str(path))
    assert code == 0, out
    assert bots(root)["meo-beo"]["tick_every"] == "10m" and bots(root)["chanh-van"]["role"] == "chief"
    assert out["projects"][0]["active"] is False and "GH_TOKEN" in out["pending"][0], "no token yet, so the jobs stay paused"
    assert all(not job["enabled"] for job in jobs(root, "meo-beo"))

    env = root / ".env"
    secret = next(line.split("=", 1)[1] for line in env.read_text().splitlines() if line.startswith("TEAM_KIT_WEBHOOK_SECRET_MEO_BEO="))
    assert len(secret) >= 32 and stat.S_IMODE(env.stat().st_mode) == 0o600
    config = yaml.safe_load((root / "config.yaml").read_text())
    listener = config["platforms"]["webhook"]
    assert (listener["enabled"], listener["extra"]["host"], listener["extra"]["port"]) == (True, "127.0.0.1", 8644)
    tick = listener["extra"]["routes"]["meo-beo-tick"]
    assert tick == {"events": ["issues", "issue_comment", "pull_request", "pull_request_review", "check_suite"], "secret": "${TEAM_KIT_WEBHOOK_SECRET_MEO_BEO}",
                    "profile": "meo-beo", "cron_job": "team-kit-tick", "deliver": "log"}
    assert listener["extra"]["routes"]["meo-beo-security"]["events"] == ["push"]
    assert secret not in (root / "config.yaml").read_text()

    hooks = json.loads((root.parent / ".fake-gh" / "hooks.json").read_text())[SHOP]
    assert sorted(h["config"]["url"] for h in hooks) == ["https://hermes.example.com/p/meo-beo/webhooks/meo-beo-security",
                                                         "https://hermes.example.com/p/meo-beo/webhooks/meo-beo-tick"]
    assert all(h["config"]["secret"] == secret and h["config"]["content_type"] == "json" and h["active"] for h in hooks)
    assert all(secret not in json.dumps(c["args"]) for c in gh_calls(root)), "the secret reaches GitHub on stdin only"
    assert all(secret not in json.dumps(c) for c in hermes_calls(root))

    (root / "profiles" / "meo-beo" / ".env").write_text("GH_TOKEN=x\n")
    code, out = admin(ready, "machine", "--config", str(path))
    assert code == 0, out
    assert out["projects"][0]["active"] and not out["pending"]
    assert all(job["enabled"] for job in jobs(root, "meo-beo"))
    assert [h["hook"] for h in out["webhooks"]] == ["updated", "updated"], "a second run never adds a second hook"
    assert len(json.loads((root.parent / ".fake-gh" / "hooks.json").read_text())[SHOP]) == 2
    assert f"TEAM_KIT_WEBHOOK_SECRET_MEO_BEO={secret}" in env.read_text(), "the secret is kept, so GitHub and Hermes still agree"


def test_machine_runs_only_from_a_plain_shell(ready):
    root, _ = ready
    code, err = admin(ready, "machine", "--config", str(machine_config(root)), profile="ong-xay")
    assert code == 1 and "runs only from hermes/install.sh" in err
