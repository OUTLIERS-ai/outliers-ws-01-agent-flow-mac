"""Shared helpers for the agent-flow workspace kit.

Every path is worked out when a function is called, never at import, so the
tests can point HOME / USERPROFILE / APPDATA at a temporary folder.
"""
from __future__ import annotations

import datetime as _dt
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

# Hide every console window a child program would otherwise open on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
# The command a member types to run Python: a Mac has python3 and no plain python.
PY = "python3" if IS_MAC else "python"

# The package version this kit was checked against on 2026-09-22.
DEFAULT_PACKAGE = "agent-flow-app@0.9.1"
UI_PORT = 3001

# Oldest versions we will run on. Node 18 stopped getting security fixes on
# 2025-04-30 and Node 20 on 2026-04-30, so the floor is 22 (checked 2026-09-22).
MIN_NODE = 22

# The 9 Claude Code events the upstream installer hooks.
EVENTS = [
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "SubagentStop",
    "Notification",
    "Stop",
    "SessionEnd",
]
HOOK_TIMEOUT_S = 2
MARKER = "agent-flow/hook.js"

KIT_DIR = Path(__file__).resolve().parent
QUIET_ENV = {"AGENT_FLOW_TELEMETRY": "false", "DO_NOT_TRACK": "1"}


# ---------------------------------------------------------------- paths
def home() -> Path:
    return Path.home()


def claude_dir() -> Path:
    """Where Claude Code keeps settings.json (honours CLAUDE_CONFIG_DIR)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else home() / ".claude"


def settings_path() -> Path:
    return claude_dir() / "settings.json"


def discovery_dir() -> Path:
    """agent-flow always uses <home>/.claude/agent-flow, whatever CLAUDE_CONFIG_DIR says."""
    return home() / ".claude" / "agent-flow"


def hook_script() -> Path:
    return discovery_dir() / "hook.js"


def config_path() -> Path:
    return KIT_DIR / "config.json"


def logs_dir() -> Path:
    return KIT_DIR / "logs"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def launch_agents_dir() -> Path:
    return home() / "Library" / "LaunchAgents"


# ---------------------------------------------------------------- files
def detect_eol(path: Path) -> str:
    """Keep the file's own line endings (an uninstall restores the exact original bytes
    from the install backup when it can; see install.remove_hooks)."""
    try:
        return "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    except OSError:
        return "\n"


def atomic_write_text(path: Path, text: str, eol: str = "\n") -> None:
    """Write to a temp file beside the target, then swap it in. Never truncates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=eol) as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Same as atomic_write_text, for exact bytes (used to put a file back as it was)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup(path: Path, tag: str) -> Path | None:
    if not path.exists():
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{tag}-{stamp}")
    n = 1
    while dest.exists():
        n += 1
        dest = path.with_name(f"{path.name}.bak-{tag}-{stamp}-{n}")
    shutil.copy2(path, dest)
    return dest


def _no_constants(name):
    raise ValueError(f"{name} is not allowed in JSON")


def strict_loads(text: str):
    """Parse JSON the way Node's JSON.parse does: no NaN/Infinity, no comments, no trailing commas."""
    return json.loads(text, parse_constant=_no_constants)


def read_settings(path: Path | None = None) -> dict:
    path = path or settings_path()
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}
    data = strict_loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not start with {{ and end with }}, so it is not a settings file")
    return data


# Python's JSON messages, said in plain words for members who are not programmers.
_JSON_WORDS = [
    ("Illegal trailing comma before end of object", "there is a comma after the last item in a block"),
    ("Illegal trailing comma before end of array", "there is a comma after the last item in a list"),
    ("Expecting ',' delimiter", "a comma is missing between 2 items"),
    ("Expecting ':' delimiter", "a colon is missing after a name"),
    ("Expecting property name enclosed in double quotes",
     "a name is missing its double quotes, or there is a comma after the last item in a block"),
    ("Expecting value", "a value is missing or mistyped"),
    ("Unterminated string", "some text is missing its closing double quote"),
    ("Extra data", "there is extra text after the file's last closing }"),
    ("Invalid control character", "there is a tab or a line break inside some text in double quotes"),
    ("Invalid \\escape", "a backslash inside double quotes must be written twice, as \\\\"),
]


def json_error_in_words(exc: Exception) -> str:
    """'line 10, column 26: a comma is missing between 2 items' instead of a Python error dump."""
    if isinstance(exc, json.JSONDecodeError):
        words = exc.msg
        for start, plain in _JSON_WORDS:
            if exc.msg.startswith(start):
                words = plain
                break
        return f"line {exc.lineno}, column {exc.colno}: {words}"
    return str(exc)


# ------------------------------------------- why agent-flow stopped straight away
# Read off agent-flow's own last lines so a member is told the reason instead of a
# file path. Every shape below was seen on 2026-09-22 or is npm's documented wording.
_ADDRESS_IN_USE = ("eaddrinuse", "address already in use", "winerror 10048",
                   "only one usage of each socket address")
_NO_NODE = ("is not recognized as an internal or external command", "command not found",
            "no such file or directory", "cannot find the path specified")
_OLD_NODE = ("ebadengine", "unsupported engine", 'required: {"node"', "requires node")
_NOT_ON_NPM = ("404 not found", "npm error e404", "code e404")
_NO_INTERNET = ("enotfound", "eai_again", "econnrefused", "etimedout", "network request to",
                "getaddrinfo", "unable to get local issuer certificate", "self-signed certificate")


def log_tail(path, lines: int = 30) -> str:
    """The last few lines of a log file, or an empty string if it is not there."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def why_it_stopped(tail: str) -> str:
    """Plain words for a start that died at once, and what to do about it."""
    low = (tail or "").lower()
    if any(w in low for w in _ADDRESS_IN_USE):
        return ("The port agent-flow needed was taken by another program a moment before it "
                f"started. Run {PY} start.py again.")
    if any(w in low for w in _OLD_NODE):
        return (f"The Node.js on this computer is older than {MIN_NODE}. Install Node.js {MIN_NODE} "
                f"or newer from nodejs.org, open a NEW terminal, then run {PY} start.py again.")
    if any(w in low for w in _NO_NODE):
        return ("Node.js was not found on this computer. Install it from nodejs.org, open a NEW "
                f"terminal, then run {PY} start.py again.")
    if any(w in low for w in _NOT_ON_NPM):
        return (f"npm has no package with the name in config.json. Put it back to {DEFAULT_PACKAGE}, "
                f"then run {PY} start.py again.")
    if any(w in low for w in _NO_INTERNET):
        return ("This computer could not reach npm, the store agent-flow is downloaded from, and "
                f"agent-flow is not saved on it yet. Connect to the internet, then run {PY} "
                "start.py again.")
    last = [ln.strip() for ln in (tail or "").splitlines() if ln.strip()]
    if last:
        return "agent-flow stopped straight away. Its own last line says: " + last[-1][:150]
    return "agent-flow stopped straight away and wrote nothing to its log."


# ---------------------------------------------------------------- settings guard
BOM = b"\xef\xbb\xbf"


def agent_flow_settings_path() -> Path:
    """The file agent-flow itself reads and rewrites. It ignores CLAUDE_CONFIG_DIR."""
    return home() / ".claude" / "settings.json"


def settings_fault(path: Path | None = None, point_at_check: bool = True) -> str | None:
    """Why this settings.json cannot be read as it stands, in plain words, or None.

    start.py (before a start) and check_hooks.py (on request) both call this, so the
    2 can never disagree about the same file the way they did before 2026-09-22:
    an empty file was called OK by one and refused by the other."""
    path = path or settings_path()
    if not path.exists():
        return None  # the caller decides what a missing file means
    raw = path.read_bytes()
    if not raw.strip():
        return (f"{path} is empty. agent-flow cannot read an empty file, so at its next start it "
                f"would replace it with a file that has only its own 9 hooks in it. Run: {PY} install.py")
    if raw.startswith(BOM):
        return (f"{path} starts with an invisible byte-order mark (some editors and PowerShell add it). "
                f"agent-flow cannot read a file like that and would replace it. Run: {PY} install.py "
                "(it removes the mark after a backup).")
    try:
        data = strict_loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return f"{path} is not plain UTF-8 text. Fix it, then run: {PY} install.py"
    except ValueError as exc:
        return (f"{path} cannot be read ({json_error_in_words(exc)}). agent-flow would replace the "
                "whole file. Open it at that line, fix it and save"
                + (f", then run: {PY} check_hooks.py" if point_at_check else "."))
    if not isinstance(data, dict):
        return (f"{path} does not start with {{ and end with }}, so it is not a settings file. "
                f"Fix it, then run: {PY} install.py")
    return None


def settings_problem() -> str | None:
    """Why it is NOT safe to start agent-flow right now, or None if it is safe.

    agent-flow 0.9.1 runs its own setup every time it starts. If it cannot read
    settings.json (a byte-order mark, a trailing comma, a comment) it throws the
    whole file away and writes a new file with only its 9 hooks in it. It does the
    same, keeping your other settings, if it cannot find its own hook in the file.
    So we only start it when it will find everything in order and leave the file alone."""
    if not hook_script().exists():
        return f"agent-flow's hook script is missing ({hook_script()}). Run: {PY} install.py"
    path = agent_flow_settings_path()
    is_claudes = norm_path(path) == norm_path(settings_path())
    if not path.exists():
        return ("Claude Code's settings.json does not exist yet, so agent-flow would write its own. "
                f"Run: {PY} install.py") if is_claudes else None
    fault = settings_fault(path)
    if fault:
        return fault
    data = strict_loads(path.read_bytes().decode("utf-8"))
    try:
        counts = count_agent_flow(data)
    except (AttributeError, TypeError):
        return f"{path} has a 'hooks' block in a shape agent-flow does not expect. Run: {PY} install.py"
    if not any(counts.values()) and is_claudes:
        return f"The agent-flow hooks are not in settings.json. Run: {PY} install.py"
    return None


def settings_changed_by_agent_flow(before: bytes, after: bytes) -> bool:
    """True when a change looks like agent-flow's rewrite, not a normal edit by the member
    or by Claude Code: the file no longer reads, a top-level setting vanished, or the
    agent-flow hook count moved away from what it was."""
    if before == after:
        return False
    try:
        old = strict_loads(before.decode("utf-8-sig"))
        new = strict_loads(after.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True
    if not isinstance(new, dict) or not isinstance(old, dict):
        return True
    if set(old) - set(new):
        return True
    try:
        return count_agent_flow(new) != count_agent_flow(old)
    except (AttributeError, TypeError):
        return True


def put_settings_back(before: bytes, path: Path | None = None) -> Path | None:
    """Keep a copy of the changed file, then put the original bytes back. Returns the copy."""
    path = path or agent_flow_settings_path()
    kept = backup(path, "agent-flow-undo")
    atomic_write_bytes(path, before)
    return kept


def dump_settings(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- hooks
def norm_command(cmd: str) -> str:
    return " ".join(cmd.replace("\\", "/").split()).lower()


def is_agent_flow_command(cmd) -> bool:
    return isinstance(cmd, str) and MARKER in norm_command(cmd)


def hook_command(node_path: str | None = None) -> str:
    """The hook line we install. Forward slashes on purpose: agent-flow 0.9.1 looks for
    the text 'agent-flow/hook.js' to decide it is already set up. A Windows path with
    backslashes never matches, so every server start used to add another copy."""
    node = node_path or shutil.which("node") or "node"
    node = str(node).replace("\\", "/")
    return f'"{node}" "{hook_script().as_posix()}"'


def count_agent_flow(settings: dict) -> dict:
    out = {}
    hooks = settings.get("hooks") or {}
    for ev in EVENTS:
        n = 0
        for group in hooks.get(ev) or []:
            for h in (group or {}).get("hooks") or []:
                if isinstance(h, dict) and is_agent_flow_command(h.get("command")):
                    n += 1
        out[ev] = n
    return out


def remove_agent_flow(settings: dict) -> tuple[dict, int]:
    """Return a copy with every agent-flow hook removed, and how many were removed.
    Empty groups, empty events and an empty 'hooks' block are dropped so an
    uninstall leaves the file as it was before we came."""
    data = json.loads(json.dumps(settings))
    hooks = data.get("hooks")
    removed = 0
    if not isinstance(hooks, dict):
        return data, 0
    for ev in list(hooks.keys()):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue
        new_groups = []
        touched = False
        for group in groups:
            inner = (group or {}).get("hooks")
            if not isinstance(inner, list):
                new_groups.append(group)
                continue
            keep = [h for h in inner if not is_agent_flow_command(h.get("command"))]
            if len(keep) != len(inner):
                removed += len(inner) - len(keep)
                touched = True
                if not keep:
                    continue
                group = dict(group)
                group["hooks"] = keep
            new_groups.append(group)
        if touched and not new_groups:
            del hooks[ev]
        else:
            hooks[ev] = new_groups
    if not hooks:
        del data["hooks"]
    return data, removed


def install_agent_flow(settings: dict, command: str) -> tuple[dict, dict]:
    """Make every one of the 9 events carry exactly 1 agent-flow hook.
    Returns (new settings, {event: copies removed}). An event that already has
    exactly our hook is left untouched, so a second run changes nothing."""
    data = json.loads(json.dumps(settings))
    hooks = data.setdefault("hooks", {})
    removed = {}
    for ev in EVENTS:
        groups = hooks.get(ev) or []
        mine = [h for g in groups for h in (g or {}).get("hooks") or [] if is_agent_flow_command(h.get("command"))]
        if len(mine) == 1 and mine[0].get("command") == command:
            removed[ev] = 0
            continue
        cleaned, n = remove_agent_flow({"hooks": {ev: groups}})
        rest = (cleaned.get("hooks") or {}).get(ev, [])
        rest.append({"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}]})
        hooks[ev] = rest
        removed[ev] = n
    return data, removed


def duplicate_report(settings: dict) -> dict:
    """{event: {command: copies}} for every command registered more than once."""
    out = {}
    for ev, groups in (settings.get("hooks") or {}).items():
        seen: dict[str, int] = {}
        for group in groups or []:
            for h in (group or {}).get("hooks") or []:
                key = norm_command(h.get("command") or h.get("url") or json.dumps(h, sort_keys=True))
                seen[key] = seen.get(key, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        if dups:
            out[ev] = dups
    return out


# ---------------------------------------------------------------- processes
def process_start_time(pid: int):
    """When the process with this number started, or None. Used to tell our own server
    apart from an unrelated program that got the same number after a restart."""
    if pid <= 0:
        return None
    if IS_WIN:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        c, e, k, u = (ctypes.c_ulonglong() for _ in range(4))
        ok = k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
        k32.CloseHandle(handle)
        return int(c.value) if ok else None
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True,
                             text=True, timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    txt = (out.stdout or "").strip()
    return txt or None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WIN:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def port_answers(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def http_answers(port: int, timeout: float = 1.5) -> bool:
    """True when something on this port ANSWERS a web request. port_answers only proves
    the number is taken, which another program sitting on it also does; this proves a web
    server is there. Told the 2 apart after the 2026-09-22 private-port race."""
    try:
        c = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
        c.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        resp = c.getresponse()
        resp.read(1)
        c.close()
        return True
    except Exception:
        return False


def _descendants(pid: int) -> list[int]:
    """Every process started by this one, and by those, and so on (Mac and Linux)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True,
                             timeout=10, creationflags=NO_WINDOW).stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], [pid]
    while todo:
        for kid in children.get(todo.pop(), []):
            if kid not in found:
                found.append(kid)
                todo.append(kid)
    return found


def kill_tree(pid: int) -> None:
    """End this program and every program it started.

    On a Mac the quick way is to end the program's whole process group. That group is
    only safe to end when the program was started in a group of its own: if it shares
    the group of the program calling kill_tree, ending the group ends the caller too
    (found on GitHub's test Macs 2026-09-24: the test run itself was ended). So the
    caller's own group is never ended; the program and its children are ended one by one."""
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)
        return
    import signal

    try:
        group = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        group = None
    if group is not None and group != os.getpgrp():
        try:
            os.killpg(group, signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError):
            pass
    for p in _descendants(pid) + [pid]:
        if p == os.getpid():
            continue
        try:
            os.kill(p, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def norm_path(p) -> str:
    try:
        r = os.path.realpath(str(p))
    except OSError:
        r = os.path.abspath(str(p))
    return os.path.normcase(r)


def discovery_files() -> list[dict]:
    """Every server registration agent-flow has left behind, with a live/stale verdict."""
    out = []
    d = discovery_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        if f.name == "workspaces.json":
            continue
        info = {"file": f, "pid": None, "port": None, "workspace": None, "live": False, "reason": ""}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            info.update(pid=int(data["pid"]), port=int(data["port"]), workspace=str(data["workspace"]))
        except Exception:
            info["reason"] = "unreadable"
            out.append(info)
            continue
        if not pid_alive(info["pid"]):
            info["reason"] = "process has ended"
        elif not port_answers(info["port"]):
            info["reason"] = "process number reused, nothing listening"
        else:
            info["live"] = True
            info["reason"] = "running"
        out.append(info)
    return out


def node_version() -> tuple[int, int, int] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    txt = (out.stdout or "").strip().lstrip("v")
    try:
        parts = [int(x) for x in txt.split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        return None


def npx_command(package: str) -> list[str]:
    """The command that starts the server. AGENT_FLOW_NPX_CMD (a JSON list) replaces it in tests."""
    override = os.environ.get("AGENT_FLOW_NPX_CMD")
    if override:
        return list(json.loads(override))
    npx = shutil.which("npx")
    if not npx:
        raise FileNotFoundError("npx was not found. It comes with Node.js.")
    return [npx, "-y", package]
