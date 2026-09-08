"""MEIZEX Chassis adapter — direct llama.cpp local inference, no LM Studio.

Chassis exposes a standard OpenAI-compatible ``/v1`` surface on the same
default bind LM Studio uses (``127.0.0.1:1234``), so this class only
supplies Chassis's defaults on top of :class:`OpenAICompatibleProvider` —
same pattern as :class:`~meizex_mrw.providers.lmstudio.LMStudioProvider`.
Nothing here assumes Chassis is the only local runtime; the two are
interchangeable at the wire level and never run at the same time on the
same port.
"""

from __future__ import annotations

import httpx

from meizex_mrw.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"


class ChassisProvider(OpenAICompatibleProvider):
    name = "chassis"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, timeout=timeout, transport=transport)
