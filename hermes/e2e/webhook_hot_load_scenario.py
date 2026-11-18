from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import yaml
from faker_kit_machine import ROUTE_JOBS, secret_env, stale_route

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

SUBSCRIPTIONS = "webhook_subscriptions.json"
RESTART = ["-p", "default", "gateway", "restart"]
ROUTE_PATH = re.compile(r"^/p/(?P<profile>[^/]+)/webhooks/(?P<route>[^/]+)$")
ENV_REF = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


class PalReader:
    def __init__(self, home: Path, reads_dynamic: bool = True) -> None:
        self.home = home
        self.reads_dynamic = reads_dynamic
        self.hits: list[tuple[str, int | str]] = []
        self.restarts_seen = self.restarts()
        self.start()

    def restarts(self) -> int:
        log = self.home / "hermes-calls.log"
        return sum(1 for line in log.read_text().splitlines() if json.loads(line) == RESTART) if log.exists() else 0

    def env(self) -> dict[str, str]:
        path = self.home / ".env"
        lines = path.read_text().splitlines() if path.exists() else []
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def start(self) -> None:
        path = self.home / "config.yaml"
        config = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
        found = ((((config.get("platforms") or {}).get("webhook") or {}).get("extra") or {}).get("routes")) or {}
        env = self.env()
        self.static = {name: {k: ENV_REF.sub(lambda m: env.get(m.group(1), ""), v) if isinstance(v, str) else v for k, v in route.items()} for name, route in found.items()}
        self.routes = dict(self.static)
        self.dynamic: dict[str, dict] = {}
        self.stat_key: tuple | None = None

    @staticmethod
    def usable(secret: object) -> bool:
        return isinstance(secret, str) and bool(secret.strip())

    def reload_dynamic(self) -> None:
        path = self.home / SUBSCRIPTIONS
        try:
            info = path.stat()
        except FileNotFoundError:
            self.dynamic, self.routes, self.stat_key = {}, dict(self.static), None
            return
        key = (info.st_ino, info.st_mtime_ns, info.st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        if key == self.stat_key:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            self.stat_key = key
            return
        if not isinstance(data, dict):
            self.stat_key = key
            return
        self.dynamic = {k: v for k, v in data.items() if isinstance(v, dict) and k not in self.static and self.usable(v.get("secret"))}
        self.routes = {**self.dynamic, **self.static}
        self.stat_key = key

    def answer(self, url: str, headers: dict[str, str], body: bytes) -> int | str:
        if self.restarts() != self.restarts_seen:
            self.restarts_seen = self.restarts()
            self.start()
        if self.reads_dynamic:
            self.reload_dynamic()
        match = ROUTE_PATH.match(urlsplit(url).path)
        if not match:
            return 404
        route = self.routes.get(match["route"])
        if not route or route.get("profile", "default") != match["profile"]:
            return 404
        secret = route.get("secret")
        if not self.usable(secret):
            return 403
        expected = "sha256=" + hmac.new(str(secret).encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("X-Hub-Signature-256", "")):
            return 401
        if route.get("events") and headers.get("X-GitHub-Event") not in route["events"]:
            return 200
        return 202

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> int | str:
        result = self.answer(url, headers, body)
        self.hits.append((urlsplit(url).path, result))
        return result


def block(name: str, entry: object) -> str:
    return "\n".join(json.dumps({name: entry}, indent=2, ensure_ascii=False).splitlines()[1:-2])


def read_env(home: Path) -> dict[str, str]:
    return {k: v.strip("\"'") for k, v in (line.split("=", 1) for line in (home / ".env").read_text().splitlines() if "=" in line)}


def stamp(path: Path) -> tuple[bytes, int, int]:
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns


def hot_load_scenario(run: Run, machine_yaml: str) -> None:
    crm = ("add-project", "acme/crm", "--bot", "crm-bot", "--project", "crm")
    (run.team / "machine.yaml").write_text(machine_yaml)
    run.update_gh(lambda state: state.update({"repo": "acme/crm"}))
    store = run.hermes / SUBSCRIPTIONS
    config_path = run.hermes / "config.yaml"
    names = [f"crm-bot-{kind}" for kind in ROUTE_JOBS]
    shown: list[str] = []
    reader = PalReader(run.hermes)

    def add() -> tuple[int, dict]:
        code, out, err = run.kit(*crm, bot=None, post=reader)
        shown.extend([out, err])
        return code, (json.loads(out) if out.strip().startswith("{") else {})

    code, added = add()
    secret = read_env(run.hermes)[secret_env("crm-bot")]
    stored = json.loads(store.read_text()) if store.exists() else {}
    run.step("a gateway started before add-project, which read its static routes once, serves both new routes with a 2xx and no restart, and add-project says loaded hot",
             code == 0 and added.get("loaded") == "hot" and [h["ping"] for h in added["webhooks"]] == ["ok", "ok"] and added["webhooks_live"] is True
             and reader.restarts() == reader.restarts_seen == 0 and all(r in (200, 202) for _, r in reader.hits) and {p.rsplit("/", 1)[-1] for p, _ in reader.hits} == set(names)
             and not set(names) & set(reader.static), json.dumps([added.get("loaded"), reader.restarts(), reader.hits[:4]]))

    fields = {"events", "secret", "profile", "cron_job", "deliver", "prompt", "filters", "managed_by"}
    run.step("the subscriptions file is mode 0600 valid JSON with one entry per route carrying the reader's fields, the literal secret and the kit's marker; the static config keeps its ${VAR} form",
             stat.S_IMODE(store.stat().st_mode) == 0o600 and sorted(stored) == sorted(names) and all(set(entry) == fields for entry in stored.values())
             and all(entry["secret"] == secret and entry["profile"] == "crm-bot" and entry["cron_job"] == "faker-kit-babysit" and entry["deliver"] == "log" for entry in stored.values())
             and stored["crm-bot-issues"]["filters"] == [{"field": "action", "equals": "labeled"}, {"field": "label.name", "equals": "faker:fix"}]
             and yaml.safe_load(config_path.read_text())["platforms"]["webhook"]["extra"]["routes"]["crm-bot-pr"]["secret"] == "${" + secret_env("crm-bot") + "}",
             json.dumps({k: sorted(v) for k, v in stored.items()}))

    first = stamp(store)
    code, again = add()
    run.step("running add-project again changes nothing in the subscriptions file: same bytes, same inode, same mtime, and it still says loaded hot",
             code == 0 and again.get("loaded") == "hot" and first == stamp(store) and reader.restarts() == 0, json.dumps([again.get("loaded"), first[1]]))

    foreign = {"deploy-hook": {"description": "someone else's", "events": ["push"], "secret": "not-the-kits", "prompt": "deploy {ref}", "created_at": "2026-01-01T00:00:00Z"},
               "crm-bot-mine": {"events": ["push"], "secret": "also-not-the-kits", "profile": "crm-bot"}}
    legacy = {"events": ["pull_request"], "secret": secret, "profile": "crm-bot", "cron_job": "faker-kit-babysit", "deliver": "log", "prompt": "old", "managed_by": "faker-kit"}
    seeded = json.dumps({**json.loads(store.read_text()), **foreign, "crm-bot-legacy": legacy}, indent=2, ensure_ascii=False)
    store.write_text(seeded)
    config = yaml.safe_load(config_path.read_text())
    config["platforms"]["webhook"]["extra"]["routes"]["crm-bot-legacy"] = {"events": ["pull_request"], "profile": "crm-bot"}
    config_path.write_text(yaml.safe_dump(config, sort_keys=True))
    code, _ = add()
    pruned = store.read_text()
    left = json.loads(pruned)
    run.step("a stale route name is dropped from the subscriptions file and the static config in one pass, and entries the kit did not write survive byte for byte, a lookalike without its marker too",
             code == 0 and stale_route("crm-bot-legacy", "crm-bot") and "crm-bot-legacy" not in left
             and "crm-bot-legacy" not in yaml.safe_load(config_path.read_text())["platforms"]["webhook"]["extra"]["routes"]
             and all(block(name, entry) in seeded and block(name, entry) in pruned for name, entry in foreign.items())
             and sorted(left) == sorted([*names, *foreign]) and stat.S_IMODE(store.stat().st_mode) == 0o600, json.dumps(sorted(left)))

    settled = stamp(store)
    code, _ = add()
    run.step("a rerun with nothing to change after the prune writes nothing: same bytes, same inode, same mtime",
             code == 0 and settled == stamp(store) and reader.restarts() == 0, json.dumps(settled[1:]))

    store.write_bytes(b"[not an object")
    code, out, err = run.kit(*crm, bot=None, post=reader)
    shown.extend([out, err])
    run.step("a subscriptions file that is not a JSON object is left as it is, add-project still finishes and names the file without printing its content",
             code == 0 and store.read_bytes() == b"[not an object" and "left it as it is" in err and "not an object\"" not in err and "[not an object" not in err, err.strip()[:200])

    logs = "".join(path.read_text() for path in (run.gh_log, run.hermes / "hermes-calls.log", run.claude_log) if path.exists())
    run.step("the fixture secret appears in no command output, no error text, no gh call and no hermes call of the whole run",
             secret not in "".join(shown) + logs, f"{len(shown)} outputs, {len(logs)} log bytes")


def fallback_scenario(run: Run, machine_yaml: str, agent_turn: dict[str, str], bootstrap: str) -> None:
    crm = ("add-project", "acme/crm", "--bot", "crm-bot", "--project", "crm")
    (run.team / "machine.yaml").write_text(machine_yaml)
    run.update_gh(lambda state: state.update({"repo": "acme/crm"}))
    deaf = PalReader(run.hermes, reads_dynamic=False)
    code, out, _err = run.kit(*crm, bot=None, post=deaf)
    owned = json.loads(out) if code == 0 else {}
    misses = [hit for hit in deaf.hits if hit[1] == 404]
    run.step("a gateway that never reads the subscriptions file is polled a bounded number of times, then restarted once, and the routes answer after it; the output says restart with the ping result",
             code == 0 and deaf.restarts() == 1 and owned["loaded"] == "restart (hot path failed: crm-bot-pr answered 404; crm-bot-issues answered 404)"
             and len(misses) == 2 * 6 and [h["ping"] for h in owned["webhooks"]] == ["ok", "ok"] and owned["webhooks_live"] is True and len(run.sleeps) <= 2 * 5 + 2 * 5,
             json.dumps([owned.get("loaded"), len(misses), len(run.sleeps)]))

    config_path = run.hermes / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    for name in (f"crm-bot-{kind}" for kind in ROUTE_JOBS):
        config["platforms"]["webhook"]["extra"]["routes"].pop(name)
    config_path.write_text(yaml.safe_dump(config, sort_keys=True))
    fresh = PalReader(run.hermes, reads_dynamic=False)
    restarts = fresh.restarts()
    code, out, _err = run.kit(*crm, bot=bootstrap, env=agent_turn, post=fresh)
    bot_turn = json.loads(out) if code == 0 else {}
    run.step("a bot's turn that cannot load the routes hot does not restart the gateway, says no with the ping result, leaves webhooks_live false, and names the owner command in one next line",
             code == 0 and fresh.restarts() == restarts and bot_turn["loaded"] == "no (hot path failed: crm-bot-pr answered 404; crm-bot-issues answered 404)" and bot_turn["webhooks_live"] is False
             and len(bot_turn["next"]) == 1 and "hermes -p default gateway restart" in bot_turn["next"][0], out[:300])
