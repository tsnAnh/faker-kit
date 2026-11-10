#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

FIRST_PLUGIN = "trust-ladder"
LAST_PLUGIN = "team-workflows"
KINDS = ("skills", "agents")


class KitLinksError(Exception):
    pass


class Link(NamedTuple):
    path: str
    target: Path


def plugin_order(kit: Path) -> list[str]:
    plugins = kit / "plugins"
    names = sorted(p.name for p in plugins.iterdir() if p.is_dir()) if plugins.is_dir() else []
    if not names:
        raise KitLinksError(f"no plugins under {plugins}")
    middle = [name for name in names if name not in (FIRST_PLUGIN, LAST_PLUGIN)]
    return [name for name in (FIRST_PLUGIN,) if name in names] + middle + [name for name in (LAST_PLUGIN,) if name in names]


def provides(kind: str, entry: Path) -> bool:
    if kind == "skills":
        return (entry / "SKILL.md").is_file()
    return entry.suffix == ".md" and entry.is_file()


def plan(kit: Path) -> list[Link]:
    kit = kit.resolve()
    links: dict[str, Link] = {}
    for name in plugin_order(kit):
        for kind in KINDS:
            folder = kit / "plugins" / name / kind
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if provides(kind, entry):
                    links.setdefault(f"{kind}/{entry.name}", Link(f"{kind}/{entry.name}", entry))
    return list(links.values())


def points_into(link: Path, kit: Path) -> bool:
    return link.readlink().is_relative_to(kit / "plugins")


def apply(kit: Path, config: Path) -> dict[str, int]:
    kit = kit.resolve()
    links = plan(kit)
    planned = {link.path for link in links}
    counts = {"skills": 0, "agents": 0, "kept": 0, "removed": 0}
    for kind in KINDS:
        folder = config / kind
        for stale in sorted(folder.iterdir()) if folder.is_dir() else []:
            if stale.is_symlink() and f"{kind}/{stale.name}" not in planned and points_into(stale, kit):
                stale.unlink()
                counts["removed"] += 1
    for link in links:
        dest = config / link.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            dest.unlink()
        elif dest.exists():
            counts["kept"] += 1
            continue
        dest.symlink_to(link.target)
        counts[link.path.split("/", 1)[0]] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kit_links.py",
        description="Link a faker-kit checkout's skills and subagents into a Claude Code config folder. Never replaces a skill or file that is not a link.",
    )
    parser.add_argument("--kit", type=Path, required=True, help="a checkout of the kit")
    parser.add_argument("--config", type=Path, required=True, help="the Claude Code config folder, usually ~/.claude")
    args = parser.parse_args(argv)
    try:
        counts = apply(args.kit, args.config)
    except (KitLinksError, OSError) as err:
        print(f"kit_links: {err}", file=sys.stderr)
        return 1
    print(f"kit_links: {counts['skills']} skills, {counts['agents']} agents linked into {args.config}; kept {counts['kept']} of your own, removed {counts['removed']} stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
