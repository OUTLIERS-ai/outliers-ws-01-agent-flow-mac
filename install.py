"""Install agent-flow for your Claude Code workspace, safely.

What it does, in order:
  1. Checks Python 3.11 or newer and Node.js 22 or newer are installed (and
     explains how to get them if not).
  2. Asks where your second brain vault and CRM vault are, and which folder to
     watch (the folder that contains both). Saves that in config.json.
  3. Runs agent-flow once, hidden, with a throwaway home folder, so it writes
     its own hook script without touching your settings; copies that script in.
  4. Backs up your Claude Code settings.json, then makes sure each of the 9
     agent-flow events has EXACTLY 1 copy of the hook. Extra copies are removed.
  5. Adds a hidden file that starts agent-flow by itself each time you switch on
     your computer and sign in (Windows: a .vbs in your Startup folder;
     Mac: a launchd job), unless you chose --no-autostart. That choice is
     saved in config.json and kept on every later run until you pass
     --autostart. start.py switches usage tracking off on every start.

Run it twice and the second run changes nothing.

    python install.py                      # interview
    python install.py --yes                # accept the defaults it finds
    python install.py --no-autostart       # no start-up file; later runs keep this
    python install.py --autostart          # put the start-up file back
    python install.py --uninstall          # remove the hook and that start-up file
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import common
import start as starter

LAUNCHER_NAME_WIN = "outliers-agent-flow.vbs"
LAUNCHER_LABEL_MAC = "com.outliers.agent-flow"

# Python 3.8 stopped getting security fixes on 2024-10-07 and 3.10 gets them only until 2026-10-31,
# so the floor is 3.11 (checked 2026-09-22). This installer TESTS it, it does not only
# name it in the README.
MIN_PY = (3, 11)


# ---------------------------------------------------------------- interview
def _pointer(name: str) -> str | None:
    p = common.home() / name
    try:
        line = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return line if line and Path(line).is_dir() else None


def find_vaults() -> list[Path]:
    """Folders with an .obsidian folder inside, up to 2 levels under home and Documents."""
    found: list[Path] = []
    roots = [common.home(), common.home() / "Documents"]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            level1 = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        except OSError:
            continue
        for p in level1:
            if (p / ".obsidian").is_dir():
                found.append(p)
                continue
            try:
                for q in p.iterdir():
                    if q.is_dir() and (q / ".obsidian").is_dir():
                        found.append(q)
            except OSError:
                pass
    uniq = []
    for p in found:
        if p not in uniq:
            uniq.append(p)
    return uniq


def mac_second_brain_homes() -> list[Path]:
    """On a Mac, where a Second Brain is looked for first, in this order. ~/Second Brain comes
    before ~/Documents/Second Brain: macOS may refuse a program that starts by itself access to
    the Documents folder, so the Mac guides put the vault outside it (build plan V3, row 23)."""
    return [common.home() / "Second Brain", common.home() / "Documents" / "Second Brain"]


def guess_paths() -> tuple[str | None, str | None]:
    sb = _pointer(".outliers-sb")
    crm = _pointer(".outliers-crm")
    vaults = find_vaults()
    if common.IS_MAC:
        firsts = [p for p in mac_second_brain_homes() if (p / ".obsidian").is_dir()]
        vaults = firsts + [v for v in vaults if v not in firsts]
    if not crm:
        for v in vaults:
            if any(w in v.name.lower() for w in ("crm", "pipeline", "sales", "clients")):
                crm = str(v)
                break
        if not crm and (common.home() / "CRM").is_dir():
            crm = str(common.home() / "CRM")
    if not sb:
        for v in vaults:
            if str(v) != crm:
                sb = str(v)
                break
        homes = mac_second_brain_homes() if common.IS_MAC else [common.home() / "Documents" / "Second Brain"]
        for p in homes:
            if not sb and p.is_dir():
                sb = str(p)
    return sb, crm


def common_parent(paths: list[str]) -> str | None:
    real = [os.path.abspath(p) for p in paths if p]
    if not real:
        return None
    if len(real) == 1:
        return str(Path(real[0]).parent)
    try:
        return os.path.commonpath(real)
    except ValueError:  # different drives on Windows
        return None


def ask(prompt: str, default: str | None, assume_yes: bool) -> str | None:
    if assume_yes:
        return default
    shown = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{shown}: ").strip().strip('"')
    except EOFError:
        ans = ""
    return ans or default


# ---------------------------------------------------------------- launcher
def pythonw() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def vbs_text() -> str:
    start_py = common.KIT_DIR / "start.py"
    return (
        "' Starts agent-flow with no window each time the computer starts and you sign in. Written by outliers-ws-01-agent-flow install.py.\n"
        "' The 0 below hides the window. Remove this file (or run install.py --uninstall) to stop it.\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        'Set env = sh.Environment("PROCESS")\n'
        'env("AGENT_FLOW_TELEMETRY") = "false"\n'
        'env("DO_NOT_TRACK") = "1"\n'
        f'sh.Run """{pythonw()}"" ""{start_py}"" --quiet", 0, False\n'
    )


def plist_text() -> str:
    from xml.sax.saxutils import escape

    node = shutil.which("node")
    path_dirs = [str(Path(node).parent)] if node else []
    path_dirs += ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
    seen = []
    for d in path_dirs:
        if d not in seen:
            seen.append(d)
    args = [sys.executable, str(common.KIT_DIR / "start.py"), "--foreground", "--quiet"]
    arg_xml = "".join(f"    <string>{escape(a)}</string>\n" for a in args)
    log = escape(str(common.logs_dir() / "launchd.log"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"  <key>Label</key><string>{LAUNCHER_LABEL_MAC}</string>\n"
        "  <key>ProgramArguments</key>\n  <array>\n" + arg_xml + "  </array>\n"
        "  <key>EnvironmentVariables</key>\n  <dict>\n"
        f"    <key>PATH</key><string>{escape(':'.join(seen))}</string>\n"
        "    <key>AGENT_FLOW_TELEMETRY</key><string>false</string>\n"
        "    <key>DO_NOT_TRACK</key><string>1</string>\n"
        "  </dict>\n"
        "  <key>RunAtLoad</key><true/>\n"
        f"  <key>StandardOutPath</key><string>{log}</string>\n"
        f"  <key>StandardErrorPath</key><string>{log}</string>\n"
        "</dict>\n</plist>\n"
    )


def launcher_path(startup_dir: str | None = None) -> Path:
    if common.IS_MAC:
        return common.launch_agents_dir() / f"{LAUNCHER_LABEL_MAC}.plist"
    return (Path(startup_dir) if startup_dir else common.startup_dir()) / LAUNCHER_NAME_WIN


def launcher_text() -> str:
    return plist_text() if common.IS_MAC else vbs_text()


# ---------------------------------------------------------------- prime
def prime_hook_script(package: str, wait_s: float) -> bool:
    """Get agent-flow's own hook.js without letting it touch your settings.

    agent-flow writes hook.js on its first start, but it also rewrites
    settings.json at the same moment (not atomically, and with the Windows
    backslash path that later piles up). So we start it once with a throwaway
    home folder, copy the hook.js it wrote into your real one, and stop it.
    Your settings.json is only ever changed by this installer, after a backup."""
    target = common.hook_script()
    if target.exists():
        return True
    scratch = Path(tempfile.mkdtemp(prefix="agent-flow-prime-"))
    fake_home = scratch / "home"
    work = scratch / "work"
    (fake_home / ".claude").mkdir(parents=True)
    work.mkdir()
    made = fake_home / ".claude" / "agent-flow" / "hook.js"
    print(f"First run: downloading {package} and letting it write its hook script "
          f"(can take up to {int(wait_s)} seconds)...")
    env_extra = {"HOME": str(fake_home), "USERPROFILE": str(fake_home)}
    try:
        proc = starter.launch_raw(str(work), package, 3099, env_extra=env_extra)
        deadline = time.time() + wait_s
        while time.time() < deadline and not made.exists():
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        time.sleep(1.0)
        common.kill_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        if made.exists():
            common.atomic_write_text(target, made.read_text(encoding="utf-8"))
    finally:
        try:
            starter.pid_file().unlink()
        except OSError:
            pass
        time.sleep(0.5)
        shutil.rmtree(scratch, ignore_errors=True)
    return target.exists()


# ---------------------------------------------------------------- settings
def apply_hooks(node_path: str | None) -> dict:
    path = common.settings_path()
    raw = path.read_bytes() if path.exists() else None
    settings = common.read_settings(path)
    before = common.count_agent_flow(settings)
    new, removed = common.install_agent_flow(settings, common.hook_command(node_path))
    # A byte-order mark makes agent-flow throw the whole file away at its next start,
    # so it is removed here too (after the same backup), even when nothing else changes.
    had_bom = bool(raw and raw.startswith(common.BOM))
    changed = new != settings or had_bom
    bak = None
    if changed:
        bak = common.backup(path, "agent-flow")
        common.atomic_write_text(path, common.dump_settings(new), common.detect_eol(path))
    return {"path": path, "before": before, "after": common.count_agent_flow(new),
            "removed": removed, "changed": changed, "backup": bak, "existed": raw is not None,
            "had_bom": had_bom}


def original_bytes_for(path, wanted: dict) -> bytes | None:
    """The first install's backup, if taking our hooks out gives exactly the settings
    it contains. Then an uninstall can give back the very same bytes (indentation, line
    endings, byte-order mark) instead of a reformatted copy."""
    for bak in sorted(path.parent.glob(path.name + ".bak-agent-flow-2*"), reverse=True):
        try:
            if common.read_settings(bak) == wanted:
                return bak.read_bytes()
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return None


def remove_hooks() -> dict:
    path = common.settings_path()
    if not path.exists():
        return {"path": path, "removed": 0, "backup": None, "exact": False}
    settings = common.read_settings(path)
    new, removed = common.remove_agent_flow(settings)
    bak = None
    exact = False
    if removed:
        bak = common.backup(path, "agent-flow-uninstall")
        original = original_bytes_for(path, new)
        if original is not None:
            common.atomic_write_bytes(path, original)
            exact = True
        else:
            common.atomic_write_text(path, common.dump_settings(new), common.detect_eol(path))
    return {"path": path, "removed": removed, "backup": bak, "exact": exact}


# ---------------------------------------------------------------- main
def refuse(msg: str) -> int:
    print("\nSTOPPED - your Claude Code settings.json was not changed.\n" + msg)
    return 2


def node_help() -> str:
    if common.IS_WIN:
        how = ("Install the LTS version from https://nodejs.org (the green button), or run:\n"
               "    winget install OpenJS.NodeJS.LTS\n")
    elif common.IS_MAC:
        how = "Install the LTS version from https://nodejs.org, or run:\n    brew install node\n"
    else:
        how = f"Install Node.js {common.MIN_NODE} or newer from https://nodejs.org or your package manager.\n"
    return (f"agent-flow needs Node.js {common.MIN_NODE} or newer (24 recommended). Node.js 18 stopped "
            "getting security fixes on 2025-04-30 and Node.js 20 on 2026-04-30.\n" + how +
            "Then close this terminal, open a new one, check with:  node --version\n"
            "and run install.py again.")


def python_help() -> str:
    want = ".".join(map(str, MIN_PY))
    have = ".".join(map(str, sys.version_info[:3]))
    return (f"This kit needs Python {want} or newer. This terminal is running Python {have}.\n"
            "Python 3.10 gets security fixes only until 2026-10-31, and older versions get none.\n"
            "Install a current Python from https://python.org (on a Mac, python.org or "
            "`brew install python`), open a NEW terminal, check with:  "
            + ("python3 --version" if common.IS_MAC else "python --version\n(on a Mac:  python3 --version)")
            + " and run install.py again.")


def want_autostart(args, cfg: dict) -> bool:
    """--autostart or --no-autostart wins; with neither, keep the answer saved in config.json.

    Without this, a plain re-run (the guide says to re-run after moving your vaults)
    put back the start-with-the-computer file the member had switched off."""
    if args.autostart:
        return True
    if args.no_autostart:
        return False
    return cfg.get("autostart", True) is not False


def do_install(args) -> int:
    print("agent-flow installer (Outliers Accelerator, guide 1 of 4)\n")
    if tuple(sys.version_info[:2]) < MIN_PY:
        return refuse(python_help())
    print(f"Python {'.'.join(map(str, sys.version_info[:3]))} found.")
    ver = common.node_version() if not os.environ.get("AGENT_FLOW_NPX_CMD") else (99, 0, 0)
    if ver is None:
        return refuse("Node.js was not found.\n" + node_help())
    if ver[0] < common.MIN_NODE:
        return refuse(f"Node.js {'.'.join(map(str, ver))} is too old.\n" + node_help())
    print(f"Node.js {'.'.join(map(str, ver))} found.")

    cfg = common.load_config()
    # Is a server from an earlier install running? If the watch folder or port changes
    # it has to be moved, or it keeps the old folder and blocks the port.
    old_watch, old_port = cfg.get("watch_folder"), int(cfg.get("port") or common.UI_PORT)
    was_running = bool(starter.our_process() or (old_watch and starter.servers_for(old_watch)))
    sb_guess, crm_guess = guess_paths()
    sb = args.second_brain or cfg.get("second_brain") or sb_guess
    crm = args.crm or cfg.get("crm") or crm_guess
    if not args.watch_folder:
        sb = ask("Where is your second brain vault?", sb, args.yes)
        crm = ask("Where is your CRM vault?", crm, args.yes)
    # With no vault found, a Mac watches the home folder: the Mac guides keep the vaults
    # outside Documents, which a program that starts by itself may be refused.
    default_watch = args.watch_folder or cfg.get("watch_folder") or common_parent([sb, crm]) \
        or str(common.home() if common.IS_MAC else common.home() / "Documents")
    watch = ask("Which folder should agent-flow watch? It must CONTAIN every vault you run Claude Code in",
                default_watch, args.yes or bool(args.watch_folder))
    if not watch or not Path(watch).is_dir():
        return refuse(f"The watch folder does not exist: {watch}\nCreate it or pass --watch-folder.")
    watch = str(Path(watch).resolve())
    for label, p in (("second brain", sb), ("CRM", crm)):
        if p and Path(p).is_dir() and not common.norm_path(p).startswith(common.norm_path(watch)):
            print(f"Warning: your {label} vault ({p}) is NOT inside {watch}; its sessions will not show.")

    new_cfg = {
        "second_brain": sb,
        "crm": crm,
        "watch_folder": watch,
        "package": args.package or cfg.get("package") or common.DEFAULT_PACKAGE,
        "port": args.port or cfg.get("port") or common.UI_PORT,
        "autostart": want_autostart(args, cfg),
    }
    moved = was_running and (common.norm_path(old_watch or "") != common.norm_path(watch)
                             or old_port != int(new_cfg["port"]))
    if moved:
        print(f"Stopping the server that runs for {old_watch} on port {old_port}; "
              "it starts again with the new settings at the end.")
        starter.stop(old_watch, quiet=True)
    if new_cfg != cfg:
        common.atomic_write_text(common.config_path(), json.dumps(new_cfg, indent=2) + "\n")
        print(f"Saved settings to {common.config_path()}")

    if not prime_hook_script(new_cfg["package"], args.wait):
        return refuse("agent-flow did not write its hook script "
                      f"({common.hook_script()}). Read {common.logs_dir() / 'agent-flow.log'}.\n"
                      "Your Claude Code settings were not touched.")
    print(f"Hook script present: {common.hook_script()}")

    try:
        res = apply_hooks(args.node_path)
    except (ValueError, UnicodeDecodeError) as exc:
        return refuse(f"Could not read {common.settings_path()}: {common.json_error_in_words(exc)}\n"
                      "Open the file at that line (often a comma after the last item), fix it, then run again.")
    except (AttributeError, TypeError):
        return refuse(f"{common.settings_path()} has a 'hooks' block in a shape this installer does not "
                      "recognise (for example an event that is not a list).\nFix it by hand, then run again.")
    print(f"\nClaude Code settings: {res['path']}")
    print(f"{'Event':<22}{'before':>8}{'after':>8}")
    for ev in common.EVENTS:
        print(f"{ev:<22}{res['before'][ev]:>8}{res['after'][ev]:>8}")
    extra = sum(max(0, n - 1) for n in res["before"].values())
    if res["changed"]:
        if res["backup"]:
            print(f"Backup taken first: {res['backup']}")
        print(f"Removed {extra} extra copies; each event now has exactly 1.")
        print("The hook line now uses forward slashes, so agent-flow recognises it and will not add "
              "another copy each time it starts.")
    else:
        print("Already correct: 1 copy per event. Nothing changed.")
    if res.get("had_bom"):
        print("Removed an invisible byte-order mark from the start of settings.json "
              "(agent-flow cannot read a file that has one).")

    if not new_cfg["autostart"]:
        lp = launcher_path(args.startup_dir)
        how = "you chose --no-autostart" if args.no_autostart else "your earlier choice, saved in config.json"
        if lp.exists():
            if common.IS_MAC:
                subprocess.run(["launchctl", "unload", str(lp)], capture_output=True)
            lp.unlink()
            print(f"\nRemoved the file that starts agent-flow when the computer starts ({how}): {lp}")
        else:
            print(f"\nagent-flow will not start by itself when the computer starts ({how}).")
        print(f"Running {common.PY} install.py again keeps this choice. "
              f"To switch it back on: {common.PY} install.py --autostart")
        print(f"Start it by hand with: {common.PY} start.py")
    else:
        lp = launcher_path(args.startup_dir)
        text = launcher_text()
        if lp.exists() and lp.read_text(encoding="utf-8") == text:
            print(f"\nFile that starts agent-flow when the computer starts, already in place: {lp}")
        else:
            common.atomic_write_text(lp, text)
            print(f"\nFile that starts agent-flow when the computer starts, written: {lp}")
            if common.IS_MAC:
                print(f"It runs the next time you sign in to your Mac. To start it now: launchctl load \"{lp}\"")
    print("Usage tracking: off. start.py switches it off every time it starts agent-flow "
          "(AGENT_FLOW_TELEMETRY=false and DO_NOT_TRACK=1), by hand or when the computer starts.")

    running_now = False
    if args.start_now or moved:
        print()
        running_now = starter.start(watch, new_cfg["package"], int(new_cfg["port"]), args.wait, False, False) == 0
    if running_now:
        print(f"\nDone. Open http://127.0.0.1:{new_cfg['port']} and start a NEW "
              "Claude Code session inside the watch folder.")
    else:
        print(f"\nDone. Start it now with:  {common.PY} start.py")
        print(f"Then open http://127.0.0.1:{new_cfg['port']} and start a NEW Claude Code session "
              "inside the watch folder.")
    print(f"Check your hooks any time with:  {common.PY} check_hooks.py")
    return 0


def do_uninstall(args) -> int:
    print("agent-flow uninstall\n")
    cfg = common.load_config()
    watch = args.watch_folder or cfg.get("watch_folder")
    if watch:
        starter.stop(watch, quiet=False)
    try:
        res = remove_hooks()
    except (ValueError, UnicodeDecodeError) as exc:
        return refuse(f"Could not read {common.settings_path()}: {common.json_error_in_words(exc)}\n"
                      "Fix the file, then run again.")
    except (AttributeError, TypeError):
        return refuse(f"{common.settings_path()} has a 'hooks' block in a shape this installer does not "
                      "recognise. Fix it by hand, then run again.")
    print(f"Removed {res['removed']} agent-flow hook entries from {res['path']}")
    if res.get("exact"):
        print("settings.json is now byte-for-byte what it was before the first install.")
    if res["backup"]:
        print(f"Backup taken first: {res['backup']}")
    lp = launcher_path(args.startup_dir)
    if lp.exists():
        if common.IS_MAC:
            subprocess.run(["launchctl", "unload", str(lp)], capture_output=True)
        lp.unlink()
        print(f"Removed the file that starts agent-flow when the computer starts: {lp}")
    else:
        print("No file that starts agent-flow when the computer starts was found.")
    print(f"Left in place (agent-flow's own files, safe to delete by hand): {common.discovery_dir()}"
          + (f" and {common.home() / '.agent-flow'}" if (common.home() / '.agent-flow').exists() else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Install agent-flow safely for your Claude Code workspace.")
    ap.add_argument("--yes", action="store_true", help="accept the defaults without asking")
    ap.add_argument("--second-brain", help="path to your second brain vault")
    ap.add_argument("--crm", help="path to your CRM vault")
    ap.add_argument("--watch-folder", help="the folder that contains your vaults")
    ap.add_argument("--package", help=f"npm package to run (default {common.DEFAULT_PACKAGE})")
    ap.add_argument("--port", type=int, help=f"web page port (default {common.UI_PORT})")
    ap.add_argument("--node-path", help="node program to put in the hook (default: the one on PATH)")
    # A Mac has no Startup folder, so the option is hidden from --help there.
    ap.add_argument("--startup-dir", help="Windows Startup folder override (for testing)" if common.IS_WIN
                    else argparse.SUPPRESS)
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--no-autostart", action="store_true", help="do not add the file that starts agent-flow by itself each time you switch on your computer and sign in (removes it if you have it); saved, so later runs keep this choice")
    when.add_argument("--autostart", action="store_true", help="put that file back after an earlier --no-autostart")
    ap.add_argument("--start-now", action="store_true", help="start the server when done")
    ap.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the first download")
    ap.add_argument("--uninstall", action="store_true", help="remove the hook and the file that starts agent-flow when the computer starts")
    args = ap.parse_args(argv)
    return do_uninstall(args) if args.uninstall else do_install(args)


if __name__ == "__main__":
    sys.exit(main())
