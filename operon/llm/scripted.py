"""A deterministic model for tests: replays scripted turns.

Each turn is a function that receives the transcript so far and returns the
assistant's content blocks (usually tool calls). Turns can read earlier tool
results — e.g. to find the ref of the "Issue refund" button — so a script
exercises the real tools, policy gate and verifier without a network model.
This is test scaffolding for the harness, not a stand-in for the agent's judgment.
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Callable

from .base import ModelResponse, Usage

Turn = Callable[[list[dict]], list[dict]]
_ids = itertools.count(1)


def tool(name: str, **inputs) -> dict:
    return {"type": "tool_use", "id": f"toolu_scripted_{next(_ids)}", "name": name, "input": inputs}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def last_tool_result(messages: list[dict]) -> str:
    """Text of the most recent tool_result in the transcript."""
    for msg in reversed(messages):
        if msg["role"] != "user" or not isinstance(msg["content"], list):
            continue
        for block in msg["content"]:
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, str):
                    return content
                return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    return ""


def find_ref(page_text: str, pattern: str) -> str:
    """Find the [eN] ref on a line matching a regex, e.g. find_ref(t, r'button "Issue refund"')."""
    for line in page_text.splitlines():
        hit = re.search(pattern, line)
        if not hit:
            continue
        refs = list(re.finditer(r"\[(e\d+)\]", line))
        before = [r for r in refs if r.start() <= hit.start()]
        if before:
            return before[-1].group(1)   # the ref that introduces the matched element
        if refs:
            return refs[0].group(1)
    raise AssertionError(f"no ref matching {pattern!r} in:\n{page_text[:2000]}")


class ScriptedModel:
    def __init__(self, turns: list[Turn | list[dict]], model: str = "scripted") -> None:
        self.turns = list(turns)
        self.model = model
        self.calls: list[list[dict]] = []

    def create(self, *, system: str, tools: list[dict], messages: list[dict],
               effort: str | None = None) -> ModelResponse:
        self.calls.append(json.loads(json.dumps(messages, default=str)))
        if not self.turns:
            return ModelResponse(content=[text("(script exhausted)")], stop_reason="end_turn",
                                 usage=Usage(calls=1), model=self.model)
        turn = self.turns.pop(0)
        content = turn(messages) if callable(turn) else turn
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return ModelResponse(content=content, stop_reason=stop, usage=Usage(calls=1), model=self.model)
