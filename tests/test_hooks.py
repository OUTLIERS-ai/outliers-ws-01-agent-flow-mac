import json

import check_hooks
import common

WIN_CMD = '"C:\\Program Files\\nodejs\\node.exe" "C:\\Users\\sam\\.claude\\agent-flow\\hook.js"'
OTHER = {"hooks": [{"type": "command", "command": "python log_tool_use.py"}]}


def piled_up(copies=12):
    hooks = {}
    for ev in common.EVENTS:
        hooks[ev] = [{"hooks": [{"type": "command", "command": WIN_CMD, "timeout": 2}]} for _ in range(copies)]
    hooks["PreToolUse"].insert(0, OTHER)
    return {"model": "sonnet", "hooks": hooks}


def test_marker_matches_both_slash_styles():
    assert common.is_agent_flow_command(WIN_CMD)
    assert common.is_agent_flow_command('"/usr/local/bin/node" "/Users/sam/.claude/agent-flow/hook.js"')
    assert not common.is_agent_flow_command("python log_tool_use.py")


def test_hook_command_uses_forward_slashes(temp_home):
    cmd = common.hook_command("C:\\Program Files\\nodejs\\node.exe")
    assert "\\" not in cmd
    assert "agent-flow/hook.js" in cmd  # the text upstream looks for, so it never adds its own copy


def test_install_cuts_12_copies_to_1_and_keeps_other_hooks(temp_home):
    s = piled_up(12)
    assert common.count_agent_flow(s)["Stop"] == 12
    new, removed = common.install_agent_flow(s, common.hook_command("node"))
    assert set(common.count_agent_flow(new).values()) == {1}
    assert removed["Stop"] == 12
    assert new["hooks"]["PreToolUse"][0] == OTHER
    assert new["model"] == "sonnet"


def test_install_is_idempotent(temp_home):
    cmd = common.hook_command("node")
    once, _ = common.install_agent_flow(piled_up(3), cmd)
    twice, removed = common.install_agent_flow(once, cmd)
    assert once == twice
    assert set(removed.values()) == {0}


def test_remove_restores_original_exactly(temp_home):
    original = {"model": "sonnet", "hooks": {"PreToolUse": [OTHER]}}
    installed, _ = common.install_agent_flow(original, common.hook_command("node"))
    back, n = common.remove_agent_flow(installed)
    assert n == 9
    assert back == original
    bare, _ = common.remove_agent_flow(common.install_agent_flow({}, "x agent-flow/hook.js")[0])
    assert bare == {}


def test_duplicate_report_and_check_hooks_exit_codes(temp_home, capsys):
    p = common.settings_path()
    p.write_text(json.dumps(piled_up(5)), encoding="utf-8")
    dups = common.duplicate_report(json.loads(p.read_text()))
    assert len(dups) == 9
    assert check_hooks.main([]) == 1
    out = capsys.readouterr().out
    assert "DUPLICATES FOUND" in out and "5 copies" in out
    assert check_hooks.main(["--fix"]) == 0
    fixed = json.loads(p.read_text())
    assert set(common.count_agent_flow(fixed).values()) == {1}
    assert fixed["hooks"]["PreToolUse"][0] == OTHER
    assert list(p.parent.glob("settings.json.bak-hookdedupe-*"))
    assert check_hooks.main([]) == 0


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "x.json"
    target.write_text("old", encoding="utf-8")
    common.atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]
