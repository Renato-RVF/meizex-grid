"""Provider-agnostic inference contract.

Nothing in this module or its callers may assume a specific runtime
(LM Studio, Ollama, llama.cpp, a cloud API). Concrete adapters live in
sibling modules and implement :class:`InferenceProvider`.
"""

from __future__ import annotations

import abc
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolSpec(BaseModel):
    """A single tool schema offered to the model for this call, JSON-Schema shaped."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ChatMessage(BaseModel):
    role: Role
    content: str | list[dict[str, Any]] | None = None
    """A plain string for text-only messages, or a list of OpenAI-shaped
    content parts (e.g. ``{"type": "text", "text": ...}`` and
    ``{"type": "image_url", "image_url": {"url": "data:..."}}``) for
    multimodal messages. The list form is passed through untouched to the
    wire payload — see OpenAICompatibleProvider._message_to_openai — so a
    provider that doesn't support vision will surface that as a normal
    provider-level error, not a silent content drop."""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str
    """Raw JSON-encoded argument string, exactly as the provider returned it."""


class Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    ttft_ms: float | None = None
    tokens_per_second: float | None = None


class ChatResult(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage | None = None
    model: str | None = None
    latency_ms: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    """Full untouched provider response, for audit/debugging. Never parsed by callers."""


class ModelInfo(BaseModel):
    id: str
    state: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    max_context_length: int | None = None
    loaded_context_length: int | None = None


class ProviderError(RuntimeError):
    """Raised for any provider-level failure (connection, timeout, malformed response)."""


class InferenceProvider(abc.ABC):
    """Contract every runtime adapter (LM Studio, future local runtimes, cloud) must satisfy.

    Callers (router, worker, context builder) depend only on this interface —
    never on a concrete provider name or wire format.
    """

    name: str

    @abc.abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Return models the provider currently knows about."""

    @abc.abstractmethod
    def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Run one chat-completion call. Must not raise for a normal model refusal —
        only for transport/protocol failures (raise ProviderError)."""

    @abc.abstractmethod
    def health(self) -> bool:
        """Cheap reachability check. Must not raise; return False on any failure."""
