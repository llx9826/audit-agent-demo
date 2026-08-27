"""Deterministic completion policy for the bounded exception Agent loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .contracts import CompletionCondition


@dataclass(frozen=True, slots=True)
class CompletionEvaluation:
    met: bool
    normalized_value: str | None = None
    independent_sources: int = 0
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()


class CompletionPolicy:
    """Evaluate observations by evidence semantics instead of Tool identity."""

    def __init__(self, condition: CompletionCondition) -> None:
        self.condition = condition

    @staticmethod
    def _source_key(action: dict[str, Any]) -> str:
        provider = action.get("provider_name") or action.get("tool") or "unknown"
        return f"{action.get('provider_type', 'UNKNOWN')}:{provider}:{action.get('tool', 'unknown')}"

    def evaluate(self, actions: Sequence[dict[str, Any]]) -> CompletionEvaluation:
        if self.condition.condition_type != "NORMALIZED_VALUE_CONSENSUS":
            return CompletionEvaluation(met=False)

        by_value: dict[str, dict[str, dict[str, Any]]] = {}
        for action in actions:
            value = action.get("normalized_value")
            confidence = action.get("confidence")
            if not action.get("executed") or not value or confidence is None:
                continue
            if float(confidence) < self.condition.minimum_confidence:
                continue
            source_key = self._source_key(action)
            by_value.setdefault(str(value), {})[source_key] = action

        for value, observations in sorted(by_value.items()):
            if len(observations) < self.condition.minimum_independent_sources:
                continue
            selected = list(observations.values())
            evidence = tuple(dict.fromkeys(
                ref for item in selected for ref in item.get("evidence_refs", [])
            ))
            return CompletionEvaluation(
                met=True,
                normalized_value=value,
                independent_sources=len(observations),
                confidence=min(float(item["confidence"]) for item in selected),
                evidence_refs=evidence,
            )
        return CompletionEvaluation(met=False)
