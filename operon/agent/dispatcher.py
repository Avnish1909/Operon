"""Executes the model's tool calls: validation, retries, loop detection, logging."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections import Counter

from ..company import Vault
from ..runlog import Ledger
from ..tools.base import Toolbox, ToolError, ToolResult, TransientError, validate

log = logging.getLogger(__name__)
BACKOFF_S = (0.5, 1.5, 3.0)


class Dispatcher:
    def __init__(self, toolbox: Toolbox, ledger: Ledger, vault: Vault, actor: str = "operator") -> None:
        self.toolbox, self.ledger, self.vault, self.actor = toolbox, ledger, vault, actor
        self.failures: Counter[str] = Counter()

    def execute(self, block: dict) -> tuple[dict, ToolResult]:
        name, args = block["name"], block.get("input") or {}
        self.ledger.emit("tool_call", self.actor, name=name, input=args)
        started = time.monotonic()
        tool = self.toolbox.get(name)
        retries = 0
        if tool is None:
            result = ToolResult(f"Unknown tool '{name}'. Available tools: {', '.join(self.toolbox.names())}.",
                                is_error=True)
        elif "__invalid_arguments__" in args:
            result = ToolResult("Your arguments for this call were not valid JSON: "
                                f"{args['__invalid_arguments__'][:300]!r}. Call the tool again with a valid JSON object.",
                                is_error=True)
        elif errors := validate(tool.input_schema, args):
            result = ToolResult("Invalid input — " + "; ".join(errors) + ". Fix the arguments and call again.",
                                is_error=True)
        else:
            result, retries = self._run(tool, args)

        signature = name + json.dumps(args, sort_keys=True, default=str)
        if result.is_error:
            self.failures[signature] += 1
            if self.failures[signature] >= 3:
                result.text += ("\n[harness] This exact call has now failed "
                                f"{self.failures[signature]} times. Do not repeat it unchanged: take a fresh look "
                                "(snapshot, re-read the procedure), try a different approach, or ask for help.")
                self.ledger.emit("guard", self.actor, message=f"repeated failing call: {name}")
        result.text = self.vault.redact(result.text)
        self.ledger.emit("tool_result", self.actor, name=name, ok=not result.is_error,
                         summary=_summary(result.text), retries=retries,
                         duration_s=round(time.monotonic() - started, 2), detail=result.text[:4000])
        return self.to_block(block["id"], result), result

    def _run(self, tool, args: dict) -> tuple[ToolResult, int]:
        retries = 0
        while True:
            try:
                result = tool.handler(args)
                if retries:
                    result.text = f"(Recovered automatically after {retries} transient failure(s).)\n{result.text}"
                return result, retries
            except TransientError as exc:
                if tool.is_idempotent(args) and retries < len(BACKOFF_S):
                    time.sleep(BACKOFF_S[retries])
                    retries += 1
                    continue
                return ToolResult(f"{exc} (still failing after {retries} automatic retries). It may be temporary: "
                                  "wait and try again, or use another route.", is_error=True), retries
            except ToolError as exc:
                return ToolResult(str(exc), is_error=True), retries
            except Exception as exc:  # a bug in a tool must not kill the run
                log.exception("tool %s crashed", tool.name)
                return ToolResult(f"The tool failed unexpectedly: {type(exc).__name__}: {exc}", is_error=True), retries

    @staticmethod
    def to_block(tool_use_id: str, result: ToolResult) -> dict:
        content: list[dict] = [{"type": "text", "text": result.text or "(no output)"}]
        for media_type, data in result.images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                        "data": base64.standard_b64encode(data).decode()}})
        block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if result.is_error:
            block["is_error"] = True
        return block


def _summary(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "(no output)"
    head = lines[0]
    events = [line for line in lines[1:6] if line.startswith(("Event:", "HELD", "DENIED", "ALLOWED", "HTTP"))]
    return " | ".join([head] + events)
