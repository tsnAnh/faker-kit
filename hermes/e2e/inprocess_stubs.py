from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import threading
import traceback
import types
from collections.abc import Mapping, Sequence
from pathlib import Path

ALLOWED_KWARGS = frozenset({"capture_output", "text", "input", "timeout", "env", "check"})
SLOW_FLAGS = (("E2E_HERMES_SHOW", "hang"),)
LOCK = threading.Lock()
STUB_CRASHES = (OSError, LookupError, ValueError, TypeError, AttributeError, RuntimeError, ArithmeticError, AssertionError, ImportError, StopIteration, EOFError, NameError,
                subprocess.SubprocessError)


class Stubs:
    def __init__(self, stubs: Mapping[str, str]) -> None:
        self.stubs = dict(stubs)
        self.code = {name: compile(text.partition("\n")[2], name, "exec") for name, text in self.stubs.items()}
        self.known: dict[tuple[str, int, int], str | None] = {}
        self.real_run = subprocess.run
        self.calls = 0

    def kind(self, name: str, env: Mapping[str, str]) -> str | None:
        found = shutil.which(name, path=env.get("PATH"))
        if found is None or name not in self.stubs:
            return None
        status = Path(found).stat()
        key = (found, status.st_mtime_ns, status.st_size)
        if key not in self.known:
            self.known[key] = name if Path(found).read_text() == self.stubs[name] else None
        return self.known[key]

    def run(self, args: Sequence[str], *rest: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if rest or not kwargs.keys() <= ALLOWED_KWARGS or not (kwargs.get("capture_output") and kwargs.get("text")) or threading.current_thread() is not threading.main_thread():
            return self.real_run(args, *rest, **kwargs)
        env = kwargs.get("env")
        merged: Mapping[str, str] = env if isinstance(env, dict) else os.environ
        if not isinstance(args, (list, tuple)) or not args or any(merged.get(key) == value for key, value in SLOW_FLAGS):
            return self.real_run(args, *rest, **kwargs)
        kind = self.kind(str(args[0]), merged)
        if kind is None:
            return self.real_run(args, *rest, **kwargs)
        done = self.execute(kind, [str(a) for a in args], dict(merged), kwargs.get("input"))
        if kwargs.get("check") and done.returncode:
            raise subprocess.CalledProcessError(done.returncode, list(args), done.stdout, done.stderr)
        return done

    def execute(self, kind: str, args: list[str], env: dict[str, str], stdin: object) -> subprocess.CompletedProcess[str]:
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with LOCK:
            saved = (sys.argv, sys.stdin, dict(os.environ))
            try:
                sys.argv = [shutil.which(kind, path=env.get("PATH")) or kind, *args[1:]]
                sys.stdin = io.StringIO(stdin if isinstance(stdin, str) else "")
                os.environ.clear()
                os.environ.update(env)
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        types.FunctionType(self.code[kind], {"__name__": "__main__"})()
                    except SystemExit as stop:
                        code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
                        if isinstance(stop.code, str):
                            print(stop.code, file=sys.stderr)
                    except STUB_CRASHES:
                        traceback.print_exc()
                        code = 1
            finally:
                sys.argv, sys.stdin = saved[0], saved[1]
                os.environ.clear()
                os.environ.update(saved[2])
        self.calls += 1
        return subprocess.CompletedProcess(args, code, out.getvalue(), err.getvalue())


def install(stubs: Mapping[str, str]) -> Stubs:
    installed = Stubs(stubs)
    subprocess.run = installed.run
    return installed
