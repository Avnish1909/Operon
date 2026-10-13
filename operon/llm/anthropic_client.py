"""Claude adapter (Anthropic Messages API).

Request shape, chosen deliberately:
* adaptive thinking with summarized display — the reasoning summaries are logged
  into the run trace so a reviewer can see *why* the operator acted;
* explicit effort (Opus 5.5 defaults to medium; we pass it so it is visible/tunable);
* a frozen system prompt + tool list with prompt caching — the harness keeps the
  message history append-only, so every turn re-reads a cached prefix and the
  thinking blocks the model produced stay valid;
* server-side refusal fallbacks, so a safety-classifier false positive degrades to
  another model instead of ending the run. Disabled automatically if rejected.
"""

from __future__ import annotations

import logging

import anthropic

from .base import ModelError, ModelResponse, Usage

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicModel:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", max_tokens: int = 16000,
                 refusal_fallbacks: bool = True, timeout: float = 300.0) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.refusal_fallbacks = refusal_fallbacks
        self.client = anthropic.Anthropic(max_retries=4, timeout=timeout)

    def create(self, *, system: str, tools: list[dict], messages: list[dict],
               effort: str | None = None) -> ModelResponse:
        params = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort or self.effort},
            cache_control={"type": "ephemeral"},
        )
        try:
            response = self._send(params)
        except anthropic.AuthenticationError as exc:
            raise ModelError("Anthropic authentication failed — set ANTHROPIC_API_KEY (see README).") from exc
        except anthropic.NotFoundError as exc:
            raise ModelError(f"Model '{self.model}' not found or not available to this API key.") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("Rate limited by the Anthropic API after retries; try again later.") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError("Could not reach the Anthropic API (network error).") from exc

        usage = response.usage
        category = None
        if response.stop_reason == "refusal" and getattr(response, "stop_details", None):
            category = getattr(response.stop_details, "category", None)
        return ModelResponse(
            content=[block.to_dict() for block in response.content],
            stop_reason=response.stop_reason,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                calls=1,
            ),
            model=response.model,
            refusal_category=category,
        )

    def _send(self, params: dict):
        if self.refusal_fallbacks:
            try:
                return self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
            except anthropic.BadRequestError as exc:
                if "fallback" not in str(exc).lower():
                    raise
                log.warning("Refusal fallbacks rejected by the API (%s); continuing without them.", exc)
                self.refusal_fallbacks = False
        return self.client.messages.create(**params)
