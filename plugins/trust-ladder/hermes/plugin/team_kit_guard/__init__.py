"""team-kit guard: deterministic tool-call policy for the dispatcher and launcher profiles.

pre_tool_call blocks deny rules and calls outside a profile's allowlist, and asks the owner to
approve side-effecting tools in a turn that read untrusted content. transform_tool_result wraps
untrusted readers' output and taints the turn. Every failure blocks or wraps; none allows.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
from pathlib import Path
from typing import Callable

from .policy import Policy, PolicyError, block, decide, load_policy, reader_source, wrap_untrusted

logger = logging.getLogger(__name__)

DECISION_TIMEOUT_SECONDS = 5.0


def config_path() -> Path:
    base = os.environ.get("TEAM_KIT_HOME") or str(Path.home() / ".hermes" / "team-kit")
    return Path(base) / "guard.yaml"


def current_profile() -> str:
    try:
        from hermes_cli.profiles import current_profile_name

        return current_profile_name("default") or "default"
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        return home.name if home.parent.name == "profiles" else "default"


class Guard:
    def __init__(self, load: Callable[[], Policy], profile: Callable[[], str], timeout: float = DECISION_TIMEOUT_SECONDS):
        self._load = load
        self._profile = profile
        self._timeout = timeout
        self._tainted: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="team-kit-guard")

    @staticmethod
    def _turn(session_id, turn_id) -> tuple[str, str]:
        return (str(session_id or ""), str(turn_id or ""))

    def is_tainted(self, session_id, turn_id) -> bool:
        with self._lock:
            return self._turn(session_id, turn_id) in self._tainted

    def taint(self, session_id, turn_id) -> None:
        with self._lock:
            self._tainted.add(self._turn(session_id, turn_id))

    def _bounded(self, fn, *args):
        return self._pool.submit(fn, *args).result(timeout=self._timeout)

    def pre_tool_call(self, tool_name="", args=None, session_id="", turn_id="", **_):
        try:
            tainted = self.is_tainted(session_id, turn_id)
            return self._bounded(lambda: decide(self._load(), self._profile(), tool_name, args or {}, tainted))
        except concurrent.futures.TimeoutError:
            return block(f"policy check for {tool_name} timed out; blocked")
        except PolicyError as err:
            return block(f"{err}; every tool call is blocked until guard.yaml loads")
        except Exception as err:
            logger.exception("team-kit guard failed")
            return block(f"policy check for {tool_name} failed ({type(err).__name__}); blocked")

    def transform_tool_result(self, tool_name="", args=None, result=None, session_id="", turn_id="", **_):
        try:
            source = self._bounded(lambda: reader_source(self._load(), tool_name, args or {}))
        except Exception:
            logger.exception("team-kit guard could not classify %s; treating its result as untrusted", tool_name)
            source = tool_name
        if source is None:
            return None
        self.taint(session_id, turn_id)
        return wrap_untrusted(source, result)

    def end_turn(self, session_id="", turn_id="", **_):
        with self._lock:
            self._tainted.discard(self._turn(session_id, turn_id))


def register(ctx) -> None:
    guard = Guard(load=lambda: load_policy(config_path()), profile=current_profile)
    ctx.register_hook("pre_tool_call", guard.pre_tool_call)
    ctx.register_hook("transform_tool_result", guard.transform_tool_result)
    ctx.register_hook("on_session_end", guard.end_turn)
