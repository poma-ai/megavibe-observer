#!/usr/bin/env python3
# <xbar.title>Megavibe Observer</xbar.title>
# <xbar.desc>Live Claude Code sessions in the menu bar; notifies when one waits for input or is blocked.</xbar.desc>
# <xbar.var>string(OBSERVER_URL="http://127.0.0.1:4747"): Where observer.py listens.</xbar.var>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
"""SwiftBar plugin for Megavibe Observer. Stdlib only, read-only: it GETs /api/snapshot and prints a menu.

The refresh interval is the "5s" in the file name. A session counts as needing attention when the snapshot reports
`attention` (waiting for input or a permission prompt) or its host's disk level is critical, the same rule the web UI
uses. A notification fires once when a session enters that state and again only after it left it.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

URL = os.environ.get("OBSERVER_URL", "http://127.0.0.1:4747").rstrip("/")
STATE_DIR = os.environ.get("SWIFTBAR_PLUGIN_DATA_PATH") or tempfile.gettempdir()
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


def plan(snapshot, notified):
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
    lines += ["---"]
    for h in snapshot.get("hosts", []):
        if h.get("ok"):
            lines.append("%s · %s GB free | color=gray" % (clean(h.get("name")), clean(h.get("disk", {}).get("freeGB", "?"), 8)))
        else:
            lines.append("%s unreachable | color=#f87171" % clean(h.get("name")))
    lines += ["Open Observer | href=" + URL, "Refresh | refresh=true"]
    return lines, notes, keys


def notify(title, text):
    # Values go through argv, never into the AppleScript source, so a session name cannot inject script.
    subprocess.run(["osascript", "-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
                    "-e", "end run", text, title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


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


def main():
    try:
        with urllib.request.urlopen(URL + "/api/snapshot", timeout=3) as r:
            snapshot = json.load(r)
    except Exception:  # offline is a state to show, not a crash; the saved state is kept so a blip does not re-notify
        print("◌ | color=gray\n---\nObserver offline | color=gray\nStart it: python3 observer.py\nOpen Observer | href=" + URL)
        return
    lines, notes, keys = plan(snapshot, load_state())
    for title, text in notes:
        try:
            notify(title, text)
        except (OSError, subprocess.SubprocessError):
            pass
    save_state(keys)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
    sys.exit(0)
