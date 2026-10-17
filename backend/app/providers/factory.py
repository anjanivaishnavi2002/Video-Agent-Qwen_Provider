"""Chooses the LLM client. The rest of the app calls `get_llm()` and never imports a concrete provider."""
import threading

from app.config import settings

_provider = None
_lock = threading.Lock()


def get_llm():
    """Shared model client, created on first use: Gemini on Vertex AI (default) or Ollama (offline dev)."""
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                if settings.llm_backend == "ollama":
                    from app.providers.ollama_provider import OllamaProvider
                    _provider = OllamaProvider()
                else:
                    from app.providers.gemini_provider import GeminiProvider
                    _provider = GeminiProvider()
                    if settings.LLM_GATEWAY_URL.strip():
                        # Vertex stays the primary; the llm-gateway (OpenAI/Anthropic/Gemini API/DeepSeek) is the
                        # automatic fallback and, with LLM_GATEWAY_SHARE > 0, takes that share of requests first.
                        from app.providers.gateway_provider import GatewayClient, RoutedLLM
                        _provider = RoutedLLM(_provider, GatewayClient(settings.LLM_GATEWAY_URL),
                                              settings.LLM_GATEWAY_SHARE)
    return _provider


def reset_llm() -> None:
    """Forget the cached client (tests, or after changing settings)."""
    global _provider
    with _lock:
        _provider = None
