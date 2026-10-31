import json
import os
import pwd
import subprocess
import sys

KIT_OWNED = True
WRAPPER = "@WRAPPER@"
HERMES_HOME = "@HERMES_HOME@"
PATH = "@PATH@"
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
TIMEOUT_SECONDS = 240
STDERR_TAIL = 400
QUIET_KEYS = ("new", "resolved", "unreadable")


def fail(message: str, stderr: str = "") -> None:
    sys.stderr.write(message.strip() + "\n")
    tail = " ".join(stderr.split())[-STDERR_TAIL:]
    if tail:
        sys.stderr.write(f"stderr tail: {tail}\n")
    sys.exit(1)


def child_env() -> dict[str, str]:
    path = [part for part in PATH.split(os.pathsep) if part]
    path += [part for part in SYSTEM_PATH if part not in path]
    return {
        "HERMES_HOME": os.environ.get("HERMES_HOME") or HERMES_HOME,
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "PATH": os.pathsep.join(path),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def main() -> None:
    try:
        done = subprocess.run([WRAPPER, "incidents"], env=child_env(), capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        fail(f"faker-kit incidents did not run: {type(err).__name__}")
        return
    if done.returncode != 0:
        fail(f"faker-kit incidents exited {done.returncode}", done.stderr)
        return
    try:
        report = json.loads(done.stdout)
    except ValueError:
        fail("faker-kit incidents printed no JSON", done.stderr)
        return
    if not isinstance(report, dict) or not all(isinstance(report.get(key), list) for key in QUIET_KEYS):
        fail("faker-kit incidents printed an unexpected report shape", done.stderr)
        return
    if all(report[key] == [] for key in QUIET_KEYS):
        sys.stdout.write(json.dumps({"wakeAgent": False}) + "\n")
        return
    ran_at = report.get("ran_at")
    if not isinstance(ran_at, str):
        fail("faker-kit incidents printed no ran_at", done.stderr)
        return
    sys.stdout.write(
        f"faker-kit incidents ran at {ran_at}.\n"
        f"New incidents: {len(report['new'])}\n"
        f"Resolved incidents: {len(report['resolved'])}\n"
        f"Unreadable sources: {len(report['unreadable'])}\n"
        f"Stop the line: {'true' if report.get('stop_the_line') is True else 'false'}\n"
        "Read this run's report with: faker-kit incidents --last\n"
    )


main()
