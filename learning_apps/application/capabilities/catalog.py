"""Product capability registry.

The application layer owns HTTP and worker workflows. Agent-only tools are
assembled by :mod:`learning_apps.adaptive_agent.tool_catalog`, keeping the core
application layer independent from the Agent runtime.
"""

from __future__ import annotations

from learning_apps.application.registry import CapabilityRegistry

from .product_catalog import product_capability_specs


CATALOG_VERSION = "product-capabilities-v1.0.0"


def build_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry(catalog_version=CATALOG_VERSION)
    for spec in product_capability_specs():
        registry.register(spec)
    return registry


_REGISTRY: CapabilityRegistry | None = None


def capability_registry() -> CapabilityRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_capability_registry()
    return _REGISTRY
