"""Provider interfaces. A new e-mail / SMS vendor = one new class implementing these, nothing else changes."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SendResult:
    provider: str
    message_id: str | None = None


class ProviderError(Exception):
    """Sending failed. `retryable` tells the caller whether trying again can help."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class EmailProvider(ABC):
    name: str

    @abstractmethod
    def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> SendResult: ...


class SmsProvider(ABC):
    name: str

    @abstractmethod
    def send(self, *, to: str, body: str) -> SendResult: ...


class VoiceProvider(ABC):
    name: str

    @abstractmethod
    def call(self, *, to: str, message: str) -> SendResult:
        """Place an outbound phone call that reads `message` aloud (text-to-speech)."""
