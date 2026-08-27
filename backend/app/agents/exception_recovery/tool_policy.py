"""Task-scoped Candidate Tool Builder。

每轮只把仍可能带来新 Observation 的 2~4 个能力暴露给模型；已执行、无状态
变化或未注册的能力保留在 blocked 列表供 Tool Gate 和前端解释。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ...tools.contracts import ToolSpec


@dataclass(frozen=True, slots=True)
class CandidateToolSet:
    enabled: tuple[str, ...]
    blocked: dict[str, str]


_COST_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def build_candidate_tools(
    *,
    master_allowlist: Sequence[str],
    visible_specs: Sequence[ToolSpec],
    actions: Sequence[dict[str, Any]],
    limit: int = 4,
) -> CandidateToolSet:
    """根据可见性、注册状态与既有 Observation 生成当前候选集合。"""

    specs = {item.name: item for item in visible_specs}
    last_action = {str(item.get("tool")): item for item in actions if item.get("tool")}
    enabled: list[ToolSpec] = []
    blocked: dict[str, str] = {}
    for name in dict.fromkeys(master_allowlist):
        spec = specs.get(name)
        if spec is None:
            blocked[name] = "NOT_REGISTERED_OR_NOT_VISIBLE"
            continue
        previous = last_action.get(name)
        if previous is not None:
            blocked[name] = (
                "NO_STATE_CHANGE" if not previous.get("state_changed") else "OBSERVATION_ALREADY_COLLECTED"
            )
            continue
        enabled.append(spec)
    enabled.sort(key=lambda item: (
        _COST_ORDER.get(item.cost_tier, 9),
        _COST_ORDER.get(item.latency_tier, 9),
        item.name,
    ))
    for spec in enabled[limit:]:
        blocked[spec.name] = "CANDIDATE_LIMIT"
    return CandidateToolSet(
        enabled=tuple(item.name for item in enabled[:limit]),
        blocked=blocked,
    )
