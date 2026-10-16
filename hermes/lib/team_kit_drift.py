"""Kit pin drift: whether a project's repository runs the kit this machine has installed, and what repairs it.

Three values must agree for a project bot to launch: the kit commit installed on this machine, `kit_ref` in the
repository's `.agents/dispatch.json`, and `kit_ref` in its vendored `.agents/team-kit/KIT.json`, both on the default
branch. `classify` decides, from what team-kit-admin read, whether a project is in sync, repairable by running
`team-kit-admin vendor` (or waiting on the kit pull request it opened), or needs the owner. It reads nothing itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

State = Literal["in_sync", "repairable", "needs_human"]
# What the reconcile step does: nothing, run vendor once, wait for tick to land the kit pull request, or tell the owner.
Action = Literal["none", "vendor", "wait", "owner"]
# The installed commit against kit_ref, as `git` or GitHub's compare answers it; None when neither could tell.
Order = Literal["same", "ahead", "behind", "diverged"]


@dataclass(frozen=True)
class KitPull:
    """An open kit pull request from `vendor`, on the repository's own branch, into its default branch."""

    number: int
    url: str
    branch: str
    head: str
    for_installed: bool  # its branch names the installed commit
    trusted: bool  # its head is the commit `vendor` recorded for that branch
    checks: str  # passed, failed, pending, not started, or none
    conflicts: bool | None  # None while GitHub has not computed it


@dataclass(frozen=True)
class Drift:
    project: str
    repo: str
    installed: str
    kit_ref: str | None
    vendored: str | None
    order: Order | None
    pulls: tuple[KitPull, ...]
    auto_merge: str
    state: State
    action: Action
    why: str

    def pull(self) -> KitPull | None:
        """The kit pull request this state is about: the one for the installed commit, if any."""
        return next((p for p in self.pulls if p.for_installed), None)

    def fingerprint(self) -> str:
        """What a vendor attempt or a notice is about. Anything here changing makes it a new state."""
        return json.dumps([self.installed, self.kit_ref, self.vendored, [(p.number, p.head, p.conflicts, p.checks) for p in self.pulls], self.why])

    def report(self) -> dict[str, object]:
        out = asdict(self)
        out["pulls"] = [asdict(p) for p in self.pulls]
        return out


def classify(installed: str, kit_ref: str | None, vendored: str | None, order: Order | None,
             pulls: tuple[KitPull, ...], auto_merge: str) -> tuple[State, Action, str]:
    if kit_ref == installed and vendored == installed:
        return "in_sync", "none", "the installed kit, kit_ref, and the vendored kit agree"
    if kit_ref is not None and kit_ref != installed:
        if order == "behind":
            return "needs_human", "owner", (f"this machine's kit is older than kit_ref {kit_ref[:12]}; only an install the owner approves "
                                            "(hermes/install.sh --ref <commit>) fixes it, so nothing was vendored")
        if order == "diverged":
            return "needs_human", "owner", f"this machine's kit and kit_ref {kit_ref[:12]} are on diverged histories; the owner picks the kit to install"
        if order != "ahead":
            return "needs_human", "owner", f"could not tell whether kit_ref {kit_ref[:12]} is older than this machine's kit, so nothing was vendored"
    if kit_ref is None and vendored == installed:
        return "needs_human", "owner", "the vendored kit is current but .agents/dispatch.json has no kit_ref; team-kit-admin setup-repo adds it"
    pull = next((p for p in pulls if p.for_installed), None)
    if pull is None:
        return "repairable", "vendor", "no kit pull request for the installed kit is open"
    if not pull.trusted:
        return "needs_human", "owner", (f"the kit pull request's head {pull.head[:12]} is not the commit team-kit-admin vendor made, so it is not "
                                        "trusted or rebuilt over; the owner checks it, and team-kit-admin vendor rebuilds it if they want")
    if pull.conflicts:
        return "repairable", "vendor", "the kit pull request conflicts with its base branch; vendor rebuilds it"
    if pull.checks == "failed":
        return "needs_human", "owner", "the kit pull request's checks failed"
    if auto_merge != "low":
        return "needs_human", "owner", "the owner merges the kit pull request: auto_merge in .agents/dispatch.json is not low"
    return "repairable", "wait", f"the kit pull request's checks are {pull.checks}; tick merges it once they pass"


def notice(drift: Drift, prefix: str) -> str:
    pull = drift.pull()
    return (f"{prefix} kit drift in project {drift.project} ({drift.repo}) needs the owner: {drift.why}. "
            f"Installed kit {short(drift.installed)}, kit_ref {short(drift.kit_ref)}, vendored kit {short(drift.vendored)}; "
            f"kit pull request {pull.url if pull else 'none'}.")


def short(sha: str | None) -> str:
    return sha[:12] if sha else "none"
