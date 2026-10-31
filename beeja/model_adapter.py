"""Open-weight model providers behind one small contract.

``generate_json(system, prompt, schema) -> dict`` returns a parsed JSON object or raises
``ModelError``. Providers:

* ``OllamaProvider`` - real inference against a local (or organiser) Ollama server.
* ``RecordingProvider`` - wraps a real provider and appends every exchange to a JSONL file.
* ``ReplayProvider`` - replays a recording made from real inference; an unknown prompt is an
  error, never a silent fallback. CI uses this so runs are deterministic and offline.

Configuration (environment): ``BEEJA_PROVIDER`` (``ollama`` | ``replay``), ``BEEJA_MODEL``
(default ``qwen3:4b``), ``OLLAMA_HOST`` (default ``http://localhost:11434``),
``BEEJA_REPLAY_FILE``, ``BEEJA_TIMEOUT`` seconds, ``BEEJA_NUM_CTX``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

DEFAULT_MODEL = "qwen3:4b"
DEFAULT_HOST = "http://localhost:11434"


class ModelError(Exception):
    pass


class ModelProvider(Protocol):
    name: str

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def exchange_key(model: str, system: str, prompt: str, schema: dict[str, Any]) -> str:
    blob = json.dumps([model, system, prompt, schema], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class OllamaProvider:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        *,
        timeout: float = 600.0,
        retries: int = 2,
        num_ctx: int = 8192,
        seed: int = 7,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.name = f"ollama:{model}"
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.num_ctx = num_ctx
        self.seed = seed
        self._client = client or httpx.Client(timeout=timeout)
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"temperature": 0, "seed": self.seed, "num_ctx": self.num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                t0 = time.time()
                r = self._client.post(f"{self.host}/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
                content = data["message"]["content"]
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ModelError("model returned JSON that is not an object")
                self.calls.append(
                    {
                        "seconds": round(time.time() - t0, 2),
                        "prompt_tokens": data.get("prompt_eval_count"),
                        "output_tokens": data.get("eval_count"),
                    }
                )
                return parsed
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ModelError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise ModelError(f"{self.name} failed after {self.retries + 1} attempts: {last}")


class RecordingProvider:
    def __init__(self, inner: OllamaProvider, path: Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        out = self.inner.generate_json(system, prompt, schema)
        rec = {
            "key": exchange_key(self.inner.model, system, prompt, schema),
            "model": self.inner.model,
            "response": out,
            "stats": self.inner.calls[-1] if self.inner.calls else None,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return out


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


def provider_from_env(record_to: Path | None = None, replay_from: Path | None = None) -> ModelProvider:
    kind = os.environ.get("BEEJA_PROVIDER", "ollama").lower()
    model = os.environ.get("BEEJA_MODEL", DEFAULT_MODEL)
    replay = replay_from or (Path(os.environ["BEEJA_REPLAY_FILE"]) if os.environ.get("BEEJA_REPLAY_FILE") else None)
    if replay is not None or kind == "replay":
        if replay is None:
            raise ModelError("BEEJA_PROVIDER=replay requires --replay or BEEJA_REPLAY_FILE")
        return ReplayProvider(replay, model=model)
    if kind != "ollama":
        raise ModelError(f"unknown BEEJA_PROVIDER {kind!r}")
    p = OllamaProvider(
        model=model,
        host=_host(os.environ.get("OLLAMA_HOST", DEFAULT_HOST)),
        timeout=float(os.environ.get("BEEJA_TIMEOUT", "600")),
        num_ctx=int(os.environ.get("BEEJA_NUM_CTX", "8192")),
    )
    return RecordingProvider(p, record_to) if record_to else p


def _host(value: str) -> str:
    value = value.strip() or DEFAULT_HOST
    if "://" not in value:
        value = "http://" + value
    if value.count(":") == 1:  # no port given
        value += ":11434"
    return value.replace("0.0.0.0", "localhost")
