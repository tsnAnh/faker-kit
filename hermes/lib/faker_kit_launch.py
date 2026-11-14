from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from faker_kit_cloud import PERMISSION_MODE, Cloud, CloudError, session_effort, session_model
from faker_kit_gh import Refused, Runner, fix_git_protocol

DEFAULT_BACKEND = "claude"
DISPATCH_FILE = ".agents/dispatch.json"
BACKEND_NAME = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class LaunchResult:
    fields: dict[str, object]


@dataclass(frozen=True)
class Dispatch:
    backend: str
    default_branch: str
    t3code: object


class Launcher(Protocol):
    def start(self, repo: str, prompt: str, workdir: Path, model: str | None, effort: str | None, /) -> LaunchResult: ...


class ClaudeLauncher:
    def __init__(self, cloud: Cloud, run: Runner) -> None:
        self.cloud = cloud
        self.run = run

    def start(self, repo: str, prompt: str, workdir: Path, model: str | None, effort: str | None, /) -> LaunchResult:
        fixed = fix_git_protocol(self.run)
        try:
            session = self.cloud.start(repo, prompt, workdir, model, effort)
        except CloudError as err:
            raise Refused(f"launch failed: {err}") from err
        used_effort, effort_note = session_effort(effort)
        used_model, model_note = session_model(model)
        fields: dict[str, object] = {"session": session, "model": used_model, "effort": used_effort, "permission_mode": PERMISSION_MODE, **model_note, **effort_note}
        if fixed:
            fields["git_protocol_fixed"] = fixed
        return LaunchResult(fields)


def read_dispatch(text: str | None, default_branch: str) -> Dispatch:
    if text is None:
        return Dispatch(DEFAULT_BACKEND, default_branch, None)
    try:
        config = json.loads(text)
    except ValueError:
        raise Refused(f"{DISPATCH_FILE} is not valid JSON, so launch cannot tell which backend it names") from None
    if not isinstance(config, dict):
        raise Refused(f"{DISPATCH_FILE} must hold one JSON object")
    backend = config.get("backend", DEFAULT_BACKEND)
    if not isinstance(backend, str):
        raise Refused(f"{DISPATCH_FILE} backend must be a name")
    return Dispatch(backend, default_branch, config.get("t3code"))


Factory = Callable[[Dispatch], Launcher]


def pick_factory(dispatch: Dispatch, registry: Mapping[str, Factory]) -> Factory:
    found = registry.get(dispatch.backend)
    if found is None:
        shown = BACKEND_NAME.sub("?", dispatch.backend)[:32]
        raise Refused(f"{DISPATCH_FILE} names the backend {shown!r}; launch knows {', '.join(sorted(registry))}")
    return found
