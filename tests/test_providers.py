"""Provider behaviour against fake HTTP servers (httpx.MockTransport); no network, no model."""

import json

import httpx
import pytest

from beeja.discourse import FetchError, fetch_topic
from beeja.model_adapter import (
    AnthropicProvider,
    ModelError,
    OllamaProvider,
    OpenAICompatibleProvider,
    _host,
    detect_provider,
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

    p = OpenAICompatibleProvider("qwen3-4b", "http://server:8000/v1/", label="openai-compatible",
                                 client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.generate_json("s", "p", SCHEMA) == {"answer": "ok"}
    assert str(bodies[0]["model"]) == "qwen3-4b" and "JSON Schema" in bodies[0]["messages"][1]["content"]
    assert p.calls[-1]["prompt_tokens"] == 5 and p.name == "openai-compatible:qwen3-4b"


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for v in ("BEEJA_PROVIDER", "BEEJA_MODEL", "BEEJA_BASE_URL", "BEEJA_API_KEY", "OLLAMA_API_KEY", "OLLAMA_HOST",
              "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env
    return monkeypatch


@pytest.mark.parametrize("key,expected", [
    ("sk-ant-api03-x", "anthropic"), ("sk-or-v1-x", "openrouter"), ("gsk_x", "groq"), ("AIzaSyx", "gemini"),
    ("xai-x", "xai"), ("sk-proj-x", "openai"), ("sk-x", "openai"), ("hf_x", "huggingface"), ("abc123", None),
])
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected


def test_no_key_means_local_ollama(clean_env):
    p = provider_from_env()
    assert isinstance(p, OllamaProvider) and p.model == "qwen3:4b" and p.host == "http://localhost:11434"


def test_any_key_picks_provider_and_default_model(clean_env):
    clean_env.setenv("BEEJA_API_KEY", "gsk_abc")
    p = provider_from_env()
    assert isinstance(p, OpenAICompatibleProvider) and p.name == "groq:llama-3.3-70b-versatile"
    assert p.base_url == "https://api.groq.com/openai/v1" and p._client.headers["authorization"] == "Bearer gsk_abc"
    clean_env.setenv("BEEJA_API_KEY", "sk-ant-abc")
    clean_env.setenv("BEEJA_MODEL", "claude-haiku-5-5")
    a = provider_from_env()
    assert isinstance(a, AnthropicProvider) and a.model == "claude-haiku-5-5"


def test_unknown_key_prefix_asks_for_provider(clean_env):
    clean_env.setenv("BEEJA_API_KEY", "tgp_v1_whatever")
    with pytest.raises(ModelError, match="set BEEJA_PROVIDER"):
        provider_from_env()
    clean_env.setenv("BEEJA_PROVIDER", "together")
    assert provider_from_env().name.startswith("together:")


def test_provider_standard_key_variable_and_errors(clean_env):
    clean_env.setenv("BEEJA_PROVIDER", "groq")
    with pytest.raises(ModelError, match="GROQ_API_KEY"):
        provider_from_env()
    clean_env.setenv("GROQ_API_KEY", "gsk_x")
    assert provider_from_env().name.startswith("groq:")
    clean_env.setenv("BEEJA_PROVIDER", "custom")
    clean_env.setenv("BEEJA_MODEL", "qwen3-4b")
    with pytest.raises(ModelError, match="BEEJA_BASE_URL"):
        provider_from_env()
    clean_env.setenv("BEEJA_BASE_URL", "http://server:8000/v1")
    assert isinstance(provider_from_env(), OpenAICompatibleProvider)  # no key needed for a private server
    clean_env.setenv("BEEJA_PROVIDER", "closedai")
    with pytest.raises(ModelError, match="unknown BEEJA_PROVIDER"):
        provider_from_env()


def test_ollama_cloud_preset(clean_env):
    clean_env.setenv("BEEJA_PROVIDER", "ollama-cloud")
    with pytest.raises(ModelError, match="API key"):
        provider_from_env()
    clean_env.setenv("OLLAMA_API_KEY", "k")
    p = provider_from_env()
    assert isinstance(p, OllamaProvider) and p.host == "https://ollama.com" and p.schema_in_prompt
    assert p.model == "gemma4:31b"


def test_dotenv_file_is_read_but_real_env_wins(clean_env, tmp_path):
    (tmp_path / ".env").write_text("# keys\nexport BEEJA_API_KEY='gsk_fromfile'\nBEEJA_MODEL=from-file\n")
    clean_env.setenv("BEEJA_MODEL", "from-env")
    p = provider_from_env()
    assert p.name == "groq:from-env"


def test_anthropic_provider_request_shape():
    seen = {}

    def handler(req):
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": '```json\n{"answer": "ok"}\n```'}],
                                         "usage": {"input_tokens": 9, "output_tokens": 4}})

    p = AnthropicProvider("claude-x", "sk-ant-k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.generate_json("sys", "prompt", SCHEMA) == {"answer": "ok"}
    assert seen["body"]["system"] == "sys" and seen["body"]["messages"][0]["role"] == "user"
    assert "JSON Schema" in seen["body"]["messages"][0]["content"]
    assert p.calls[-1]["prompt_tokens"] == 9


def test_cli_providers_lists_presets(clean_env, capsys):
    from beeja.cli import main

    assert main(["providers", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["active"] == "ollama" and {"groq", "anthropic", "openai-compatible"} <= {r["provider"] for r in out["providers"]}


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
