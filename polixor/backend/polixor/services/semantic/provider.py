"""
The semantic model behind the intelligence pipeline – provider-agnostic.

  anthropic  primary. Official SDK, structured JSON output (json_schema),
             adaptive thinking with an explicit effort, the system prompt
             cached, and the server-side `fallbacks: "default"` safety net
             (a declined request is re-run on Anthropic's recommended
             fallback model inside the same call).
  openai     adapter (Chat Completions with a json_schema response format).
  ollama     local adapter (JSON schema as the `format`).

Keys come only from the server-side secret store (or POLIXOR_* environment
variables) and are never logged. Every answer is cached on disk by a hash of
the request, so a resumed or regenerated job never pays twice for the same
question. Usage (calls, tokens, cache reads, seconds) is recorded per task for
the run report.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ...config import SECRETS, AppSettings

log = logging.getLogger("polixor.semantic")

# raised when the prompts or schemas change: old cached answers are not reused
PROMPT_VERSION = 1
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 32000
TIMEOUT = 900.0


class SemanticError(Exception):
    """The model could not answer (network, refusal, truncated, unparseable)."""


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0                       # answers served from the disk cache
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0
    failures: int = 0
    by_task: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, task: str, **kw: float) -> None:
        t = self.by_task.setdefault(task, {})
        for k, v in kw.items():
            setattr(self, k, getattr(self, k) + v)
            t[k] = round(t.get(k, 0) + v, 3)

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "cached": self.cached, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens, "seconds": round(self.seconds, 1),
                "failures": self.failures, "by_task": self.by_task}


class SemanticProvider:
    """complete_json(task, system, user, schema) -> dict, with a disk cache and usage accounting."""

    name = "base"

    def __init__(self, model: str, *, cache_dir: Optional[Path] = None) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.usage = Usage()
        self._lock = threading.Lock()

    # ---- cache ----
    def _key(self, task: str, system: str, user: str, schema: dict[str, Any], extra: Any) -> str:
        raw = json.dumps([PROMPT_VERSION, self.name, self.model, task, system, user, schema, extra],
                         sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _cached(self, key: str) -> Optional[dict[str, Any]]:
        if self.cache_dir is None:
            return None
        p = self.cache_dir / f"{key}.json"
        try:
            return json.loads(p.read_text("utf-8")) if p.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    def _store(self, key: str, value: dict[str, Any]) -> None:
        if self.cache_dir is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self.cache_dir / f"{key}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        tmp.replace(p)

    # editorial guidance of the content profile (profile.py), added to the judgment tasks only;
    # it is part of the system prompt, so it is part of the cache key
    guidance: str = ""
    GUIDED_TASKS = frozenset({"candidates", "rank", "boundaries", "editor", "longform", "hooks"})

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any], *,
                      max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any]:
        if self.guidance and task in self.GUIDED_TASKS:
            system = system + "\n\n" + self.guidance
        key = self._key(task, system, user, schema, self.cache_extra())
        hit = self._cached(key)
        from ...util import profiler

        profiler.cache_event("model_answers", hit is not None)
        if hit is not None:
            with self._lock:
                self.usage.add(task, cached=1)
            return hit
        t0 = time.time()
        if self.name in ("anthropic", "openai"):
            from ..paid_guard import PaidAIDisabled, check

            try:
                check(f"{self.name}:{task}")
            except PaidAIDisabled as exc:
                with self._lock:
                    self.usage.add(task, failures=1)
                raise SemanticError(str(exc)) from None
        try:
            data, usage = self._complete(task, system, user, schema, max_tokens)
        except SemanticError:
            with self._lock:
                self.usage.add(task, failures=1, seconds=time.time() - t0)
            raise
        with self._lock:
            self.usage.add(task, calls=1, seconds=time.time() - t0, **usage)
        profiler.model_wait(task, time.time() - t0)
        self._store(key, data)
        return data

    def cache_extra(self) -> Any:
        return None

    def _complete(self, task: str, system: str, user: str, schema: dict[str, Any],
                  max_tokens: int) -> tuple[dict[str, Any], dict[str, float]]:
        raise NotImplementedError


def parse_json(text: str) -> dict[str, Any]:
    from ..llm import _parse_json
    from ...errors import AiProviderError

    try:
        out = _parse_json(text)
    except AiProviderError as exc:
        raise SemanticError(f"unparseable answer: {exc.message}") from exc
    if not isinstance(out, dict):
        raise SemanticError("answer is not a JSON object")
    return out


# --------------------------------------------------------------------------
# Anthropic (primary)
# --------------------------------------------------------------------------
def _supports_adaptive(model: str) -> bool:
    m = model.lower()
    return any(x in m for x in ("opus-5", "sonnet-5", "fable", "mythos", "opus-4-6", "opus-4-7",
                                "opus-4-8", "sonnet-4-6"))


class AnthropicProvider(SemanticProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, *, effort: str = "high",
                 cache_dir: Optional[Path] = None) -> None:
        super().__init__(model, cache_dir=cache_dir)
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=TIMEOUT)
        self.effort = effort
        # request features dropped after a 400 that names them (older models, other platforms)
        self._no_fallbacks = False
        self._no_format = False
        self._no_thinking = not _supports_adaptive(model)

    def cache_extra(self) -> Any:
        return {"effort": self.effort}

    def _request(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        req: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        cfg: dict[str, Any] = {}
        if not self._no_thinking:
            req["thinking"] = {"type": "adaptive"}
            cfg["effort"] = self.effort
        if not self._no_format:
            cfg["format"] = {"type": "json_schema", "schema": schema}
        if cfg:
            req["output_config"] = cfg
        if not self._no_fallbacks:
            req["betas"] = [FALLBACK_BETA]
            req["fallbacks"] = "default"
        return req

    def _complete(self, task: str, system: str, user: str, schema: dict[str, Any],
                  max_tokens: int) -> tuple[dict[str, Any], dict[str, float]]:
        a = self._anthropic
        for _attempt in range(4):
            req = self._request(system, user, schema, max_tokens)
            try:
                if "betas" in req:
                    with self.client.beta.messages.stream(**req) as stream:
                        msg = stream.get_final_message()
                else:
                    with self.client.messages.stream(**req) as stream:
                        msg = stream.get_final_message()
            except a.BadRequestError as exc:
                text = str(getattr(exc, "message", exc)).lower()
                # degrade one optional feature at a time, never the content
                if not self._no_fallbacks and ("fallback" in text or "beta" in text):
                    self._no_fallbacks = True
                    continue
                if not self._no_format and ("output_config" in text or "format" in text or "schema" in text):
                    self._no_format = True
                    continue
                if not self._no_thinking and ("thinking" in text or "effort" in text):
                    self._no_thinking = True
                    continue
                raise SemanticError(f"Anthropic rejected the request: {getattr(exc, 'message', exc)}") from exc
            except (a.AuthenticationError, a.PermissionDeniedError) as exc:
                raise SemanticError("Anthropic key rejected") from exc
            except a.APIError as exc:
                raise SemanticError(f"Anthropic error: {getattr(exc, 'message', exc)}") from exc
            if msg.stop_reason == "refusal":
                raise SemanticError("the model declined this request")
            if msg.stop_reason == "max_tokens":
                raise SemanticError("the answer was cut at the token limit")
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            u = msg.usage
            usage = {"input_tokens": int(getattr(u, "input_tokens", 0) or 0),
                     "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
                     "cache_read_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
                     "cache_write_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0)}
            return parse_json(text), usage
        raise SemanticError("Anthropic request could not be made compatible with this model")


# --------------------------------------------------------------------------
# OpenAI (adapter)
# --------------------------------------------------------------------------
class OpenAIProvider(SemanticProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str, *, cache_dir: Optional[Path] = None) -> None:
        super().__init__(model, cache_dir=cache_dir)
        self._key_value = api_key
        self._no_schema = False

    def _complete(self, task: str, system: str, user: str, schema: dict[str, Any],
                  max_tokens: int) -> tuple[dict[str, Any], dict[str, float]]:
        import httpx

        for _attempt in range(2):
            fmt = ({"type": "json_object"} if self._no_schema else
                   {"type": "json_schema", "json_schema": {"name": task.replace(".", "_")[:60],
                                                           "schema": schema, "strict": False}})
            try:
                with httpx.Client(timeout=TIMEOUT, trust_env=True) as c:
                    r = c.post("https://api.openai.com/v1/chat/completions",
                               headers={"Authorization": f"Bearer {self._key_value}",
                                        "content-type": "application/json"},
                               json={"model": self.model, "response_format": fmt,
                                     "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]})
            except Exception as exc:                    # noqa: BLE001
                raise SemanticError(f"OpenAI unreachable: {type(exc).__name__}") from exc
            if r.status_code == 400 and not self._no_schema and "response_format" in r.text:
                self._no_schema = True
                continue
            if r.status_code >= 400:
                raise SemanticError(f"OpenAI error {r.status_code}")
            data = r.json()
            u = data.get("usage") or {}
            usage = {"input_tokens": int(u.get("prompt_tokens", 0) or 0),
                     "output_tokens": int(u.get("completion_tokens", 0) or 0),
                     "cache_read_tokens": int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)}
            return parse_json(data["choices"][0]["message"]["content"] or ""), usage
        raise SemanticError("OpenAI request failed")


# --------------------------------------------------------------------------
# Ollama (local adapter)
# --------------------------------------------------------------------------
class OllamaProvider(SemanticProvider):
    name = "ollama"

    def _complete(self, task: str, system: str, user: str, schema: dict[str, Any],
                  max_tokens: int) -> tuple[dict[str, Any], dict[str, float]]:
        import httpx

        from ..llm import OLLAMA_URL

        try:
            with httpx.Client(timeout=TIMEOUT, trust_env=True) as c:
                r = c.post(f"{OLLAMA_URL}/api/chat",
                           json={"model": self.model, "stream": False, "format": schema,
                                 "options": {"temperature": 0.2, "num_ctx": 32768},
                                 "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}]})
        except Exception as exc:                        # noqa: BLE001
            raise SemanticError(f"Ollama unreachable: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise SemanticError(f"Ollama error {r.status_code}")
        data = r.json()
        usage = {"input_tokens": int(data.get("prompt_eval_count", 0) or 0),
                 "output_tokens": int(data.get("eval_count", 0) or 0)}
        return parse_json((data.get("message") or {}).get("content", "")), usage


# --------------------------------------------------------------------------
# tests and offline replays
# --------------------------------------------------------------------------
class FunctionProvider(SemanticProvider):
    """Answers with a Python function (tests, offline replays): fn(task, system, user, schema) -> dict."""

    name = "function"

    def __init__(self, fn: Callable[[str, str, str, dict[str, Any]], dict[str, Any]], model: str = "test",
                 cache_dir: Optional[Path] = None) -> None:
        super().__init__(model, cache_dir=cache_dir)
        self.fn = fn

    def _complete(self, task: str, system: str, user: str, schema: dict[str, Any],
                  max_tokens: int) -> tuple[dict[str, Any], dict[str, float]]:
        out = self.fn(task, system, user, schema)
        if not isinstance(out, dict):
            raise SemanticError("test provider returned no object")
        return out, {"input_tokens": len(system + user) // 4, "output_tokens": len(json.dumps(out)) // 4}


# --------------------------------------------------------------------------
def resolve(settings: AppSettings, *, cache_dir: Optional[Path] = None
            ) -> tuple[Optional[SemanticProvider], str]:
    """
    The configured semantic provider, or (None, reason). The reason is shown
    to the user as the cause of degraded mode.
    """
    from ..llm import _mode
    from . import scripted

    if scripted.enabled():
        # benchmarks / development: a deterministic stand-in, no network, no cost (never via the interface)
        return FunctionProvider(scripted.ScriptedEditor(), model="scripted", cache_dir=cache_dir), ""
    mode = _mode(settings)
    if mode == "heuristic":
        if settings.ai_mode == "heuristic":
            return None, "ai_off"
        return None, "no_key"
    if mode == "ollama":
        return OllamaProvider(settings.ai_model or "llama3.1", cache_dir=cache_dir), ""
    if settings.ai_provider == "openai":
        key = SECRETS.get("openai_api_key")
        if not key:
            return None, "no_key"
        return OpenAIProvider(settings.ai_model or "gpt-5", key, cache_dir=cache_dir), ""
    key = SECRETS.get("anthropic_api_key")
    if not key:
        return None, "no_key"
    try:
        return AnthropicProvider(settings.ai_model or "claude-opus-5-5", key,
                                 effort=getattr(settings, "semantic_effort", "high"), cache_dir=cache_dir), ""
    except ImportError:
        return None, "sdk_missing"
