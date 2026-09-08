"""The one sanctioned place the core imports a concrete provider by name.

tool_router, task_classifier, and the profiles/events/mcp packages must
never import LMStudioProvider (or any other concrete provider) directly —
only this factory does, keyed off ExecutionProfile.provider. Adding a
second provider later (a different local runtime, or cloud) means adding
one entry here, not touching routing/execution code.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from meizex_mrw.profiles.schema import ExecutionProfile
from meizex_mrw.providers.base import InferenceProvider
from meizex_mrw.providers.chassis import ChassisProvider
from meizex_mrw.providers.gemini import GeminiProvider
from meizex_mrw.providers.lmstudio import LMStudioProvider
from meizex_mrw.providers.openai_cloud import OpenAICloudProvider


def _lookup(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _timeout(profile: ExecutionProfile) -> float:
    return profile.inference.provider_timeout_s if profile.inference.provider_timeout_s else 60.0


def _require_key(*names: str, provider: str) -> str:
    key = _lookup(*names)
    if not key:
        raise ValueError(
            f"provider {provider!r} requires an API key; set one of: {', '.join(names)}"
        )
    return key


_FACTORIES: dict[str, Callable[[ExecutionProfile], InferenceProvider]] = {
    "lmstudio": lambda profile: LMStudioProvider(timeout=_timeout(profile)),
    "chassis": lambda profile: ChassisProvider(timeout=_timeout(profile)),
    "gemini": lambda profile: GeminiProvider(
        _require_key("GEMINI_API_KEY", "GOOGLE_API_KEY", provider="gemini"),
        timeout=_timeout(profile),
    ),
    "openai_cloud": lambda profile: OpenAICloudProvider(
        _require_key("OPENAI_API_KEY", provider="openai_cloud"),
        timeout=_timeout(profile),
    ),
}


def create_provider(profile: ExecutionProfile) -> InferenceProvider:
    factory = _FACTORIES.get(profile.provider)
    if factory is None:
        known = ", ".join(sorted(_FACTORIES)) or "(none registered)"
        raise ValueError(f"unknown provider {profile.provider!r}; known providers: {known}")
    return factory(profile)
