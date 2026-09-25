"""Mac only (fault row 23, build plan V3): on a Mac the Second Brain is looked for at ~/Second Brain first.

macOS may refuse a program that starts by itself (a LaunchAgent) access to ~/Documents, so the
Mac guides put the Second Brain at ~/Second Brain. The installer must find it there first, then
at ~/Documents/Second Brain, and with no vault at all it must not pick ~/Documents as the folder
agent-flow watches. Windows keeps its own look-ups, unchanged.

Run by name only:  python3 -m pytest -q tests/mac/mac_vault_lookup.py
"""
import json
import sys

import pytest

import common
import install
from conftest import free_port

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac look-up order")


def make_vault(path):
    (path / ".obsidian").mkdir(parents=True)
    return path


def test_second_brain_outside_documents_comes_first(temp_home):
    outside = make_vault(temp_home / "Second Brain")
    make_vault(temp_home / "Documents" / "Second Brain")
    sb, _ = install.guess_paths()
    assert sb == str(outside)


def test_a_plain_second_brain_folder_outside_documents_is_found(temp_home):
    """No vault anywhere yet, only the folder the Mac guide makes: it is the guess."""
    (temp_home / "Second Brain").mkdir()
    sb, _ = install.guess_paths()
    assert sb == str(temp_home / "Second Brain")


def test_documents_is_still_found_when_it_is_the_only_one(temp_home):
    inside = make_vault(temp_home / "Documents" / "Second Brain")
    sb, _ = install.guess_paths()
    assert sb == str(inside)


def test_with_no_vault_the_watch_folder_is_home_not_documents(temp_home):
    port = free_port()
    assert install.main(["--yes", "--port", str(port), "--wait", "30", "--node-path", "node", "--no-autostart"]) == 0
    watch = json.loads(common.config_path().read_text())["watch_folder"]
    assert watch == str(temp_home.resolve())
