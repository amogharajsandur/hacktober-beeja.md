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

Configuration (environment): ``BEEJA_PROVIDER`` (``ollama`` | ``openai`` | ``replay``),
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
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = f"openai-compatible:{model}@{urlsplit(self.base_url).hostname}"
        self.retries = retries
        self.seed = seed
        self.schema_in_prompt = True
        self._structured = True
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def _chat(self, messages, schema):
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0, "seed": self.seed}
        if self._structured:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "beeja", "schema": schema, "strict": False}}
        r = self._client.post(f"{self.base_url}/chat/completions", json=payload)
        if r.status_code in (400, 422) and self._structured:
            self._structured = False  # server does not support structured output; prompt + parse instead
            payload.pop("response_format")
            r = self._client.post(f"{self.base_url}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        return data["choices"][0]["message"]["content"] or "", {
            "prompt_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}


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


def _schema_flag() -> bool | None:
    v = os.environ.get("BEEJA_SCHEMA_IN_PROMPT", "auto").strip().lower()
    return None if v in ("", "auto") else v in ("1", "true", "yes")


def provider_from_env(record_to: Path | None = None, replay_from: Path | None = None) -> ModelProvider:
    kind = os.environ.get("BEEJA_PROVIDER", "ollama").lower()
    model = os.environ.get("BEEJA_MODEL", DEFAULT_MODEL)
    replay = replay_from or (Path(os.environ["BEEJA_REPLAY_FILE"]) if os.environ.get("BEEJA_REPLAY_FILE") else None)
    if replay is not None or kind == "replay":
        if replay is None:
            raise ModelError("BEEJA_PROVIDER=replay requires --replay or BEEJA_REPLAY_FILE")
        return ReplayProvider(replay, model=model)
    timeout = float(os.environ.get("BEEJA_TIMEOUT", "600"))
    key = os.environ.get("BEEJA_API_KEY") or None
    if kind == "ollama":
        p: Any = OllamaProvider(
            model=model,
            host=_host(os.environ.get("BEEJA_BASE_URL") or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST),
            timeout=timeout,
            num_ctx=int(os.environ.get("BEEJA_NUM_CTX", "8192")),
            api_key=key or os.environ.get("OLLAMA_API_KEY") or None,
            schema_in_prompt=_schema_flag(),
        )
    elif kind == "openai":
        base = os.environ.get("BEEJA_BASE_URL")
        if not base:
            raise ModelError("BEEJA_PROVIDER=openai requires BEEJA_BASE_URL (e.g. http://server:8000/v1)")
        p = OpenAICompatibleProvider(model=model, base_url=base, api_key=key, timeout=timeout)
    else:
        raise ModelError(f"unknown BEEJA_PROVIDER {kind!r} (use ollama, openai or replay)")
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
