"""The 6 faults the 2026-09-22 reliability audit proved, each written here first.

Every test runs against a temporary home folder and the stand-in agent-flow
(tests/fake_agent_flow.py). None of them touches the real ~/.claude, a vault,
or the Startup folder.
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import check_hooks
import cleanup
import common
import guard
import install
import start
from conftest import KIT, free_port


def wait_until(cond, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return cond()


def installed(temp_home):
    port = free_port()
    assert install.main(["--yes", "--port", str(port), "--wait", "30", "--node-path", "node"]) == 0
    watch = json.loads(common.config_path().read_text())["watch_folder"]
    return watch, port


# ---------------------------------------------- 1. a failed start says why, in 1 sentence
NPM_NO_INTERNET = """
npm error code ENOTFOUND
npm error syscall getaddrinfo
npm error errno ENOTFOUND
npm error network request to https://registry.npmjs.org/agent-flow-app failed, reason: getaddrinfo ENOTFOUND registry.npmjs.org
npm error network This is a problem related to network connectivity.
"""

PORT_TAKEN = """
Agent Flow

node:events:485
      throw er; // Unhandled 'error' event
      ^
Error: listen EADDRINUSE: address already in use 127.0.0.1:63019
"""

NODE_MISSING = """
'node' is not recognized as an internal or external command,
operable program or batch file.
"""

NODE_TOO_OLD = """
npm error code EBADENGINE
npm error engine Unsupported engine
npm error engine Not compatible with your version of node/npm: agent-flow-app@0.9.1
npm error notsup Required: {"node":">=18"}
npm error notsup Actual:   {"npm":"9.2.0","node":"v16.19.0"}
"""


@pytest.mark.parametrize("tail,expect", [
    (PORT_TAKEN, "port"),
    (NPM_NO_INTERNET, "internet"),
    (NODE_MISSING, "Node.js"),
    (NODE_TOO_OLD, "Node.js"),
])
def test_a_failed_start_is_explained_in_one_plain_sentence(tail, expect):
    said = common.why_it_stopped(tail)
    assert said, "every known failure shape must produce a sentence"
    assert expect.lower() in said.lower()
    assert "\n" not in said and len(said) < 230, "short and on 1 line, not a paragraph: " + said
    assert "npm error" not in said and "Traceback" not in said


def test_an_unknown_failure_quotes_the_log_instead_of_the_path():
    said = common.why_it_stopped("something nobody has seen before\nlast line of the log\n")
    assert "last line of the log" in said


def test_start_prints_why_it_failed_and_status_repeats_it(vaults, temp_home, capsys, monkeypatch):
    watch, port = installed(temp_home)
    # a stand-in that dies at once with the port-taken message, the way agent-flow does
    dying = Path(common.KIT_DIR) / "dying.py"
    dying.write_text("import sys;sys.stderr.write('Error: listen EADDRINUSE: address already "
                     "in use 127.0.0.1:63019\\n');sys.exit(1)\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_FLOW_NPX_CMD", json.dumps([sys.executable, str(dying)]))
    capsys.readouterr()
    rc = start.start(watch, "x", port, 25, False, False)
    out = capsys.readouterr().out
    assert rc == 4
    assert "port" in out.lower(), "the member must be told the reason, not a file path: " + out
    assert "agent-flow.log" in out, "the log is still offered for the detail"
    assert start.main(["--status", "--watch-folder", watch, "--port", str(port)]) == 1
    status = capsys.readouterr().out
    assert "port" in status.lower(), "--status must repeat the last failure: " + status


# ---------------------------------------------- 2. the private-port race in guard.free_port
def test_guard_tries_another_port_when_the_one_it_picked_is_taken(vaults, temp_home, monkeypatch):
    """The race, forced: the port the guard picked is occupied before the child uses it."""
    squatter = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: stop anything else binding it
        squatter.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(4)
    taken = squatter.getsockname()[1]
    picks = [taken]

    def fake_free_port():
        return picks.pop(0) if picks else _real_free_port()

    _real_free_port = guard.free_port
    monkeypatch.setattr(guard, "free_port", fake_free_port)
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    out = open(common.logs_dir() / "agent-flow.log", "ab")
    try:
        child, port = guard.start_child(str(temp_home), "x", out)
        assert child is not None, "the guard gave up instead of trying another port"
        assert port != taken
        assert wait_until(lambda: common.http_answers(port), 20)
        common.kill_tree(child.pid)
    finally:
        out.close()
        squatter.close()


def test_guard_gives_up_after_three_tries_and_says_so(vaults, temp_home, monkeypatch):
    monkeypatch.setenv("AGENT_FLOW_NPX_CMD", json.dumps([sys.executable, "-c", "raise SystemExit(1)"]))
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    out = open(common.logs_dir() / "agent-flow.log", "ab")
    try:
        child, port = guard.start_child(str(temp_home), "x", out, attempts=3)
        assert child is None
    finally:
        out.close()


# ---------------------------------------------- 3. a hard kill leaves a second server
def fake_server(temp_home, workspace, name="zzz"):
    """A process sitting on a port, registered the way agent-flow registers itself."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import socket,sys,time;s=socket.socket();s.bind(('127.0.0.1',0));s.listen(4);"
         "print(s.getsockname()[1],flush=True);time.sleep(300)"],
        stdout=subprocess.PIPE, text=True, creationflags=common.NO_WINDOW)
    port = int(proc.stdout.readline().strip())
    d = common.discovery_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}-{proc.pid}.json"
    f.write_text(json.dumps({"pid": proc.pid, "port": port, "workspace": str(workspace)}), encoding="utf-8")
    return proc, f


def test_cleanup_counts_servers_not_folders_and_ends_the_extra(vaults, temp_home, tmp_path, capsys):
    ws = str(tmp_path)
    a, fa = fake_server(temp_home, ws, "aaa")
    time.sleep(1.1)  # the newest registration is the one kept
    b, fb = fake_server(temp_home, ws, "bbb")
    try:
        res = cleanup.run(quiet=False)
        out = capsys.readouterr().out
        assert res["kept"] == 1, "2 servers watching 1 folder must not be counted as 1 server"
        assert res["ended"] == 1
        assert "more than 1 agent-flow server" in out
        assert wait_until(lambda: a.poll() is not None, 10), "the extra server was left running"
        assert not fa.exists()
        assert fb.exists() and b.poll() is None
    finally:
        a.kill()
        b.kill()


def test_a_server_left_by_a_crash_is_ended_before_a_new_start(vaults, temp_home, capsys):
    """The crash case: the guard is gone, agent-flow is not, so the page port is dead
    while a registration for the folder is still live."""
    watch, port = installed(temp_home)
    orphan, f = fake_server(temp_home, watch, "orphan")
    try:
        assert start.servers_for(watch), "the orphan must look live before the fix runs"
        capsys.readouterr()
        assert start.start(watch, "x", port, 30, False, False) == 0
        assert "left behind" in capsys.readouterr().out
        assert wait_until(lambda: orphan.poll() is not None, 10)
        assert len(start.servers_for(watch)) == 1, "1 folder must end with 1 server"
    finally:
        orphan.kill()
        start.stop(watch, quiet=True)


# ---------------------------------------------- 4. check_hooks and start.py disagree
def test_check_hooks_and_start_agree_that_an_empty_settings_file_is_a_problem(temp_home, capsys):
    common.settings_path().write_text("", encoding="utf-8")
    (common.discovery_dir()).mkdir(parents=True, exist_ok=True)
    common.hook_script().write_text("// hook\n", encoding="utf-8")
    refused = common.settings_problem()
    assert refused and "empty" in refused.lower()
    rc = check_hooks.main([])
    out = capsys.readouterr().out
    assert rc == 2, "check_hooks called an empty file OK while start.py refused it"
    assert "RESULT: PROBLEM" in out and "empty" in out.lower()
    assert "install.py" in out


# ---------------------------------------------- 5. no temporary render file in the download
def test_the_download_has_no_temporary_render_files():
    strays = [p.name for p in KIT.rglob("*")
              if p.is_file() and (p.name.startswith(".tmp-") or p.suffix == ".tmp")]
    assert strays == [], f"temporary files must never ship to a member: {strays}"


def test_gitignore_covers_temporary_render_files():
    lines = {ln.strip() for ln in (KIT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ".tmp-*" in lines and "*.tmp" in lines


# ---------------------------------------------- 6. version floors
def test_the_installer_refuses_a_python_older_than_the_floor(temp_home, monkeypatch, capsys):
    monkeypatch.setattr(install, "MIN_PY", (99, 0))
    assert install.main(["--yes"]) == 2
    out = capsys.readouterr().out
    assert "Python 99.0 or newer" in out
    assert not common.settings_path().exists(), "nothing may be written when it refuses"


def test_the_python_refusal_tells_the_truth_about_security_fixes(monkeypatch):
    """Added 2026-09-24: the printed refusal said a 3.10 'no longer gets security fixes',
    while the guide said (correctly, python.org) it gets them until 2026-10-31."""
    monkeypatch.setattr(install.sys, "version_info", (3, 10, 14, "final", 0))
    text = install.python_help()
    assert "no longer gets security fixes" not in text
    assert "3.10 gets security fixes only until 2026-10-31" in text


def test_the_installer_refuses_a_node_older_than_the_floor(temp_home, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_FLOW_NPX_CMD")
    monkeypatch.setattr(common, "node_version", lambda: (20, 19, 0))
    assert install.main(["--yes"]) == 2
    out = capsys.readouterr().out
    assert "Node.js 22" in out
    assert not common.settings_path().exists()


def test_the_floors_are_the_same_number_everywhere():
    readme = (KIT / "README.md").read_text(encoding="utf-8")
    guide = (KIT / "guide" / "GUIDE.md").read_text(encoding="utf-8")
    for text, where in ((readme, "README.md"), (guide, "guide/GUIDE.md")):
        assert "Python 3.11 or newer" in text, where
        assert "Node.js 22 or newer" in text, where
        assert "Python 3.8 or newer" not in text, where
        assert "Node.js 18 or newer" not in text, where
    assert install.MIN_PY == (3, 11)
    assert common.MIN_NODE == 22
