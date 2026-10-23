"""LLM access. The reviewer only needs one thing: a structured (JSON) answer to a prompt.

Structured output is forced through a tool call, so the model cannot reply with free text.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import anthropic


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


@dataclass
class LLMResponse:
    data: dict
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


class AnthropicClient:
    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-5-5",
        temperature: float | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        client: Any = None,  # injected in tests
    ):
        self.model = model
        self.temperature = temperature
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [
                {
                    "name": tool_name,
                    "description": "Report the structured review result.",
                    "input_schema": tool_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        try:
            resp = self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise LLMError("response was cut off at max_tokens; structured output is incomplete")

        usage = getattr(resp, "usage", None)
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                if not isinstance(block.input, dict):
                    raise LLMError("tool input was not a JSON object")
                return LLMResponse(
                    data=dict(block.input),
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                )
        raise LLMError("model did not return structured output")


def get_llm(settings) -> LLMClient | None:
    """None when no API key is configured; the reviewer then reports tool findings only."""
    if not settings.anthropic_api_key:
        return None
    return AnthropicClient(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
