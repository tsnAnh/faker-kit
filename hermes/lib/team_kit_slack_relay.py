"""Outbound-only Slack Socket Mode relay for team-kit Hermes notices."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from team_kit_guard.policy import Policy, SlackConfig

NOTICE = "team-kit notice:"


class SlackError(Exception):
    pass


MAX_SLACK_TEXT = 1_200
ENV_LINE = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z")
NOTICE_IN_TEXT = re.compile(r"team-kit\s+notice\s*:", re.IGNORECASE)
SEEN_LIMIT = 2_000
# What a dropped Socket Mode connection raises: our own refusals, and socket timeouts, resets, and closes.
RECONNECT_ERRORS = (SlackError, OSError, EOFError)


def profile_home(root: Path, profile: str) -> Path:
    return root if profile == "default" else root / "profiles" / profile


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return env
    except OSError as err:
        raise SlackError(f"cannot read profile environment file: {path}") from err
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE.match(line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def profile_env(policy: Policy, bot: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    root = policy.home.parent
    merged = dict(base or os.environ)
    for key, value in parse_env_file(root / ".env").items():
        merged.setdefault(key, value)
    for key, value in parse_env_file(profile_home(root, bot) / ".env").items():
        merged.setdefault(key, value)
    return merged


def slack_config(policy: Policy) -> SlackConfig:
    config = policy.slack
    if config is None or not config.enabled:
        raise SlackError("slack.enabled is not true in guard.yaml")
    if not config.bot or not config.channel_id or not config.channel_name:
        raise SlackError("guard.yaml slack needs bot, channel_id, and channel_name")
    if config.bot not in policy.bots:
        raise SlackError("guard.yaml slack.bot is not in bots.yaml")
    return config


def token(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SlackError(f"environment variable {name} is not set")
    return value


def check(policy: Policy, env: Mapping[str, str] | None = None) -> dict:
    config = slack_config(policy)
    merged = profile_env(policy, config.bot or "", env)
    token(merged, config.app_token_env)
    if config.mirror_notices:
        token(merged, config.bot_token_env)
    return {
        "enabled": True,
        "bot": config.bot,
        "channel_id": config.channel_id,
        "channel_name": config.channel_name,
        "app_token_env": config.app_token_env,
        "bot_token_env": config.bot_token_env,
        "mirror_notices": config.mirror_notices,
    }


class SlackApi:
    def post_json(self, method: str, bearer: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        request = urllib.request.Request(
            f"https://slack.com/api/{method}",
            method="POST",
            data=data,
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Slack API host
                raw = response.read().decode()
        except urllib.error.HTTPError as err:
            raise SlackError(f"Slack API {method} returned HTTP {err.code}") from None
        except OSError as err:
            raise SlackError(f"Slack API {method} failed: {type(err).__name__}") from err
        try:
            out = json.loads(raw)
        except ValueError as err:
            raise SlackError(f"Slack API {method} did not return JSON") from err
        if not out.get("ok"):
            raise SlackError(f"Slack API {method} refused: {out.get('error') or 'unknown_error'}")
        return out

    def socket_url(self, app_token: str) -> str:
        try:
            url = str(self.post_json("apps.connections.open", app_token, {})["url"])
        except KeyError as err:
            raise SlackError("Slack API apps.connections.open returned no WebSocket URL") from err
        if not url.startswith("wss://"):
            raise SlackError("Slack API apps.connections.open returned a non-WebSocket URL")
        return url

    def post_message(self, bot_token: str, channel: str, text: str) -> dict:
        return self.post_json("chat.postMessage", bot_token, {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False})


class WebSocket:
    def __init__(self, url: str, timeout: float = 60.0):
        self.url, self.timeout = url, timeout
        self.sock: socket.socket | ssl.SSLSocket | None = None

    def connect(self) -> None:
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise SlackError("Socket Mode URL must be wss://")
        port = parsed.port or 443
        raw = socket.create_connection((parsed.hostname, port), timeout=self.timeout)
        raw.settimeout(self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._read_until(b"\r\n\r\n")
        head, *header_lines = response.decode("iso-8859-1").split("\r\n")
        if " 101 " not in head:
            raise SlackError("Socket Mode WebSocket upgrade was refused")
        headers = {}
        for line in header_lines:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != accept:
            raise SlackError("Socket Mode WebSocket upgrade returned the wrong accept key")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self._send_frame(0x8, b"")
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def recv_text(self) -> str | None:
        while True:
            first = self._read_exact(2)
            opcode, length = first[0] & 0x0F, first[1] & 0x7F
            masked = bool(first[1] & 0x80)
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                return payload.decode()

    def send_json(self, data: dict) -> None:
        self._send_frame(0x1, json.dumps(data, separators=(",", ":")).encode())

    def _read_until(self, marker: bytes) -> bytes:
        buf = b""
        while marker not in buf:
            chunk = self._sock().recv(4096)
            if not chunk:
                raise SlackError("Socket Mode WebSocket closed during handshake")
            buf += chunk
        return buf

    def _read_exact(self, size: int) -> bytes:
        buf = b""
        while len(buf) < size:
            chunk = self._sock().recv(size - len(buf))
            if not chunk:
                raise SlackError("Socket Mode WebSocket closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if len(payload) < 126:
            header = bytes([0x80 | opcode, 0x80 | len(payload)])
        elif len(payload) <= 0xFFFF:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", len(payload))
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", len(payload))
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock().sendall(header + mask + masked)

    def _sock(self):
        if self.sock is None:
            raise SlackError("Socket Mode WebSocket is not connected")
        return self.sock


class SocketModeTransport:
    def __init__(self, app_token: str, api: SlackApi | None = None):
        self.app_token = app_token
        self.api = api or SlackApi()
        self.ws: WebSocket | None = None

    def connect(self) -> None:
        self.ws = WebSocket(self.api.socket_url(self.app_token))
        self.ws.connect()

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def receive(self) -> dict | None:
        if self.ws is None:
            raise SlackError("Socket Mode transport is not connected")
        text = self.ws.recv_text()
        try:
            return None if text is None else json.loads(text)
        except ValueError as err:
            raise SlackError("Socket Mode WebSocket returned non-JSON data") from err

    def ack(self, envelope_id: str) -> None:
        if self.ws is None:
            raise SlackError("Socket Mode transport is not connected")
        self.ws.send_json({"envelope_id": envelope_id})


class BotChat:
    """Delivers a notice into a profile's Bot Chat as an inbound turn.

    Hermes's own cron delivery to `bot-chat:<profile>` runs `hermes -p <profile> chat --in ~ -c "Bot Chat"
    --create-if-missing -Q --query-file <file>` (hermes-agent pull request #91487), so this uses the same
    command. hermes/README.md lists it among the things to check in the first pilot, because the tests fake Hermes.
    """

    def __init__(self, root: Path | None = None, runner=subprocess.run):
        self.root = root or Path.home() / ".hermes"
        self.runner = runner

    def deliver(self, bot: str, notice: str) -> None:
        directory = self.root / "team-kit" / "slack"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, prefix="notice-", suffix=".txt", delete=False) as handle:
            path = Path(handle.name)
            handle.write(notice)
            handle.write("\n")
        path.chmod(0o600)
        try:
            done = self.runner(
                ["hermes", "-p", bot, "chat", "--in", str(Path.home()), "-c", "Bot Chat", "--create-if-missing", "-Q", "--query-file", str(path)],
                capture_output=True,
                text=True,
            )
            if done.returncode:
                raise SlackError(f"Hermes Bot Chat delivery failed for {bot}")
        finally:
            path.unlink(missing_ok=True)


def clean_slack_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<#([A-Z0-9]+)\|([^>]+)>", r"#\2", text)
    text = re.sub(r"<@([A-Z0-9]+)>", r"@\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = " ".join(text.split())
    text = re.sub(r"\A@[A-Z0-9][A-Z0-9_-]*[:,]?\s*", "", text)
    if len(text) > MAX_SLACK_TEXT:
        text = text[:MAX_SLACK_TEXT - 15].rstrip() + " [truncated]"
    return text or "(empty message)"


def inline_untrusted(source: str, text: str) -> str:
    """Fence text so it can neither close the fence nor pass for a second notice."""
    body = NOTICE_IN_TEXT.sub(lambda m: m.group(0)[:-1] + "&#58;", html.escape(text, quote=False))
    return f'<untrusted source="{html.escape(source, quote=True)}">{body}</untrusted>'


def author(event: dict) -> str:
    raw = str(event.get("user_name") or event.get("username") or event.get("user") or "unknown").lstrip("@")
    return "@" + re.sub(r"[^A-Za-z0-9_.-]", "-", raw)[:80]


def notice_from_payload(config: SlackConfig, payload: dict) -> str | None:
    if payload.get("type") != "events_api":
        return None
    event = ((payload.get("payload") or {}).get("event") or {})
    if event.get("type") != "app_mention" or event.get("channel") != config.channel_id:
        return None
    if event.get("bot_id") or event.get("subtype"):
        return None
    text = clean_slack_text(str(event.get("text") or ""))
    source = f"slack:{config.channel_id}:{event.get('ts') or payload.get('envelope_id') or 'event'}"
    return f"{NOTICE} proposal from {author(event)} in #{config.channel_name}: {inline_untrusted(source, text)}"


def stderr_log(line: str) -> None:
    print(f"team-kit-slack: {line}", file=sys.stderr, flush=True)


def event_keys(payload: dict) -> list[str]:
    keys = []
    if payload.get("envelope_id"):
        keys.append(f"envelope:{payload['envelope_id']}")
    event_id = (payload.get("payload") or {}).get("event_id")
    if event_id:
        keys.append(f"event:{event_id}")
    return keys


@dataclass
class SlackRelay:
    policy: Policy
    env: Mapping[str, str] | None = None
    transport: object | None = None
    bot_chat: object | None = None
    sleep: object = time.sleep
    log: object = stderr_log
    max_backoff: float = 300.0
    seen: "OrderedDict[str, None]" = field(default_factory=OrderedDict)

    def __post_init__(self) -> None:
        self.config = slack_config(self.policy)
        self.env = profile_env(self.policy, self.config.bot or "", self.env)
        if self.transport is None:
            self.transport = SocketModeTransport(token(self.env, self.config.app_token_env))
        if self.bot_chat is None:
            self.bot_chat = BotChat(self.policy.home.parent)

    def _seen(self, keys: list[str]) -> bool:
        return any(key in self.seen for key in keys)

    def _remember(self, keys: list[str]) -> None:
        for key in keys:
            self.seen[key] = None
        while len(self.seen) > SEEN_LIMIT:
            self.seen.popitem(last=False)

    def handle(self, payload: dict) -> bool:
        """Ack and relay one payload. True when it was a new events_api event, the only kind run counts.

        Slack retries an event with the same envelope_id or event_id, so each is relayed once. An event
        is remembered only after its delivery succeeds, so a failed delivery is not lost to the dedupe.
        """
        envelope = payload.get("envelope_id")
        if envelope:
            self.transport.ack(str(envelope))
        if payload.get("type") != "events_api":
            return False
        keys = event_keys(payload)
        if self._seen(keys):
            return False
        notice = notice_from_payload(self.config, payload)
        if notice:
            self.bot_chat.deliver(self.config.bot, notice)
        self._remember(keys)
        return True

    def run(self, max_events: int | None = None, max_reconnects: int | None = None) -> int:
        """Relay until max_events new events_api events, reconnecting with backoff when the socket drops.

        Slack's `hello`, `disconnect`, and retried events are not counted. max_reconnects bounds the
        reconnects (None: forever); when it runs out, the relay stops with an error.
        """
        count, reconnects, backoff = 0, 0, 1.0
        while max_events is None or count < max_events:
            why = "connection closed"
            try:
                self.transport.connect()
                while max_events is None or count < max_events:
                    payload = self.transport.receive()
                    if payload is None:
                        break
                    backoff = 1.0
                    if payload.get("type") == "disconnect":
                        why = "Slack asked to reconnect"
                        break
                    if self.handle(payload):
                        count += 1
            except RECONNECT_ERRORS as err:
                why = f"connection error {type(err).__name__}"
            finally:
                self.transport.close()
            if max_events is not None and count >= max_events:
                break
            if max_reconnects is not None and reconnects >= max_reconnects:
                raise SlackError(f"relay gave up after {reconnects} reconnects: {why}")
            reconnects += 1
            self.log(f"{why}; reconnecting in {backoff:g}s")
            self.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff)
        return count


def mirror_notice(policy: Policy, text: str, env: Mapping[str, str] | None = None, api: SlackApi | None = None) -> dict:
    config = slack_config(policy)
    if not config.mirror_notices:
        return {"mirrored": False, "why": "mirror_notices is false"}
    if not str(text).lstrip().startswith(NOTICE):
        raise SlackError("only team-kit notice text may be mirrored")
    merged = profile_env(policy, config.bot or "", env)
    (api or SlackApi()).post_message(token(merged, config.bot_token_env), config.channel_id or "", text.strip())
    return {"mirrored": True, "channel_id": config.channel_id}


def systemd_unit(policy: Policy) -> str:
    config = slack_config(policy)
    home = str(policy.home.parent)
    return f"""[Unit]
Description=team-kit Slack Socket Mode relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=TEAM_KIT_HOME={policy.home}
ExecStart={home}/bin/team-kit-slack run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
# Tokens stay in {profile_home(policy.home.parent, config.bot or '') / '.env'} as {config.app_token_env} and {config.bot_token_env}.
"""


def launchd_plist(policy: Policy) -> str:
    config = slack_config(policy)
    home = policy.home.parent
    program = home / "bin" / "team-kit-slack"
    logs = policy.home / "logs"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>team-kit.slack</string>
  <key>ProgramArguments</key>
  <array>
    <string>{html.escape(str(program))}</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TEAM_KIT_HOME</key><string>{html.escape(str(policy.home))}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{html.escape(str(logs / 'slack.out.log'))}</string>
  <key>StandardErrorPath</key><string>{html.escape(str(logs / 'slack.err.log'))}</string>
</dict>
</plist>
<!-- Tokens stay in {html.escape(str(profile_home(home, config.bot or '') / '.env'))} as {config.app_token_env} and {config.bot_token_env}. -->
"""
