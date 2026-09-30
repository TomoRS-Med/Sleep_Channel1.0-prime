"""HTTP integration checks for the loopback browser interface."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_app import Handler, JobManager, checked_config


class BrowserInterface(unittest.TestCase):
    def setUp(self):
        self.output = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.token = "test-local-token"
        self.server.manager = JobManager(self.output.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.output.cleanup()

    def request(self, route, payload=None, authenticated=True, binary=False):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["X-App-Token"] = self.server.token
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + route, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        return body if binary else json.loads(body)

    def completed(self, job):
        for _ in range(240):
            current = self.request("/api/job/" + job["id"])
            if current["state"] != "running":
                return current
            time.sleep(.25)
        self.fail("Job did not finish in 60 seconds")

    def test_authentication_trace_and_paired_search_survive_restart(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/bootstrap", authenticated=False)
        self.assertEqual(context.exception.code, 403)

        default = self.request("/api/bootstrap")["default"]
        config = {key: default[key] for key in
                  ("role", "model", "composition", "custom_modules", "parameters", "fixed", "ranges")}
        baseline = self.completed(self.request("/api/run", {**config, "mode": "baseline"}))
        self.assertEqual(baseline["state"], "done", baseline["error"])
        self.assertEqual(baseline["completed"], 20000)
        self.assertTrue(self.request(f'/api/file/{baseline["id"]}/trace.pdf', binary=True)
                        .startswith(b"%PDF-"))

        axes = ["gK", "gLeak"]
        group = {"name": "paired", "kind": "random", "basis": "edited", "samples": 2,
                 "parameters": axes, "points": {key: 2 for key in axes},
                 "ranges": {key: default["ranges"][key] for key in axes}}
        search = self.completed(self.request("/api/run", {**config, "mode": "search",
                                                          "groups": [group], "workers": 2}))
        self.assertEqual(search["state"], "done", search["error"])
        self.assertEqual(search["total"], 2)
        search_results = self.request("/api/results/" + search["id"])
        self.assertEqual(search_results["total"], 2)
        first = search_results["rows"][0]
        inspected = self.completed(self.request("/api/inspect", {
            "job": search["id"], "group": first["group"], "index": first["index"]}))
        self.assertEqual(inspected["state"], "done", inspected["error"])
        relative = f'{first["group"]}/candidate_{int(first["index"]):06d}/trace.pdf'
        self.assertTrue(self.request(f'/api/file/{search["id"]}/{relative}', binary=True)
                        .startswith(b"%PDF-"))
        self.assertEqual(self.request("/api/results/" + search["id"])["rows"][0]["trace_path"],
                         relative.removesuffix("/trace.pdf"))
        restored = JobManager(self.output.name)
        self.assertEqual(restored.get(baseline["id"])["state"], "done")
        self.assertEqual(restored.results(search["id"])["total"], 2)

    def test_browser_accepts_search_above_previous_condition_cap(self):
        default = self.request("/api/bootstrap")["default"]
        config = {key: default[key] for key in
                  ("role", "model", "composition", "custom_modules", "parameters", "fixed", "ranges")}
        axes = ["gK", "gLeak"]
        count = 1_500
        group = {"name": "large_grid", "kind": "sweep", "basis": "edited",
                 "samples": count**2, "parameters": axes,
                 "points": dict.fromkeys(axes, count)}
        self.assertEqual(checked_config({**config, "groups": [group], "workers": 1},
                                        search=True)["model"], config["model"])


if __name__ == "__main__":
    unittest.main()
