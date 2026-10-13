"""Tool contract shared by every capability the operator can use."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    """A tool failed in a way the model should see and adapt to (bad ref, validation error...)."""


class TransientError(ToolError):
    """A failure worth retrying automatically (5xx, timeouts) — only for idempotent tools."""


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    images: list[tuple[str, bytes]] = field(default_factory=list)   # (media type, bytes) shown to the model
    data: dict[str, Any] = field(default_factory=dict)              # structured details for logs and reports
    terminal: bool = False                                          # ends the run (verified or escalated)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], ToolResult]
    # Idempotent calls are retried automatically on TransientError; non-idempotent ones never are.
    # Either a flag or a function of the call's input (e.g. an API GET is idempotent, a POST is not).
    idempotent: bool | Callable[[dict], bool] = True

    def is_idempotent(self, args: dict) -> bool:
        return self.idempotent(args) if callable(self.idempotent) else self.idempotent

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class Toolbox:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def extend(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.add(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict]:
        # Deterministic order keeps the request prefix byte-identical across turns (prompt cache).
        return [self._tools[name].spec() for name in self._tools]


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate(input_schema: dict, data: Any, path: str = "input") -> list[str]:
    """Minimal JSON-schema check (types, required, enums, nested objects/arrays)."""
    errors: list[str] = []
    expected = input_schema.get("type")
    if expected:
        py = _TYPES[expected]
        ok = isinstance(data, py) and not (expected in ("integer", "number") and isinstance(data, bool))
        if not ok:
            return [f"{path} should be {expected}, got {type(data).__name__}"]
    if "enum" in input_schema and data not in input_schema["enum"]:
        errors.append(f"{path} must be one of {input_schema['enum']}, got {data!r}")
    if expected == "object":
        props = input_schema.get("properties", {})
        for req in input_schema.get("required", []):
            if req not in data:
                errors.append(f"{path}.{req} is required")
        for key, value in data.items():
            if key in props:
                errors.extend(validate(props[key], value, f"{path}.{key}"))
            elif input_schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key} is not a recognised field")
    if expected == "array" and "items" in input_schema:
        for i, item in enumerate(data):
            errors.extend(validate(input_schema["items"], item, f"{path}[{i}]"))
    return errors
