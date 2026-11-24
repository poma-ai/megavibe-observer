# Megavibe Observer

A read-only dashboard of the Claude Code sessions on your machines. One card per live session: state (busy, idle, waiting for input or a permission), model, running agents, sub-sessions, Codex jobs, background shells, host and free disk. Click a card for its totals: tokens, tools, every agent started, and plan usage.

It only reads local files (`~/.claude/sessions`, `~/.claude/projects`, and megavibe's `~/.megavibe` when present). It sends and changes nothing, and it never reads transcript text, only counts and names.

## Requirements

- `python3` 3.7 or newer runs the whole thing: `observer.py` is the server and `collector/snapshot.py` the collector. Standard library only.
- Node 18+ with `npm`, only to build the UI once.
- Other machines (optional): `python3` 3.6+ and passwordless `ssh`. Nothing is installed there.

## Start

```
cd ui && npm install && npm run build && cd ..
python3 observer.py
```

Open http://127.0.0.1:4747. The server listens on localhost only; `PORT=5000 python3 observer.py` changes the port. Stop it with Ctrl-C.

After changing `observer.py` or `collector/snapshot.py`, restart it. A UI change only needs `npm run build` and a page reload. For UI development with hot reload, run the server and `cd ui && npm run dev` (it proxies `/api`).

## More machines

Copy `hosts.example.json` to `hosts.json` and list them:

```json
[
  { "name": "local" },
  { "name": "other-mac", "ssh": "user@other-mac.local" }
]
```

The local host is read in-process. For a host with `ssh`, the server pipes `collector/snapshot.py` to `python3` there over `ssh -o BatchMode=yes`. Without a `hosts.json` it watches the local machine only.

## Menu bar (macOS, optional)

A [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin shows the live sessions in the menu bar. Each line opens the web UI. A notification fires when a session waits for input, hits a permission prompt or sits on a host with critically low disk, and fires again only after that session recovered.

```
brew install swiftbar
ln -s "$PWD/menubar/megavibe-observer.5s.py" "<your SwiftBar plugin folder>/"
```

`observer.py` must be running (the plugin shows "Observer offline" otherwise). Set `OBSERVER_URL` in the plugin's settings if you use another port. Notifications are sent by `osascript`, so macOS attributes them to Script Editor: allow that app in System Settings, Notifications. Clicking a notification does not open the UI.

## How it works

- `observer.py` serves `ui/dist`, `/api/snapshot` (all hosts) and `/api/detail?host=…&id=…` (one session, parsed on demand and cached 30 s), and `/api/history[?tmp=1]` (past sessions per host, cached 30 s).
- `collector/snapshot.py` builds one host's snapshot. `--detail <session id>` parses one session's transcript and subagent logs for the click-through panel. `--history [tmp]` lists past sessions from transcript stat plus the first 64 KB of each; sessions whose cwd is a temp directory are hidden unless `tmp` is given.
- `ui/` is Svelte 5 + Vite.

Tests: `python3 -m unittest discover -s tests`.

## Good to know

- Sub-sessions are linked through megavibe's launch records (`~/.megavibe/launches/`, written by the `megavibe` wrapper) and, as a fallback, by process ancestry. Sessions started before that change have no record.
- "Background shells" is an approximate count (monitors and background commands run as shells under the session). Commands started with `exec` are missed.
- Free disk below 8 GB marks every session on that host as blocked.
