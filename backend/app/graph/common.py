"""Shared event-envelope helpers for graph nodes."""
from __future__ import annotations

from typing import Any

from .state import AuditState

def _snapshot(state: AuditState, patch: dict[str, Any], node: str) -> dict[str, Any]:
    tasks = patch.get("audit_plan", state.get("audit_plan", []))
    return {
        "status": patch.get("status", state.get("status", "READY")),
        "active_node": node,
        "current_task_id": patch.get("current_task_id", state.get("current_task_id")),
        "case_version": patch.get("case_version", state.get("case_version", 1)),
        "plan_version": patch.get("plan_version", state.get("plan_version", 1)),
        "completeness_status": patch.get(
            "completeness_status", state.get("completeness_status", "NOT_STARTED")
        ),
        "task_statuses": {task["task_id"]: task["status"] for task in tasks},
        "changed_facts": patch.get("changed_facts", state.get("changed_facts", [])),
        "dirty_tasks": patch.get("dirty_tasks", state.get("dirty_tasks", [])),
        "invalidated_tasks": patch.get("invalidated_tasks", state.get("invalidated_tasks", [])),
    }


def _event(
    state: AuditState,
    patch: dict[str, Any],
    *,
    event_type: str,
    node: str,
    actor: str,
    task_id: str | None = None,
    action: str | None = None,
    tool: str | None = None,
    observation: Any = None,
    state_diff: Any = None,
    evidence: list[Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = _snapshot(state, patch, node)
    payload = {
        "node": node,
        "actor": actor,
        "task_id": task_id,
        "action": action,
        "tool": tool,
        "observation": observation,
        "state_diff": state_diff if state_diff is not None else {},
        "evidence": evidence or [],
        "evidence_refs": evidence or [],
        "case_version": snapshot["case_version"],
        "plan_version": snapshot["plan_version"],
        "state_snapshot": snapshot,
    }
    if details:
        payload.update(details)
    return {"event_type": event_type, "actor": actor, "payload": payload}
