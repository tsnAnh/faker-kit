"""A loopback HTTP ingress that hands each GitHub webhook request to the event queue's receiver.

One process serves every route in one JSON config file. A route is a URL path, the repository it accepts, and a path to
the file holding its webhook secret. The route builds a one-entry project map for `receive`, so a payload naming any
other repository gets the receiver's 403 and nothing is written. Signature, parsing, binding, and recording stay in
`team_kit_event_queue.receive`; this module only reads one bounded request from a socket and writes its answer.

Concurrency is the standard threading server with a semaphore that bounds in-flight connections; a connection over the
bound is answered 503 at once. Every connection is closed after one answer. SQLite writes serialize through the store.
The listener binds a loopback address literal only; HTTPS and any public address belong in front of it.
"""

from __future__ import annotations

import contextlib
import http.client
import http.server
import io
import ipaddress
import json
import os
import re
import socket
import socketserver
import sqlite3
import stat
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from team_kit_event_queue import EventStore, receive

MAX_BODY_CEILING = 25 << 20
MAX_HEADER_BYTES_CEILING = 65536
MAX_HEADERS_CEILING = 100
MAX_CONNECTIONS_CEILING = 1024
READ_TIMEOUT_CEILING = 300.0
REPOSITORY = re.compile(r"\A[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
SINGLE_HEADERS = ("Content-Length", "X-Hub-Signature-256", "X-GitHub-Delivery", "X-GitHub-Event")
DRAIN_SECONDS = 0.25
DRAIN_BYTES = 65536
REASONS = {
    400: "bad request",
    404: "not found",
    405: "method not allowed",
    408: "request timed out",
    411: "length required",
    413: "payload too large",
    414: "request target too long",
    431: "request headers too large",
    503: "service unavailable",
}


class ConfigError(Exception):
    """Every problem found, each naming the file or field and never a secret value."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Route:
    path: str
    repo: str
    project: str
    secret_file: Path


@dataclass(frozen=True)
class Limits:
    # GitHub accepts payloads up to 25 MiB; 1 MiB covers the events the queue keeps pointers for.
    max_body: int = 1 << 20
    max_header_bytes: int = 16384
    max_headers: int = 64
    read_timeout: float = 10.0
    max_connections: int = 16


def loopback_problem(bind: object) -> str | None:
    if not isinstance(bind, str):
        return "bind must be a string holding an IP address literal"
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        return f"bind {bind!r} is not an IP address literal; hostnames such as localhost are refused because they can resolve beyond loopback, so use 127.0.0.1 or ::1"
    if not address.is_loopback:
        return f"bind {bind} is not a loopback address; the ingress listens on 127.0.0.0/8 or ::1 only, and HTTPS on a public address belongs in front of it"
    return None


@dataclass(frozen=True)
class IngressConfig:
    bind: str
    port: int
    routes: Mapping[str, Route]
    limits: Limits

    def __post_init__(self) -> None:
        problem = loopback_problem(self.bind)
        if problem is not None:
            raise ConfigError([problem])


def read_secret_file(path: Path) -> bytes:
    """The secret's bytes, stripped. Checked on every call so a rotation or a loosened mode takes effect without a restart."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as err:
        raise ConfigError([f"secret file {path} is missing or unreadable"]) from err
    info = os.fstat(fd)
    problem = None
    if not stat.S_ISREG(info.st_mode):
        problem = f"secret file {path} is not a regular file"
    elif info.st_mode & 0o077:
        problem = f"secret file {path} is mode {info.st_mode & 0o777:04o}; it must not be group or world accessible (chmod 600)"
    if problem is not None:
        os.close(fd)
        raise ConfigError([problem])
    with os.fdopen(fd, "rb") as handle:
        try:
            value = handle.read().strip()
        except OSError as err:
            raise ConfigError([f"secret file {path} is missing or unreadable"]) from err
    if not value:
        raise ConfigError([f"secret file {path} is empty"])
    return value


def _unknown_keys(where: str, table: Mapping[str, object], allowed: frozenset[str]) -> list[str]:
    return [f"{where}: unknown key {str(key)[:40]!r}" for key in table if key not in allowed]


def _parse_limits(where: str, raw: object) -> tuple[Limits, list[str]]:
    if raw is None:
        return Limits(), []
    if not isinstance(raw, dict):
        return Limits(), [f"{where}: limits must be an object"]
    problems = _unknown_keys(f"{where} limits", raw, frozenset(Limits.__dataclass_fields__))
    ceilings = {"max_body": MAX_BODY_CEILING, "max_header_bytes": MAX_HEADER_BYTES_CEILING, "max_headers": MAX_HEADERS_CEILING,
                "max_connections": MAX_CONNECTIONS_CEILING}
    values: dict[str, int] = {}
    for name, ceiling in ceilings.items():
        if name not in raw:
            continue
        value = raw[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= ceiling:
            problems.append(f"{where} limits.{name} must be an integer from 1 to {ceiling}")
        else:
            values[name] = value
    timeout = Limits.read_timeout
    if "read_timeout" in raw:
        given = raw["read_timeout"]
        if isinstance(given, bool) or not isinstance(given, (int, float)) or not 0 < given <= READ_TIMEOUT_CEILING:
            problems.append(f"{where} limits.read_timeout must be a number of seconds above 0 and at most {READ_TIMEOUT_CEILING:g}")
        else:
            timeout = float(given)
    if problems:
        return Limits(), problems
    return Limits(
        max_body=values.get("max_body", Limits.max_body), max_header_bytes=values.get("max_header_bytes", Limits.max_header_bytes),
        max_headers=values.get("max_headers", Limits.max_headers), read_timeout=timeout,
        max_connections=values.get("max_connections", Limits.max_connections)), []


def _parse_route(where: str, index: int, raw: object, base: Path, projects: Mapping[str, str]) -> tuple[Route | None, list[str]]:
    label = f"{where}: route {index}"
    if not isinstance(raw, dict):
        return None, [f"{label} must be an object"]
    problems = _unknown_keys(label, raw, frozenset({"path", "repository", "secret_file"}))
    path, repo, secret_ref = raw.get("path"), raw.get("repository"), raw.get("secret_file")
    if not isinstance(path, str) or not path.startswith("/"):
        problems.append(f"{label} path must be a string starting with /")
    if not isinstance(repo, str) or not repo.strip():
        problems.append(f"{label} has no repository")
        repo = None
    elif not REPOSITORY.match(repo):
        problems.append(f"{label} repository {repo!r} is not owner/name")
        repo = None
    project = projects.get(repo.lower()) if repo is not None else None
    if repo is not None and project is None:
        problems.append(f"{label} repository {repo} is not a project in bots.yaml")
    secret_file: Path | None = None
    if not isinstance(secret_ref, str) or not secret_ref.strip():
        problems.append(f"{label} has no secret_file")
    else:
        secret_file = base / secret_ref
        try:
            read_secret_file(secret_file)
        except ConfigError as err:
            problems.extend(f"{label}: {problem}" for problem in err.problems)
    if not isinstance(path, str) or repo is None or project is None or secret_file is None:
        return None, problems
    return Route(path, repo, project, secret_file), problems


def load_config(path: Path, projects: Mapping[str, str]) -> IngressConfig:
    """Parse the JSON config, collecting every problem before raising. `projects` maps a lowercased repository to its project."""
    where = f"config {path}"
    try:
        data: object = json.loads(path.read_text())
    except OSError as err:
        raise ConfigError([f"{where} is missing or unreadable"]) from err
    except ValueError as err:
        raise ConfigError([f"{where} is not valid JSON: {err}"]) from err
    if not isinstance(data, dict):
        raise ConfigError([f"{where} must hold a JSON object"])
    problems = _unknown_keys(where, data, frozenset({"bind", "port", "routes", "limits"}))
    bind, port = data.get("bind"), data.get("port")
    if "bind" not in data:
        problems.append(f"{where}: bind is missing")
    elif (problem := loopback_problem(bind)) is not None:
        problems.append(f"{where}: {problem}")
    if "port" not in data:
        problems.append(f"{where}: port is missing")
    elif not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        problems.append(f"{where}: port must be an integer from 0 to 65535")
    limits, limit_problems = _parse_limits(where, data.get("limits"))
    problems.extend(limit_problems)
    routes: dict[str, Route] = {}
    seen_repos: set[str] = set()
    raw_routes = data.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        problems.append(f"{where}: routes must be a non-empty list")
        raw_routes = []
    for index, raw in enumerate(raw_routes, 1):
        route, route_problems = _parse_route(where, index, raw, path.resolve().parent, projects)
        problems.extend(route_problems)
        if route is None:
            continue
        if route.path in routes:
            problems.append(f"{where}: route {index} repeats path {route.path}")
        if route.repo.lower() in seen_repos:
            problems.append(f"{where}: route {index} repeats repository {route.repo}")
        routes.setdefault(route.path, route)
        seen_repos.add(route.repo.lower())
    if problems or not isinstance(bind, str) or not isinstance(port, int):
        raise ConfigError(problems)
    return IngressConfig(bind, port, routes, limits)


# server -----------------------------------------------------------------------------------------------


if TYPE_CHECKING:
    # Only for annotations: evaluated at import, `|` between these types fails on the Python 3.9 the bot commands support.
    Request = socket.socket | tuple[bytes, socket.socket]


def stderr_log(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _stamp(now: Callable[[], float]) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now()))


class _HeaderBudget(io.BufferedIOBase):
    """Counts the bytes the request line and header lines take, and refuses a line past the budget."""

    def __init__(self, raw: io.BufferedIOBase, budget: int) -> None:
        super().__init__()
        self.raw_reader = raw
        self.remaining = budget

    def readline(self, size: int | None = -1, /) -> bytes:
        take = self.remaining + 1 if size is None or size < 0 else min(size, self.remaining + 1)
        line = self.raw_reader.readline(take)
        self.remaining -= len(line)
        if self.remaining < 0:
            raise http.client.LineTooLong("header")
        return line


@dataclass(frozen=True)
class _Services:
    config: IngressConfig
    db: Path
    now: Callable[[], float]
    log: Callable[[str], None]
    open_store: Callable[[Path], EventStore]


class IngressServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, services: _Services, handler: type[http.server.BaseHTTPRequestHandler]) -> None:
        config = services.config
        self.address_family = socket.AF_INET6 if ipaddress.ip_address(config.bind).version == 6 else socket.AF_INET
        self.services = services
        self.slots = threading.BoundedSemaphore(config.limits.max_connections)
        super().__init__((config.bind, config.port), handler)

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name, self.server_port = host, port

    @property
    def bound_port(self) -> int:
        return int(self.socket.getsockname()[1])

    def process_request(self, request: Request, client_address: object) -> None:
        if not self.slots.acquire(blocking=False):
            self._refuse_busy(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Request, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def _refuse_busy(self, request: Request) -> None:
        sock = request if isinstance(request, socket.socket) else request[1]
        body = json.dumps({"status": 503, "reason": REASONS[503], "delivery": None}).encode()
        head = f"HTTP/1.0 503 Service Unavailable\r\nRetry-After: 1\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        with contextlib.suppress(OSError):
            sock.settimeout(0.2)
            sock.sendall(head.encode() + body)
        self.shutdown_request(request)

    def shutdown_request(self, request: Request) -> None:
        """Close after one answer, first draining what the client still sends so the close never resets a reply it has not read."""
        sock = request if isinstance(request, socket.socket) else request[1]
        try:
            sock.shutdown(socket.SHUT_WR)
            end = time.monotonic() + DRAIN_SECONDS
            drained = 0
            while drained < DRAIN_BYTES and (left := end - time.monotonic()) > 0:
                sock.settimeout(left)
                chunk = sock.recv(DRAIN_BYTES)
                if not chunk:
                    break
                drained += len(chunk)
        except OSError:
            pass
        self.close_request(request)

    def handle_error(self, request: Request, client_address: object) -> None:
        del request, client_address
        self.services.log(f"{_stamp(self.services.now)} connection error")


def _handler_for(services: _Services) -> type[http.server.BaseHTTPRequestHandler]:
    limits = services.config.limits

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        default_request_version = "HTTP/1.0"
        timeout = limits.read_timeout

        def setup(self) -> None:
            super().setup()
            self.request_version = self.default_request_version
            self.body_reader = self.rfile
            self.rfile = _HeaderBudget(self.body_reader, limits.max_header_bytes)
            self.expired = threading.Event()
            self.answered = False
            self.read_complete = False
            self.route_name = "-"
            self.body_length = 0
            self.watchdog = threading.Timer(limits.read_timeout, self.expire)
            self.watchdog.daemon = True
            self.watchdog.start()

        def expire(self) -> None:
            self.expired.set()
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RD)

        def finish(self) -> None:
            self.watchdog.cancel()
            super().finish()
            self.body_reader.close()

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except http.client.LineTooLong:
                self.close_connection = True
                self.answer(431)

        def log_message(self, format: str, *args: object) -> None:
            pass

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            pass

        def log_error(self, format: str, *args: object) -> None:
            pass

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            del message, explain
            self.answer(405 if code == 501 else code)

        def answer(self, status: int, reason: str | None = None, delivery: str | None = None) -> None:
            if self.answered:
                return
            self.answered = True
            # A timer that fires after the read finished must not turn a committed answer into a 408.
            if self.expired.is_set() and not self.read_complete:
                status, reason, delivery = 408, None, None
            payload = json.dumps({"status": status, "reason": reason or REASONS.get(status, "request refused"), "delivery": delivery}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if status == 405:
                self.send_header("Allow", "POST")
            self.send_header("Connection", "close")
            self.end_headers()
            if getattr(self, "command", None) != "HEAD":
                self.wfile.write(payload)
            self.close_connection = True
            services.log(f"{_stamp(services.now)} route={self.route_name} status={status} delivery={delivery or '-'} bytes={self.body_length}")

        def refuse_method(self) -> None:
            self.answer(405)

        do_GET = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = refuse_method

        def read_body(self, length: int) -> bytes | None:
            chunks: list[bytes] = []
            remaining = length
            try:
                while remaining:
                    chunk = self.body_reader.read(min(65536, remaining))
                    if not chunk:
                        self.answer(408 if self.expired.is_set() else 400)
                        return None
                    chunks.append(chunk)
                    remaining -= len(chunk)
            except OSError:  # socket.timeout is not TimeoutError before Python 3.10
                self.answer(408)
                return None
            return b"".join(chunks)

        def declared_length(self) -> int | None:
            """The Content-Length to read, or None after the refusal has been sent."""
            headers = self.headers
            lengths = headers.get_all("Content-Length")
            if headers.get("Transfer-Encoding") is not None or not lengths:
                self.answer(411)
            elif any(len(headers.get_all(name) or []) > 1 for name in SINGLE_HEADERS) or not (digits := lengths[0].strip()).isascii() or not digits.isdigit():
                self.answer(400)
            elif len(digits) > 18 or int(digits) > limits.max_body:
                self.answer(413)
            else:
                return int(digits)
            return None

        def do_POST(self) -> None:
            if len(self.headers) > limits.max_headers:
                self.answer(431)
                return
            route = services.config.routes.get(self.path)
            if route is None:
                self.answer(404)
                return
            self.route_name = route.project
            length = self.declared_length()
            if length is None:
                return
            body = self.read_body(length)
            if body is None:
                return
            self.body_length = length
            self.watchdog.cancel()
            if self.expired.is_set():
                self.answer(408)
                return
            self.read_complete = True
            try:
                secret = read_secret_file(route.secret_file)
            except ConfigError:
                self.answer(503, "route secret unavailable")
                return
            try:
                store = services.open_store(services.db)
            except (OSError, sqlite3.Error):
                self.answer(503, "could not open the queue; GitHub should redeliver")
                return
            try:
                result = receive(body, dict(self.headers.items()), secret, {route.repo.lower(): route.project}, store, services.now())
            finally:
                store.close()
            self.answer(result.status, result.reason, result.delivery_id)

    return Handler


def make_server(config: IngressConfig, db: Path, *, now: Callable[[], float] = time.time, log: Callable[[str], None] = stderr_log,
                open_store: Callable[[Path], EventStore] = EventStore) -> IngressServer:
    services = _Services(config, db, now, log, open_store)
    return IngressServer(services, _handler_for(services))
