# -*- coding: utf-8 -*-
"""Wave 6 (2026-09-25): on a Mac the installer says when its start-up file runs in the words Ashley
ruled on 2026-09-24, "when you switch on your Mac and sign in". "Log in" alone reads as needing an
account, and "when the computer starts" is the other system's wording. Windows keeps its words.

Run by name only:  python3 -m pytest -q tests/mac/mac_signin_wording.py
(pytest never collects tests/mac/ in a plain run, so the counts the guides print stay true.)
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAC_WORDS = "switch on your Mac and sign in"


def test_the_windows_wording_is_written_once_and_only_as_the_other_systems_choice():
    text = (ROOT / "install.py").read_text(encoding="utf-8")
    # every message about the start-up file takes its words from WHEN_STARTS / EACH_TIME
    assert text.count("when the computer starts") == 1, "a message still says 'when the computer starts' on every system"
    assert "sign in to your Mac" not in text
    assert re.search(r'^WHEN_STARTS = "when you switch on your Mac and sign in" if common\.IS_MAC', text, re.M)


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac wording is printed on a Mac only")
def test_on_a_mac_the_help_says_switch_on_your_mac_and_sign_in():
    r = subprocess.run([sys.executable, "install.py", "--help"], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=60, creationflags=NO_WINDOW)
    out = " ".join(r.stdout.split())
    assert r.returncode == 0, r.stderr
    assert "when the computer starts" not in out
    assert "each time you " + MAC_WORDS in out and "when you " + MAC_WORDS in out, out
