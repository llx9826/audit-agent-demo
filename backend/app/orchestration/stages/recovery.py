"""共享 Exception Recovery Sub-Agent 的主图适配层。

三个上游来源先建立同一种最小 Handoff，再进入唯一的异常 Agent 节点。Agent 只
产生 Observation；来源相关的字段校验由确定性适配器完成，最后统一经过 Result
Gate 决定回到 Association Evidence、Task Matcher 或 HITL。
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Literal
from uuid import uuid4

from ...agents.exception_recovery import ExceptionTask
from ...domain.models import HumanTask
from ...graph.common import _event
from ...graph.state import AuditState
from ..dependencies import ExceptionRecoveryCapability


RecoveryOrigin = Literal["CASE_ASSOCIATION", "MATERIAL_MATCHER", "MATERIAL_AUDIT"]


def _matcher_recovery_scope(state: AuditState) -> tuple[dict[str, Any], list[str]]:
    recovery_statuses = {"LOW_CONFIDENCE", "TOOL_FAILURE", "PAGE_INTEGRITY_AMBIGUOUS"}
    page_ids = {
        str(page["page_id"])
        for page in state.get("pages", [])
        if page.get("status") in recovery_statuses
    }
    tasks = sorted(
        (
            task for task in state.get("audit_plan", [])
            if task.get("status") in {"UNREADABLE", "AMBIGUOUS"}
            and page_ids.intersection(task.get("matched_page_ids", []))
        ),
        key=lambda item: item["task_id"],
    )
    if not tasks:
        raise ValueError("matcher recovery requires a scoped failed task")
    task = tasks[0]
    scoped = sorted(page_ids.intersection(task.get("matched_page_ids", [])))
    return task, scoped


def prepare_handoff(state: AuditState, *, origin: RecoveryOrigin) -> dict[str, Any]:
    """把不同来源规范化为可审计、可版本校验的最小恢复合同。"""

    if origin == "CASE_ASSOCIATION":
        request = state.get("association_recovery_request") or {}
        source_task_id = str(request.get("source_task_id") or "ASSOCIATION-RECOVERY")
        page_ids = list(dict.fromkeys(request.get("page_ids") or state.get("association_page_ids", [])))
        exception_type = str(request.get("exception_type") or "OWNER_ASSIGNMENT_AMBIGUOUS")
        problem = str(request.get("problem") or "人员、角色或材料归属证据不足")
        return_target = "ASSOCIATION_EVIDENCE"
    elif origin == "MATERIAL_AUDIT":
        assignment = state.get("audit_assignment") or {}
        decision = state.get("audit_decision") or {}
        issue = assignment.get("issue") or {}
        source_task_id = str(issue.get("task_id"))
        page_ids = list(dict.fromkeys(
            page_id
            for candidate in assignment.get("candidates", [])
            for page_id in candidate.get("page_ids", [])
        ))
        exception_type = {
            "OWNER_EVIDENCE_INSUFFICIENT": "OWNER_ASSIGNMENT_AMBIGUOUS",
            "TYPE_EVIDENCE_INSUFFICIENT": "MATERIAL_TYPE_AMBIGUOUS",
            "CROSS_PAGE_EVIDENCE_INSUFFICIENT": "CROSS_PAGE_CONFLICT",
            "BUNDLE_EVIDENCE_INSUFFICIENT": "CROSS_PAGE_CONFLICT",
            "REQUIREMENT_EVIDENCE_INSUFFICIENT": "MATERIAL_TYPE_AMBIGUOUS",
            "PAGE_INTEGRITY_EVIDENCE_INSUFFICIENT": "PAGE_MISSING_OR_DUPLICATE",
            "TOOL_RECOVERY_REQUIRED": "TOOL_FAILURE",
        }.get(str(decision.get("exception_type")), "OWNER_ASSIGNMENT_AMBIGUOUS")
        problem = str(decision.get("rationale_summary") or "材料语义候选缺少独立 Observation")
        return_target = "TASK_MATCHER"
    else:
        task, page_ids = _matcher_recovery_scope(state)
        source_task_id = str(task["task_id"])
        page_by_id = {str(page["page_id"]): page for page in state.get("pages", [])}
        page_statuses = {page_by_id[page_id].get("status") for page_id in page_ids}
        if "TOOL_FAILURE" in page_statuses:
            exception_type = "TOOL_FAILURE"
        elif "PAGE_INTEGRITY_AMBIGUOUS" in page_statuses:
            exception_type = "PAGE_MISSING_OR_DUPLICATE"
        else:
            exception_type = "MATERIAL_IMAGE_LOW_CONFIDENCE"
        problem = f"Task {source_task_id} 的机器 Observation 不足，无法完成材料匹配"
        return_target = "TASK_MATCHER"

    handoff = {
        "schema_version": "1.0",
        "handoff_id": (
            f"EX-{state.get('case_id')}-C{state.get('case_version', 1)}-"
            f"P{state.get('plan_version', 1)}-{source_task_id}"
        ),
        "origin": origin,
        "return_target": return_target,
        "case_id": state.get("case_id"),
        "thread_id": state.get("thread_id") or state.get("case_id"),
        "case_version": int(state.get("case_version", 1)),
        "plan_version": int(state.get("plan_version", 1)),
        "source_task_id": source_task_id,
        "exception_type": exception_type,
        "problem": problem,
        "page_ids": page_ids,
    }
    patch = {
        "exception_handoff": handoff,
        "active_node": f"prepare_{origin.lower()}_recovery",
        "current_task_id": source_task_id,
    }
    event = _event(
        state, patch, event_type="EXCEPTION_HANDOFF_PREPARED", node=patch["active_node"],
        actor="workflow", task_id=source_task_id, action="BUILD_TYPED_EXCEPTION_HANDOFF",
        observation=handoff, details={"write_authority": "NONE"},
    )
    return {**patch, "pending_events": [event]}


def _run_material_recovery(
    state: AuditState,
    *,
    exception_agent: ExceptionRecoveryCapability,
    handoff: dict[str, Any],
) -> dict[str, Any]:
    """执行 Matcher/Audit 来源的恢复，并由父 Workflow 校验 Observation。"""

    tasks = deepcopy(state.get("audit_plan", []))
    pages = deepcopy(state.get("pages", []))
    task = next(item for item in tasks if item["task_id"] == handoff["source_task_id"])
    scoped_page_ids = set(handoff.get("page_ids", []))
    page = next(item for item in pages if item["page_id"] in scoped_page_ids)
    recovery_values = list((page.get("extracted_fields") or {}).get("recovery_values", []))
    vlm_value = str(recovery_values[0]) if recovery_values else str(page.get("owner_person_id") or "UNKNOWN")
    trusted_value = str(recovery_values[1]) if len(recovery_values) > 1 else str(page.get("owner_person_id") or "UNKNOWN")
    result = exception_agent.resolve(
        ExceptionTask(
            exception_type=str(handoff["exception_type"]),
            source_task_id=task["task_id"],
            problem=str(handoff["problem"]),
            evidence_refs=list(page.get("evidence_refs", [])),
            context_refs=[*sorted(scoped_page_ids), str(task.get("requirement_id"))],
        ),
        vlm_value=vlm_value,
        trusted_document_value=trusted_value,
        tool_context={
            "page": deepcopy(page),
            "scoped_page_ids": sorted(scoped_page_ids),
            "requirement_id": task.get("requirement_id"),
            "person_id": task.get("person_id"),
        },
    )
    normalized_values = [
        str(action["normalized_value"])
        for action in result.actions
        if action.get("normalized_value") is not None
    ]
    consensus_value, consensus_count = (
        Counter(normalized_values).most_common(1)[0] if normalized_values else (None, 0)
    )
    human_tasks = deepcopy(state.get("human_tasks", []))
    pending_request: dict[str, Any] | None = None
    effective_status = result.status
    audit_recovery = handoff["origin"] == "MATERIAL_AUDIT"

    if audit_recovery:
        fields = page.get("extracted_fields") or {}
        assignment = state.get("audit_assignment") or {}
        issue_type = str((assignment.get("issue") or {}).get("issue_type"))
        candidates_by_issue = {
            "OWNER_AMBIGUOUS": list(fields.get("owner_candidates", [])),
            "TYPE_AMBIGUOUS": list(fields.get("candidate_material_types", [])),
            "BUNDLE_AMBIGUOUS": list(fields.get("candidate_bundle_ids", [])),
            "REQUIREMENT_MATCH_AMBIGUOUS": list(fields.get("candidate_requirement_ids", [])),
        }
        allowed_values = [str(item) for item in candidates_by_issue.get(issue_type, [])]
        resolved = result.status == "RESOLVED" and consensus_count >= 2 and consensus_value in allowed_values
        if resolved:
            if issue_type == "OWNER_AMBIGUOUS":
                page["owner_person_id"] = consensus_value
            elif issue_type == "TYPE_AMBIGUOUS":
                page["material_type"] = consensus_value
            elif issue_type == "BUNDLE_AMBIGUOUS":
                page["bundle_id"] = consensus_value
            elif issue_type == "REQUIREMENT_MATCH_AMBIGUOUS":
                updated_fields = deepcopy(fields)
                updated_fields["matched_requirement_id"] = consensus_value
                page["extracted_fields"] = updated_fields
            page["status"] = "VERIFIED"
            page["confidence"] = max(float(action.get("confidence") or 0) for action in result.actions)
        elif handoff["exception_type"] == "PAGE_MISSING_OR_DUPLICATE" and any(
            action.get("tool") == "page_integrity_check"
            and action.get("executed")
            and float(action.get("confidence") or 0) >= 0.9
            for action in result.actions
        ):
            page["status"] = "VERIFIED"
            page["confidence"] = max(float(action.get("confidence") or 0) for action in result.actions)
            effective_status = "RESOLVED"
        elif handoff["exception_type"] == "TOOL_FAILURE" and any(
            action.get("tool") == "document_reload" and action.get("executed")
            for action in result.actions
        ):
            page["status"] = "LOW_CONFIDENCE"
            effective_status = "RESOLVED"
        else:
            effective_status = "NEED_HUMAN"
            human_task_id = f"HUMAN-{uuid4().hex[:8].upper()}"
            pending_request = {
                "type": "MATERIAL_HITL",
                "action": "CONFIRM_OWNER" if issue_type == "OWNER_AMBIGUOUS" else "REVIEW_IMAGE",
                "task_id": task["task_id"],
                "human_task_id": human_task_id,
                "person_id": task["person_id"],
                "material_type": task["material_type"],
                "requirement_id": task["requirement_id"],
                "title": {
                    "TYPE_AMBIGUOUS": "确认材料类型",
                    "BUNDLE_AMBIGUOUS": "确认跨页材料分组",
                    "REQUIREMENT_MATCH_AMBIGUOUS": "确认材料与清单项对应关系",
                }.get(issue_type, "确认材料归属"),
                "reason": result.conclusion or "异常恢复未形成两个独立一致 Observation，转人工确认。",
                "reason_code": result.stop_reason,
                "candidate_page_ids": [page["page_id"]],
                "candidate_options": assignment.get("candidates", []),
                "resume_contract": ["event_id", "action", "task_id", "page_id", "person_id"],
            }
            human_tasks.append(asdict(HumanTask(
                human_task_id=human_task_id,
                task_type=pending_request["action"],
                title=pending_request["title"],
                reason=pending_request["reason"],
                task_id=task["task_id"],
                candidate_options=assignment.get("candidates", []),
                evidence_refs=list(result.evidence_refs),
                expected_case_version=int(state.get("case_version", 1)),
            )))
    else:
        if handoff["exception_type"] == "TOOL_FAILURE" and any(
            action.get("tool") == "document_reload" and action.get("executed")
            for action in result.actions
        ):
            page["status"] = "LOW_CONFIDENCE"
            effective_status = "RESOLVED"
        elif handoff["exception_type"] == "PAGE_MISSING_OR_DUPLICATE" and any(
            action.get("tool") == "page_integrity_check"
            and action.get("executed")
            and float(action.get("confidence") or 0) >= 0.9
            for action in result.actions
        ):
            page["status"] = "VERIFIED"
            effective_status = "RESOLVED"
        else:
            page["status"] = "VERIFIED" if result.status == "RESOLVED" else "RECOVERY_EXHAUSTED"
            page["confidence"] = result.confidence if result.status == "RESOLVED" else page.get("confidence")

    page["evidence_refs"] = list(dict.fromkeys([*page.get("evidence_refs", []), *result.evidence_refs]))
    patch = {
        "pages": pages,
        "pending_human_request": pending_request,
        "human_tasks": human_tasks,
        "exception_context": {
            "source_task_id": task["task_id"],
            "page_id": page["page_id"],
            "handoff_source": handoff["origin"],
            "exception_type": handoff["exception_type"],
            "status": effective_status,
            "stop_reason": result.stop_reason,
            "steps_used": result.steps_used,
            "step_budget": result.step_budget,
            "tool_trace": result.actions,
            "decision_trace": result.decision_trace,
            "loop_guard_triggered": result.loop_guard_triggered,
        },
        "active_node": "exception_recovery_agent",
        "current_task_id": task["task_id"],
    }
    events = [
        _event(
            state, patch, event_type="HANDOFF_CREATED", node="exception_recovery_agent",
            actor="workflow", task_id=task["task_id"], action="DELEGATE_TO_EXCEPTION_SUB_AGENT",
            observation={
                "page_id": page["page_id"],
                "max_steps": result.step_budget,
                "exception_type": handoff["exception_type"],
            },
            evidence=list(page.get("evidence_refs", [])),
            details={"handoff": {
                "source": handoff["origin"],
                "context_isolation": True,
                "allowed_tools": result.allowed_tools,
            }},
        ),
    ]
    for action in result.actions:
        events.append(_event(
            state, patch, event_type="AGENT_TOOL_FINISHED", node="exception_recovery_agent",
            actor="exception_agent", task_id=task["task_id"], action="BOUNDED_TOOL_LOOP",
            tool=action["tool"],
            observation={
                "step": action["step"],
                "result": action.get("result"),
                "state_changed": action.get("state_changed", False),
            },
            evidence=list(action.get("evidence_refs", [])),
            details={"remaining_budget": action.get("remaining_budget_after")},
        ))
    events.append(_event(
        state, patch,
        event_type="EXCEPTION_RESOLVED" if effective_status == "RESOLVED" else "EXCEPTION_NEEDS_HUMAN",
        node="exception_recovery_agent", actor="exception_agent", task_id=task["task_id"],
        action="RETURN_TYPED_RESOLUTION",
        observation={
            "status": effective_status,
            "stop_reason": result.stop_reason,
            "steps_used": result.steps_used,
        },
        evidence=result.evidence_refs,
    ))
    return {**patch, "pending_events": events}


def run(state: AuditState, *, exception_agent: ExceptionRecoveryCapability) -> dict[str, Any]:
    """唯一共享异常 Agent 节点；按 Handoff 来源调用确定性结果适配器。"""

    handoff = deepcopy(state.get("exception_handoff") or {})
    if not handoff:
        raise ValueError("exception recovery requires a prepared handoff")
    if (
        handoff.get("case_id") != state.get("case_id")
        or handoff.get("thread_id") != (state.get("thread_id") or state.get("case_id"))
        or int(handoff.get("case_version", -1)) != int(state.get("case_version", 1))
        or int(handoff.get("plan_version", -1)) != int(state.get("plan_version", 1))
    ):
        raise ValueError("stale exception handoff rejected before agent execution")

    if handoff["origin"] == "CASE_ASSOCIATION":
        # 关联恢复的 Observation 由 Association Gate 适配器校验并回填页级投影。
        from . import association

        patch = association.recover(state, exception_agent=exception_agent)
    else:
        patch = _run_material_recovery(
            state,
            exception_agent=exception_agent,
            handoff=handoff,
        )

    patch["active_node"] = "exception_recovery_agent"
    patch["exception_handoff"] = handoff
    patch["pending_events"] = [
        {**event, "node": "exception_recovery_agent"}
        if event.get("node") in {"association_exception_recovery", "exception_recovery"}
        else event
        for event in patch.get("pending_events", [])
    ]
    return patch


def result_gate(state: AuditState) -> dict[str, Any]:
    """校验共享 Agent 返回与 Handoff 一致，并产生唯一回程路由。"""

    handoff = deepcopy(state.get("exception_handoff") or {})
    result = deepcopy(state.get("exception_context") or {})
    output_matches_handoff = bool(
        handoff
        and result
        and str(result.get("source_task_id")) == str(handoff.get("source_task_id"))
        and str(result.get("exception_type")) == str(handoff.get("exception_type"))
    )
    if not output_matches_handoff:
        raise ValueError("exception result does not match its typed handoff")

    if state.get("pending_human_request"):
        route = "ASSOCIATION_HUMAN" if handoff["origin"] == "CASE_ASSOCIATION" else "MATERIAL_HUMAN"
    else:
        route = (
            "ASSOCIATION_RETRY"
            if handoff["return_target"] == "ASSOCIATION_EVIDENCE"
            else "MATCHER_RETRY"
        )
    patch = {
        "exception_result_gate": {
            "accepted": True,
            "origin": handoff["origin"],
            "return_target": handoff["return_target"],
            "route": route,
        },
        "exception_handoff": None,
        "active_node": "exception_result_gate",
    }
    event = _event(
        state, patch, event_type="EXCEPTION_RESULT_GATE_EVALUATED", node="exception_result_gate",
        actor="workflow_result_gate", task_id=handoff["source_task_id"],
        action="VALIDATE_AND_ROUTE_EXCEPTION_RESULT",
        observation=patch["exception_result_gate"],
        details={"write_authority": "WORKFLOW_RESULT_GATE"},
    )
    return {**patch, "pending_events": [event]}


def route_after_result(
    state: AuditState,
) -> Literal["association_retry", "association_human", "matcher_retry", "material_human"]:
    route = (state.get("exception_result_gate") or {}).get("route")
    return {
        "ASSOCIATION_RETRY": "association_retry",
        "ASSOCIATION_HUMAN": "association_human",
        "MATCHER_RETRY": "matcher_retry",
        "MATERIAL_HUMAN": "material_human",
    }[str(route)]


__all__ = ["prepare_handoff", "result_gate", "route_after_result", "run"]
