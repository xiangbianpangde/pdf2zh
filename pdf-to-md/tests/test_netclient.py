"""Transport behaviour against a local stub: retries, headers, error mapping."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import support  # noqa: F401 - sets sys.path
import errors
import netclient
import translate


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length)
        self.server.seen.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": payload.decode("utf-8", "replace"),
        })
        plan = self.server.plan
        status = plan.pop(0) if plan else 200
        if status == 200:
            payload_obj = {"choices": [{"message": {"content": self.server.content}}]}
        else:
            payload_obj = {"error": {"message": "stub says no"}}
        body = json.dumps(payload_obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        return


class StubServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.seen = []
        self.server.plan = []
        self.server.content = "OK"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def seen(self) -> list[dict]:
        return self.server.seen

    @property
    def plan(self) -> list[int]:
        return self.server.plan

    @property
    def content(self) -> str:
        return self.server.content

    @content.setter
    def content(self, value: str) -> None:
        self.server.content = value


class RetryTests(unittest.TestCase):
    def test_429_is_retried_then_succeeds(self):
        with StubServer() as stub:
            stub.plan.extend([429, 429])
            response = netclient.request(f"{stub.base}/x", method="POST", json_body={},
                                        retries=3, backoff=0.01)
            self.assertEqual(response.status, 200)
            self.assertEqual(len(stub.seen), 3)

    def test_exhausted_retries_return_the_last_status(self):
        with StubServer() as stub:
            stub.plan.extend([429, 429, 429])
            response = netclient.request(f"{stub.base}/x", method="POST", json_body={},
                                        retries=3, backoff=0.01)
            self.assertEqual(response.status, 429)
            self.assertEqual(len(stub.seen), 3)

    def test_client_errors_are_not_retried(self):
        with StubServer() as stub:
            stub.plan.extend([400, 400])
            response = netclient.request(f"{stub.base}/x", method="POST", json_body={},
                                        retries=3, backoff=0.01)
            self.assertEqual(response.status, 400)
            self.assertEqual(len(stub.seen), 1)

    def test_explicit_user_agent_is_always_sent(self):
        with StubServer() as stub:
            netclient.request(f"{stub.base}/x", method="POST", json_body={}, retries=1)
            self.assertEqual(stub.seen[0]["headers"]["User-Agent"], netclient.USER_AGENT)

    def test_upload_refuses_non_https(self):
        with self.assertRaises(ValueError):
            netclient.upload("http://127.0.0.1:9/put", b"data")


class TranslationClientTests(unittest.TestCase):
    def endpoint(self, stub: StubServer) -> translate.Endpoint:
        return translate.Endpoint(base_url=f"{stub.base}/v1", model="m", api_key="k",
                                  retries=1)

    def test_http_error_becomes_translation_error(self):
        with StubServer() as stub:
            stub.plan.extend([400])
            with self.assertRaises(errors.TranslationError) as case:
                translate.Client(self.endpoint(stub)).complete("hi")
            self.assertEqual(case.exception.status, 400)

    def test_empty_completion_is_rejected(self):
        with StubServer() as stub:
            stub.content = ""
            with self.assertRaises(errors.TranslationError):
                translate.Client(self.endpoint(stub)).complete("hi")

    def test_reasoning_block_is_stripped(self):
        with StubServer() as stub:
            stub.content = "<think>let me think</think>正文"
            self.assertEqual(translate.Client(self.endpoint(stub)).complete("hi"), "正文")

    def test_endpoint_url_and_opencode_session(self):
        endpoint = translate.Endpoint(base_url="https://opencode.ai/zen/go/v1/",
                                      model="m", api_key="k")
        self.assertEqual(endpoint.url, "https://opencode.ai/zen/go/v1/chat/completions")
        self.assertIn("x-opencode-session", endpoint.headers)
        full = translate.Endpoint(base_url="https://x/v1/chat/completions", model="m", api_key="k")
        self.assertEqual(full.url, "https://x/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
