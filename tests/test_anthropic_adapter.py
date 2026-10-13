"""The Claude adapter, exercised through the real SDK against a local fake Messages endpoint.

Checks the request the operator actually sends (model, adaptive thinking, effort, prompt
caching, refusal-fallback beta) and that responses are parsed into the harness format.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from operon.llm.anthropic_client import FALLBACK_BETA, AnthropicModel

CANNED = {
    "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
    "stop_reason": "tool_use", "stop_sequence": None,
    "content": [
        {"type": "thinking", "thinking": "Need the ticket first.", "signature": "sig123"},
        {"type": "text", "text": "Looking up the ticket."},
        {"type": "tool_use", "id": "toolu_1", "name": "api_request",
         "input": {"system": "desk", "method": "GET", "path": "/desk/api/tickets/1042"}},
    ],
    "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 9000,
              "cache_creation_input_tokens": 0},
}


class FakeAPI(BaseHTTPRequestHandler):
    requests: list[dict] = []
    reject_fallbacks = False

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if FakeAPI.reject_fallbacks and "fallbacks" in body:
            payload = {"type": "error", "error": {"type": "invalid_request_error",
                                                  "message": "fallbacks: Extra inputs are not permitted"}}
            self._send(400, payload)
            return
        self._send(200, CANNED)

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def fake_api(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeAPI.requests, FakeAPI.reject_fallbacks = [], False
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    yield FakeAPI
    srv.shutdown()


def test_request_shape_and_parsing(fake_api):
    model = AnthropicModel("claude-opus-5-5", effort="medium")
    messages = [{"role": "user", "content": "Ticket 1042"}]
    resp = model.create(system="SYSTEM", tools=[{"name": "api_request", "description": "d",
                                                 "input_schema": {"type": "object", "properties": {}}}],
                        messages=messages, effort="high")
    sent = fake_api.requests[0]
    body = sent["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in sent["headers"].get("anthropic-beta", "")
    assert "tool_choice" not in body          # forced tool choice is rejected by Opus 5.5

    assert resp.stop_reason == "tool_use"
    assert resp.tool_uses[0]["input"]["path"] == "/desk/api/tickets/1042"
    assert resp.thinking_summaries == ["Need the ticket first."]
    assert resp.content[0]["signature"] == "sig123"  # thinking blocks are kept verbatim for replay
    assert resp.usage.cache_read_input_tokens == 9000
    # The parsed content can be sent back as-is on the next turn.
    json.dumps(resp.content)


def test_fallbacks_disabled_when_rejected(fake_api):
    fake_api.reject_fallbacks = True
    model = AnthropicModel("claude-opus-5-5")
    resp = model.create(system="S", tools=[], messages=[{"role": "user", "content": "hi"}])
    assert resp.stop_reason == "tool_use"
    assert model.refusal_fallbacks is False
    assert "fallbacks" not in fake_api.requests[-1]["body"]
