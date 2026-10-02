import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from spendguard.server import App, make_handler


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App({})
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: None)
        cls.port = cls.srv.server_address[1]
        cls.srv.RequestHandlerClass = make_handler(cls.app, cls.port)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_index_and_state(self):
        c, b = self.req("/")
        self.assertEqual(c, 200)
        self.assertIn(b"SpendGuard", b)
        c, b = self.req("/api/state")
        self.assertEqual(json.loads(b)["mode"], "replay")

    def test_scenarios_end_to_end(self):
        c, _ = self.req("/api/scenario", {"name": "too_big"})
        self.assertEqual(c, 200)
        st = json.loads(self.req("/api/state")[1])
        self.assertIn("deny", [e["kind"] for e in st["events"]])
        self.assertEqual(st["orders"], [])

    def test_policy_edit_validates(self):
        self.req("/api/reset_all", {})
        c, _ = self.req("/api/policy", {"per_order_limit": "-5"})
        self.assertEqual(c, 400)
        c, _ = self.req("/api/policy", {"per_order_limit": "60", "approval_threshold": "70"})
        self.assertEqual(c, 400)
        c, _ = self.req("/api/policy", {"per_order_limit": "60", "approval_threshold": "30"})
        self.assertEqual(c, 200)
        self.assertEqual(json.loads(self.req("/api/state")[1])["policy"]["per_order_limit"], 6000)

    def test_wrong_host_and_foreign_origin_rejected(self):
        c, _ = self.req("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(c, 403)
        c, _ = self.req("/api/approve", {"id": 1}, headers={"Origin": "http://evil.example"})
        self.assertEqual(c, 403)

    def test_bad_json_and_unknown_path(self):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/task", data=b"{nope", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=5)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.req("/api/nothing", {})[0], 404)

    def test_buyer_approve_flow_over_http(self):
        self.req("/api/scenario", {"name": "normal"})
        oid = json.loads(self.req("/api/state")[1])["orders"][0]["order_id"]
        self.assertEqual(json.loads(self.req("/api/capture", {"order_id": oid})[1])["status"], "awaiting_buyer")
        self.req("/api/buyer_approve", {"order_id": oid})
        self.assertEqual(json.loads(self.req("/api/capture", {"order_id": oid})[1])["status"], "paid")


if __name__ == "__main__":
    unittest.main()
