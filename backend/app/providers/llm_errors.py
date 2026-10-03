"""
Typed LLM errors.

Every failure of the model layer is raised as one of these, so the API can tell the
client what actually went wrong. Nothing here is ever turned into a made-up
interviewer answer: if the model could not answer, the request fails.

`http_status` is what the API returns; `retryable` tells the provider whether a
short automatic retry makes sense.
"""


class LLMError(Exception):
    http_status = 502
    retryable = False
    code = "llm_error"

    def __init__(self, message: str, *, detail: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail   # technical detail for the server log only
        if retryable is not None:
            self.retryable = retryable

    def __str__(self) -> str:
        return self.message


class LLMConfigError(LLMError):
    """Missing/invalid settings (no Ollama host, no model name ...)."""
    http_status = 503
    code = "llm_not_configured"


class LLMAuthError(LLMError):
    """The model server refused the request."""
    http_status = 503
    code = "llm_auth_failed"


class LLMModelNotFoundError(LLMError):
    """The configured model id does not exist (or is not available in that location)."""
    http_status = 503
    code = "llm_model_not_found"


class LLMRateLimitError(LLMError):
    http_status = 429
    retryable = True
    code = "llm_rate_limited"


class LLMTimeoutError(LLMError):
    http_status = 504
    retryable = True
    code = "llm_timeout"


class LLMConnectionError(LLMError):
    http_status = 503
    retryable = True
    code = "llm_unreachable"


class LLMUnavailableError(LLMError):
    """Model server 5xx / overloaded."""
    http_status = 503
    retryable = True
    code = "llm_unavailable"


class LLMResponseError(LLMError):
    """The model answered, but not with a usable result (empty, blocked, cut off, malformed JSON)."""
    http_status = 502
    retryable = True
    code = "llm_bad_response"
