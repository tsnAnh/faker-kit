#!/usr/bin/env python3
"""team-kit-events: record GitHub webhook deliveries durably, in shadow mode, and show the queue.

  receive --request PATH|- (--secret-env NAME | --secret-file PATH) [--db PATH]
      Read one HTTP request (header lines, a blank line, then the raw body bytes) and print the answer the
      receiver gives GitHub as JSON: 202 only after the delivery is committed, 401 for a bad signature, 403 for
      a repository with no project in bots.yaml, 409 for a delivery id seen with a different body, 503 when the
      commit failed. Exit 0 on 202, 1 otherwise.
  serve --config PATH [--db PATH]
      Listen on a loopback address and answer GitHub webhook requests with the same receiver. The JSON config names
      the bind address, the port, and one route per project: a URL path, the repository it accepts, and the path of
      its secret file. See docs/explanation/event-receiver.md. Stops on SIGTERM or Ctrl-C.
  check-config --config PATH
      Validate that config and its secret files' modes without starting a listener; print the routes as JSON and exit
      0, or print every problem on stderr and exit 1. Neither command prints a secret.
  status [--project P] [--db PATH]          read-only: queue depth, consumer states, retries, conflicts, dead letters
  dead-letters [--project P] [--db PATH]    read-only: rows in the dead state, with their reasons

The signing secret is read from the environment variable or file named, never from an argument value. Nothing
here registers a listener, changes a label, launches, merges, or closes: each row records the job its event
would trigger, and `team-kit-tick` stays authoritative.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
# Last, so an installed PyYAML wins; the vendored copy keeps the command working on a Python without one.
if (HERE.parent / "vendor").is_dir() and str(HERE.parent / "vendor") not in sys.path:
    sys.path.append(str(HERE.parent / "vendor"))

from team_kit_event_queue import EventStore, Response, dead_letters, default_db, open_read_only, receive, status
from team_kit_guard.policy import PolicyError, load_policy, team_kit_home
from team_kit_ingress import ConfigError, IngressConfig, load_config, make_server

REQUEST_LINE = re.compile(rb"\A[A-Z]+ \S+ HTTP/\d")


class Refused(Exception):
    pass


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="team-kit-events")
    sub = top.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("receive")
    rec.add_argument("--request", required=True, help="request file, or - for stdin")
    secret = rec.add_mutually_exclusive_group(required=True)
    secret.add_argument("--secret-env", metavar="NAME", help="name of the environment variable holding the webhook secret")
    secret.add_argument("--secret-file", metavar="PATH", help="file holding the webhook secret")
    for name in ("serve", "check-config"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True, help="JSON route configuration")
        if name == "serve":
            command.add_argument("--db", help="queue database (default: $TEAM_KIT_HOME/events/events.sqlite3)")
    for name in ("receive", "status", "dead-letters"):
        command = rec if name == "receive" else sub.add_parser(name)
        command.add_argument("--db", help="queue database (default: $TEAM_KIT_HOME/events/events.sqlite3)")
        if name != "receive":
            command.add_argument("--project")
    return top


def split_request(data: bytes) -> tuple[dict[str, str], bytes]:
    """Header lines, a blank line, then the body exactly as sent; a leading request line is skipped."""
    ends = [i for i in (data.find(b"\r\n\r\n"), data.find(b"\n\n")) if i >= 0]
    if not ends:
        raise Refused("request has no blank line between headers and body")
    end = min(ends)
    body = data[end + (4 if data[end:end + 4] == b"\r\n\r\n" else 2):]
    headers: dict[str, str] = {}
    for line in data[:end].splitlines():
        if not line.strip() or REQUEST_LINE.match(line):
            continue
        name, sep, value = line.decode("latin-1").partition(":")
        if not sep:
            raise Refused("malformed header line")
        headers[name.strip()] = value.strip()
    return headers, body


def read_secret(args: argparse.Namespace) -> bytes:
    if args.secret_env:
        value = os.environ.get(args.secret_env, "")
        if not value:
            raise Refused(f"environment variable {args.secret_env} is empty or unset")
        return value.encode()
    try:
        value = Path(args.secret_file).read_text().strip()
    except OSError as err:
        raise Refused(f"cannot read secret file {args.secret_file}") from err
    if not value:
        raise Refused(f"secret file {args.secret_file} is empty")
    return value.encode()


def projects(home: Path) -> dict[str, str]:
    return {bot.repo.lower(): bot.project for bot in load_policy(home).bots.values() if bot.role == "project" and bot.repo and bot.project}


def ingress_config(args: argparse.Namespace, home: Path) -> IngressConfig | None:
    try:
        return load_config(Path(args.config), projects(home))
    except ConfigError as err:
        for problem in err.problems:
            print(f"team-kit-events: refused: {problem}", file=sys.stderr)
        return None


def run_ingress(args: argparse.Namespace, home: Path, db: Path) -> int:
    config = ingress_config(args, home)
    if config is None:
        return 1
    if args.command == "check-config":
        print(json.dumps({"ok": True, "routes": [{"path": r.path, "repository": r.repo, "project": r.project} for r in config.routes.values()]}))
        return 0
    server = make_server(config, db)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    print(f"team-kit-events: serving {len(config.routes)} route(s) on {config.bind}:{server.bound_port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def run(args: argparse.Namespace) -> int:
    home = team_kit_home()
    db = Path(args.db) if getattr(args, "db", None) else default_db(home)
    if args.command in ("serve", "check-config"):
        return run_ingress(args, home, db)
    if args.command == "status":
        print(json.dumps(status(open_read_only(db), args.project), indent=2, sort_keys=True))
        return 0
    if args.command == "dead-letters":
        print(json.dumps(dead_letters(open_read_only(db), args.project), indent=2))
        return 0
    secret = read_secret(args)
    raw = sys.stdin.buffer.read() if args.request == "-" else Path(args.request).read_bytes()
    headers, body = split_request(raw)
    try:
        store = EventStore(db)
    except sqlite3.Error:
        answer = Response(503, "could not open the queue; GitHub should redeliver")
    else:
        try:
            answer = receive(body, headers, secret, projects(home), store, time.time())
        finally:
            store.close()
    print(json.dumps({"status": answer.status, "reason": answer.reason, "delivery": answer.delivery_id, "shadow_check": answer.shadow_check}))
    return 0 if answer.status == 202 else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (Refused, PolicyError, OSError) as err:
        print(f"team-kit-events: refused: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
