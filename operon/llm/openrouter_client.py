"""OpenRouter adapter (OpenAI-compatible chat completions), for running Operon on other models.

The harness keeps its transcript in the Anthropic Messages format; this adapter
translates each request to the chat-completions format and the response back:

* assistant tool_use blocks  <-> assistant.tool_calls
* user tool_result blocks    ->  role "tool" messages (errors prefixed with "ERROR:")
* images in tool results     ->  a follow-up user message with data-URL images
                                 (or a text placeholder for text-only models)
* reasoning text             ->  logged as thinking; never sent back

Rate limits: 429s and 5xx are retried with backoff; the free tier's *daily* cap
is reported immediately because waiting will not help.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import time
import urllib.error
import urllib.request

from .base import ModelError, ModelResponse, Usage

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
STOP_REASONS = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}
EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
_call_ids = itertools.count(1)


class OpenRouterModel:
    def __init__(self, model: str, effort: str = "medium", max_tokens: int = 16000, api_key: str | None = None,
                 base_url: str = BASE_URL, timeout: float = 300.0, max_retries: int = 5) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._vision: bool | None = None

    # ------------------------------------------------------------------ public

    def create(self, *, system: str, tools: list[dict], messages: list[dict],
               effort: str | None = None) -> ModelResponse:
        body = {
            "model": self.model,
            "messages": to_chat_messages(system, messages, vision=self._supports_vision()),
            "tools": [to_chat_tool(t) for t in tools],
            "max_tokens": self.max_tokens,
            "reasoning": {"effort": EFFORT.get(effort or self.effort, "medium")},
            "usage": {"include": True},
        }
        if not tools:
            body.pop("tools")
        return from_chat_response(self._post("/chat/completions", body), self.model)

    # ------------------------------------------------------------------ transport

    def _post(self, path: str, body: dict) -> dict:
        if not self.api_key:
            raise ModelError("OPENROUTER_API_KEY is not set (see .env.example).")
        data = json.dumps(body).encode()
        delay = 4.0
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.base_url + path, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "X-Title": "Operon"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                status, retry_after = 200, None
            except urllib.error.HTTPError as exc:
                status, retry_after = exc.code, exc.headers.get("Retry-After")
                try:
                    payload = json.loads(exc.read().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = {"error": {"message": f"HTTP {exc.code}"}}
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == self.max_retries:
                    raise ModelError(f"Could not reach OpenRouter: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue

            error = payload.get("error") or _choice_error(payload)
            if not error:
                return payload
            message = str(error.get("message", error))
            code = int(error.get("code", status) or status) if str(error.get("code", status)).isdigit() else status
            if code == 429 and ("per-day" in message or "per day" in message or "daily" in message.lower()):
                raise ModelError(f"OpenRouter daily free-model quota exhausted: {message}")
            if code in (429, 500, 502, 503, 504, 408) and attempt < self.max_retries:
                wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else delay
                log.warning("OpenRouter %s (%s); retrying in %.0fs", code, message[:120], wait)
                time.sleep(min(wait, 60))
                delay *= 2
                continue
            if code == 401:
                raise ModelError("OpenRouter rejected the API key (401).")
            if code == 402:
                raise ModelError("OpenRouter: insufficient credits for this model (402).")
            raise ModelError(f"OpenRouter error {code}: {message}")
        raise ModelError("OpenRouter: retries exhausted")

    def _supports_vision(self) -> bool:
        if self._vision is None:
            try:
                with urllib.request.urlopen(f"{self.base_url}/models", timeout=30) as resp:
                    models = json.loads(resp.read())["data"]
                entry = next((m for m in models if m["id"] == self.model), {})
                self._vision = "image" in ((entry.get("architecture") or {}).get("input_modalities") or [])
            except (OSError, ValueError, KeyError):
                self._vision = False
        return self._vision


# ---------------------------------------------------------------------- format translation

def to_chat_tool(tool: dict) -> dict:
    return {"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""),
                                             "parameters": tool["input_schema"]}}


def to_chat_messages(system: str, messages: list[dict], vision: bool = True) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in content if b.get("type") == "tool_use"]
            if not text and not calls:
                continue  # a reasoning-only turn carries nothing the other side can use
            entry: dict = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        texts: list[str] = []
        images: list[dict] = []
        for block in content:
            kind = block.get("type")
            if kind == "tool_result":
                parts = block.get("content")
                if isinstance(parts, str):
                    parts = [{"type": "text", "text": parts}]
                body = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
                images += [p for p in parts if p.get("type") == "image"]
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                            "content": ("ERROR: " if block.get("is_error") else "") + body})
            elif kind == "text":
                texts.append(block["text"])
            elif kind == "image":
                images.append(block)
        if texts or images:
            parts: list[dict] = [{"type": "text", "text": t} for t in texts]
            for img in images:
                if vision:
                    src = img["source"]
                    parts.append({"type": "image_url",
                                  "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
                else:
                    parts.append({"type": "text", "text": "[screenshot omitted: this model has no image input]"})
            if images and not texts:
                parts.insert(0, {"type": "text", "text": "Screenshot from the previous tool call:"})
            out.append({"role": "user", "content": parts})
    return out


def from_chat_response(payload: dict, model: str) -> ModelResponse:
    choices = payload.get("choices") or []
    if not choices:
        raise ModelError(f"OpenRouter returned no choices: {str(payload)[:300]}")
    choice = choices[0]
    msg = choice.get("message") or {}
    blocks: list[dict] = []
    reasoning = msg.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        blocks.append({"type": "thinking", "thinking": reasoning.strip(), "signature": ""})
    if isinstance(msg.get("content"), str) and msg["content"].strip():
        blocks.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            args = {"__invalid_arguments__": str(raw)[:2000]}
        blocks.append({"type": "tool_use", "id": call.get("id") or f"call_{next(_call_ids)}",
                       "name": fn.get("name", ""), "input": args})
    stop = STOP_REASONS.get(choice.get("finish_reason") or "", "end_turn")
    if any(b["type"] == "tool_use" for b in blocks):
        stop = "tool_use"
    usage = payload.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return ModelResponse(
        content=blocks, stop_reason=stop, model=payload.get("model", model),
        usage=Usage(input_tokens=(usage.get("prompt_tokens") or 0) - cached,
                    output_tokens=usage.get("completion_tokens") or 0,
                    cache_read_input_tokens=cached, calls=1,
                    reported_cost_usd=float(usage.get("cost") or 0.0)),
    )


def _choice_error(payload: dict) -> dict | None:
    for choice in payload.get("choices") or []:
        if choice.get("error"):
            return choice["error"]
    return None
