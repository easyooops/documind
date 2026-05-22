"""Custom exceptions for DocuMind."""

from __future__ import annotations


class DocuMindError(Exception):
    """Base exception for all DocuMind errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AgentError(DocuMindError):
    """Raised when an agent node fails during pipeline execution."""

    def __init__(self, agent_name: str, message: str, **kwargs):
        super().__init__(f"[{agent_name}] {message}", **kwargs)
        self.agent_name = agent_name


class ValidationError(DocuMindError):
    """Raised when validation fails (HTML structure, CSS safety, visual quality)."""

    def __init__(self, level: int, issues: list[str], **kwargs):
        msg = f"Validation failed at level {level}: {len(issues)} issue(s)"
        super().__init__(msg, **kwargs)
        self.level = level
        self.issues = issues


class ConversionError(DocuMindError):
    """Raised when HTML→OOXML conversion fails."""

    pass


class LLMProviderError(DocuMindError):
    """Raised when LLM/VLM provider communication fails."""

    def __init__(self, provider: str, message: str, **kwargs):
        super().__init__(f"[{provider}] {message}", **kwargs)
        self.provider = provider


class StorageError(DocuMindError):
    """Raised when storage operations fail."""

    pass


class TemplateError(DocuMindError):
    """Raised when template parsing/analysis fails."""

    pass


class CircuitBreakerError(DocuMindError):
    """Raised when an agent hits the circuit breaker threshold (5 consecutive failures)."""

    def __init__(self, agent_name: str, failure_count: int):
        super().__init__(
            f"Circuit breaker triggered for {agent_name} after {failure_count} failures"
        )
        self.agent_name = agent_name
        self.failure_count = failure_count
