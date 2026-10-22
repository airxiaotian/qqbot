"""本机执行器的签名、缺 key、并发拒绝。不调用真正的 Cursor Agent。"""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import executor
import server
from tools import sign_request


def _request(port: int, method: str, path: str, body: bytes, secret: str, nonce: str) -> tuple[int, dict]:
    timestamp = str(int(time.time()))
    headers = {
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": sign_request(secret, timestamp, nonce, body),
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


class ExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executor._seen.clear()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), executor.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.secret = executor.ensure_secret()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_health_does_not_reveal_key(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/internal/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn(payload["configured"], (True, False))
        self.assertNotIn("cursor_", json.dumps(payload))

    def test_bad_signature_rejected(self):
        status, payload = _request(self.port, "POST", "/internal/jobs", b"{}", "wrong-secret", "bad-sign")
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_missing_key_or_busy_without_calling_agent(self):
        server._running = True
        try:
            self.assertIsNone(server.start_job("second"))
            if server.api_key():
                status, payload = _request(
                    self.port, "POST", "/internal/jobs",
                    json.dumps({"instruction": "不要执行"}).encode(),
                    self.secret, "busy-nonce",
                )
                self.assertEqual(status, 409)
                self.assertIn("上一条", payload["error"])
        finally:
            server._running = False
        if server.api_key():
            return
        status, payload = _request(
            self.port, "POST", "/internal/jobs",
            json.dumps({"instruction": "看一眼状态"}).encode(),
            self.secret, "missing-key",
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "执行器未配置")
        started = time.time()
        result = server.execute("ping")
        self.assertLess(time.time() - started, 2)
        self.assertEqual(result["status"], "error")
        self.assertIn("Cursor API key", result["result"])


if __name__ == "__main__":
    unittest.main()
