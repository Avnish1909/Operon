"""The OpenRouter adapter against a local fake chat-completions endpoint (no network, no quota)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from operon.llm.base import ModelError
from operon.llm.openrouter_client import OpenRouterModel, to_chat_messages

TRANSCRIPT = [
    {"role": "user", "content": "Ticket 1042"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "hmm", "signature": ""},
        {"type": "text", "text": "Reading the ticket."},
        {"type": "tool_use", "id": "call_1", "name": "api_request",
         "input": {"system": "desk", "method": "GET", "path": "/desk/api/tickets/1042"}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "is_error": True,
         "content": [{"type": "text", "text": "HTTP 503"},
                     {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]},
        {"type": "text", "text": "[operator status] step 1/90"}]},
]


def test_transcript_translation():
    out = to_chat_messages("SYS", TRANSCRIPT, vision=True)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "Reading the ticket."
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"])["path"] == "/desk/api/tickets/1042"
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": "ERROR: HTTP 503"}
    assert out[4]["role"] == "user"
    assert out[4]["content"][0]["text"].startswith("[operator status]")
    assert out[4]["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    text_only = to_chat_messages("SYS", TRANSCRIPT, vision=False)
    assert "screenshot omitted" in json.dumps(text_only[4])


class Fake(BaseHTTPRequestHandler):
    script: list[tuple[int, dict]] = []
    seen: list[dict] = []

    def do_GET(self):  # noqa: N802  (model catalog for the vision check)
        self._send(200, {"data": [{"id": "vendor/model:free", "architecture": {"input_modalities": ["text", "image"]}}]})

    def do_POST(self):  # noqa: N802
        Fake.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        status, payload = Fake.script.pop(0)
        self._send(status, payload)

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if status == 429:
            self.send_header("Retry-After", "0")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def fake():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Fake.script, Fake.seen = [], []
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


OK = {"model": "vendor/model:free", "choices": [{"finish_reason": "tool_calls", "message": {
    "role": "assistant", "content": "", "reasoning": "The ticket first.",
    "tool_calls": [{"id": "abc", "type": "function", "function": {"name": "note", "arguments": "{\"key\": \"k\", \"value\": \"v\"}"}},
                   {"id": "", "type": "function", "function": {"name": "note", "arguments": "{broken"}}]}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 400}, "cost": 0}}


def test_request_and_response(fake):
    Fake.script = [(429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min"}}), (200, OK)]
    model = OpenRouterModel("vendor/model:free", api_key="k", base_url=fake, max_retries=2)
    resp = model.create(system="SYS", tools=[{"name": "note", "description": "d",
                                              "input_schema": {"type": "object", "properties": {}}}],
                        messages=TRANSCRIPT, effort="xhigh")
    body = Fake.seen[-1]
    assert len(Fake.seen) == 2                                  # retried once after the 429
    assert body["reasoning"] == {"effort": "high"} and body["tools"][0]["function"]["name"] == "note"
    assert resp.stop_reason == "tool_use" and resp.thinking_summaries == ["The ticket first."]
    assert resp.tool_uses[0]["input"] == {"key": "k", "value": "v"}
    assert "__invalid_arguments__" in resp.tool_uses[1]["input"] and resp.tool_uses[1]["id"]
    assert resp.usage.cache_read_input_tokens == 400 and resp.usage.input_tokens == 600


def test_daily_quota_fails_fast(fake):
    Fake.script = [(429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})]
    model = OpenRouterModel("vendor/model:free", api_key="k", base_url=fake, max_retries=3)
    with pytest.raises(ModelError, match="daily"):
        model.create(system="S", tools=[], messages=[{"role": "user", "content": "hi"}])
    assert len(Fake.seen) == 1
