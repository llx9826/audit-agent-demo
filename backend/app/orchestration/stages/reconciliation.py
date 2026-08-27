"""补件恢复阶段：对比事实、失效受影响结果并只重跑 Dirty Task。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from ...domain.models import AuditResult, RequiredMaterialTask
from ...graph.common import _event
from ...graph.state import AuditState
from ...planning.planner import impacted_task_ids, selective_replan as rebuild_impacted_plan


def reconcile(state: AuditState) -> dict[str, Any]:
    """从同一 thread_id 的 Checkpoint 承接旧状态，并显式记录变化事实。"""

    patch = {"active_node": "reconcile_state", "current_task_id": state.get("current_task_id")}
    events = [
        _event(
            state, patch, event_type="STATE_RECONCILIATION_STARTED", node="reconcile_state",
            actor="reconciler", task_id=state.get("current_task_id"),
            action="LOAD_CHECKPOINT_AND_PATCH",
            observation={"case_version": state.get("case_version"), "plan_version": state.get("plan_version")},
        ),
        _event(
            state, patch, event_type="FACTS_CHANGED", node="reconcile_state", actor="reconciler",
            task_id=state.get("current_task_id"), action="DETECT_CHANGED_FACTS",
            observation={"changed_facts": state.get("changed_facts", [])},
        ),
        _event(
            state, patch, event_type="STATE_RECONCILIATION_COMPLETED", node="reconcile_state",
            actor="reconciler", task_id=state.get("current_task_id"),
            action="MERGE_AND_DIFF_MATERIAL_STATE",
            observation={"changed_fact_count": len(state.get("changed_facts", []))},
        ),
        _event(
            state, patch, event_type="STATE_RECONCILED", node="reconcile_state", actor="reconciler",
            task_id=state.get("current_task_id"), action="MERGE_AND_DIFF_MATERIAL_STATE",
            observation={"changed_facts": state.get("changed_facts", [])},
        ),
    ]
    return {**patch, "pending_events": events}


def selective_replan(state: AuditState) -> dict[str, Any]:
    """根据 Task Dependency 做影响分析，保留未受影响结果并提交新 Plan Version。"""

    task_objects: list[RequiredMaterialTask] = []
    for item in state.get("audit_plan", []):
        payload = deepcopy(item)
        if payload.get("result"):
            payload["result"] = AuditResult(**payload["result"])
        task_objects.append(RequiredMaterialTask(**payload))
    impacted = impacted_task_ids(task_objects, state.get("changed_facts", []))
    revised = rebuild_impacted_plan(task_objects, state.get("changed_facts", []))
    old_tasks = {item["task_id"]: item for item in state.get("audit_plan", [])}
    changed_facts = set(state.get("changed_facts", []))
    plan = [asdict(task) for task in revised]
    decisions = []
    for task in plan:
        old_task = old_tasks.get(task["task_id"], {})
        dependencies = set(old_task.get("fact_dependencies") or old_task.get("depends_on") or [])
        dependencies.update(f"page:{page_id}" for page_id in old_task.get("matched_page_ids", []))
        matched_facts = sorted(changed_facts.intersection(dependencies))
        before_result_version = int(old_task.get("result_version") or 0)
        operation = "RERUN" if task["task_id"] in impacted else "KEEP"
        decisions.append({
            "task_id": task["task_id"],
            "operation": operation,
            "before": old_task.get("status"),
            "after": task["status"],
            # 影响原因随事件一起输出，前端不复制 Planner 规则来猜测。
            "matched_changed_facts": matched_facts,
            "matched_fact_dependencies": sorted(dependencies.intersection(changed_facts)),
            "before_result_version": before_result_version,
            "after_result_version": (
                before_result_version + 1 if operation == "RERUN" else before_result_version
            ),
        })
    next_plan_version = int(state.get("plan_version", 1)) + 1
    patch = {
        "audit_plan": plan,
        "dirty_tasks": impacted,
        "invalidated_tasks": [item["task_id"] for item in plan if item["status"] == "INVALIDATED"],
        "replan_decisions": decisions,
        "plan_version": next_plan_version,
        "active_node": "selective_replan",
    }
    events = [
        _event(
            state, patch, event_type="IMPACT_ANALYSIS_COMPLETED", node="selective_replan",
            actor="planner", action="RESOLVE_TASK_DEPENDENCIES",
            observation={"changed_facts": state.get("changed_facts", []), "impacted_task_ids": impacted},
        ),
    ]
    for decision in decisions:
        events.append(_event(
            state, patch,
            event_type="TASK_RESULT_INVALIDATED" if decision["operation"] == "RERUN" else "TASK_RESULT_REUSED",
            node="selective_replan", actor="planner", task_id=decision["task_id"],
            action=decision["operation"], observation=decision,
        ))
    events.extend([
        _event(
            state, patch, event_type="SELECTIVE_REPLAN_COMPLETED", node="selective_replan",
            actor="planner", action="INVALIDATE_ONLY_IMPACTED_TASKS",
            observation={"dirty_task_ids": impacted, "reused_count": len(plan) - len(impacted)},
            details={"decisions": decisions},
        ),
        _event(
            state, patch, event_type="READY_TASKS_DISPATCHED", node="selective_replan",
            actor="workflow", action="DISPATCH_DIRTY_TASKS",
            observation={"task_ids": impacted, "dispatch_mode": "DEPENDENCY_READY_BATCH"},
        ),
        _event(
            state, patch, event_type="PLAN_VERSION_COMMITTED", node="selective_replan",
            actor="plan_gate", action="COMMIT_PLAN_VERSION",
            observation={"plan_version": next_plan_version},
            state_diff={"plan_version": [state.get("plan_version", 1), next_plan_version]},
        ),
        _event(
            state, patch, event_type="SELECTIVE_REPLAN_APPLIED", node="selective_replan",
            actor="workflow", action="INVALIDATE_ONLY_IMPACTED_TASKS",
            observation={"dirty_task_ids": impacted, "reused_count": len(plan) - len(impacted)},
            state_diff={"plan_version": [state.get("plan_version", 1), next_plan_version]},
            details={"decisions": decisions},
        ),
    ])
    return {**patch, "pending_events": events}


__all__ = ["reconcile", "selective_replan"]
