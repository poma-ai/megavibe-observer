# Megavibe Observer

A read-only dashboard of the Claude Code sessions on your machines. One card per live session: state (busy, idle, waiting for input or a permission), model, running agents, sub-sessions, Codex jobs, background shells, host and free disk. Click a card for its totals: tokens, tools, every agent started, and plan usage.

It only reads local files (`~/.claude/sessions`, `~/.claude/projects`, and megavibe's `~/.megavibe` when present). It sends and changes nothing, and it never reads transcript text, only counts and names.

## Requirements

- `python3` 3.7 or newer runs the whole thing: `observer.py` is the server and `collector/snapshot.py` the collector. Standard library only.
- Node 18+ with `npm`, only to build the UI once.
- Other machines (optional): `python3` 3.6+ and passwordless `ssh`. Nothing is installed there.
- Menu bar plugin (optional): SwiftBar, Argos or Waybar, plus `notify-send` on Linux and `tmux` for Start/Stop. See [Menu bar](#menu-bar-optional).

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

## Menu bar (optional)

One plugin file, `menubar/megavibe-observer.5s.py`, shows the live sessions in the menu or status bar on macOS and Linux. Each line opens the web UI. A notification fires when a session waits for input, hits a permission prompt or sits on a host with critically low disk, and fires again only after that session recovered. Set `OBSERVER_URL` in the plugin's settings if the observer uses another port.

### macOS (SwiftBar)

```
brew install swiftbar
ln -s "$PWD/menubar/megavibe-observer.5s.py" "<your SwiftBar plugin folder>/"
```

Notifications are sent by `osascript`, so macOS attributes them to Script Editor: allow that app in System Settings, Notifications. Clicking a notification does not open the UI.

### Linux, GNOME (Argos)

The [Argos](https://github.com/p-e-w/argos) extension reads the same line format as SwiftBar. Link the same file into `~/.config/argos/`. Notifications use `notify-send` (libnotify).

### Linux, Waybar

Add a custom module. Notifications use `notify-send`. The tooltip lists the sessions and hosts, and the class is `attention`, `busy`, `idle` or `offline` for styling. There is no Start/Stop item here, start the observer yourself.

```
"custom/observer": {"exec": "/path/to/megavibe-observer/menubar/megavibe-observer.5s.py --waybar", "return-type": "json", "interval": 5, "on-click": "xdg-open http://127.0.0.1:4747"}
```

### Start and stop from the menu

When the observer is down, the SwiftBar and Argos menus show "Start observer", which runs it in a tmux session named `megavibe-observer` (needs `tmux`; local `OBSERVER_URL` only). While that session exists they show "Stop observer". Watch it with `tmux attach -t megavibe-observer`.

The same menus also start and stop the metrics stack (Grafana, `telemetry/metrics.sh`; needs Docker) and open it, and the web UI header has a Metrics link. See [TELEMETRY.md](TELEMETRY.md).

## Good to know

- Sub-sessions are linked through megavibe's launch records (`~/.megavibe/launches/`, written by the `megavibe` wrapper) and, as a fallback, by process ancestry. Sessions started before that change have no record.
- "Background shells" is an approximate count (monitors and background commands run as shells under the session). Commands started with `exec` are missed.
- Free disk below 8 GB marks every session on that host as blocked.
