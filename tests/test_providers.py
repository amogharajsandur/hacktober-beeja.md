"""Provider behaviour against fake HTTP servers (httpx.MockTransport); no network, no model."""

import json

import httpx
import pytest

from beeja.discourse import FetchError, fetch_topic
from beeja.model_adapter import (
    ModelError,
    OllamaProvider,
    OpenAICompatibleProvider,
    _host,
    parse_json_loose,
    provider_from_env,
)

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


@pytest.mark.parametrize("text", [
    '{"answer": "ok"}',
    '  \n{"answer": "ok"}\n',
    '```json\n{"answer": "ok"}\n```',
    '<think>let me see {not json}</think>\n{"answer": "ok"}',
    'Sure! Here is the result:\n{"answer": "ok"}\nHope this helps.',
])
def test_parse_json_loose(text):
    assert parse_json_loose(text) == {"answer": "ok"}


@pytest.mark.parametrize("text", ["**question**: markdown only", "[1, 2]", ""])
def test_parse_json_loose_rejects_non_objects(text):
    with pytest.raises(ValueError):
        parse_json_loose(text)


@pytest.mark.parametrize("value,expected", [
    ("", "http://localhost:11434"),
    ("0.0.0.0", "http://localhost:11434"),
    ("my-server", "http://my-server:11434"),            # Ollama convention: bare host -> 11434
    ("http://my-server", "http://my-server"),           # explicit URL keeps its default port
    ("https://ollama.com", "https://ollama.com"),
    ("http://localhost", "http://localhost:11434"),
    ("http://[::1]:8080", "http://[::1]:8080"),
    ("http://10.0.0.5:11434/", "http://10.0.0.5:11434"),
])
def test_host_normalisation(value, expected):
    assert _host(value) == expected


def _ollama(handler, **kw):
    return OllamaProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), retries=1, **kw)


def test_cloud_model_gets_schema_in_prompt_and_markdown_is_repaired():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body["messages"])
        # first reply ignores the schema (as Ollama Cloud does), the repair reply complies
        content = "**answer**: ok" if len(seen) == 1 else '```json\n{"answer": "ok"}\n```'
        return httpx.Response(200, json={"message": {"content": content}})

    p = _ollama(handler, model="gemma4:31b-cloud")
    assert p.schema_in_prompt
    assert p.generate_json("sys", "prompt", SCHEMA) == {"answer": "ok"}
    assert "JSON Schema" in seen[0][1]["content"]
    assert seen[1][-2] == {"role": "assistant", "content": "**answer**: ok"}  # repair turn


def test_local_model_prompt_unchanged():
    def handler(req):
        body = json.loads(req.content)
        assert body["messages"][1]["content"] == "prompt" and body["format"] == SCHEMA
        return httpx.Response(200, json={"message": {"content": '{"answer": "x"}'}})

    p = _ollama(handler, model="qwen3:4b")
    assert not p.schema_in_prompt and p.generate_json("s", "prompt", SCHEMA) == {"answer": "x"}


def test_missing_model_gives_actionable_error_without_retries():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404, json={"error": "model 'gemma4:31b' not found"})

    with pytest.raises(ModelError, match="ollama pull gemma4:31b"):
        _ollama(handler, model="gemma4:31b").generate_json("s", "p", SCHEMA)
    assert len(calls) == 1


def test_api_key_only_sent_to_remote_hosts():
    def handler(req):
        return httpx.Response(200, json={"message": {"content": json.dumps({"auth": req.headers.get("authorization")})}})

    remote = OllamaProvider(host="https://ollama.com", api_key="k", retries=0)
    assert remote._client.headers.get("authorization") == "Bearer k"
    local = OllamaProvider(host="http://localhost:11434", api_key="k", retries=0)
    assert "authorization" not in local._client.headers


def test_openai_compatible_falls_back_when_structured_output_unsupported():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": 'Result: {"answer": "ok"}'}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    p = OpenAICompatibleProvider("qwen3-4b", "http://server:8000/v1/",
                                 client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.generate_json("s", "p", SCHEMA) == {"answer": "ok"}
    assert str(bodies[0]["model"]) == "qwen3-4b" and "JSON Schema" in bodies[0]["messages"][1]["content"]
    assert p.calls[-1]["prompt_tokens"] == 5 and p.name == "openai-compatible:qwen3-4b@server"


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("BEEJA_PROVIDER", "openai")
    monkeypatch.delenv("BEEJA_BASE_URL", raising=False)
    with pytest.raises(ModelError, match="BEEJA_BASE_URL"):
        provider_from_env()
    monkeypatch.setenv("BEEJA_BASE_URL", "http://server:8000/v1")
    monkeypatch.setenv("BEEJA_MODEL", "qwen3-4b")
    assert isinstance(provider_from_env(), OpenAICompatibleProvider)
    monkeypatch.setenv("BEEJA_PROVIDER", "ollama")
    monkeypatch.setenv("BEEJA_BASE_URL", "https://ollama.com")
    p = provider_from_env()
    assert isinstance(p, OllamaProvider) and p.host == "https://ollama.com" and p.schema_in_prompt
    monkeypatch.setenv("BEEJA_PROVIDER", "closedai")
    with pytest.raises(ModelError, match="unknown BEEJA_PROVIDER"):
        provider_from_env()


def test_discourse_fetch_rejects_non_topics(monkeypatch):
    real_client = httpx.Client

    def fake_client(**kw):
        def handler(req):
            if "404" in str(req.url):
                return httpx.Response(404)
            return httpx.Response(200, json={"not": "a topic"})
        kw.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "Client", fake_client)
    with pytest.raises(FetchError, match="not a Discourse topic"):
        fetch_topic("https://d.example", 1)
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_topic("https://d.example/404", 2)
