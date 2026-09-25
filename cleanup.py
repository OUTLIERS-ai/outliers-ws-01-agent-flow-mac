"""Clear agent-flow's stale server registrations.

Each time the agent-flow server starts it writes a small file into
<home>/.claude/agent-flow/ named <code>-<process number>.json, saying which
folder it watches and which port it listens on. The Claude Code hook reads
these files to find the server.

On Windows the hook never removes files for servers that have ended (its
"is it alive?" check always answers yes). It also sends each event only to the
server with the NARROWEST matching folder. So an old file for a narrower
folder can swallow every event while the real server sits idle.

This script keeps a file only if its process is running AND its port answers. It also
counts SERVERS, not folders: when 2 servers are watching 1 folder (what a hard kill
leaves behind) it ends the older server and takes its file away.

    python cleanup.py            # remove stale files, report what was kept
    python cleanup.py --dry-run  # only report
"""
from __future__ import annotations

import argparse
import sys

import common


def end_servers(infos: list, dry_run: bool = False) -> int:
    """End these agent-flow servers and take their registration files away."""
    n = 0
    for info in infos:
        if dry_run:
            n += 1
            continue
        if common.pid_alive(info["pid"]):
            common.kill_tree(info["pid"])
        try:
            info["file"].unlink()
        except OSError:
            pass
        n += 1
    return n


def extra_servers(kept: list) -> list:
    """Servers watching a folder that already has one. Counting FOLDERS hid this:
    after a hard kill, 2 servers watching 1 folder were reported as 1 running server.
    The newest registration is kept, because that is the server just started."""
    extra = []
    by_folder: dict = {}
    for info in kept:
        if not info["workspace"]:
            continue
        by_folder.setdefault(common.norm_path(info["workspace"]), []).append(info)
    for group in by_folder.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda i: i["file"].stat().st_mtime if i["file"].exists() else 0)
        extra.extend(group[:-1])
    return extra


def run(dry_run: bool = False, quiet: bool = False) -> dict:
    files = common.discovery_files()
    removed, kept = [], []
    for info in files:
        if info["live"]:
            kept.append(info)
            continue
        if not dry_run:
            try:
                info["file"].unlink()
            except OSError as exc:
                info["reason"] += f" (could not remove: {exc})"
        removed.append(info)
    extra = extra_servers(kept)
    ended = end_servers(extra, dry_run)
    if not dry_run:
        kept = [i for i in kept if i not in extra]
    if not quiet:
        where = common.discovery_dir()
        print(f"Registration folder: {where}")
        print(f"Found {len(files)} registration file(s): {len(kept)} running, {len(removed)} left over from stopped servers.")
        for info in kept:
            print(f"  KEEP    {info['file'].name}  port {info['port']}  watching {info['workspace']}")
        verb = "WOULD REMOVE" if dry_run else "REMOVED"
        for info in removed:
            print(f"  {verb}  {info['file'].name}  ({info['reason']})")
        for info in extra:
            print(f"  {'WOULD END' if dry_run else 'ENDED'}  {info['file'].name}  "
                  f"(more than 1 agent-flow server was watching {info['workspace']})")
        if ended:
            print(f"Ended {ended} agent-flow server(s) left over after a server was closed by force. "
                  "Each extra server keeps a port open and draws the same events twice.")
        folders = sorted({i["workspace"] for i in kept if i["workspace"]}, key=len)
        if len(folders) > 1:
            print("Note: servers are running for more than 1 folder. A session only reports to the "
                  "one watching the narrowest folder that contains it.")
    return {"kept": len(kept), "removed": len(removed), "ended": ended, "dry_run": dry_run}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="report only, remove nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    run(dry_run=args.dry_run, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
