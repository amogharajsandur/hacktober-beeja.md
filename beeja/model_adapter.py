"""Open-weight model providers behind one small contract.

``generate_json(system, prompt, schema) -> dict`` returns a parsed JSON object or raises
``ModelError``. Providers:

* ``OllamaProvider`` - an Ollama server: local (default), the organiser's server, or Ollama
  Cloud (``*-cloud`` model tags through the local daemon, or ``https://ollama.com`` directly).
* ``OpenAICompatibleProvider`` - any OpenAI-compatible ``/v1/chat/completions`` server serving an
  open-weight model (vLLM, llama.cpp server, LM Studio, LiteLLM, an organiser endpoint ...).
* ``RecordingProvider`` - wraps a live provider and records every exchange to a JSONL file.
* ``ReplayProvider`` - replays a recording made from real inference; an unknown prompt is an
  error, never a silent fallback. CI uses this so runs are deterministic and offline.

Local Ollama enforces the JSON schema with grammar-constrained decoding. Hosted endpoints often
do not (Ollama Cloud ignores ``format``), so for those the schema is also stated in the prompt,
replies are parsed leniently (code fences, ``<think>`` blocks, preamble) and one repair turn is
attempted before failing.

Configuration (environment, or a ``.env`` file): ``BEEJA_PROVIDER`` (``auto`` | a name in
``PRESETS`` | ``replay``; ``auto`` = local Ollama, or the provider detected from ``BEEJA_API_KEY``),
``BEEJA_MODEL`` (default ``qwen3:4b``), ``BEEJA_BASE_URL`` / ``OLLAMA_HOST`` (server URL),
``BEEJA_API_KEY`` (or ``OLLAMA_API_KEY``; only sent to non-local hosts), ``BEEJA_SCHEMA_IN_PROMPT``
(``auto`` | ``1`` | ``0``), ``BEEJA_REPLAY_FILE``, ``BEEJA_TIMEOUT`` seconds, ``BEEJA_NUM_CTX``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

DEFAULT_MODEL = "qwen3:4b"
DEFAULT_HOST = "http://localhost:11434"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class ModelError(Exception):
    pass


class ModelProvider(Protocol):
    name: str

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def exchange_key(model: str, system: str, prompt: str, schema: dict[str, Any]) -> str:
    blob = json.dumps([model, system, prompt, schema], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def parse_json_loose(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model reply that may wrap it in fences, thinking or prose."""
    text = _THINK.sub("", text or "").strip()
    candidates = [m.group(1).strip() for m in _FENCE.finditer(text)] + [text]
    dec = json.JSONDecoder()
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            obj = None
            for i, ch in enumerate(c):
                if ch == "{":
                    try:
                        obj, _ = dec.raw_decode(c, i)
                        break
                    except json.JSONDecodeError:
                        continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("reply contains no JSON object")


def schema_instruction(schema: dict[str, Any]) -> str:
    return ("Respond with ONLY one JSON object - no Markdown, no code fences, no explanation - "
            "that validates against this JSON Schema:\n" + json.dumps(schema, separators=(",", ":")))


def is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in _LOCAL_HOSTS


class _ChatProvider:
    """Shared retry / parse / repair loop. Subclasses implement ``_chat``."""

    model: str
    name: str
    retries: int
    schema_in_prompt: bool

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        user = prompt + ("\n\n" + schema_instruction(schema) if self.schema_in_prompt else "")
        base = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            t0 = time.time()
            try:
                content, stats = self._chat(base, schema)
                try:
                    parsed = parse_json_loose(content)
                except ValueError:
                    # one repair turn: show the model its own reply and ask for the JSON only
                    repair = base + [{"role": "assistant", "content": content[:6000]},
                                     {"role": "user", "content": "That reply was not valid JSON. " + schema_instruction(schema)}]
                    content, stats = self._chat(repair, schema)
                    parsed = parse_json_loose(content)
                self.calls.append({"seconds": round(time.time() - t0, 2), **stats})
                return parsed
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                detail = exc.response.text[:300].strip()
                last = ModelError(f"HTTP {code} from {exc.request.url}: {detail}")
                if code == 404 and "not found" in detail.lower():
                    raise ModelError(f"{self.name}: model not found on the server ({detail}). "
                                     f"For local Ollama run `ollama pull {self.model}`.") from exc
                if code in (400, 401, 403, 404):
                    raise last from exc  # not transient: retrying cannot help
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                last = exc
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
        raise ModelError(f"{self.name} failed after {self.retries + 1} attempts: {last}")


class OllamaProvider(_ChatProvider):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        *,
        timeout: float = 600.0,
        retries: int = 2,
        num_ctx: int = 8192,
        seed: int = 7,
        api_key: str | None = None,
        schema_in_prompt: bool | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.name = f"ollama:{model}"
        self.host = host.rstrip("/")
        self.retries = retries
        self.num_ctx = num_ctx
        self.seed = seed
        hosted = model.endswith("-cloud") or not is_local(self.host)
        self.schema_in_prompt = hosted if schema_in_prompt is None else schema_in_prompt
        headers = {"Authorization": f"Bearer {api_key}"} if api_key and not is_local(self.host) else {}
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def _chat(self, messages, schema):
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0, "seed": self.seed, "num_ctx": self.num_ctx},
            "messages": messages,
        }
        r = self._client.post(f"{self.host}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        return data["message"]["content"], {"prompt_tokens": data.get("prompt_eval_count"),
                                            "output_tokens": data.get("eval_count")}


class OpenAICompatibleProvider(_ChatProvider):
    """``POST {base_url}/chat/completions``. Asks for ``json_schema`` output and falls back
    to plain completion (schema in prompt + lenient parsing) if the server rejects it."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 600.0,
        retries: int = 2,
        seed: int = 7,
        label: str | None = None,
        extra_headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = f"{label or 'openai-compatible'}:{model}"
        self.retries = retries
        self.seed = seed
        self.schema_in_prompt = True
        self._structured = True
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        headers.update(extra_headers or {})
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def _chat(self, messages, schema):
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if self._structured:
            payload["seed"] = self.seed
        if self._structured:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "beeja", "schema": schema, "strict": False}}
        r = self._client.post(f"{self.base_url}/chat/completions", json=payload)
        if r.status_code in (400, 422) and self._structured:
            # server rejects structured output (or seed): fall back to schema-in-prompt + lenient parsing
            self._structured = False
            payload.pop("response_format")
            payload.pop("seed", None)
            r = self._client.post(f"{self.base_url}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        return data["choices"][0]["message"]["content"] or "", {
            "prompt_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}


class AnthropicProvider(_ChatProvider):
    """Anthropic Messages API (``POST /v1/messages``); schema in prompt + lenient parsing."""

    def __init__(self, model: str, api_key: str, *, base_url: str = "https://api.anthropic.com",
                 timeout: float = 600.0, retries: int = 2, client: httpx.Client | None = None) -> None:
        super().__init__()
        self.model = model
        self.name = f"anthropic:{model}"
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.schema_in_prompt = True
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def _chat(self, messages, schema):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        payload = {"model": self.model, "max_tokens": 8192, "temperature": 0, "system": system,
                   "messages": [m for m in messages if m["role"] != "system"]}
        r = self._client.post(f"{self.base_url}/v1/messages", json=payload)
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return text, {"prompt_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}


class RecordingProvider:
    """Calls the live model and records each exchange.

    If the recording already holds a response for the *identical* (model, system, prompt, schema)
    it is reused instead of calling the model again, so re-running after a change to a later
    pipeline stage only spends inference on prompts that changed. ``compact()`` rewrites the
    file with exactly the exchanges used by the latest run, in call order.
    """

    def __init__(self, inner: Any, path: Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._existing: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    if rec.get("model") == inner.model:
                        self._existing[rec["key"]] = rec
        self.used: list[dict[str, Any]] = []
        self.reused = 0

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = exchange_key(self.inner.model, system, prompt, schema)
        if key in self._existing:
            self.reused += 1
            rec = self._existing[key]
        else:
            out = self.inner.generate_json(system, prompt, schema)
            rec = {"key": key, "model": self.inner.model, "response": out,
                   "stats": self.inner.calls[-1] if self.inner.calls else None}
            self._existing[key] = rec
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self.used.append(rec)
        return rec["response"]

    def compact(self) -> None:
        seen, lines = set(), []
        for rec in self.used:
            if rec["key"] not in seen:
                seen.add(rec["key"])
                lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class ReplayProvider:
    def __init__(self, path: Path, model: str = DEFAULT_MODEL) -> None:
        self.path = Path(path)
        self.model = model
        self.name = f"replay:{model}"
        if not self.path.is_file():
            raise ModelError(f"replay file not found: {self.path}")
        self._db: dict[str, dict[str, Any]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                self._db[rec["key"]] = rec["response"]

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = exchange_key(self.model, system, prompt, schema)
        if key not in self._db:
            raise ModelError(f"no recorded response for this prompt in {self.path} (key {key[:12]})")
        return self._db[key]


# name: (kind, base_url, key env vars, default model, open_weight_default)
PRESETS: dict[str, tuple[str, str | None, tuple[str, ...], str, bool]] = {
    "ollama":       ("ollama", None, (), DEFAULT_MODEL, True),
    "ollama-cloud": ("ollama", "https://ollama.com", ("OLLAMA_API_KEY",), "gemma4:31b", True),
    "openai":       ("openai", "https://api.openai.com/v1", ("OPENAI_API_KEY",), "gpt-4o-mini", False),
    "anthropic":    ("anthropic", "https://api.anthropic.com", ("ANTHROPIC_API_KEY",), "claude-sonnet-5-5", False),
    "gemini":       ("openai", "https://generativelanguage.googleapis.com/v1beta/openai",
                     ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "gemini-2.5-flash", False),
    "groq":         ("openai", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",), "llama-3.3-70b-versatile", True),
    "openrouter":   ("openai", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",),
                     "meta-llama/llama-3.3-70b-instruct", True),
    "together":     ("openai", "https://api.together.xyz/v1", ("TOGETHER_API_KEY",),
                     "meta-llama/Llama-3.3-70B-Instruct-Turbo", True),
    "fireworks":    ("openai", "https://api.fireworks.ai/inference/v1", ("FIREWORKS_API_KEY",),
                     "accounts/fireworks/models/llama-v3p3-70b-instruct", True),
    "cerebras":     ("openai", "https://api.cerebras.ai/v1", ("CEREBRAS_API_KEY",), "llama-3.3-70b", True),
    "deepseek":     ("openai", "https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",), "deepseek-chat", True),
    "mistral":      ("openai", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",), "mistral-small-latest", True),
    "xai":          ("openai", "https://api.x.ai/v1", ("XAI_API_KEY",), "grok-3-mini", False),
    "huggingface":  ("openai", "https://router.huggingface.co/v1", ("HF_TOKEN",), "Qwen/Qwen3-32B", True),
    "openai-compatible": ("openai", None, (), "", True),  # any server: set BEEJA_BASE_URL
}
ALIASES = {"local": "ollama", "google": "gemini", "claude": "anthropic", "hf": "huggingface", "custom": "openai-compatible"}

# key prefix -> preset, most specific first
_KEY_PREFIXES = [("sk-ant-", "anthropic"), ("sk-or-", "openrouter"), ("gsk_", "groq"), ("AIza", "gemini"),
                 ("xai-", "xai"), ("fw_", "fireworks"), ("csk-", "cerebras"), ("hf_", "huggingface"),
                 ("sk-proj-", "openai"), ("sk-", "openai")]


def detect_provider(api_key: str) -> str | None:
    """Guess the provider from the key's prefix (None if unknown, e.g. Together/Mistral/Ollama keys)."""
    for prefix, name in _KEY_PREFIXES:
        if api_key.startswith(prefix):
            return name
    return None


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env support (KEY=VALUE lines); real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _schema_flag() -> bool | None:
    v = os.environ.get("BEEJA_SCHEMA_IN_PROMPT", "auto").strip().lower()
    return None if v in ("", "auto") else v in ("1", "true", "yes")


def resolve_provider_name() -> str:
    name = os.environ.get("BEEJA_PROVIDER", "auto").strip().lower() or "auto"
    name = ALIASES.get(name, name)
    if name == "auto":
        key = os.environ.get("BEEJA_API_KEY", "").strip()
        if not key:
            return "ollama"  # no key: local model
        if os.environ.get("BEEJA_BASE_URL"):
            return "openai-compatible"
        found = detect_provider(key)
        if found is None:
            raise ModelError("cannot tell which provider BEEJA_API_KEY belongs to; set BEEJA_PROVIDER "
                             f"(one of: {', '.join(sorted(PRESETS))})")
        return found
    if name not in PRESETS and name != "replay":
        raise ModelError(f"unknown BEEJA_PROVIDER {name!r} (use one of: auto, replay, {', '.join(sorted(PRESETS))})")
    return name


def provider_from_env(record_to: Path | None = None, replay_from: Path | None = None) -> ModelProvider:
    """Build a provider from the environment.

    * No key: local Ollama (``qwen3:4b``).
    * ``BEEJA_API_KEY`` alone: provider detected from the key prefix, with that provider's default model.
    * ``BEEJA_PROVIDER`` names a preset (see ``PRESETS``); its standard key variable also works
      (e.g. ``GROQ_API_KEY``). ``BEEJA_MODEL`` / ``BEEJA_BASE_URL`` override model and endpoint.
    """
    load_dotenv()
    replay = replay_from or (Path(os.environ["BEEJA_REPLAY_FILE"]) if os.environ.get("BEEJA_REPLAY_FILE") else None)
    name = "replay" if replay is not None else resolve_provider_name()
    if name == "replay":
        if replay is None:
            raise ModelError("BEEJA_PROVIDER=replay requires --replay or BEEJA_REPLAY_FILE")
        return ReplayProvider(replay, model=os.environ.get("BEEJA_MODEL", DEFAULT_MODEL))
    kind, base, key_vars, default_model, _ = PRESETS[name]
    model = os.environ.get("BEEJA_MODEL") or default_model
    base = os.environ.get("BEEJA_BASE_URL") or base
    key = os.environ.get("BEEJA_API_KEY") or next((os.environ[v] for v in key_vars if os.environ.get(v)), None)
    timeout = float(os.environ.get("BEEJA_TIMEOUT", "600"))
    if kind == "ollama":
        p: Any = OllamaProvider(
            model=model,
            host=_host(base or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST),
            timeout=timeout,
            num_ctx=int(os.environ.get("BEEJA_NUM_CTX", "8192")),
            api_key=key or os.environ.get("OLLAMA_API_KEY") or None,
            schema_in_prompt=_schema_flag(),
        )
        if name == "ollama-cloud" and not (key or os.environ.get("OLLAMA_API_KEY")):
            raise ModelError("ollama-cloud needs an API key: set BEEJA_API_KEY or OLLAMA_API_KEY")
    else:
        if not model:
            raise ModelError(f"{name}: set BEEJA_MODEL")
        if not base:
            raise ModelError(f"{name}: set BEEJA_BASE_URL (e.g. http://server:8000/v1)")
        if not key and name != "openai-compatible":
            raise ModelError(f"{name} needs an API key: set BEEJA_API_KEY or {' / '.join(key_vars)}")
        if kind == "anthropic":
            p = AnthropicProvider(model, key or "", base_url=base, timeout=timeout)
        else:
            extra = {"X-Title": "beeja"} if name == "openrouter" else None
            p = OpenAICompatibleProvider(model=model, base_url=base, api_key=key, timeout=timeout,
                                         label=name, extra_headers=extra)
    return RecordingProvider(p, record_to) if record_to else p


def _host(value: str) -> str:
    """Normalise an Ollama host. Like Ollama's OLLAMA_HOST: a bare host means port 11434."""
    value = value.strip() or DEFAULT_HOST
    bare = "://" not in value
    parts = urlsplit(("http://" + value) if bare else value)
    host = parts.hostname or "localhost"
    if host == "0.0.0.0":
        host = "localhost"
    port = parts.port or (11434 if bare or host in _LOCAL_HOSTS else None)
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
