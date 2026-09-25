"""Start or stop the agent-flow server with no window and usage tracking off.

It runs from the folder saved in config.json (the folder that contains your
vaults), because the server only receives events from Claude Code sessions
running inside the folder it was started from.

Before every start it checks your Claude Code settings.json. agent-flow runs its
own setup each time it starts, and if it cannot read that file (a trailing comma,
an invisible byte-order mark) it replaces the whole file with only its hooks. So
if the file is not safe, start.py does NOT start agent-flow and says why, on screen
and in logs/start.log. guard.py then watches the file for the first minute
and puts it back if agent-flow changed it anyway.

    python start.py              # start it in the background, then print the address
    python start.py --stop       # stop the server this kit started
    python start.py --status     # say whether it is running (and why not, if it refused)
    python start.py --foreground # run and wait (used by the Mac start-up job)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cleanup
import common

REFUSED = "[start.py] REFUSED:"


def servers_for(workspace: str) -> list[dict]:
    want = common.norm_path(workspace)
    return [i for i in common.discovery_files()
            if i["live"] and i["workspace"] and common.norm_path(i["workspace"]) == want]


def pid_file() -> Path:
    return common.logs_dir() / "agent-flow.pid"


def log_line(msg: str) -> None:
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(common.logs_dir() / "start.log", "a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {msg}\n")


def last_refusal() -> str | None:
    p = common.logs_dir() / "start.log"  # the kit's own lines; agent-flow's own output is agent-flow.log
    if not p.exists():
        return None
    last = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        if REFUSED in line:
            last = line
        elif "[guard] page ready" in line:
            last = None
    return last


def _popen(cmd, workspace, env_extra, foreground):
    env = dict(os.environ, **common.QUIET_ENV, **(env_extra or {}))
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    log = open(common.logs_dir() / "agent-flow.log", "ab")
    kwargs = dict(cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    if common.IS_WIN:
        kwargs["creationflags"] = common.NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = not foreground
    return subprocess.Popen(cmd, **kwargs)


def launch_raw(workspace: str, package: str, port: int, env_extra: dict | None = None) -> subprocess.Popen:
    """agent-flow itself, with no guard. Only install.py uses this, inside a throwaway home."""
    return _popen(common.npx_command(package) + ["--no-open", "--port", str(port)], workspace, env_extra, False)


def launch(workspace: str, package: str, port: int, foreground: bool = False,
           wait_s: float = 180.0) -> subprocess.Popen:
    """agent-flow behind guard.py. Records the guard's process number AND start time,
    so --stop can never mistake another program for it after a restart."""
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "guard.py"), "--kit-dir", str(common.KIT_DIR),
           "--workspace", workspace,
           "--package", package, "--port", str(port), "--wait", str(wait_s)]
    proc = _popen(cmd, workspace, None, foreground)
    record = {"pid": proc.pid, "started": common.process_start_time(proc.pid)}
    common.atomic_write_text(pid_file(), json.dumps(record))
    return proc


def pass_stop_signals_to(guard: subprocess.Popen) -> None:
    """Mac and Linux, --foreground only (the login job): when this start.py is stopped
    (SIGTERM when the login job is switched off, SIGHUP when its Terminal window is closed),
    stop the guard the normal way and wait for it, so the guard's clean-up ends agent-flow.
    Without this, start.py ended at once and the guard was left to whatever the Mac did
    to the rest of the job. Windows is unchanged."""
    if common.IS_WIN:
        return
    import signal

    def stop(signum, frame):
        for s in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, signal.SIG_IGN)
        try:
            guard.terminate()
            guard.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise SystemExit(128 + signum)

    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, stop)


def start(workspace: str, package: str, port: int, wait_s: float, foreground: bool, quiet: bool) -> int:
    def say(msg):
        if not quiet:
            print(msg)

    if not Path(workspace).is_dir():
        say(f"The watch folder does not exist: {workspace}")
        log_line(f"{REFUSED} the watch folder does not exist: {workspace}")
        return 2
    problem = common.settings_problem()
    if problem:
        log_line(f"{REFUSED} {problem}")
        say("agent-flow was NOT started, to protect your Claude Code settings.")
        say(problem)
        return 5
    cleanup.run(quiet=True)
    if servers_for(workspace) and common.port_answers(port):
        say(f"Already running for {workspace}. Open http://127.0.0.1:{port}")
        return 0
    leftover = servers_for(workspace)
    if leftover:
        # Registered for this folder, but nothing is serving the page: agent-flow
        # outlived the guard that started it (Task Manager, a log-off, a crash).
        # Starting on top of it would leave 2 servers and 2 node programs running.
        n = cleanup.end_servers(leftover)
        say(f"Ended {n} agent-flow server(s) left behind when the last one was closed by force.")
        log_line(f"[start.py] ended {n} agent-flow server(s) left behind for {workspace}")
    if common.port_answers(port):
        say(f"Port {port} is already in use by another program. "
            f"Pick another with:  {common.PY} install.py --port {port + 1}   then:  {common.PY} start.py")
        log_line(f"{REFUSED} port {port} is already in use by another program")
        return 3
    proc = launch(workspace, package, port, foreground, wait_s)
    if foreground:
        pass_stop_signals_to(proc)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if servers_for(workspace) and common.port_answers(port):
            say(f"agent-flow is running. Open http://127.0.0.1:{port}")
            say("Start a NEW Claude Code session inside the watch folder to see it draw.")
            if foreground:
                return proc.wait()
            return 0
        if proc.poll() is not None:
            # Say WHY, read off agent-flow's own last lines, and write it down as a
            # refusal so `python start.py --status` can repeat it later.
            why = common.why_it_stopped(common.log_tail(common.logs_dir() / "agent-flow.log", 30))
            log_line(f"{REFUSED} {why}")
            say("agent-flow did not start.")
            say(why)
            say(f"The full detail is in {common.logs_dir() / 'agent-flow.log'}")
            return 4
        time.sleep(0.5)
    say(f"Still starting after {int(wait_s)} seconds (the first run downloads the package). "
        f"Check http://127.0.0.1:{port} in a minute, or read {common.logs_dir() / 'agent-flow.log'}")
    if foreground:
        return proc.wait()
    return 0


def our_process() -> int | None:
    """The guard process start.py launched, if it is still the same process. A process
    number alone is not enough: after a restart Windows can give it to any program."""
    pf = pid_file()
    if not pf.exists():
        return None
    try:
        rec = json.loads(pf.read_text(encoding="utf-8"))
        pid, started = int(rec["pid"]), rec["started"]
    except (ValueError, KeyError, TypeError):
        return None  # old plain-number file: cannot prove it is ours, so leave it alone
    if started is None or not common.pid_alive(pid):
        return None
    return pid if common.process_start_time(pid) == started else None


def stop(workspace: str | None, quiet: bool = False) -> int:
    killed = 0
    targets = servers_for(workspace) if workspace else []
    ours = our_process()
    if ours is not None:
        common.kill_tree(ours)
        killed += 1
    for info in targets:
        if common.pid_alive(info["pid"]):
            common.kill_tree(info["pid"])
            killed += 1
    try:
        pid_file().unlink()
    except OSError:
        pass
    time.sleep(0.5)
    for info in targets:
        try:
            info["file"].unlink()
        except OSError:
            pass
    cleanup.run(quiet=True)
    if not quiet:
        print("Stopped agent-flow." if killed else "agent-flow was not running. Nothing to stop.")
    return 0


def main(argv=None) -> int:
    cfg = common.load_config()
    ap = argparse.ArgumentParser(description="Start or stop agent-flow quietly.")
    ap.add_argument("--watch-folder", default=cfg.get("watch_folder"))
    ap.add_argument("--package", default=cfg.get("package", common.DEFAULT_PACKAGE))
    ap.add_argument("--port", type=int, default=int(cfg.get("port", common.UI_PORT)))
    ap.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the first start")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--foreground", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.watch_folder:
        print("No watch folder set. Run install.py first, or pass --watch-folder.")
        return 2
    if args.stop:
        return stop(args.watch_folder, args.quiet)
    if args.status:
        live = servers_for(args.watch_folder) and common.port_answers(args.port)
        print(f"{'Running' if live else 'Not running'} for {args.watch_folder}"
              + (f" - http://127.0.0.1:{args.port}" if live else ""))
        if not live:
            why = last_refusal()
            if why:
                print("Why the last start did not work: " + why.split(REFUSED, 1)[1].strip())
        return 0 if live else 1
    return start(args.watch_folder, args.package, args.port, args.wait, args.foreground, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
