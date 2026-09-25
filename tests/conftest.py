import json
import os
import socket
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """A made-up home folder. Nothing in the tests may touch the real one."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / "AppData" / "Roaming").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("AGENT_FLOW_NPX_CMD", json.dumps([sys.executable, str(KIT / "tests" / "fake_agent_flow.py")]))
    import common

    kit_copy = tmp_path / "kit"
    kit_copy.mkdir()
    monkeypatch.setattr(common, "KIT_DIR", kit_copy)  # config.json and logs go to the temp kit
    assert str(common.settings_path()).startswith(str(tmp_path))
    assert str(common.startup_dir()).startswith(str(tmp_path))
    return home


@pytest.fixture
def vaults(temp_home):
    docs = temp_home / "Documents"
    sb = docs / "Second Brain"
    crm = docs / "Priya Shah CRM"
    for v in (sb, crm):
        (v / ".obsidian").mkdir(parents=True)
    (sb / "People").mkdir()
    (sb / "People" / "Sam the bookkeeper.md").write_text("# Sam the bookkeeper\n", encoding="utf-8")
    return sb, crm
