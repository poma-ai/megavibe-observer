#!/usr/bin/env python3
"""Megavibe Observer: serves the dashboard (ui/dist) and its JSON API on 127.0.0.1.

  GET /api/snapshot               every host's sessions
  GET /api/detail?host=H&id=SID   one session's totals (parsed on demand, cached 30 s)
  GET /api/history[?tmp=1]        every host's past sessions from their transcripts (temp-dir sessions hidden
                                  unless tmp=1; cached 30 s)

The local host is read in-process through collector/snapshot.py. A host with an "ssh" entry in hosts.json gets
the same file piped to `python3 -` over `ssh -o BatchMode=yes`, so nothing is installed there.

  POST /api/open-terminal         {host, id}: opens a macOS Terminal window: attaches to the session's tmux session
                                  if live in tmux, `claude --resume <id>` in its cwd if ended, refused if live in a
                                  plain terminal (ssh -t for a remote host). The one action the server takes.

Otherwise read-only: it never writes outside its own process and sends nothing anywhere but the listed hosts.
"""
import json
import mimetypes
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "collector"))
import snapshot as collector  # noqa: E402

with open(os.path.join(ROOT, "collector", "snapshot.py")) as _f:
    SCRIPT = _f.read()
DIST = os.path.realpath(os.environ.get("OBSERVER_DIST", os.path.join(ROOT, "ui", "dist")))
PORT = int(os.environ.get("PORT", "4747"))
SESSION_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
DETAIL_TTL_S = 30
LOCAL_LOCK = threading.Lock()   # the collector keeps per-run state in module globals: one local call at a time


def load_hosts():
    path = os.environ.get("OBSERVER_HOSTS", os.path.join(ROOT, "hosts.json"))
    if not os.path.exists(path):
        return [{"name": "local"}]
    with open(path) as f:
        hosts = json.load(f)
    for h in hosts:
        if not isinstance(h, dict) or not isinstance(h.get("name"), str):
            raise SystemExit("hosts.json: every entry needs a \"name\"")
        if "ssh" in h and (not isinstance(h["ssh"], str) or h["ssh"].startswith("-")):
            raise SystemExit("hosts.json: \"ssh\" must be a user@host string, not an option")
    return hosts


HOSTS = load_hosts()


def collect(h, detail_id=None, timeout=15, history=None):
    """One host's snapshot (or one session's detail, or its history: history is "" or "tmp") as a dict;
    a failure becomes {ok: false, error}."""
    err = "no output"
    try:
        if h.get("ssh"):
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", h["ssh"], "python3", "-"]
            if detail_id:
                cmd += ["--detail", detail_id]
            if history is not None:
                cmd += ["--history"] + ([history] if history else [])
            p = subprocess.run(cmd, input=SCRIPT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=timeout)
            err = (p.stderr.strip().splitlines() or ["no output"])[-1]
            data = json.loads(p.stdout)
        else:
            with LOCAL_LOCK:
                if history is not None:
                    data = collector.history(include_tmp=history == "tmp")
                elif detail_id:
                    data = collector.detail(detail_id)
                else:
                    data = collector.snapshot()
        data["name"], data["ok"] = h["name"], True
        return data
    except subprocess.TimeoutExpired:
        err = "timed out"
    except Exception as e:  # a host that fails must not take the others down
        err = err if isinstance(e, ValueError) else type(e).__name__ + ": " + str(e)
    return {"name": h["name"], "ok": False, "error": err[:200], "sessions": []}


_detail = {}
_detail_lock = threading.Lock()


def detail(h, sid):
    """Shared between concurrent requests, cached briefly: a transcript can be large."""
    key = (h["name"], sid)
    with _detail_lock:
        e = _detail.get(key)
        owner = not e or time.time() - e["at"] >= DETAIL_TTL_S
        if owner:
            e = {"at": time.time(), "done": threading.Event(), "result": None}
            _detail[key] = e
            while len(_detail) > 50:
                _detail.pop(next(iter(_detail)))
    if owner:
        try:
            e["result"] = collect(h, sid, 40)
        finally:
            e["done"].set()
    else:
        e["done"].wait(45)
    return e["result"] or {"name": h["name"], "ok": False, "error": "no result", "sessions": []}


_history = {}


def history(include_tmp):
    """Every host's history, cached briefly: it stats and partly reads hundreds of transcripts per host."""
    key = "tmp" if include_tmp else ""
    with _detail_lock:
        e = _history.get(key)
        if e and time.time() - e["at"] < DETAIL_TTL_S:
            return e["result"]
    with ThreadPoolExecutor(max_workers=max(1, len(HOSTS))) as ex:
        result = {"hosts": list(ex.map(lambda h: collect(h, timeout=40, history=key), HOSTS))}
    with _detail_lock:
        _history[key] = {"at": time.time(), "result": result}
    return result


def session_info(h, sid):
    """(cwd, where, error): the session's working directory and, when it is live, where it runs ("tmux s:w" or
    "terminal"; None when ended), as its own host reports it. Never taken from the request. A host whose live
    snapshot failed is an error, never "ended": that would resume a session that is still running."""
    snap = collect(h)
    if not snap.get("ok"):
        return None, None, (502, "host unreachable: " + str(snap.get("error", ""))[:120])
    for s in snap.get("sessions") or []:
        if s.get("sessionId") == sid:
            if not s.get("cwd"):
                return None, None, (404, "session working directory not found")
            return s["cwd"], s.get("where") or "terminal", None
    for key in ("", "tmp"):
        hist = collect(h, timeout=40, history=key)
        if not hist.get("ok"):
            return None, None, (502, "host unreachable: " + str(hist.get("error", ""))[:120])
        for r in hist.get("history") or []:
            if r.get("sessionId") == sid and r.get("cwd"):
                return r["cwd"], None, None
    return None, None, (404, "session working directory not found")


# Terminal's `do script` types the command into a tty, where control characters (^U, newline) act on the line
# editor even inside shell quotes, so a cwd or tmux name carrying one must never reach it.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_recent = {}
_recent_lock = threading.Lock()
DEDUPE_S = 10


def open_terminal(h, sid):
    """Open a Terminal window on the session: attach to its tmux session when it is live in tmux, resume it
    (`claude --resume`) when it has ended, refuse when it is live in a plain terminal (a second instance would
    fork the transcript). Over ssh for a remote host. Returns (http status, message). macOS Terminal.app only."""
    if sys.platform != "darwin":
        return 501, "open in terminal is only supported on macOS"
    with _recent_lock:   # a double click must not open two windows on one transcript
        now = time.time()
        for k in [k for k, t in _recent.items() if now - t >= DEDUPE_S]:
            del _recent[k]
        if (h["name"], sid) in _recent:
            return 429, "already opening this session"
        _recent[(h["name"], sid)] = now
    cwd, where, err = session_info(h, sid)
    if err:
        return err
    if not os.path.isabs(cwd) or CONTROL.search(cwd):
        return 404, "session working directory not usable"
    if where is None:
        inner = "cd %s && claude --resume %s" % (shlex.quote(cwd), sid)
    elif where.startswith("tmux "):
        name = where[5:].split(":", 1)[0]
        if not name or CONTROL.search(name):
            return 404, "tmux session not found"
        inner = "tmux attach -t %s" % shlex.quote("=" + name)
    else:
        return 409, "session is running in a plain terminal and cannot be opened from here"
    # ssh runs a non-login shell where tmux and claude are often off PATH: use the remote user's login shell
    remote = '"$SHELL" -lc ' + shlex.quote(inner)
    cmd = "ssh -t %s %s" % (shlex.quote(h["ssh"]), shlex.quote(remote)) if h.get("ssh") else inner
    script = ['on run argv', 'tell application "Terminal"', 'activate', 'do script (item 1 of argv)',
              'end tell', 'end run']
    args = ["osascript"] + [x for line in script for x in ("-e", line)] + [cmd]
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 500, type(e).__name__
    return (200, "opened") if p.returncode == 0 else (500, (p.stderr.strip().splitlines() or ["osascript failed"])[-1][:200])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype, no_store=False):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        if no_store:
            self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj):
        self.send(200, json.dumps(obj).encode(), "application/json", no_store=True)

    def do_POST(self):
        """The only action the server takes. A page on another origin must not be able to trigger it: the Host
        must be this server, any Origin must match it, and a custom header forces a CORS preflight."""
        host = self.headers.get("host", "")
        origin = self.headers.get("origin")
        bound = self.server.server_address[1]
        if host not in ("127.0.0.1:%d" % bound, "localhost:%d" % bound) or self.headers.get("x-observer") != "1" \
                or (origin is not None and origin != "http://" + host):
            return self.send(403, b"forbidden", "text/plain")
        if urlparse(self.path).path != "/api/open-terminal":
            return self.send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(max(0, min(int(self.headers.get("content-length") or 0), 4096))) or b"{}")
        except ValueError:
            return self.send(400, b"bad json", "text/plain")
        ok = isinstance(body, dict)
        h = next((x for x in HOSTS if x["name"] == body.get("host")), None) if ok else None
        sid = body.get("id") if ok else None
        if not h or not isinstance(sid, str) or not SESSION_ID.match(sid):
            return self.send(400, b"bad host or id", "text/plain")
        code, msg = open_terminal(h, sid)
        self.send(code, json.dumps({"ok": code == 200, "message": msg}).encode(), "application/json", no_store=True)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/snapshot":
            with ThreadPoolExecutor(max_workers=max(1, len(HOSTS))) as ex:
                return self.json({"hosts": list(ex.map(collect, HOSTS))})
        if url.path == "/api/history":
            return self.json(history((parse_qs(url.query).get("tmp") or [""])[0] == "1"))
        if url.path == "/api/detail":
            q = parse_qs(url.query)
            h = next((x for x in HOSTS if x["name"] == (q.get("host") or [""])[0]), None)
            sid = (q.get("id") or [""])[0]
            if not h or not SESSION_ID.match(sid):
                return self.send(400, b"bad host or id", "text/plain")
            return self.json(detail(h, sid))
        rel = unquote(url.path)
        target = os.path.realpath(os.path.join(DIST, "index.html" if rel == "/" else rel.lstrip("/")))
        if not target.startswith(DIST + os.sep) or not os.path.isfile(target):
            return self.send(404, b"not found", "text/plain")
        with open(target, "rb") as f:
            self.send(200, f.read(), mimetypes.guess_type(target)[0] or "application/octet-stream")


PROBE = os.path.join(os.path.expanduser("~"), ".megavibe", "scripts", "usage-probe.sh")
PROBE_EVERY_S = int(os.environ.get("OBSERVER_PROBE_S", "300"))   # 0 switches the timer off


def probe_usage(script=PROBE):
    """One `usage-probe.sh run --every 5` call (refreshes the Codex usage reading; it skips itself when a reading
    is fresh or codex is missing). Returns True when it ran, False when the script is absent or failed."""
    if not os.path.isfile(script):
        return False
    try:
        return subprocess.run(["bash", script, "run", "--every", "5"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe_loop(every=PROBE_EVERY_S, stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        probe_usage()
        stop.wait(every)


def make_server(port=PORT):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    try:
        srv = make_server()
    except OSError as e:
        raise SystemExit("cannot listen on 127.0.0.1:%d: %s" % (PORT, e))
    srv.daemon_threads = True
    if PROBE_EVERY_S > 0:
        threading.Thread(target=probe_loop, daemon=True).start()
    print("observer on http://127.0.0.1:%d" % PORT, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
