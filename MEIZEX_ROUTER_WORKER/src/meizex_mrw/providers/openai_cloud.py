"""OpenAI cloud adapter — the real api.openai.com, not a local runtime.

Ported from MOL (meizex_orchestrator_lite/semantic_mapper/service.py,
``_remote_client``): OpenAI's own API is the reference implementation
of the wire format :class:`OpenAICompatibleProvider` already speaks, so
this class only supplies OpenAI's base URL and requires an explicit API
key — no new wire parsing, same pattern as
:class:`~meizex_mrw.providers.gemini.GeminiProvider`.

Named ``openai_cloud`` (not ``openai``) to keep the registered provider
name unambiguous from the generic ``OpenAICompatibleProvider`` wire
format itself, which every local and cloud adapter here reuses.
"""

from __future__ import annotations

import httpx

from meizex_mrw.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICloudProvider(OpenAICompatibleProvider):
    name = "openai_cloud"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAICloudProvider requires a non-empty api_key")
        super().__init__(base_url, api_key=api_key, timeout=timeout, transport=transport)
