"""Smoke tests for observer.py: API shape, detail parsing and caching, static serving, host config.
Run: python3 -m unittest discover -s tests   (uses a scratch HOME; touches nothing real)."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="observer-test.")
SID = "sess-1"


def setUpModule():
    global observer, srv, port
    home = os.path.join(TMP, "home")
    os.makedirs(os.path.join(home, ".claude", "sessions"))
    os.makedirs(os.path.join(home, ".claude", "projects", "p"))
    now = int(time.time() * 1000)
    with open(os.path.join(home, ".claude", "sessions", "%d.json" % os.getpid()), "w") as f:
        json.dump({"pid": os.getpid(), "sessionId": SID, "name": "fake", "cwd": "/x", "status": "idle", "startedAt": now}, f)
    line = {"type": "assistant", "timestamp": "2026-11-23T10:00:00.000Z",
            "message": {"id": "m1", "model": "claude-x", "usage": {"input_tokens": 5, "output_tokens": 7,
                        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100},
                        "content": [{"type": "tool_use", "id": "t1", "name": "Bash"}]}}
    line2 = {"type": "assistant", "timestamp": "2026-11-23T10:01:00.000Z",
             "message": {"id": "m2", "model": "claude-x", "usage": {"output_tokens": 3}, "content": []}}
    with open(os.path.join(home, ".claude", "projects", "p", SID + ".jsonl"), "w") as f:
        for d in (line, line, line2):        # one reply is written as several lines with the same id
            f.write(json.dumps(d) + "\n")
    for proj, sid, cwd in (("-x-real", "hist-real", "/x/real"), ("-x-scratch", "hist-tmp", "/tmp/scratch"),
                           ("-private-tmp-claude-w", "hist-tmpdir", None)):
        os.makedirs(os.path.join(home, ".claude", "projects", proj), exist_ok=True)
        with open(os.path.join(home, ".claude", "projects", proj, sid + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "cwd": cwd, "gitBranch": "main", "timestamp": "2026-11-23T09:00:00.000Z"}) + "\n")
    dist = os.path.join(TMP, "dist")
    os.makedirs(os.path.join(dist, "assets"))
    with open(os.path.join(dist, "index.html"), "w") as f:
        f.write("<title>observer-test</title>")
    with open(os.path.join(dist, "assets", "x.js"), "w") as f:
        f.write("1")
    os.makedirs(os.path.join(TMP, "bin"))
    with open(os.path.join(TMP, "bin", "ssh"), "w") as f:
        f.write("#!/bin/sh\necho 'Permission denied (publickey)' >&2\nexit 255\n")
    os.chmod(os.path.join(TMP, "bin", "ssh"), 0o755)
    hosts = os.path.join(TMP, "hosts.json")
    with open(hosts, "w") as f:
        json.dump([{"name": "local"}, {"name": "far", "ssh": "u@far.invalid"}], f)
    os.environ.update(HOME=home, OBSERVER_DIST=dist, OBSERVER_HOSTS=hosts,
                      PATH=os.path.join(TMP, "bin") + os.pathsep + os.environ["PATH"])
    sys.path.insert(0, ROOT)
    import observer as o
    observer = o
    srv = o.make_server(0)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def tearDownModule():
    srv.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)


def get(path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.getheader("content-type"), r.read()


class Probe(unittest.TestCase):
    def test_probe_usage(self):
        self.assertFalse(observer.probe_usage(os.path.join(TMP, "missing.sh")))
        ok, bad = os.path.join(TMP, "ok.sh"), os.path.join(TMP, "bad.sh")
        for path, body in ((ok, "echo \"$@\" > %s/args\nexit 0\n" % TMP), (bad, "exit 3\n")):
            with open(path, "w") as f:
                f.write(body)
        self.assertTrue(observer.probe_usage(ok))
        self.assertEqual(open(os.path.join(TMP, "args")).read().strip(), "run --every 5")
        self.assertFalse(observer.probe_usage(bad))

    def test_probe_loop_stops(self):
        stop = threading.Event()
        stop.set()
        observer.probe_loop(1, stop)


class Observer(unittest.TestCase):
    def test_snapshot_lists_local_sessions_and_reports_a_failing_host(self):
        code, _, body = get("/api/snapshot")
        d = json.loads(body)
        self.assertEqual(code, 200)
        local, far = d["hosts"]
        self.assertTrue(local["ok"])
        self.assertIn("fake", [s["name"] for s in local["sessions"]])
        self.assertFalse(far["ok"])
        self.assertIn("Permission denied", far["error"])     # the remote's last stderr line, not a crash

    def test_history_hides_temp_dir_sessions_unless_asked(self):
        observer._history.clear()
        local = json.loads(get("/api/history")[2])["hosts"][0]
        ids = {r["sessionId"]: r for r in local["history"]}
        self.assertIn("hist-real", ids)
        self.assertEqual(ids["hist-real"]["cwd"], "/x/real")
        self.assertEqual(ids["hist-real"]["branch"], "main")
        self.assertNotIn("hist-tmp", ids)                        # cwd under /tmp
        self.assertNotIn("hist-tmpdir", ids)                     # project dir of a temp cwd
        self.assertEqual(local["hiddenTmp"], 2)
        all_ids = {r["sessionId"] for r in json.loads(get("/api/history?tmp=1")[2])["hosts"][0]["history"]}
        self.assertTrue({"hist-tmp", "hist-tmpdir"} <= all_ids)

    def test_remote_history_failure_is_reported(self):
        far = json.loads(get("/api/history")[2])["hosts"][1]
        self.assertFalse(far["ok"])

    def test_detail_validates_its_input(self):
        self.assertEqual(get("/api/detail?host=local&id=x;y")[0], 400)
        self.assertEqual(get("/api/detail?host=nope&id=abc")[0], 400)
        self.assertEqual(get("/api/detail?host=local")[0], 400)

    def test_detail_counts_each_reply_once_and_caches(self):
        calls = []
        real = observer.collect
        observer.collect = lambda *a, **k: (calls.append(a), real(*a, **k))[1]
        try:
            observer._detail.clear()
            first = json.loads(get("/api/detail?host=local&id=" + SID)[2])
            json.loads(get("/api/detail?host=local&id=" + SID)[2])
        finally:
            observer.collect = real
        s = first["session"]
        self.assertTrue(first["found"])
        self.assertEqual(s["messages"], 2)                       # three lines, two messages
        self.assertEqual(s["tokens"]["output"], 10)
        self.assertEqual(s["tokens"]["cacheRead"], 100)
        self.assertEqual(s["tools"], [["Bash", 1]])
        self.assertEqual(len(calls), 1)                          # the second request came from the cache

    def test_detail_for_an_unknown_session(self):
        self.assertFalse(json.loads(get("/api/detail?host=local&id=nope-1")[2])["found"])

    def test_remote_detail_failure_is_reported(self):
        d = json.loads(get("/api/detail?host=far&id=" + SID)[2])
        self.assertFalse(d["ok"])

    def test_static_files_and_traversal(self):
        code, ctype, body = get("/")
        self.assertEqual((code, b"observer-test" in body), (200, True))
        self.assertEqual(get("/assets/x.js")[0], 200)
        with open(os.path.join(TMP, "secret.txt"), "w") as f:    # a real file one level above dist/
            f.write("do not serve")
        for path in ("/../secret.txt", "/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            code, _, body = get(path)
            self.assertEqual(code, 404, path)
            self.assertNotIn(b"do not serve", body)
        self.assertEqual(get("/assets")[0], 404)                 # a directory
        self.assertEqual(get("/missing.css")[0], 404)

    def test_hosts_config_rejects_an_ssh_option(self):
        bad = os.path.join(TMP, "bad.json")
        with open(bad, "w") as f:
            json.dump([{"name": "x", "ssh": "-oProxyCommand=evil"}], f)
        old = os.environ["OBSERVER_HOSTS"]
        os.environ["OBSERVER_HOSTS"] = bad
        try:
            with self.assertRaises(SystemExit):
                observer.load_hosts()
        finally:
            os.environ["OBSERVER_HOSTS"] = old


if __name__ == "__main__":
    unittest.main()
