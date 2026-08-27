"""Public knowledge contracts with a lazy service export to avoid import cycles."""
from __future__ import annotations

from .contracts import GroundedAnswerDecision, KnowledgeIntentDecision

__all__ = [
    "GroundedAnswerDecision", "KnowledgeIntentDecision", "KnowledgeRunManager", "KnowledgeService",
]


def __getattr__(name: str):
    if name == "KnowledgeService":
        from .service import KnowledgeService

        return KnowledgeService
    if name == "KnowledgeRunManager":
        from .run_manager import KnowledgeRunManager

        return KnowledgeRunManager
    raise AttributeError(name)
