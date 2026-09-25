import json
import os
import socket

import cleanup
import common
from conftest import free_port


def write(d, name, pid, port, ws):
    (d / name).write_text(json.dumps({"pid": pid, "port": port, "workspace": ws}), encoding="utf-8")


def test_cleanup_keeps_live_and_removes_stale(temp_home, tmp_path):
    d = common.discovery_dir()
    d.mkdir(parents=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    live_port = listener.getsockname()[1]
    try:
        write(d, "aaa-1.json", os.getpid(), live_port, str(tmp_path))             # running
        write(d, "bbb-2.json", 999999, free_port(), str(tmp_path / "narrow"))       # process ended
        write(d, "ccc-3.json", os.getpid(), free_port(), str(tmp_path / "narrow"))  # number reused, no port
        (d / "ddd-4.json").write_text("not json", encoding="utf-8")                 # unreadable
        (d / "hook.js").write_text("//", encoding="utf-8")
        dry = cleanup.run(dry_run=True, quiet=True)
        assert dry == {"kept": 1, "removed": 3, "ended": 0, "dry_run": True}
        assert len(list(d.glob("*.json"))) == 4
        res = cleanup.run(quiet=True)
        assert res["kept"] == 1 and res["removed"] == 3
        assert sorted(f.name for f in d.iterdir()) == ["aaa-1.json", "hook.js"]
    finally:
        listener.close()


def test_cleanup_with_no_folder_is_harmless(temp_home):
    assert cleanup.run(quiet=True) == {"kept": 0, "removed": 0, "ended": 0, "dry_run": False}
