"""Task-scoped tool exposure: models only see capabilities relevant now."""
from __future__ import annotations

from .contracts import ToolSpec
from .registry import ToolRegistry


class ToolVisibilityPolicy:
    def visible_specs(self, registry: ToolRegistry, *, task_intents: list[str]) -> list[ToolSpec]:
        requested = set(task_intents)
        return [
            spec for spec in registry.specs()
            if "*" in spec.supported_intents or requested.intersection(spec.supported_intents)
        ]

    def visible_names(self, registry: ToolRegistry, *, task_intents: list[str]) -> list[str]:
        return [spec.name for spec in self.visible_specs(registry, task_intents=task_intents)]

