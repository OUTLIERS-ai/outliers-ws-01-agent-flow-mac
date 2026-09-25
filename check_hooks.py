"""Count the Claude Code hooks registered on every event and flag duplicates.

A hook is a command Claude Code runs on an event (a tool call, a session
start). Each copy starts a program. When the same command is registered 12
times on 1 event, every tool call starts 12 programs; on Windows each can flash
a window. This check makes that visible.

    python check_hooks.py          # report; exit code 1 if anything is duplicated
    python check_hooks.py --fix    # keep the first copy of each, after a dated backup
"""
from __future__ import annotations

import argparse
import sys

import common


def dedupe(settings: dict) -> tuple[dict, int]:
    import json

    data = json.loads(json.dumps(settings))
    removed = 0
    for ev, groups in (data.get("hooks") or {}).items():
        seen = set()
        new_groups = []
        for group in groups or []:
            inner = (group or {}).get("hooks")
            if not isinstance(inner, list):
                new_groups.append(group)
                continue
            keep = []
            for h in inner:
                key = (str((group or {}).get("matcher", "")),
                       common.norm_command(h.get("command") or h.get("url") or json.dumps(h, sort_keys=True)))
                if key in seen:
                    removed += 1
                    continue
                seen.add(key)
                keep.append(h)
            if keep:
                g = dict(group)
                g["hooks"] = keep
                new_groups.append(g)
        data["hooks"][ev] = new_groups
    return data, removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Count hook copies per event and flag duplicates.")
    ap.add_argument("--settings", help="settings.json to check (default: your Claude Code settings)")
    ap.add_argument("--fix", action="store_true", help="remove repeat copies after a dated backup")
    args = ap.parse_args(argv)

    from pathlib import Path

    path = Path(args.settings) if args.settings else common.settings_path()
    if not path.exists():
        print(f"No settings file at {path}. Nothing to check.")
        return 0
    # The same reader start.py uses before a start, so the 2 always agree about a file.
    fault = common.settings_fault(path, point_at_check=False)
    if fault:
        print(f"Settings file: {path}")
        print(f"RESULT: PROBLEM - {fault}")
        print("start.py will not start agent-flow until this is put right, to stop agent-flow "
              "replacing the file. Save your fix, then run this check again.")
        return 2
    try:
        settings = common.read_settings(path)
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"Settings file: {path}")
        print(f"RESULT: PROBLEM - settings.json cannot be read ({common.json_error_in_words(exc)}).")
        return 2
    if not isinstance(settings.get("hooks") or {}, dict):
        print(f"RESULT: PROBLEM - 'hooks' in {path} is not a block of events. Fix it by hand.")
        return 2
    hooks = settings.get("hooks") or {}
    print(f"Settings file: {path}")
    total = 0
    print(f"{'Event':<22}{'hook commands':>14}{'agent-flow copies':>19}")
    af = common.count_agent_flow(settings)
    for ev in sorted(set(hooks) | set(common.EVENTS)):
        n = sum(len((g or {}).get("hooks") or []) for g in hooks.get(ev) or [])
        total += n
        if n or ev in common.EVENTS:
            print(f"{ev:<22}{n:>14}{af.get(ev, 0) if ev in af else '-':>19}")
    print(f"Total hook commands: {total}")

    dups = common.duplicate_report(settings)
    if not dups:
        print("RESULT: OK - no command is registered twice on the same event.")
        return 0
    print("RESULT: DUPLICATES FOUND")
    for ev, items in dups.items():
        for cmd, n in items.items():
            print(f"  {ev}: {n} copies of  {cmd[:110]}")
    if not args.fix:
        print("Run again with --fix to keep 1 copy of each (a backup is taken first).")
        return 1
    new, removed = dedupe(settings)
    bak = common.backup(path, "hookdedupe")
    common.atomic_write_text(path, common.dump_settings(new), common.detect_eol(path))
    print(f"Removed {removed} repeat copies. Backup: {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
