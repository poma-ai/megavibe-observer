#!/usr/bin/env python3
"""Megavibe Observer: serves the dashboard (ui/dist) and its JSON API on 127.0.0.1.

  GET /api/snapshot               every host's sessions
  GET /api/detail?host=H&id=SID   one session's totals (parsed on demand, cached 30 s)

The local host is read in-process through collector/snapshot.py. A host with an "ssh" entry in hosts.json gets
the same file piped to `python3 -` over `ssh -o BatchMode=yes`, so nothing is installed there. Read-only: it
never writes outside its own process and sends nothing anywhere but the listed hosts.
"""
import json
import mimetypes
import os
import re
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


def collect(h, detail_id=None, timeout=15):
    """One host's snapshot (or one session's detail) as a dict; a failure becomes {ok: false, error}."""
    err = "no output"
    try:
        if h.get("ssh"):
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", h["ssh"], "python3", "-"]
            if detail_id:
                cmd += ["--detail", detail_id]
            p = subprocess.run(cmd, input=SCRIPT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=timeout)
            err = (p.stderr.strip().splitlines() or ["no output"])[-1]
            data = json.loads(p.stdout)
        else:
            with LOCAL_LOCK:
                data = collector.detail(detail_id) if detail_id else collector.snapshot()
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

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/snapshot":
            with ThreadPoolExecutor(max_workers=max(1, len(HOSTS))) as ex:
                return self.json({"hosts": list(ex.map(collect, HOSTS))})
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


def make_server(port=PORT):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    try:
        srv = make_server()
    except OSError as e:
        raise SystemExit("cannot listen on 127.0.0.1:%d: %s" % (PORT, e))
    srv.daemon_threads = True
    print("observer on http://127.0.0.1:%d" % PORT, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
