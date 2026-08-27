"""动态清单阶段：规则引擎解析适用要求，再编译带依赖的审核 Task。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from ...graph.common import _event
from ...graph.state import AuditState
from ...planning.planner import build_plan
from ...rag.requirements.rule_engine import requirement_to_domain
from ..dependencies import RequirementResolver


def resolve_requirements(
    state: AuditState,
    *,
    resolver: RequirementResolver,
) -> dict[str, Any]:
    """按产品、渠道、日期和已确认角色确定应交清单；检索排名不参与判定。"""

    fields = state.get("business_fields", {})
    roles = sorted({role for person in state.get("persons", []) for role in person.get("roles", [])})
    resolution = resolver.resolve(
        product=str(fields.get("product_type", "")),
        channel=str(fields.get("channel", "ALL")),
        case_date=date.fromisoformat(str(fields.get("case_date"))),
        person_roles=roles,
    )
    requirements = [requirement_to_domain(item) for item in resolution["requirements"]]
    patch = {"requirements": requirements, "active_node": "resolve_requirements"}
    event = _event(
        state, patch, event_type="REQUIREMENT_RULES_RESOLVED", node="resolve_requirements",
        actor="rule_engine", action="RESOLVE_APPLICABLE_REQUIREMENTS",
        tool="sqlite_requirement_catalog",
        observation={
            "rule": resolution["rule"],
            "input": resolution["input"],
            "requirement_ids": resolution["requirement_ids"],
            "count": resolution["count"],
        },
        details={"selection_authority": "DETERMINISTIC_RULE_ENGINE", "retrieval_used": False},
    )
    return {**patch, "pending_events": [event]}


def compile_checklist(state: AuditState) -> dict[str, Any]:
    """把 Requirement × 已确认人员编译为可依赖、可增量失效的 Task。"""

    tasks = [asdict(task) for task in build_plan(state.get("requirements", []), state.get("persons", []))]
    patch = {
        "audit_plan": tasks,
        "plan_version": max(1, int(state.get("plan_version", 1))),
        "active_node": "compile_checklist",
    }
    event = _event(
        state, patch, event_type="AUDIT_PLAN_COMPILED", node="compile_checklist",
        actor="workflow", action="COMPILE_REQUIREMENT_PERSON_TASKS",
        observation={"task_count": len(tasks), "task_ids": [task["task_id"] for task in tasks]},
        details={"write_authority": "WORKFLOW_ONLY"},
    )
    return {**patch, "pending_events": [event]}


__all__ = ["compile_checklist", "resolve_requirements"]
