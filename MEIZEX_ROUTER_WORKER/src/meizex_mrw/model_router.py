"""v0 model router — deterministic route selection from an ExecutionProfile.

Milestone 3 introduces the first explicit route. It is deliberately trivial:
there is exactly one operational local provider (lmstudio) and one configured
model per profile, so `route()` derives the route straight from the profile
instead of consulting models/list or a ranking table. The shape of the output
(ModelRoute) is what the rest of the stack depends on — when a second
provider or a model fallback chain arrives, only this module changes, not the
worker/context/verification layers. No cloud is ever chosen here.
"""

from __future__ import annotations

from pydantic import BaseModel

from meizex_mrw.profiles.schema import ExecutionProfile


class ModelRoute(BaseModel):
    provider: str
    model: str
    reason: str
    cloud_allowed: bool
    profile_name: str


def route(profile: ExecutionProfile) -> ModelRoute:
    """Resolve the operational model route for a profile.

    Deterministic, no network, no side effects. Raises nothing: the profile is
    already validated by its loader, so provider/model are guaranteed present.
    """
    return ModelRoute(
        provider=profile.provider,
        model=profile.model,
        reason="single local operational route declared by the execution profile",
        cloud_allowed=profile.cloud_allowed,
        profile_name=profile.name,
    )
