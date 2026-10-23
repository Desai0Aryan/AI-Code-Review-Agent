"""Test doubles for the LLM."""

from app.llm import LLMResponse


class FakeLLM:
    """Replays scripted answers, one per call. An Exception in the script is raised."""

    def __init__(self, *responses, input_tokens: int = 100, output_tokens: int = 20):
        self.queue = list(responses)
        self.calls: list[dict] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def complete_json(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        item = self.queue.pop(0) if self.queue else {"summary": "ok", "findings": []}
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(item, self.input_tokens, self.output_tokens)
