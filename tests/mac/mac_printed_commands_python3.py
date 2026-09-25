"""Mac only (fault row 18, build plan V3): what agent-flow prints must never tell a Mac member to type `python`.

A Mac has `python3` and no plain `python`, so a line such as "Start it now with:  python start.py"
fails with "command not found" when a Mac member types it (measured on GitHub's test Macs,
2026-09-24, wave 0a M7). Windows keeps printing `python`, which is right there.

Run by name only:  python3 -m pytest -q tests/mac/mac_printed_commands_python3.py
"""
import re
import subprocess
import sys

import pytest

import common
import install
import start
from conftest import KIT, free_port

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac wording")

# `python` or `pip` typed as a command: not python3, not python.org, not "Python 3.14".
COMMAND = re.compile(r"(?<![\w/.\-])(python|pip)(?![\w.\-])")


def bad_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if COMMAND.search(ln)]


def test_install_prints_python3(vaults, temp_home, capsys):
    port = free_port()
    assert install.main(["--yes", "--port", str(port), "--wait", "30", "--node-path", "node", "--no-autostart"]) == 0
    out = capsys.readouterr().out
    assert "python3 start.py" in out and "python3 check_hooks.py" in out
    assert bad_lines(out) == []


def test_help_names_no_windows_folder():
    r = subprocess.run([sys.executable, str(KIT / "install.py"), "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "Windows" not in r.stdout and "Startup folder" not in r.stdout
    assert bad_lines(r.stdout) == []


def test_every_reason_it_stopped_says_python3():
    for tail in ("EADDRINUSE", "npm warn EBADENGINE", "npx: command not found", "npm error E404",
                 "getaddrinfo ENOTFOUND registry.npmjs.org"):
        why = common.why_it_stopped(tail)
        assert bad_lines(why) == [], why


def test_settings_messages_say_python3(temp_home):
    sp = common.settings_path()
    for raw in (b"", b"\xef\xbb\xbf{}", b"{,}", b"[]"):
        sp.write_bytes(raw)
        msg = common.settings_fault(sp) or ""
        assert msg and bad_lines(msg) == [], msg
    msg = common.settings_problem() or ""
    assert msg and bad_lines(msg) == [], msg


def test_port_in_use_message_says_python3(temp_home, tmp_path, capsys):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        watch = tmp_path / "watch"
        watch.mkdir()
        common.hook_script().parent.mkdir(parents=True, exist_ok=True)
        common.hook_script().write_text("//", encoding="utf-8")
        common.settings_path().write_text(common.dump_settings(
            common.install_agent_flow({}, common.hook_command("node"))[0]), encoding="utf-8")
        assert start.start(str(watch), "x", port, 5, False, False) == 3
    finally:
        s.close()
    out = capsys.readouterr().out
    assert "python3 install.py --port" in out
    assert bad_lines(out) == []
