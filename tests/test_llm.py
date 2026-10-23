from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.config import Settings
from app.llm import AnthropicClient, LLMError, get_llm

TOOL = "report_findings"
SCHEMA = {"type": "object", "properties": {}}


class FakeAnthropic:
    def __init__(self, content=None, stop_reason="tool_use", error=None):
        self.requests: list[dict] = []
        self._content = content
        self._stop = stop_reason
        self._error = error
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error:
            raise self._error
        content = self._content or [
            SimpleNamespace(type="text", text="thinking"),
            SimpleNamespace(type="tool_use", name=TOOL, input={"summary": "ok", "findings": []}),
        ]
        return SimpleNamespace(
            content=content,
            stop_reason=self._stop,
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )


def call(client: AnthropicClient):
    return client.complete_json(system="sys", user="usr", tool_name=TOOL, tool_schema=SCHEMA)


def test_request_forces_the_tool_and_parses_usage():
    fake = FakeAnthropic()
    resp = call(AnthropicClient(model="m-1", client=fake))
    req = fake.requests[0]
    assert req["model"] == "m-1" and req["system"] == "sys"
    assert req["messages"] == [{"role": "user", "content": "usr"}]
    assert req["tool_choice"] == {"type": "tool", "name": TOOL}
    assert req["tools"][0]["name"] == TOOL and req["tools"][0]["input_schema"] == SCHEMA
    assert "temperature" not in req
    assert resp.data == {"summary": "ok", "findings": []}
    assert (resp.input_tokens, resp.output_tokens) == (11, 7)


def test_temperature_is_sent_only_when_configured():
    fake = FakeAnthropic()
    call(AnthropicClient(temperature=0.0, client=fake))
    assert fake.requests[0]["temperature"] == 0.0


def test_api_errors_become_llm_errors():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    with pytest.raises(LLMError, match="APIConnectionError"):
        call(AnthropicClient(client=FakeAnthropic(error=err)))


def test_missing_truncated_or_malformed_output_is_an_error():
    only_text = [SimpleNamespace(type="text", text="no tool call")]
    with pytest.raises(LLMError, match="structured output"):
        call(AnthropicClient(client=FakeAnthropic(content=only_text)))
    with pytest.raises(LLMError, match="cut off"):
        call(AnthropicClient(client=FakeAnthropic(stop_reason="max_tokens")))
    bad = [SimpleNamespace(type="tool_use", name=TOOL, input="a string")]
    with pytest.raises(LLMError, match="JSON object"):
        call(AnthropicClient(client=FakeAnthropic(content=bad)))


def test_get_llm_needs_a_key():
    assert get_llm(Settings(anthropic_api_key="")) is None
    client = get_llm(Settings(anthropic_api_key="sk-test", llm_model="custom-model"))
    assert isinstance(client, AnthropicClient) and client.model == "custom-model"
