**This is the Mac version.** On Windows, use [outliers-ws-01-agent-flow](https://github.com/OUTLIERS-ai/outliers-ws-01-agent-flow).

# outliers-ws-01-agent-flow-mac

Watch your Claude Code agents hand work to each other, live, in your browser.

```
git clone https://github.com/OUTLIERS-ai/outliers-ws-01-agent-flow-mac; cd outliers-ws-01-agent-flow-mac; python3 install.py
```

Run the line from your home folder: the file that starts agent-flow by itself each time you switch on your computer and sign in points at this folder, so keep it where you cloned it.

## What this is

agent-flow is a free, open-source web page by another developer (github.com/patoles/agent-flow, Apache 2.0 licence). It runs on your own computer at http://127.0.0.1:3001 and draws each Claude Code session as a glowing hexagon. When the session starts a subagent, a second hexagon appears with a line between them, and every tool call pops up as a card.

This folder does not contain agent-flow. It contains the safe way to install and run it:

| File | What it does |
|---|---|
| `install.py` | Checks Python 3.11+ and Node.js 22+, asks where your vaults are, runs agent-flow once with a throwaway home folder so it writes its hook script without touching your settings, copies that script in, backs up your Claude Code `settings.json` and makes sure each of the 9 agent-flow events has exactly 1 copy of the hook, then adds a hidden file that starts agent-flow when the computer starts. `--uninstall` takes it all out again. |
| `start.py` | Starts agent-flow with no window and usage tracking off, from the folder that contains your vaults. Refuses to start it if your `settings.json` would be wiped (see below). When a start fails it names the cause in 1 sentence. `--stop`, `--status`. |
| `guard.py` | Started by `start.py`. Runs agent-flow on a port picked at random that only guard.py uses, serves the page on 3001 only to requests addressed to 127.0.0.1 / localhost, and puts `settings.json` back if agent-flow rewrites it in its first 60 seconds. |
| `only_this_computer.js` | Loaded into agent-flow by `guard.py` (Node.js's `--require` option), so agent-flow's own 2 ports also refuse requests from other websites. agent-flow's files are unchanged. |
| `check_hooks.py` | Counts every hook on every Claude Code event and flags any command registered twice. `--fix` keeps 1 of each after a backup. |
| `cleanup.py` | Deletes the files left behind by agent-flow servers that have stopped, and ends a second server left watching the same folder. |
| `common.py` | Shared code for the scripts above. |
| `tests/` | 59 checks, run in the private Python folder the guide's "The safe way to change it" makes first: `source ~/outliers-checks/bin/activate && python -m pip install pytest`, then `source ~/outliers-checks/bin/activate && python -m pytest -q` in this folder. They use a temporary home folder and a stand-in for agent-flow; they never touch your real setup. 6 of them need Node.js. Set `AGENT_FLOW_REAL=1` to also run 4 checks against the real npm package; without it those 4 are skipped, and on a Mac 1 more is skipped because it checks a path a Mac does not use. Measured on GitHub's test Macs on 2026-09-25: 54 passed, 5 skipped. |
| `guide/GUIDE.md` | The full guide: how it was built, what went wrong, how to fit it to your own system. |

## What you need

- Claude Code, Python 3.11 or newer, Git. Check with `python3 --version`.
- Node.js 22 or newer (24 recommended). Check with `node --version`.

`install.py` tests both version numbers before it touches anything. If either is missing or too old it stops, changes nothing, and tells you how to get it. The floors are dated: Python 3.8 stopped getting security fixes on 2024-10-07, Node.js 18 on 2025-04-30 and Node.js 20 on 2026-04-30 (checked 2026-09-22).

## Useful commands

```
python3 install.py --yes                 # accept the defaults it finds
python3 install.py --watch-folder "$HOME/Documents" --yes
python3 install.py --start-now           # start it straight away instead of the next time the computer starts
python3 install.py --no-autostart        # does not start by itself when the computer starts (removes that file if you have it); start by hand with start.py. Saved: later runs keep it
python3 install.py --autostart           # starts by itself when the computer starts again, after an earlier --no-autostart
python3 install.py --port 3002           # another port; a running server moves to it
python3 install.py --uninstall           # remove the hook and the file that starts agent-flow
python3 start.py --status
python3 check_hooks.py
python3 cleanup.py --dry-run
```

After installing, open http://127.0.0.1:3001 and start a NEW Claude Code session inside the watch folder. Sessions that were already open show nothing, because Claude Code reads hooks when a session starts.

## Faults this fixes

1. **Registration files left behind by stopped servers.** Each start writes `<home>/.claude/agent-flow/<code>-<process number>.json`. `start.py` runs `cleanup.py` before every start, so any left behind are removed.
2. **agent-flow can wipe your whole `settings.json` at start.** It runs its own setup every time it starts; if it cannot read the file (a trailing comma, or an invisible byte-order mark that some editors add) it writes a new file containing only its hooks. Because agent-flow starts by itself when the computer starts, that happens with nothing on screen to tell you. `start.py` checks the file first and will not start agent-flow on an unsafe file (the reason goes to the screen and `logs/start.log`; `python3 start.py --status` repeats it). `guard.py` also keeps the exact bytes and puts them back if agent-flow changes the file anyway. `python3 install.py` removes a byte-order mark after a backup. Proven with the real package on 2026-09-22 (`tests/test_real_package.py`).
3. **Other websites could read the live page** (a trick where a website sends its requests to your own computer). agent-flow's page ignores the web address a request was sent to. `guard.py` answers only requests addressed to your own computer on port 3001. On 2026-09-24 a test with the real package showed agent-flow's own 2 ports were still open: its private page port gave the page and the live stream to a request naming another website, and its event port drew a made-up event sent from another website. `guard.py` now loads `only_this_computer.js` into agent-flow, and both ports refuse such requests (5 of 5 refused in the same test; `tests/test_other_websites.py`). A copy of agent-flow started by hand has none of these checks. Stop agent-flow with `python3 start.py --stop` when you are not watching.
4. **`--stop` could close an unrelated program** after a restart reused the saved process number. The saved record now includes the process start time and stop checks both.
5. **A start that failed told you nothing you could use.** It printed "The server stopped straight away" and a path to 20 lines of Node.js text. `start.py` now reads the last 30 lines of `logs/agent-flow.log`, matches the known shapes (port taken, no internet with the package not saved yet, Node.js missing or too old, package name wrong) and prints 1 plain sentence. `python3 start.py --status` repeats it.
6. **The private port was picked, let go, then handed to agent-flow**, and the computer could give that number away in between. It failed 1 start in 34 on 2026-09-22. If agent-flow now dies within 12 seconds and the port is answering to something else, the guard picks another number and tries again, 3 times in all.
7. **Closing agent-flow by force left a second server running.** `cleanup.py` counted folders, so 2 servers watching 1 folder looked like 1. It now counts servers, ends the older server, and `start.py` ends a server left behind before it starts a new one.
8. **`check_hooks.py` called an empty `settings.json` "OK" while `start.py` refused it.** Both now use the same reader, so they cannot disagree.

## Usage tracking

agent-flow 0.9.1 sends anonymous usage events (an install code, version, operating system, session length) to its author's server unless `AGENT_FLOW_TELEMETRY=false` or `DO_NOT_TRACK=1` is set. Both are set by `start.py` and by the file that starts agent-flow when the computer starts. If you ever run `npx agent-flow-app` by hand, set them yourself first.

## Licence

Our files: MIT (see `LICENSE`). agent-flow itself: Apache 2.0, downloaded by you from npm, not included here. See `WHAT-I-STOLE.md`.

This repo is made automatically from outliers-ws-01-agent-flow@a09dd1c. To report a problem or suggest a change, use that repo, not this one.
