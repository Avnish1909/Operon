"""Model interface.

The transcript format is the Anthropic Messages format (plain dicts), which every
part of the harness reads and writes. A ModelClient turns (system, tools,
messages) into the next assistant turn. Swapping providers means writing one
adapter; the scripted client in tests implements the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# USD per million tokens: (input, output, cache read, cache write 5m)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0
    reported_cost_usd: float = 0.0   # when the provider reports cost per call (OpenRouter)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.calls += other.calls
        self.reported_cost_usd += other.reported_cost_usd

    def cost_usd(self, model: str) -> float | None:
        price = PRICES.get(model)
        if not price:
            return round(self.reported_cost_usd, 4) if self.reported_cost_usd or "/" in model else None
        p_in, p_out, p_read, p_write = price
        return (self.input_tokens * p_in + self.output_tokens * p_out
                + self.cache_read_input_tokens * p_read + self.cache_creation_input_tokens * p_write) / 1e6

    def as_dict(self) -> dict:
        return {
            "calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


@dataclass
class ModelResponse:
    content: list[dict]
    stop_reason: str | None
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    refusal_category: str | None = None

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    @property
    def thinking_summaries(self) -> list[str]:
        return [b["thinking"].strip() for b in self.content
                if b.get("type") == "thinking" and (b.get("thinking") or "").strip()]


class ModelClient(Protocol):
    model: str

    def create(self, *, system: str, tools: list[dict], messages: list[dict],
               effort: str | None = None) -> ModelResponse: ...


class ModelError(RuntimeError):
    """Raised when the model provider fails in a way the run cannot continue from."""
