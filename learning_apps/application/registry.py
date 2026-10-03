from __future__ import annotations

from threading import RLock

from .contracts import CapabilityError, CapabilitySpec


class CapabilityRegistry:
    def __init__(self, *, catalog_version: str):
        self.catalog_version = catalog_version
        self._specs: dict[str, CapabilitySpec] = {}
        self._lock = RLock()

    def register(self, spec: CapabilitySpec) -> None:
        with self._lock:
            if spec.name in self._specs:
                raise CapabilityError("duplicate_capability", spec.name)
            if not spec.handler:
                raise CapabilityError("missing_capability_handler", spec.name)
            self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise CapabilityError("capability_not_found", name) from exc

    def all(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def agent_tools(self, allowlist: set[str] | None = None) -> tuple[CapabilitySpec, ...]:
        names = allowlist if allowlist is not None else set(self._specs)
        return tuple(
            spec
            for spec in self.all()
            if spec.agent_visible and spec.name in names
        )
