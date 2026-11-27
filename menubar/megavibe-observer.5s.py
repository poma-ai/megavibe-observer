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
import socket
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
METRICS_PORT = int(os.environ.get("METRICS_GRAFANA_PORT", "47430"))   # same variable telemetry/metrics.sh reads
METRICS_URL = "http://127.0.0.1:%d/d/megavibe-agents" % METRICS_PORT
METRICS_SH = os.path.join(ROOT, "telemetry", "metrics.sh")


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


def plan(snapshot, notified, now=None, ctl=(), met=()):
    """(menu lines, notifications [(title, text)], keys still in attention). Pure, so it can be tested.
    Sections: observer (usage, links, `ctl`), metrics (`met`), then the live sessions."""
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
    sess = []
    order = sorted(roots, key=lambda r: (0 if r[2] else 1 if r[1].get("status") == "busy" else 2))
    for h, s, why in order:
        mark = "⚠" if why else "●" if s.get("status") == "busy" else "○"
        state = why or s.get("status") or "?"
        color = " color=#f87171" if why else ""
        sess.append("%s %s  (%s) · %s | href=%s%s" % (mark, clean(s.get("name") or "session"), clean(h.get("name")), clean(state), URL, color))
    if not order:
        sess.append("No live sessions | color=gray")
    now = time.time() if now is None else now
    obs = []
    for h in snapshot.get("hosts", []):
        if h.get("ok"):
            obs.append("%s · %s GB free | color=black,white" % (clean(h.get("name")), clean(h.get("disk", {}).get("freeGB", "?"), 8)))
            for prov, key, color in (("Claude", "plan", "#f08a24"), ("Codex", "codexPlan", "#86efac")):
                p = h.get(key) or {}
                parts = ["%s %s%%" % (lbl, clean((p[k] or {}).get("pct"), 5)) for k, lbl in (("fiveHour", "5h"), ("sevenDay", "7d")) if p.get(k)]
                if parts and isinstance(p.get("at"), (int, float)):
                    obs.append("%s | color=%s" % (prov, color))
                    obs.append("   %s · as of %s | color=black,white" % (" · ".join(parts), age(p["at"], now)))
        else:
            obs.append("%s unreachable | color=#f87171" % clean(h.get("name")))
    obs += ["---", "Open Observer | href=" + URL, "Poll usage | refresh=true", "Refresh | refresh=true"] + list(ctl)
    lines = [title, "---"] + obs + ["---"] + (list(met) + ["---"] if met else []) + sess
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


def metrics_up():
    """Grafana answers on its port. A TCP connect, not `docker ps`: the menu refreshes every 5 s and must not depend on the docker CLI."""
    try:
        with socket.create_connection(("127.0.0.1", METRICS_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def metrics_control(up):
    """Menu items for the telemetry stack (telemetry/metrics.sh); empty when that script is not in this checkout."""
    if not os.path.exists(METRICS_SH):
        return []
    cmd = "bash=%s terminal=false refresh=true" % SELF
    if up:
        return ["Open Metrics | href=" + METRICS_URL, "Stop metrics | %s param1=--metrics-stop" % cmd]
    return ["Start metrics | %s param1=--metrics-start" % cmd]


def metrics(verb):
    r = subprocess.run([METRICS_SH, verb], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
    if r.returncode:
        notify("Megavibe metrics: %s failed" % verb, (r.stdout.strip().splitlines() or ["see telemetry/metrics.sh"])[-1][:200])


def action(name):
    """Run by SwiftBar when a menu item is clicked (`--start` / `--stop` / `--metrics-start` / `--metrics-stop`)."""
    if name in ("--metrics-start", "--metrics-stop"):
        try:
            metrics(name[len("--metrics-"):])
        except (OSError, subprocess.SubprocessError):
            notify("Megavibe metrics", "could not run telemetry/metrics.sh")
    elif name == "--start" and tmux("has-session", "-t", SESSION) != 0:
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
            print("\n".join(["◌ | color=gray", "---", "Observer offline | color=gray"] + control(False) + ["---"] + metrics_control(metrics_up())))
        return
    lines, notes, keys = plan(snapshot, load_state(), ctl=[] if as_waybar else control(True), met=[] if as_waybar else metrics_control(metrics_up()))
    for title, text in notes:
        try:
            notify(title, text)
        except (OSError, subprocess.SubprocessError):
            pass
    save_state(keys)
    print(waybar(lines) if as_waybar else "\n".join(lines))


if __name__ == "__main__":
    if sys.argv[1:] == ["--waybar"]:
        main(True)
    elif len(sys.argv) > 1:
        action(sys.argv[1])
    else:
        main()
    sys.exit(0)
