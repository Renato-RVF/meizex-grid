"""Capability Routing MVP — capability registry reader, requirements classifier,
candidate ranking, and the routing pipeline. Pure selection; never executes."""

from meizex_mrw.capabilities.descriptors import (
    CapabilityDescriptor,
    DescriptorError,
    DescriptorLoadError,
    MatchSpec,
    load_descriptors,
)
from meizex_mrw.capabilities.registry import (
    CapabilityRegistry,
    RegistryError,
    RegistryLoadError,
    get_registry,
)
from meizex_mrw.capabilities.requirements import (
    required_capabilities,
    requirements,
)
from meizex_mrw.capabilities.router import route

__all__ = [
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "DescriptorError",
    "DescriptorLoadError",
    "MatchSpec",
    "RegistryError",
    "RegistryLoadError",
    "get_registry",
    "load_descriptors",
    "required_capabilities",
    "requirements",
    "route",
]
