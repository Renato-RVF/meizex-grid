"""Gemini adapter — Google's own OpenAI-compatible endpoint, not a bespoke API.

Ported from MOL (meizex_orchestrator_lite/semantic_mapper/service.py,
``_remote_client``): Google exposes an OpenAI-chat-completions-shaped
surface at ``/v1beta/openai`` specifically so existing OpenAI clients
work unmodified. This class only supplies Gemini's base URL and
requires an explicit API key — same pattern as
:class:`~meizex_mrw.providers.lmstudio.LMStudioProvider` and
:class:`~meizex_mrw.providers.chassis.ChassisProvider`, no new wire
parsing.
"""

from __future__ import annotations

import httpx

from meizex_mrw.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiProvider(OpenAICompatibleProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiProvider requires a non-empty api_key")
        super().__init__(base_url, api_key=api_key, timeout=timeout, transport=transport)
