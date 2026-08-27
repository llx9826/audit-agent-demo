"""结束门禁：只有全部材料 Task 完成才允许本次齐套审核结束。"""
from __future__ import annotations

from typing import Any

from ...graph.common import _event
from ...graph.state import AuditState


def run(state: AuditState) -> dict[str, Any]:
    """验证材料齐套完成条件；明确不输出任何贷款审批结论。"""

    incomplete = [
        {"task_id": task["task_id"], "status": task.get("status")}
        for task in state.get("audit_plan", [])
        if task.get("status") != "MATCHED"
    ]
    passed = not incomplete
    patch = {
        "status": "COMPLETED" if passed else "FAILED",
        "completeness_status": "COMPLETE" if passed else "INCOMPLETE",
        "active_node": "final_validator",
        "current_task_id": None,
    }
    event = _event(
        state, patch,
        event_type="COMPLETENESS_VALIDATED" if passed else "COMPLETENESS_VALIDATION_FAILED",
        node="final_validator", actor="validator", action="VERIFY_ALL_REQUIRED_MATERIAL_TASKS",
        observation={
            "result": "MATERIALS_COMPLETE" if passed else "MATERIALS_INCOMPLETE",
            "incomplete": incomplete,
            "credit_decision": "OUT_OF_SCOPE",
        },
        evidence=[ref for task in state.get("audit_plan", []) for ref in task.get("evidence_refs", [])],
    )
    return {**patch, "pending_events": [event]}


__all__ = ["run"]
