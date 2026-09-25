"""The settings.json wipe, reproduced with the REAL agent-flow package from npm.

Skipped unless AGENT_FLOW_REAL=1, because it downloads agent-flow-app (about
1 MB) and needs Node.js. It still uses a temporary home folder only.

    AGENT_FLOW_REAL=1 python -m pytest -q tests/test_real_package.py
"""
import json
import os
import time

import pytest

import common
import install
import start
from conftest import free_port

pytestmark = pytest.mark.skipif(os.environ.get("AGENT_FLOW_REAL") != "1",
                                reason="set AGENT_FLOW_REAL=1 to run against the real npm package")

MEMBER = {"permissions": {"allow": ["Read"], "deny": ["Bash(rm:*)"]}, "model": "opus"}


@pytest.fixture
def real(vaults, temp_home, monkeypatch):
    monkeypatch.delenv("AGENT_FLOW_NPX_CMD")
    sp = common.settings_path()
    sp.write_text(json.dumps(MEMBER) + "\n", encoding="utf-8")
    port = free_port()
    assert install.main(["--yes", "--port", str(port), "--wait", "170"]) == 0
    watch = json.loads(common.config_path().read_text())["watch_folder"]
    yield sp, watch, port
    start.stop(watch, quiet=True)


@pytest.mark.parametrize("damage", ["trailing comma", "byte-order mark"])
def test_real_agent_flow_start_keeps_member_settings(real, damage):
    sp, watch, port = real
    if damage == "trailing comma":
        good = sp.read_text(encoding="utf-8").rstrip()
        sp.write_text(good[:-1].rstrip() + ",\n}\n", encoding="utf-8")
    else:
        sp.write_bytes(b"\xef\xbb\xbf" + sp.read_bytes())
    before = sp.read_bytes()
    start.start(watch, common.DEFAULT_PACKAGE, port, 120, False, True)
    time.sleep(5)
    after = sp.read_bytes()
    assert after == before, "agent-flow rewrote settings.json:\n" + after[:300].decode("utf-8", "replace")


def test_real_agent_flow_clean_start_changes_nothing_and_page_is_guarded(real):
    import http.client

    sp, watch, port = real
    before = sp.read_bytes()
    assert start.start(watch, common.DEFAULT_PACKAGE, port, 120, False, True) == 0
    time.sleep(3)
    assert sp.read_bytes() == before
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("GET", "/events", skip_host=True)
    c.putheader("Host", f"attacker.example:{port}")
    c.endheaders()
    assert c.getresponse().status == 403
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/")
    r = c.getresponse()
    assert r.status == 200 and b"index.js" in r.read()
    c.close()

    # agent-flow's own 2 ports refuse other websites too (only_this_computer.js, 2026-09-24)
    import re

    log = (common.logs_dir() / "start.log").read_text(encoding="utf-8")
    private = int(re.findall(r"answering on private port (\d+)", log)[-1])
    event = [i for i in common.discovery_files() if i["live"]][-1]["port"]

    def status(p, method, host, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", p, timeout=10)
        c.putrequest(method, "/", skip_host=True)
        c.putheader("Host", host)
        if origin:
            c.putheader("Origin", origin)
        c.putheader("Content-Length", "2" if method == "POST" else "0")
        c.endheaders()
        if method == "POST":
            c.send(b"{}")
        s = c.getresponse().status
        c.close()
        return s

    assert status(private, "GET", f"127.0.0.1:{private}") == 200
    assert status(private, "GET", f"attacker.example:{private}") == 403
    assert status(event, "POST", f"127.0.0.1:{event}") == 200
    assert status(event, "POST", f"127.0.0.1:{event}", origin="https://attacker.example") == 403


def test_real_agent_flow_rewrite_is_put_back_by_the_guard(real):
    """Skips start.py's check on purpose (launches the guard directly) so the real
    agent-flow DOES wipe the file, then proves the guard puts it back."""
    sp, watch, port = real
    good = sp.read_text(encoding="utf-8").rstrip()
    sp.write_text(good[:-1].rstrip() + ",\n}\n", encoding="utf-8")
    before = sp.read_bytes()
    proc = start.launch(watch, common.DEFAULT_PACKAGE, port, False, 120)
    try:
        end = time.time() + 60
        while time.time() < end and not list(sp.parent.glob("settings.json.bak-agent-flow-undo-*")):
            time.sleep(0.5)
        undo = list(sp.parent.glob("settings.json.bak-agent-flow-undo-*"))
        assert undo, "the real agent-flow did not rewrite the file, or the guard missed it"
        assert b"permissions" not in undo[0].read_bytes(), "the kept copy is agent-flow's wiped version"
        time.sleep(1)
        assert sp.read_bytes() == before
    finally:
        common.kill_tree(proc.pid)
