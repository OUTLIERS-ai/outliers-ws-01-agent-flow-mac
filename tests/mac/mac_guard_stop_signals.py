"""Mac only (wave 1 second read, after fault row 3): when the guard is stopped, agent-flow stops too.

Row 3 gave agent-flow a process group of its own (guard.start_child), so that stopping agent-flow
never stops the program that asked. The cost: agent-flow is no longer ended together with the
guard's group. The guard ends agent-flow in its own clean-up, but Python skips that clean-up
when the program is ended by SIGTERM or SIGHUP, which is what a Mac sends when:
- the login job is switched off (`python3 install.py --no-autostart` or `--uninstall` run
  `launchctl unload`, and the Mac's job manager sends SIGTERM), or
- the Terminal window running `python3 start.py --foreground` is closed (SIGHUP).
Before this fix agent-flow kept running after either, still taking Claude Code's events.

Each check starts the real guard.py (or start.py --foreground) with the tests' stand-in for
agent-flow, in a throwaway home and kit folder, waits for the page, stops it the way the Mac
does, and requires the stand-in's own process to be gone.

Run by name only:  python3 -m pytest -q tests/mac/mac_guard_stop_signals.py
(pytest never collects this file in a plain run, so the counts the guides print stay true.)
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM and SIGHUP stop signals are a Mac and Linux idea")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A finished child nobody has collected yet still answers kill(pid, 0); ps shows it as Z.
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def setup(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    kit = tmp_path / "kit"
    kit.mkdir()
    ws = tmp_path / "watch"
    ws.mkdir()
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "AGENT_FLOW_NPX_CMD": json.dumps([sys.executable, str(KIT / "tests" / "fake_agent_flow.py")])}
    return home, kit, ws, env


def wait_for_agent_flow(home: Path, kit: Path, proc: subprocess.Popen) -> int:
    """The stand-in's process number, read from the discovery file it writes, once the page is ready."""
    log = kit / "logs" / "start.log"
    end = time.time() + 90
    while time.time() < end:
        if proc.poll() is not None:
            break
        ready = log.exists() and "page ready" in log.read_text(encoding="utf-8", errors="replace")
        found = sorted((home / ".claude" / "agent-flow").glob("*-*.json"))
        if ready and found:
            return int(json.loads(found[-1].read_text(encoding="utf-8"))["pid"])
        time.sleep(0.2)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "(no start.log)"
    raise AssertionError(f"agent-flow's page never became ready (exit {proc.poll()}):\n{text}")


def gone_within(pid: int, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


def start_guard(tmp_path: Path):
    home, kit, ws, env = setup(tmp_path)
    cmd = [sys.executable, str(KIT / "guard.py"), "--kit-dir", str(kit), "--workspace", str(ws),
           "--package", "x", "--port", str(free_port()), "--wait", "60"]
    # A session of its own, as the login job gives it, so the signal below reaches the guard's
    # group and never pytest's.
    proc = subprocess.Popen(cmd, cwd=str(ws), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    return home, kit, proc


@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])  # by name: Windows has no SIGHUP to collect
def test_stopping_the_guard_stops_agent_flow(tmp_path, name):
    sig = getattr(signal, name)
    home, kit, guard = start_guard(tmp_path)
    af = None
    try:
        af = wait_for_agent_flow(home, kit, guard)
        os.killpg(guard.pid, sig)  # the guard's own group only: agent-flow is in a group of its own
        guard.wait(timeout=30)
        assert gone_within(af, 15), (
            f"the guard was stopped with {sig.name} but agent-flow (process {af}) is still running")
    finally:
        for pid in ([af] if af else []):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if guard.poll() is None:
            guard.kill()


def test_stopping_a_foreground_start_py_stops_agent_flow(tmp_path):
    """The login job runs `start.py --foreground`. Switching the job off sends SIGTERM to
    start.py; whatever the Mac then does to the rest of the job, agent-flow must end."""
    home, kit, ws, env = setup(tmp_path)
    kit_copy = tmp_path / "kitcode"
    shutil.copytree(KIT, kit_copy, ignore=shutil.ignore_patterns(".git", "tests", "logs", "__pycache__"))
    script = tmp_path / "job.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(kit_copy)!r})
        import common, start
        common.KIT_DIR = __import__("pathlib").Path({str(kit)!r})
        common.settings_problem = lambda: None  # settings.json safety is checked elsewhere
        sys.exit(start.start({str(ws)!r}, "x", {free_port()}, 60.0, True, True))
    """), encoding="utf-8")
    job = subprocess.Popen([sys.executable, str(script)], cwd=str(ws), env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    af = None
    try:
        af = wait_for_agent_flow(home, kit, job)
        os.kill(job.pid, signal.SIGTERM)  # start.py alone, as the job manager does first
        job.wait(timeout=30)
        assert gone_within(af, 15), (
            f"start.py --foreground was stopped with SIGTERM but agent-flow (process {af}) is still running")
    finally:
        for pid in ([af] if af else []):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.killpg(job.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
