import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("menubar", os.path.join(ROOT, "menubar", "megavibe-observer.5s.py"))
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)


def snap(sessions, disk="ok"):
    return {"hosts": [{"name": "local", "ok": True, "disk": {"level": disk, "freeGB": 40}, "sessions": sessions}]}


class Menubar(unittest.TestCase):
    def test_waiting_session_notifies_once_until_it_recovers(self):
        s = snap([{"sessionId": "a", "name": "alpha", "status": "waiting", "attention": {"why": "permission prompt"}},
                  {"sessionId": "b", "name": "beta", "status": "busy"}])
        lines, notes, keys = mb.plan(s, set())
        self.assertTrue(lines[0].startswith("⚠ 1"))
        self.assertEqual(len(notes), 1)
        self.assertIn("alpha", notes[0][0])
        self.assertEqual(mb.plan(s, keys)[1], [])                    # already notified
        ok = snap([{"sessionId": "a", "name": "alpha", "status": "idle"}])
        _, notes2, keys2 = mb.plan(ok, keys)
        self.assertEqual((notes2, keys2), ([], set()))               # recovered: re-armed
        self.assertEqual(len(mb.plan(s, keys2)[1]), 1)

    def test_critical_disk_blocks_every_session_on_the_host(self):
        lines, notes, _ = mb.plan(snap([{"sessionId": "a", "name": "alpha", "status": "idle"}], disk="critical"), set())
        self.assertEqual(len(notes), 1)
        self.assertIn("blocked: disk", "\n".join(lines))

    def test_sub_sessions_are_not_listed_and_names_cannot_inject_parameters(self):
        s = snap([{"sessionId": "a", "name": "x | href=http://evil", "status": "idle"},
                  {"sessionId": "k", "name": "kid", "parentSessionId": "a", "status": "idle"}])
        lines, _, _ = mb.plan(s, set())
        self.assertFalse(any("kid" in l for l in lines))
        row = next(l for l in lines if "evil" in l)
        self.assertEqual(row.count("href="), 2)                      # the name's pipe was neutralised; only the real href and "evil" text remain
        self.assertNotIn("| href=http://evil", row)

    def test_start_only_for_local_urls_and_uses_the_url_port(self):
        a = mb.start_args("http://127.0.0.1:4747")
        self.assertEqual(a[:4], ["new-session", "-d", "-s", "megavibe-observer"])
        self.assertIn("PORT=4747", a[-1])
        self.assertIsNone(mb.start_args("http://example.com:4747"))   # never launch for a remote address
        self.assertIsNone(mb.start_args("http://127.0.0.1"))

    def test_metrics_items_follow_grafana_state(self):
        up = "\n".join(mb.metrics_control(True))
        self.assertIn("Open Metrics | href=http://127.0.0.1:%d/d/megavibe-agents" % mb.METRICS_PORT, up)
        self.assertIn("param1=--metrics-stop", up)
        down = mb.metrics_control(False)
        self.assertEqual(len(down), 1)
        self.assertIn("param1=--metrics-start", down[0])
        self.assertNotIn("href=", down[0])

    def test_metrics_actions_run_the_script_with_the_verb(self):
        calls = []
        orig = mb.metrics
        mb.metrics = calls.append
        try:
            mb.action("--metrics-start")
            mb.action("--metrics-stop")
        finally:
            mb.metrics = orig
        self.assertEqual(calls, ["start", "stop"])

    def test_waybar_json(self):
        import json
        s = snap([{"sessionId": "a", "name": "a<b>", "status": "waiting", "attention": {"why": "permission prompt"}}])
        out = json.loads(mb.waybar(mb.plan(s, set())[0]))
        self.assertEqual((out["text"], out["class"]), ("⚠ 1", "attention"))
        self.assertIn("a&lt;b&gt;", out["tooltip"])                   # tooltip is Pango markup
        self.assertNotIn("href=", out["tooltip"])
        idle = json.loads(mb.waybar(mb.plan(snap([]), set())[0]))
        self.assertEqual(idle["class"], "idle")

    def test_quiet_state(self):
        lines, notes, _ = mb.plan(snap([]), set())
        self.assertEqual(notes, [])
        self.assertIn("No live sessions | color=gray", lines)


if __name__ == "__main__":
    unittest.main()
