"""Mac only (fault row 3, build plan V3): stopping agent-flow must never stop the program that asked.

On GitHub's test Macs on 2026-09-24 the whole test run died with SIGTERM straight after the
first test that started agent-flow: guard.start_child did not give agent-flow a process group
of its own, and common.kill_tree ends the whole group of the program it is given, which was
the caller's group. Each check below runs in a separate Python program, so a wrong kill_tree
ends that program and the check reports it, instead of ending the test run.

Run by name only:  python3 -m pytest -q tests/mac/mac_stop_spares_caller.py
(pytest never collects this file in a plain run, so the counts the guides print stay true.)
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are a Mac and Linux idea")


def run_script(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    script = tmp_path / "caller.py"
    script.write_text(textwrap.dedent(f"""
        import json, os, subprocess, sys, time
        sys.path.insert(0, {str(KIT)!r})
        import common, guard
        common.KIT_DIR = __import__("pathlib").Path({str(tmp_path / "kit")!r})
        common.logs_dir().mkdir(parents=True, exist_ok=True)
    """) + textwrap.dedent(body) + "\nprint('CALLER SURVIVED', flush=True)\n", encoding="utf-8")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "AGENT_FLOW_NPX_CMD": json.dumps([sys.executable, str(KIT / "tests" / "fake_agent_flow.py")])}
    # The caller runs in a session of its own, so a wrong kill_tree ends only the caller and
    # what it started, never pytest or the terminal pytest runs in.
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env,
                          start_new_session=True)


def test_ending_agent_flow_started_by_the_guard_spares_the_caller(tmp_path):
    r = run_script("""
        out = open(common.logs_dir() / "agent-flow.log", "ab")
        child, port = guard.start_child(os.getcwd(), "x", out)
        assert child is not None, "the stand-in agent-flow did not start"
        common.kill_tree(child.pid)
        child.wait(timeout=20)
        print("child ended with", child.returncode, flush=True)
    """, tmp_path)
    assert "CALLER SURVIVED" in r.stdout, f"kill_tree ended its caller (exit {r.returncode}): {r.stdout}{r.stderr}"
    assert "child ended with" in r.stdout


def test_ending_a_program_that_shares_our_group_spares_the_caller(tmp_path):
    """kill_tree is also handed programs it did not start (cleanup.py, start.py --stop):
    whatever group they are in, the caller's own group is never ended."""
    r = run_script("""
        kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        common.kill_tree(kid.pid)
        kid.wait(timeout=20)
        print("child ended with", kid.returncode, flush=True)
    """, tmp_path)
    assert "CALLER SURVIVED" in r.stdout, f"kill_tree ended its caller (exit {r.returncode}): {r.stdout}{r.stderr}"
    assert "child ended with" in r.stdout


def test_children_of_a_program_in_our_group_are_ended_too(tmp_path):
    """Without the group, the program's own children must still be ended (npx starts node)."""
    r = run_script("""
        kid = subprocess.Popen([sys.executable, "-c",
            "import subprocess, sys, time; g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            "print(g.pid, flush=True); time.sleep(60)"], stdout=subprocess.PIPE, text=True)
        grandkid = int(kid.stdout.readline())
        common.kill_tree(kid.pid)
        kid.wait(timeout=20)
        end = time.time() + 10
        while time.time() < end and common.pid_alive(grandkid):
            time.sleep(0.2)
        print("grandchild alive:", common.pid_alive(grandkid), flush=True)
    """, tmp_path)
    assert "CALLER SURVIVED" in r.stdout, f"kill_tree ended its caller (exit {r.returncode}): {r.stdout}{r.stderr}"
    assert "grandchild alive: False" in r.stdout, r.stdout + r.stderr
