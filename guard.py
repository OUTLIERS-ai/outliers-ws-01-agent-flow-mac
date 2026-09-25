"""Runs agent-flow behind 2 safety checks. start.py starts this; you never run it by hand.

1. Your settings.json. agent-flow 0.9.1 runs its own setup each time it starts and,
   if it cannot read settings.json, writes a new file with only its hooks in it. start.py
   refuses to start it when the file is not safe; this file also keeps a copy of the
   exact bytes before agent-flow starts, watches the file for the first 60 seconds,
   and if agent-flow rewrote it, keeps the rewritten version as
   settings.json.bak-agent-flow-undo-<date> and puts yours back.

2. The page address. agent-flow's page streams your live Claude Code conversation and
   answers any request, whatever web address the browser thinks it is talking to. A
   website can use that to read the stream (the trick is called DNS rebinding: a web
   address that first points at the website, then at your own computer). So agent-flow
   runs on a private port, and this file serves the page on your port (3001), passing
   on only requests addressed to 127.0.0.1, localhost or [::1]. Anything else gets 403.
   That alone left agent-flow's own 2 ports open (tested 2026-09-24): the private port
   answered a request naming another website, and the port hook.js sends events to took
   events from any website. So agent-flow is also started with only_this_computer.js
   loaded first (Node.js's --require option, see child_env), which applies the same
   check inside agent-flow, on both of its ports. agent-flow's own files are unchanged.

    python guard.py --workspace <folder> --package agent-flow-app@0.9.1 --port 3001
"""
from __future__ import annotations

import argparse
import datetime as _dt
import http.client
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import common

WATCH_SETTINGS_S = 60


def log(msg: str) -> None:
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(common.logs_dir() / "start.log", "a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] [guard] {msg}\n")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def child_env() -> dict:
    """The settings agent-flow starts with: usage tracking off, and only_this_computer.js
    loaded first, so agent-flow's own 2 ports refuse requests from other websites.
    Any NODE_OPTIONS you set yourself are kept."""
    js = str(Path(__file__).resolve().parent / "only_this_computer.js").replace("\\", "/")
    js = js.replace('"', '\\"')  # Node reads NODE_OPTIONS like a command line: quote the path
    mine = os.environ.get("NODE_OPTIONS", "").strip()
    return dict(os.environ, **common.QUIET_ENV, NODE_OPTIONS=(mine + " " if mine else "") + f'--require "{js}"')


# Seconds a child has to die before we treat it as the port race rather than a real fault.
RACE_WINDOW_S = 12.0

# Every agent-flow this guard started, so a stop signal at any moment still ends it (see main).
_STARTED: list = []


def end_on_stop_signals() -> None:
    """Mac and Linux: turn SIGTERM and SIGHUP into a normal exit, so the clean-up that ends
    agent-flow runs. Python's default for both is to end at once with no clean-up, and on a
    Mac agent-flow runs in a process group of its own (start_child), so it is not ended with
    the guard's group. SIGTERM is what the Mac sends when the login job is switched off
    (launchctl unload); SIGHUP is what a closed Terminal window sends. Windows is unchanged."""
    if common.IS_WIN:
        return
    import signal

    def stop(signum, frame):
        for s in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, signal.SIG_IGN)  # a 2nd signal must not cut the clean-up short
        log(f"stopped by signal {signum}; ending agent-flow")
        raise SystemExit(128 + signum)

    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, stop)


def start_child(workspace: str, package: str, out, attempts: int = 3, on_tick=None):
    """Start agent-flow on a private port and hand back (child, port).

    Windows can give that port number to another program in the moment between us
    letting go of it and agent-flow claiming it. It happened once in 34 starts on
    2026-09-22. agent-flow prints the port it was given, not the port it got, so we
    cannot read the real one back from it: instead, when the child dies within a few
    seconds and the port is now answering to somebody else, we pick another number
    and try again. (child, port) is (None, last port) when every try failed."""
    env = child_env()
    kwargs = dict(cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=out,
                  stderr=subprocess.STDOUT)
    if common.IS_WIN:
        kwargs["creationflags"] = common.NO_WINDOW
    else:
        # A process group of its own, so kill_tree ends agent-flow and its children, never us.
        kwargs["start_new_session"] = True
    port = 0
    for attempt in range(1, attempts + 1):
        port = free_port()
        child = subprocess.Popen(common.npx_command(package) + ["--no-open", "--port", str(port)],
                                 **kwargs)
        _STARTED.append(child)
        started = time.time()
        while time.time() - started < RACE_WINDOW_S:
            if on_tick:
                on_tick()
            if child.poll() is None and common.http_answers(port):
                return child, port
            if child.poll() is not None:
                break
            time.sleep(0.2)
        if child.poll() is None:
            return child, port  # slow first download: let run() keep waiting on it
        tail = common.log_tail(common.logs_dir() / "agent-flow.log", 30).lower()
        race = common.port_answers(port) or any(w in tail for w in common._ADDRESS_IN_USE)
        if not race or attempt == attempts:
            return None, port
        log(f"private port {port} was taken by another program; trying another (try {attempt + 1} of {attempts})")
    return None, port


class PageServer(ThreadingHTTPServer):
    """The standard server with no name look-up when it starts."""

    def server_bind(self):
        # http.server's own server_bind also asks for this address's name
        # (socket.getfqdn), only to fill in server_name, which nothing here uses.
        # On GitHub's test Macs that look-up took 35 seconds on every start
        # (measured 2026-09-24), so the page answered 35 seconds late and the
        # checks that wait 15 or 20 seconds for it failed. The name is now the
        # address as given, with no look-up.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port


def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def make_handler(public_port: int, private_port: int):
    ok_hosts = allowed_hosts(public_port)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # each answer ends by closing, which suits the live stream

        def log_message(self, *a):
            pass

        def refuse(self, code: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in ok_hosts:
                return self.refuse(403, f"agent-flow only answers at http://127.0.0.1:{public_port}\n")
            try:
                up = http.client.HTTPConnection("127.0.0.1", private_port, timeout=30)
                up.connect()
                sock = up.sock  # kept: http.client lets go of it once a reply is read to the end
                up.request("GET", self.path, headers={"Host": f"127.0.0.1:{private_port}",
                                                      "Accept": self.headers.get("Accept", "*/*")})
                resp = up.getresponse()
            except OSError:
                return self.refuse(502, "agent-flow is still starting. Refresh in a few seconds.\n")
            ctype = resp.getheader("Content-Type", "")
            try:
                self.send_response(resp.status)
                for k in ("Content-Type", "Cache-Control"):
                    v = resp.getheader(k)
                    if v:
                        self.send_header(k, v)
                if "text/event-stream" in ctype:
                    self.end_headers()
                    self.wfile.flush()
                    sock.settimeout(None)  # the stream can be quiet for minutes
                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    body = resp.read()
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            except OSError:
                pass  # the browser tab closed
            finally:
                up.close()

        def do_POST(self):
            self.refuse(405, "Not allowed\n")

        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


def run(workspace: str, package: str, port: int, wait_s: float) -> int:
    settings = common.agent_flow_settings_path()
    before = settings.read_bytes() if settings.exists() else None
    common.logs_dir().mkdir(parents=True, exist_ok=True)
    out = open(common.logs_dir() / "agent-flow.log", "ab")

    def check_settings():
        nonlocal before
        if before is None or not settings.exists():
            return
        now = settings.read_bytes()
        if now == before:
            return
        if common.settings_changed_by_agent_flow(before, now):
            kept = common.put_settings_back(before, settings)
            log(f"agent-flow rewrote {settings}; put back your version. Its version is kept as {kept}")
        else:
            before = now  # a normal edit by you or Claude Code: accept it

    log(f"starting agent-flow for {workspace} (page on {port})")
    child, private = start_child(workspace, package, out, on_tick=check_settings)
    started = time.time()
    server = None
    try:
        if child is None:
            check_settings()
            why = common.why_it_stopped(common.log_tail(common.logs_dir() / "agent-flow.log", 30))
            log(f"agent-flow stopped straight away. {why}")
            return 4
        log(f"agent-flow answering on private port {private}")
        while time.time() - started < wait_s and child.poll() is None:
            check_settings()
            if common.port_answers(private):
                break
            time.sleep(0.3)
        if child.poll() is not None:
            check_settings()
            why = common.why_it_stopped(common.log_tail(common.logs_dir() / "agent-flow.log", 30))
            log(f"agent-flow stopped straight away (exit code {child.returncode}). {why}")
            return 4
        try:
            server = PageServer(("127.0.0.1", port), make_handler(port, private))
        except OSError as exc:
            log(f"could not serve the page on port {port}: {exc}")
            return 3
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log(f"page ready at http://127.0.0.1:{port}")
        while child.poll() is None:
            if time.time() - started < WATCH_SETTINGS_S:
                check_settings()
            time.sleep(0.5)
        log(f"agent-flow ended (exit code {child.returncode})")
        return child.returncode or 0
    finally:
        if server is not None:
            server.shutdown()
        if child is not None and child.poll() is None:
            common.kill_tree(child.pid)
        out.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run agent-flow behind the settings and address checks.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--package", default=common.DEFAULT_PACKAGE)
    ap.add_argument("--port", type=int, default=common.UI_PORT)
    ap.add_argument("--wait", type=float, default=180.0)
    ap.add_argument("--kit-dir", help="where config.json and logs/ live (default: this folder)")
    a = ap.parse_args(argv)
    if a.kit_dir:
        common.KIT_DIR = Path(a.kit_dir)
    if common.IS_WIN:
        return run(a.workspace, a.package, a.port, a.wait)
    end_on_stop_signals()
    try:
        return run(a.workspace, a.package, a.port, a.wait)
    finally:
        for child in _STARTED:  # a stop signal while agent-flow was still starting
            if child.poll() is None:
                common.kill_tree(child.pid)


if __name__ == "__main__":
    sys.exit(main())
