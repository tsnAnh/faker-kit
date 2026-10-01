import re
import shlex

import pytest
import yaml
from conftest import HERMES, KIT, TEMPLATES
from team_kit_contracts import LAUNCH_PROMPTS, ContractError, launch_command

PREFIXES = ("/faker-mode ", "$faker-mode ")
LAUNCHER_ALLOW = [re.compile(p) for p in yaml.safe_load((TEMPLATES / "guard.yaml").read_text())["profiles"]["launcher"]["terminal_allow"]]


@pytest.mark.parametrize("backend", sorted(LAUNCH_PROMPTS))
def test_every_launch_prompt_template_starts_with_faker_mode(backend):
    assert LAUNCH_PROMPTS[backend].startswith(PREFIXES)


@pytest.mark.parametrize(
    ("backend", "size", "expected"),
    [
        ("claude", "large", "claude --cloud '/faker-mode Work the brief in acme/shop#123 using the follow-brief skill'"),
        ("codex", "small", "codex cloud exec --env env_7 --attempts 1 '$faker-mode Work the brief in acme/shop#123 using the follow-brief skill'"),
        ("codex", "medium", "codex cloud exec --env env_7 --attempts 2 '$faker-mode Work the brief in acme/shop#123 using the follow-brief skill'"),
        ("codex", "large", "codex cloud exec --env env_7 --attempts 3 '$faker-mode Work the brief in acme/shop#123 using the follow-brief skill'"),
    ],
)
def test_launch_command_is_exact_and_the_launcher_guard_allows_it(backend, size, expected):
    command = launch_command(backend, "acme/shop", 123, size, "env_7")
    assert command == expected
    assert shlex.split(command)[-1].startswith(PREFIXES)
    assert any(p.fullmatch(command) for p in LAUNCHER_ALLOW)


@pytest.mark.parametrize(
    "command",
    [
        "claude --cloud '/faker-mode Work the brief in acme/shop#123 using the follow-brief skill'; rm -rf ~",
        "claude --cloud 'Work the brief in acme/shop#123 using the follow-brief skill'",
        "claude --cloud \"/faker-mode Work the brief in acme/shop#123 using the follow-brief skill\"",
        "codex cloud exec --env env_7 --attempts 9 '$faker-mode Work the brief in acme/shop#123 using the follow-brief skill'",
        "codex cloud exec --env $(id) --attempts 1 '$faker-mode Work the brief in acme/shop#123 using the follow-brief skill'",
        "gh pr merge 123",
    ],
)
def test_launcher_guard_rejects_any_other_command(command):
    assert not any(p.fullmatch(command) for p in LAUNCHER_ALLOW)


@pytest.mark.parametrize(("repo", "env"), [("acme/shop;id", "e"), ("acme", "e"), ("acme/shop", "e;id"), ("acme/shop", None)])
def test_launch_command_refuses_unsafe_parts(repo, env):
    with pytest.raises(ContractError):
        launch_command("codex", repo, 1, "small", env)


def written_launch_prompts():
    """Every `claude --cloud` or `codex cloud exec` prompt written in the kit's dispatch prose."""
    files = [*HERMES.rglob("*.md"), *(KIT / "plugins" / "trust-ladder" / "skills").glob("*dispatch*/**/*.md"),
             *(KIT / "plugins" / "trust-ladder" / "skills" / "follow-brief").rglob("*.md"),
             KIT / "docs" / "how-to" / "dispatch-with-hermes.md", KIT / "plugins" / "trust-ladder" / "references" / "compat.md"]
    pattern = re.compile(r"(?:claude --cloud|codex cloud exec(?: --env \S+)?(?: --attempts \S+)?) (['\"])(.*?)\1")
    return [(f.relative_to(KIT), m.group(2)) for f in files for m in pattern.finditer(f.read_text())]


def test_every_written_launch_prompt_starts_with_faker_mode():
    prompts = written_launch_prompts()
    assert len(prompts) >= 2
    assert [(str(f), p) for f, p in prompts if not p.startswith(PREFIXES)] == []
