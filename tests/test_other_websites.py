"""agent-flow's own 2 ports must refuse requests that come from another website.

Proven with the real agent-flow 0.9.1 on 2026-09-24 (review/final2-fix-1-agent-flow.md):
with only guard.py in front, the random port agent-flow serves its page on answered a
request whose Host named another website (what a DNS-rebinding page sends) with the
page AND the live stream of the conversation, and the random port that receives events
from hook.js took a made-up event carrying another website's Origin. guard.py only
protected port 3001.

These tests start a stand-in for agent-flow written in Node.js (tests/fake_agent_flow_node.js),
which, like the real one, checks nothing, exactly the way guard.py starts the real one.
They need Node.js, which this piece needs anyway.
"""
import http.client
import json
import os
import shutil
import time

import pytest

import common
import guard
from conftest import KIT

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="needs Node.js")


def ask(port, method="GET", path="/", host=None, origin=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    if origin:
        c.putheader("Origin", origin)
    data = (body or "").encode()
    if method == "POST":
        c.putheader("Content-Type", "text/plain")  # what a web page may send without asking first
        c.putheader("Content-Length", str(len(data)))
    c.endheaders()
    if method == "POST":
        c.send(data)
    status = c.getresponse().status
    c.close()
    return status


@pytest.fixture
def node_agent_flow(temp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_FLOW_NPX_CMD", json.dumps([NODE, str(KIT / "tests" / "fake_agent_flow_node.js")]))
    ws = tmp_path / "watch"
    ws.mkdir()
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    out = open(common.logs_dir() / "agent-flow.log", "ab")
    child, page_port = guard.start_child(str(ws), "unused", out)
    try:
        assert child is not None, "the stand-in agent-flow did not start"
        end = time.time() + 10
        while time.time() < end and not (ws / "event-port.txt").exists():
            time.sleep(0.1)
        event_port = int((ws / "event-port.txt").read_text())
        yield page_port, event_port
    finally:
        if child is not None:
            common.kill_tree(child.pid)
        out.close()


def test_page_port_answers_this_computer(node_agent_flow):
    page_port, _ = node_agent_flow
    assert ask(page_port) == 200
    assert ask(page_port, host=f"localhost:{page_port}") == 200


def test_page_port_refuses_a_rebinding_host(node_agent_flow):
    page_port, _ = node_agent_flow
    assert ask(page_port, host=f"attacker.example:{page_port}") == 403
    assert ask(page_port, path="/events", host=f"attacker.example:{page_port}") == 403


def test_page_port_refuses_another_websites_origin(node_agent_flow):
    page_port, _ = node_agent_flow
    assert ask(page_port, path="/events", origin="https://attacker.example") == 403


def test_event_port_takes_hook_js_events(node_agent_flow):
    """hook.js sends Host 127.0.0.1:<port> and no Origin: that must still get through."""
    _, event_port = node_agent_flow
    assert ask(event_port, method="POST", body='{"session_id":"s","hook_event_name":"Stop"}') == 200


def test_event_port_refuses_made_up_events_from_a_website(node_agent_flow):
    _, event_port = node_agent_flow
    fake = '{"session_id":"s","hook_event_name":"SessionStart"}'
    assert ask(event_port, method="POST", body=fake, origin="https://attacker.example") == 403
    assert ask(event_port, method="POST", body=fake, host=f"attacker.example:{event_port}",
               origin=f"http://attacker.example:{event_port}") == 403


def test_members_own_node_options_are_kept(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=512")
    env = guard.child_env()
    assert "--max-old-space-size=512" in env["NODE_OPTIONS"]
    assert "--require" in env["NODE_OPTIONS"]
    assert env["AGENT_FLOW_TELEMETRY"] == "false" and env["DO_NOT_TRACK"] == "1"
