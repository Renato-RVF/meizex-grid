"""LM Studio adapter — the first operational provider, not a permanent dependency.

LM Studio exposes a standard OpenAI-compatible ``/v1`` surface, so this
class only supplies LM Studio's defaults on top of
:class:`OpenAICompatibleProvider`. It optionally enriches
:meth:`list_models` via LM Studio's extended ``/api/v0/models`` endpoint
(capabilities, load state, context length) when available, but falls
back cleanly to the plain OpenAI ``/v1/models`` shape if that endpoint
is absent — so nothing here assumes a permanent, LM-Studio-only wire
format.
"""

from __future__ import annotations

import httpx

from meizex_mrw.providers.base import ModelInfo
from meizex_mrw.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"


class LMStudioProvider(OpenAICompatibleProvider):
    name = "lmstudio"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, timeout=timeout, transport=transport)

    def list_models(self) -> list[ModelInfo]:
        extended = self._try_extended_models()
        if extended is not None:
            return extended
        return super().list_models()

    def _try_extended_models(self) -> list[ModelInfo] | None:
        # LM Studio's own /api/v0/models sits outside the /v1 prefix, so it needs
        # the server root, not self._client (which is scoped to base_url=".../v1").
        root = self.base_url.removesuffix("/v1")
        try:
            resp = httpx.get(f"{root}/api/v0/models", timeout=5.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        data = resp.json()
        items = data.get("data")
        if not items:
            return None
        return [
            ModelInfo(
                id=item["id"],
                state=item.get("state"),
                capabilities=item.get("capabilities", []),
                max_context_length=item.get("max_context_length"),
                loaded_context_length=item.get("loaded_context_length"),
            )
            for item in items
        ]
