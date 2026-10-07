"""Provider HTTP transport tests against a local fake server."""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast

from localization.models import Batch, TranslationUnit
from localization.provider import (
    MAX_TOKENS_CEILING,
    Provider,
    ProviderConfig,
    ProviderStopError,
    TransientProviderError,
    build_request_body,
)


class HarnessServer(HTTPServer):
    """HTTPServer with typed harness state for the fake endpoint."""

    def __init__(self, requests, responses):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.lock = threading.Lock()
        self.requests: list[dict] = requests
        self.responses: list = responses


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = cast(HarnessServer, self.server)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        with server.lock:
            server.requests.append(
                {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(body) if body else None,
                }
            )
            if server.responses:
                status, payload = server.responses.pop(0)
            else:
                status, payload = 200, {
                    "choices": [
                        {"message": {"content": '{"translations": {}}'}, "finish_reason": "stop"}
                    ]
                }
        self.send_response(status)
        if isinstance(payload, bytes):
            self.send_header("Content-Type", "text/event-stream")
        else:
            self.send_header("Content-Type", "application/json")
        if status == 429:
            self.send_header("Retry-After", "0")
        self.end_headers()
        out = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.wfile.write(out)

    def log_message(self, format, *args):
        del format, args


class FakeServer:
    """Scripted OpenAI-compatible endpoint on localhost, streaming-capable."""

    def __init__(self, responses):
        self.requests: list[dict] = []
        self.httpd = HarnessServer(self.requests, list(responses))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_unit(i: int) -> TranslationUnit:
    return TranslationUnit(unit_id=f"u{i}", source="Hello world", source_hash=f"h{i}", path="x.json")


def make_batch() -> Batch:
    return Batch(batch_id="b1", units=[make_unit(0), make_unit(1)])


def cfg_for(server: FakeServer, **kw) -> ProviderConfig:
    return ProviderConfig(
        base_url=f"http://127.0.0.1:{server.port}/v1",
        model="deepseek-v4-flash",
        api_key="secret-test-key",
        **kw,
    )


EMPTY_JSON = {"choices": [{"message": {"content": '{"translations": {}}'}, "finish_reason": "stop"}]}
SSE_STREAM = (
    'data: {"choices":[{"delta":{"content":"{\\"translations\\":"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
).encode()


class ProviderHttpTests(unittest.TestCase):
    def setUp(self):
        self.servers: list[FakeServer] = []

    def _server(self, responses) -> FakeServer:
        server = FakeServer(responses)
        self.servers.append(server)
        return server

    def tearDown(self):
        for server in self.servers:
            server.stop()

    def test_request_shape_exact(self):
        server = self._server([(200, SSE_STREAM)])
        provider = Provider(cfg_for(server, stream=True))
        response = provider.complete([{"role": "user", "content": "hello"}], make_batch(), 1)
        assert response.content == '{"translations":'
        body = server.requests[0]["body"]
        assert set(body) == {
            "model", "stream", "max_tokens", "response_format", "thinking",
            "reasoning_effort", "messages",
        }, body
        assert body["model"] == "deepseek-v4-flash"
        assert body["stream"] is True
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "max"
        assert "temperature" not in body and "top_p" not in body
        assert "chat_template_kwargs" not in body and "enable_thinking" not in body
        headers = server.requests[0]["headers"]
        assert headers["user-agent"] == "Translation Pipeline The Iron Oath FI Transparency"
        assert headers["x-request-id"] == "run-b1-1"
        assert headers["authorization"].startswith("Bearer ")

    def test_max_tokens_ceiling_rejected_before_request(self):
        def cfg(tokens: int) -> ProviderConfig:
            return ProviderConfig(
                base_url="https://freeinference.org/v1",
                model="deepseek-v4-flash",
                api_key="x",
                max_tokens=tokens,
            )

        with self.assertRaises(ValueError):
            Provider(cfg(MAX_TOKENS_CEILING + 1))
        Provider(cfg(MAX_TOKENS_CEILING))

    def test_429_retries_then_succeeds(self):
        server = self._server([(429, {"error": "slow down"}), (200, EMPTY_JSON)])
        provider = Provider(cfg_for(server, stream=False))
        started = time.monotonic()
        response = provider.complete([{"role": "user", "content": "x"}], make_batch(), 0)
        # Retry-After: 0 means an immediate retry, well below the default
        # exponential backoff (~1-1.3s for the first retry).
        assert time.monotonic() - started < 0.5
        assert response.content == '{"translations": {}}'
        assert len(server.requests) == 2

    def test_401_stops_without_retry(self):
        server = self._server([(401, {"error": "no"})])
        provider = Provider(cfg_for(server))
        with self.assertRaises(ProviderStopError):
            provider.complete([{"role": "user", "content": "x"}], make_batch(), 0)
        assert len(server.requests) == 1

    def test_404_stops_without_retry(self):
        server = self._server([(404, {"error": "model not found"})])
        provider = Provider(cfg_for(server))
        with self.assertRaises(ProviderStopError):
            provider.complete([{"role": "user", "content": "x"}], make_batch(), 0)
        assert len(server.requests) == 1

    def test_empty_final_content_fails(self):
        server = self._server(
            [(200, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})]
        )
        provider = Provider(cfg_for(server, retries=0))
        with self.assertRaises(TransientProviderError):
            provider.complete([{"role": "user", "content": "x"}], make_batch(), 0)

    def test_request_body_none_effort(self):
        body = build_request_body(ProviderConfig(
            base_url="https://freeinference.org/v1",
            model="deepseek-v4-flash",
            api_key="x",
            reasoning_effort="none",
        ))
        assert body["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body


if __name__ == "__main__":
    unittest.main()