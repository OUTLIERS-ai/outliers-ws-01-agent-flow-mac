"""Faults found by the 2026-09-22 security audit and walkthrough, each proven here first.

All of these run against a temporary home folder and the stand-in agent-flow
(tests/fake_agent_flow.py), which copies what the real package does to a
settings.json it cannot read: it starts fresh and writes only its own hooks.
"""
import http.client
import json
import subprocess
import sys
import time

import check_hooks
import common
import install
import start
from conftest import free_port

MEMBER = {
    "model": "opus",
    "permissions": {"allow": ["Read"], "deny": ["Bash(rm:*)"]},
    "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python log_tool_use.py"}]}]},
}


def install_for(port, extra=()):
    return install.main(["--yes", "--port", str(port), "--wait", "30", "--node-path", "node", *extra])


def setup_installed(temp_home):
    sp = common.settings_path()
    sp.write_text(json.dumps(MEMBER, indent=2) + "\n", encoding="utf-8")
    port = free_port()
    assert install_for(port) == 0
    watch = json.loads(common.config_path().read_text())["watch_folder"]
    return sp, watch, port


def add_trailing_comma(sp):
    good = sp.read_text(encoding="utf-8").rstrip()
    assert good.endswith("}")
    bad = good[:-1].rstrip() + ",\n}\n"
    sp.write_text(bad, encoding="utf-8")
    try:
        json.loads(bad)
        raise AssertionError("the file should now be broken")
    except ValueError:
        pass


def wait_until(cond, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return cond()


def log_text():
    p = common.logs_dir() / "start.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# ------------------------------------------------ the logon launcher must never wipe settings.json
def test_start_refuses_when_settings_has_a_trailing_comma(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    add_trailing_comma(sp)
    before = sp.read_bytes()
    try:
        rc = start.start(watch, "x", port, 20, False, True)
        time.sleep(1.5)
        assert rc != 0
        assert sp.read_bytes() == before, "agent-flow must not have been started on a file it cannot read"
        assert start.servers_for(watch) == []
        assert "REFUSED" in log_text()
    finally:
        start.stop(watch, quiet=True)


def test_start_refuses_when_settings_has_a_byte_order_mark(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    sp.write_bytes(b"\xef\xbb\xbf" + sp.read_bytes())
    before = sp.read_bytes()
    try:
        rc = start.start(watch, "x", port, 20, False, True)
        time.sleep(1.5)
        assert rc != 0
        assert sp.read_bytes() == before
        assert start.servers_for(watch) == []
    finally:
        start.stop(watch, quiet=True)


def test_install_again_removes_a_byte_order_mark(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    sp.write_bytes(b"\xef\xbb\xbf" + sp.read_bytes())
    assert install_for(port) == 0
    assert not sp.read_bytes().startswith(b"\xef\xbb\xbf")
    assert common.read_settings()["model"] == "opus"


def test_settings_put_back_if_agent_flow_rewrites_them(vaults, temp_home, monkeypatch):
    sp, watch, port = setup_installed(temp_home)
    before = sp.read_bytes()
    monkeypatch.setenv("FAKE_AF_CLOBBER", "1")
    try:
        assert start.start(watch, "x", port, 30, False, True) == 0
        assert wait_until(lambda: list(sp.parent.glob("settings.json.bak-agent-flow-undo-*")), 20)
        assert wait_until(lambda: sp.read_bytes() == before, 10)
        assert common.read_settings()["permissions"]["deny"] == ["Bash(rm:*)"]
        assert "put back" in log_text()
    finally:
        start.stop(watch, quiet=True)


# ------------------------------------------------ stop must never kill an unrelated program
def test_stop_leaves_an_unrelated_program_alone(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 creationflags=common.NO_WINDOW)
    try:
        common.logs_dir().mkdir(parents=True, exist_ok=True)
        start.pid_file().write_text(str(bystander.pid))  # old format, left behind by a restart
        start.stop(watch, quiet=True)
        time.sleep(0.5)
        assert bystander.poll() is None, "an unrelated program was killed"
        start.pid_file().write_text(json.dumps({"pid": bystander.pid, "started": 12345}))  # wrong start time
        start.stop(watch, quiet=True)
        time.sleep(0.5)
        assert bystander.poll() is None, "an unrelated program was killed"
        assert not start.pid_file().exists()
    finally:
        bystander.kill()


def test_stop_after_watch_folder_changed_still_stops_and_says_so(vaults, temp_home, capsys):
    sp, watch, port = setup_installed(temp_home)
    assert start.start(watch, "x", port, 30, False, True) == 0
    capsys.readouterr()
    start.stop(str(temp_home), quiet=False)  # a different watch folder from the one running
    out = capsys.readouterr().out
    assert wait_until(lambda: not common.port_answers(port), 10)
    assert "Stopped agent-flow" in out
    assert "Stopped 0" not in out


# ------------------------------------------------ install options that did not do what the guide said
def test_no_autostart_removes_an_existing_launcher(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    lp = install.launcher_path()
    assert lp.exists()
    assert install_for(port, ["--no-autostart"]) == 0
    assert not lp.exists()


def test_changing_the_port_moves_a_running_server(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    assert start.start(watch, "x", port, 30, False, True) == 0
    new_port = free_port()
    try:
        assert install_for(new_port) == 0
        assert wait_until(lambda: common.port_answers(new_port), 20)
        assert wait_until(lambda: not common.port_answers(port), 10)
    finally:
        start.stop(watch, quiet=True)


def test_changing_the_watch_folder_moves_a_running_server(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    assert start.start(watch, "x", port, 30, False, True) == 0
    new_watch = str(temp_home.resolve())
    try:
        assert install.main(["--yes", "--port", str(port), "--wait", "30", "--node-path", "node",
                             "--watch-folder", new_watch]) == 0
        assert wait_until(lambda: start.servers_for(new_watch), 20)
        assert start.servers_for(watch) == []
    finally:
        start.stop(new_watch, quiet=True)
        start.stop(watch, quiet=True)


# ------------------------------------------------ plain words instead of a Python error dump
def test_check_hooks_explains_a_broken_file(temp_home, capsys):
    common.settings_path().write_text('{\n  "model": "opus",\n}\n', encoding="utf-8")
    rc = check_hooks.main([])
    out = capsys.readouterr().out
    assert rc == 2
    assert "cannot be read" in out and "line 2" in out
    assert "comma after the last item" in out


def test_odd_settings_shape_stops_cleanly(vaults, temp_home, capsys):
    sp = common.settings_path()
    sp.write_text(json.dumps({"hooks": {"Stop": ["oops"]}}), encoding="utf-8")
    before = sp.read_bytes()
    assert install_for(free_port()) == 2
    assert "STOPPED" in capsys.readouterr().out
    assert sp.read_bytes() == before


def test_uninstall_gives_back_the_same_bytes(vaults, temp_home):
    sp = common.settings_path()
    original = b'\xef\xbb\xbf{\n    "model": "opus",\n    "permissions": {"allow": ["Read"]}\n}\n'
    sp.write_bytes(original)
    assert install_for(free_port()) == 0
    assert install.main(["--uninstall"]) == 0
    assert sp.read_bytes() == original


# ------------------------------------------------ agent-flow's page only answers to its own address
def get(port, path, host):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("GET", path, skip_host=True)
    c.putheader("Host", host)
    c.endheaders()
    r = c.getresponse()
    status = r.status
    body = r.read(200) if path != "/events" else r.read1(200)
    c.close()
    return status, body


def test_page_refuses_a_foreign_host_name(vaults, temp_home):
    sp, watch, port = setup_installed(temp_home)
    assert start.start(watch, "x", port, 30, False, True) == 0
    try:
        status, _ = get(port, "/events", f"attacker.example:{port}")
        assert status == 403
        status, body = get(port, "/", f"127.0.0.1:{port}")
        assert status == 200 and b"WAITING" in body
        status, body = get(port, "/", f"localhost:{port}")
        assert status == 200
        status, body = get(port, "/events", f"127.0.0.1:{port}")
        assert status == 200 and b"agent-event" in body
    finally:
        start.stop(watch, quiet=True)
