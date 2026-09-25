"""A stand-in for `npx agent-flow-app`, used only by the tests.

It copies the upstream 0.9.1 start-up behaviour that matters to us (read from
dist/app.js, scripts/setup.js, on 2026-09-22):
- "already set up" means: hook.js exists AND settings.json parses as strict JSON
  AND some event has an entry whose command contains 'agent-flow/hook.js'.
  A byte-order mark or a trailing comma makes the parse fail.
- if not set up: writes hook.js if missing, then reads settings.json; if that read
  fails it STARTS FRESH ({}), adds 1 hook per event with a native-separator path,
  and writes the file straight over the old one (no backup, not atomic).
- serves a web page on --port: GET / (html), GET /events (a stream), anything else 404.
- writes a discovery file <code>-<pid>.json and runs until it is killed.

FAKE_AF_CLOBBER=1 makes it rewrite settings.json with only its hooks even when it
is already set up, so the tests can prove start.py puts the file back.
"""
import hashlib
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EVENTS = ["SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart",
          "SubagentStop", "Notification", "Stop", "SessionEnd"]

home = Path.home()
d = home / ".claude" / "agent-flow"
d.mkdir(parents=True, exist_ok=True)
hook = d / "hook.js"
settings_p = home / ".claude" / "settings.json"


def strict_load(text):
    return json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


def already():
    if not hook.exists() or not settings_p.exists():
        return False
    try:
        s = strict_load(settings_p.read_text(encoding="utf-8"))  # a BOM stays in, so it fails like Node
    except Exception:
        return False
    return any("agent-flow/hook.js" in h.get("command", "")
               for groups in (s.get("hooks") or {}).values() for g in groups for h in g.get("hooks", []))


def configure(start_fresh=False):
    if not hook.exists():
        hook.write_text("// fake hook for tests\n", encoding="utf-8")
    s = {}
    if not start_fresh and settings_p.exists():
        try:
            s = strict_load(settings_p.read_text(encoding="utf-8"))
        except Exception:
            s = {}  # "Could not read existing settings, starting fresh"
    hooks = s.setdefault("hooks", {})
    cmd = f'"node" "{hook}"'  # native separators, like upstream
    for ev in EVENTS:
        lst = [g for g in hooks.get(ev, []) if not any("agent-flow/hook.js" in h.get("command", "")
                                                       for h in g.get("hooks", []))]
        lst.append({"hooks": [{"type": "command", "command": cmd, "timeout": 2}]})
        hooks[ev] = lst
    settings_p.parent.mkdir(parents=True, exist_ok=True)
    settings_p.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")


if os.environ.get("FAKE_AF_CLOBBER") == "1":
    configure(start_fresh=True)
elif not already():
    configure()

port = 3001
if "--port" in sys.argv:
    port = int(sys.argv[sys.argv.index("--port") + 1])


class UI(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"type":"agent-event","tool":"Read","args":"People/Sam.md"}\n\n')
            self.wfile.flush()
            return
        if self.path in ("/", "/index.html"):
            body = b"<html><body>WAITING FOR AGENT SESSION</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class QuickServer(ThreadingHTTPServer):
    """No name look-up when it starts. The real agent-flow (Node.js) does none; Python's
    server looks up the name of 127.0.0.1, which took 35 seconds on GitHub's test Macs
    (2026-09-24), so this stand-in answered later than the checks wait."""

    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]


ui = QuickServer(("127.0.0.1", port), UI)
threading.Thread(target=ui.serve_forever, daemon=True).start()

hk = socket.socket()
hk.bind(("127.0.0.1", 0))
hk.listen(5)


def accept_forever(sock):
    while True:
        try:
            c, _ = sock.accept()
            c.close()
        except OSError:
            return


threading.Thread(target=accept_forever, args=(hk,), daemon=True).start()

ws = os.path.realpath(os.getcwd())
code = hashlib.sha256(ws.encode()).hexdigest()[:16]
(d / f"{code}-{os.getpid()}.json").write_text(
    json.dumps({"port": hk.getsockname()[1], "pid": os.getpid(), "workspace": ws}), encoding="utf-8")
while True:
    time.sleep(1)
