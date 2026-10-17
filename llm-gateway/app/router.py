"""Spreads requests across the enabled providers (smooth weighted round-robin) and falls over to the others on failure.

* Spread:   each request starts at the next provider in a weighted rotation, so load is shared in proportion to the weights.
* Failover: if that provider fails, the remaining ones are tried in turn, healthy ones first.
* Cooldown: a provider that just failed with a connectivity/auth/limit error is skipped for a while, so a dead provider
            does not slow every request down. If every provider is cooling down they are all tried anyway.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import Completion, Provider, ProviderError
from app.providers.gemini_api import GeminiApiProvider
from app.providers.openai_compat import OpenAICompatible

logger = logging.getLogger(__name__)

_COOLDOWN_KINDS = {"auth", "model_not_found", "rate_limit", "unavailable", "timeout", "connection"}
_STOP_KINDS = {"blocked"}        # a safety block is not shopped around to another model


def build_providers(cfg: Settings) -> dict[str, Provider]:
    found: dict[str, Provider] = {}
    if cfg.OPENAI_API_KEY:
        found["openai"] = OpenAICompatible("openai", cfg.OPENAI_API_KEY, cfg.OPENAI_MODEL,
                                           "https://api.openai.com/v1/chat/completions", token_param="max_completion_tokens")
    if cfg.ANTHROPIC_API_KEY:
        found["anthropic"] = AnthropicProvider(cfg.ANTHROPIC_API_KEY, cfg.ANTHROPIC_MODEL)
    if cfg.GEMINI_API_KEY:
        found["gemini_api"] = GeminiApiProvider(cfg.GEMINI_API_KEY, cfg.GEMINI_API_MODEL)
    if cfg.DEEPSEEK_API_KEY:
        found["deepseek"] = OpenAICompatible("deepseek", cfg.DEEPSEEK_API_KEY, cfg.DEEPSEEK_MODEL,
                                             "https://api.deepseek.com/chat/completions", token_param="max_tokens")
    return {name: p for name, p in found.items() if name not in cfg.excluded}


@dataclass
class Attempt:
    provider: str
    ok: bool
    kind: str = ""
    message: str = ""


@dataclass
class Result:
    completion: Completion
    provider: str
    fallback: bool
    attempts: list[Attempt] = field(default_factory=list)


class AllProvidersFailed(Exception):
    def __init__(self, attempts: list[Attempt]):
        super().__init__("No LLM provider could answer.")
        self.attempts = attempts
        self.retryable = any(a.kind in ("rate_limit", "unavailable", "timeout", "connection", "bad_response")
                             for a in attempts)


class NoProviders(Exception):
    pass


class Router:
    def __init__(self, providers: dict[str, Provider], cfg: Settings):
        self.providers = providers
        self.cfg = cfg
        self._weights = {name: cfg.weights.get(name, 1.0) for name in providers}
        self._current = {name: 0.0 for name in providers}
        self._cooldown_until: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- ordering -----------------------------------------------------------------------------------
    def in_cooldown(self, name: str) -> bool:
        return self._cooldown_until.get(name, 0.0) > time.monotonic()

    def order(self, only: list[str] | None = None) -> list[str]:
        names = [n for n in self.providers if not only or n in only]
        if not names:
            return []
        healthy = [n for n in names if not self.in_cooldown(n)] or names
        first = self._pick(healthy)
        rest = [n for n in names if n != first]
        rest.sort(key=lambda n: (self.in_cooldown(n), -self._weights.get(n, 0.0)))
        return ([first] if first else []) + rest

    def _pick(self, candidates: list[str]) -> str | None:
        weighted = [n for n in candidates if self._weights.get(n, 0.0) > 0]
        if not weighted:
            return candidates[0] if candidates else None
        with self._lock:
            total = sum(self._weights[n] for n in weighted)
            for n in weighted:
                self._current[n] += self._weights[n]
            chosen = max(weighted, key=lambda n: self._current[n])
            self._current[chosen] -= total
        return chosen

    # ---- calling ------------------------------------------------------------------------------------
    def complete(self, messages: list[dict], *, schema: dict | None, temperature: float, max_tokens: int,
                 only: list[str] | None = None) -> Result:
        order = self.order(only)
        if not order:
            raise NoProviders()
        attempts: list[Attempt] = []
        tries = 1 + max(0, self.cfg.GATEWAY_RETRIES)
        for index, name in enumerate(order):
            provider = self.providers[name]
            for attempt in range(1, tries + 1):
                started = time.monotonic()
                try:
                    completion = provider.complete(messages, schema=schema, temperature=temperature,
                                                   max_tokens=max_tokens, timeout=self.cfg.REQUEST_TIMEOUT_SECONDS)
                except ProviderError as exc:
                    logger.warning("provider=%s attempt=%d/%d failed kind=%s: %s", name, attempt, tries, exc.kind, exc)
                    if exc.retryable and attempt < tries:
                        time.sleep(self.cfg.GATEWAY_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                        continue
                    attempts.append(Attempt(name, False, exc.kind, str(exc)))
                    if exc.kind in _COOLDOWN_KINDS:
                        self._cooldown_until[name] = time.monotonic() + self.cfg.GATEWAY_COOLDOWN_SECONDS
                    if exc.kind in _STOP_KINDS:
                        raise AllProvidersFailed(attempts) from exc
                    break
                except Exception as exc:                      # never let one provider's bug take the gateway down
                    logger.exception("provider=%s crashed", name)
                    attempts.append(Attempt(name, False, "error", f"{type(exc).__name__}"))
                    break
                else:
                    self._cooldown_until.pop(name, None)
                    attempts.append(Attempt(name, True))
                    logger.info("answered provider=%s model=%s ms=%d tokens_in=%s tokens_out=%s fallback=%s",
                                name, completion.model, int((time.monotonic() - started) * 1000),
                                completion.tokens_in, completion.tokens_out, index > 0)
                    return Result(completion, name, index > 0, attempts)
        raise AllProvidersFailed(attempts)
