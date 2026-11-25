#!/usr/bin/env python3
# <xbar.title>Megavibe Observer</xbar.title>
# <xbar.desc>Live Claude Code sessions in the menu bar; notifies when one waits for input or is blocked.</xbar.desc>
# <xbar.var>string(OBSERVER_URL="http://127.0.0.1:4747"): Where observer.py listens.</xbar.var>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
"""SwiftBar (macOS) / Argos (GNOME) plugin for Megavibe Observer. Stdlib only, read-only: it GETs /api/snapshot and prints a menu.
`--waybar` prints one JSON object instead (Waybar custom module, `return-type: json`); same logic, different output.

The refresh interval is the "5s" in the file name. A session counts as needing attention when the snapshot reports
`attention` (waiting for input or a permission prompt) or its host's disk level is critical, the same rule the web UI
uses. A notification fires once when a session enters that state and again only after it left it.
"""
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

URL = os.environ.get("OBSERVER_URL", "http://127.0.0.1:4747").rstrip("/")
STATE_DIR = os.environ.get("SWIFTBAR_PLUGIN_DATA_PATH") or tempfile.gettempdir()
SESSION = "megavibe-observer"   # tmux session the Start item creates: `tmux attach -t megavibe-observer` to watch it
SELF = os.path.realpath(__file__)
ROOT = os.path.dirname(os.path.dirname(SELF))
STATE = os.path.join(STATE_DIR, "megavibe-observer-notified.json")


def clean(v, n=60):
    """Menu lines are `text | key=value ...`: a pipe or newline in a session name would inject parameters."""
    return "".join(c if c.isprintable() and c not in "|\n" else " " for c in str(v))[:n]


def blocked_reason(s, host):
    if s.get("attention"):
        return clean((s["attention"] or {}).get("why") or "waiting for input")
    if host.get("disk", {}).get("level") == "critical":
        return "blocked: disk"
    return None


def age(ms, now):
    m = max(0, int((now - ms / 1000) // 60))
    return "just now" if m < 1 else "%d min ago" % m if m < 60 else "%d h ago" % (m // 60)


def plan(snapshot, notified, now=None):
    """(menu lines, notifications [(title, text)], keys still in attention). Pure, so it can be tested."""
    rows = []
    for h in snapshot.get("hosts", []):
        for s in h.get("sessions", []) if h.get("ok") else []:
            rows.append((h, s, blocked_reason(s, h)))
    ids = {(h.get("name"), s.get("sessionId")) for h, s, _ in rows}
    roots = [r for r in rows if (r[0].get("name"), r[1].get("parentSessionId")) not in ids]
    attn = [r for r in rows if r[2]]
    keys = {"%s/%s/%s" % (h.get("name"), s.get("sessionId"), why) for h, s, why in attn}
    notes = [("Megavibe: %s" % clean(s.get("name") or "session"), "%s (%s)" % (why, clean(h.get("name"))))
             for h, s, why in attn if "%s/%s/%s" % (h.get("name"), s.get("sessionId"), why) not in notified]
    busy = sum(1 for _, s, _ in rows if s.get("status") == "busy")
    title = ("⚠ %d" % len(attn) + " | color=#f87171") if attn else ("● %d" % len(roots) + (" | color=#60a5fa" if busy else ""))
    lines = [title, "---"]
    order = sorted(roots, key=lambda r: (0 if r[2] else 1 if r[1].get("status") == "busy" else 2))
    for h, s, why in order:
        mark = "⚠" if why else "●" if s.get("status") == "busy" else "○"
        state = why or s.get("status") or "?"
        color = " color=#f87171" if why else ""
        lines.append("%s %s  (%s) · %s | href=%s%s" % (mark, clean(s.get("name") or "session"), clean(h.get("name")), clean(state), URL, color))
    if not order:
        lines.append("No live sessions | color=gray")
    now = time.time() if now is None else now
    lines += ["---"]
    for h in snapshot.get("hosts", []):
        if h.get("ok"):
            lines.append("%s · %s GB free | color=gray" % (clean(h.get("name")), clean(h.get("disk", {}).get("freeGB", "?"), 8)))
            p = h.get("plan") or {}
            parts = ["%s %s%%" % (lbl, clean((p[k] or {}).get("pct"), 5)) for k, lbl in (("fiveHour", "5h"), ("sevenDay", "7d")) if p.get(k)]
            if parts and isinstance(p.get("at"), (int, float)):
                lines.append("   %s · as of %s | color=gray" % (" · ".join(parts), age(p["at"], now)))
        else:
            lines.append("%s unreachable | color=#f87171" % clean(h.get("name")))
    lines += ["Open Observer | href=" + URL, "Poll usage | refresh=true", "Refresh | refresh=true"]
    return lines, notes, keys


def tmux(*args):
    exe = shutil.which("tmux", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]))
    if not exe:
        return None
    return subprocess.run([exe] + list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5).returncode


def start_args(url=URL):
    """tmux arguments that start observer.py on the port of `url`; None unless it is a local address."""
    u = urlparse(url)
    if u.hostname not in ("127.0.0.1", "localhost") or not u.port:
        return None
    return ["new-session", "-d", "-s", SESSION, "-c", ROOT, "env PORT=%d %s observer.py" % (u.port, shutil.which("python3") or "python3")]


def action(name):
    """Run by SwiftBar when a menu item is clicked (`--start` / `--stop`)."""
    if name == "--start" and tmux("has-session", "-t", SESSION) != 0:
        a = start_args()
        if a:
            tmux(*a)
    elif name == "--stop" and tmux("has-session", "-t", SESSION) == 0:
        tmux("kill-session", "-t", SESSION)   # by exact session name, never by pattern


def control(running):
    """Menu items for starting or stopping the observer this plugin started."""
    cmd = "bash=%s terminal=false refresh=true" % SELF
    if not running:
        return ["Start observer | %s param1=--start" % cmd] if start_args() else []
    return ["Stop observer | %s param1=--stop" % cmd] if tmux("has-session", "-t", SESSION) == 0 else []


def notify(title, text):
    if sys.platform == "darwin":
        # Values go through argv, never into the AppleScript source, so a session name cannot inject script.
        subprocess.run(["osascript", "-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
                        "-e", "end run", text, title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Megavibe Observer", "--", title, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


def waybar(lines):
    """Waybar JSON for the menu `plan` built: title -> text, the session and host rows -> tooltip, state -> css class."""
    bare = lambda l: l.split(" | ")[0]
    tip = [bare(l) for l in lines[2:] if l != "---" and not l.startswith(("Open Observer", "Poll usage", "Refresh"))]
    cls = "attention" if lines[0].startswith("⚠") else "busy" if "#60a5fa" in lines[0] else "idle"
    return json.dumps({"text": bare(lines[0]), "tooltip": html.escape("\n".join(tip)), "class": cls})


def load_state():
    try:
        with open(STATE) as f:
            return set(json.load(f))
    except (OSError, ValueError, TypeError):
        return set()


def save_state(keys):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(keys), f)
        os.replace(tmp, STATE)
    except OSError:
        pass


def main(as_waybar=False):
    try:
        with urllib.request.urlopen(URL + "/api/snapshot", timeout=3) as r:
            snapshot = json.load(r)
    except Exception:  # offline is a state to show, not a crash; the saved state is kept so a blip does not re-notify
        if as_waybar:
            print(json.dumps({"text": "◌", "tooltip": "Observer offline", "class": "offline"}))
        else:
            print("\n".join(["◌ | color=gray", "---", "Observer offline | color=gray"] + control(False)))
        return
    lines, notes, keys = plan(snapshot, load_state())
    for title, text in notes:
        try:
            notify(title, text)
        except (OSError, subprocess.SubprocessError):
            pass
    save_state(keys)
    print(waybar(lines) if as_waybar else "\n".join(lines + control(True)))


if __name__ == "__main__":
    if sys.argv[1:] == ["--waybar"]:
        main(True)
    elif len(sys.argv) > 1:
        action(sys.argv[1])
    else:
        main()
    sys.exit(0)
