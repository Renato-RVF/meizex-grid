"""Generic adapter for any OpenAI-chat-completions-shaped HTTP server.

This is the one module allowed to know about the OpenAI wire format
(``/v1/models``, ``/v1/chat/completions``, ``choices[0].message``...).
Runtime-specific adapters (LM Studio, others) subclass this and only
override defaults (base_url) or add runtime-specific extras — they must
not reimplement the wire parsing.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from meizex_mrw.providers.base import (
    ChatMessage,
    ChatResult,
    InferenceProvider,
    ModelInfo,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)


def _tool_spec_to_openai(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _message_to_openai(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        payload["content"] = message.content
    if message.name is not None:
        payload["name"] = message.name
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls is not None:
        payload["tool_calls"] = message.tool_calls
    return payload


class OpenAICompatibleProvider(InferenceProvider):
    """Talks to any server implementing the OpenAI chat-completions contract."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )

    def list_models(self) -> list[ModelInfo]:
        try:
            resp = self._client.get("/models")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"list_models failed against {self.base_url}: {exc}") from exc
        data = resp.json()
        return [ModelInfo(id=item["id"]) for item in data.get("data", [])]

    def health(self) -> bool:
        try:
            resp = self._client.get("/models", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_message_to_openai(m) for m in messages],
        }
        if tools:
            payload["tools"] = [_tool_spec_to_openai(t) for t in tools]
            payload["tool_choice"] = "auto"
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if extra:
            payload.update(extra)

        start = time.perf_counter()
        try:
            resp = self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"chat failed against {self.base_url}: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000.0

        data = resp.json()
        return self._parse_chat_response(data, latency_ms=latency_ms)

    def _parse_chat_response(self, data: dict[str, Any], *, latency_ms: float) -> ChatResult:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"provider returned no choices: {data!r}")
        choice = choices[0]
        message = choice.get("message") or {}

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            tool_calls.append(
                ToolCall(
                    id=raw_call.get("id", ""),
                    name=function.get("name", ""),
                    arguments=function.get("arguments", ""),
                )
            )

        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens"),
            completion_tokens=usage_raw.get("completion_tokens"),
            total_tokens=usage_raw.get("total_tokens"),
        )

        return ChatResult(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            model=data.get("model"),
            latency_ms=latency_ms,
            raw=data,
        )
