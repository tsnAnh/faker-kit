import json
import shutil
import sys
from pathlib import Path

import pytest

HERMES = Path(__file__).resolve().parent.parent
KIT = HERMES.parents[2]
TEMPLATES = HERMES.parent / "templates" / "dispatch"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github"

for path in (HERMES / "webhook", HERMES / "profiles" / "dispatcher", HERMES / "plugin"):
    sys.path.insert(0, str(path))


@pytest.fixture
def team_kit_home(tmp_path, monkeypatch):
    """A ~/.hermes/team-kit with the sample guard.yaml, filled in for the recorded payloads' repository."""
    home = tmp_path / "team-kit"
    home.mkdir()
    text = (TEMPLATES / "guard.yaml").read_text()
    text = text.replace("  - OWNER/REPO", "  - Codertocat/Hello-World").replace("  - OWNER-LOGIN", "  - Codertocat")
    (home / "guard.yaml").write_text(text)
    monkeypatch.setenv("TEAM_KIT_HOME", str(home))
    return home


@pytest.fixture
def payload():
    def load(name):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    return load


def fill_brief(card="t_1a2b", standing=None):
    text = (TEMPLATES / "brief.md").read_text().replace("<card id>", card)
    if standing is not None:
        text = text.replace("<`.agents/dispatch.md`, pasted verbatim.>", standing)
    return text
