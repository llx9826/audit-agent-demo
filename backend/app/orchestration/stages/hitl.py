"""人工协同阶段：持久化请求、interrupt 暂停、结构化命令恢复。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from ...domain.models import PageAsset, SupplementRequest
from ...graph.common import _event
from ...graph.state import AuditState


def prepare_problem(state: AuditState) -> dict[str, Any]:
    """缺件/不可读是确定性结果，绑定制度依据后建立人工任务。"""

    open_supplements = {
        item["task_id"] for item in state.get("supplement_requests", []) if item.get("status") == "OPEN"
    }
    task = next(
        item for item in state.get("audit_plan", [])
        if item.get("status") in {"MISSING", "UNREADABLE"}
    )
    grounding = next(
        (item for item in state.get("supplement_groundings", []) if item.get("task_id") == task["task_id"]),
        None,
    )
    if task["task_id"] in open_supplements:
        action = "SUPPLEMENT_RECEIVED"
        request_type = "SUPPLEMENT_UPLOAD"
        title = "等待补件影像"
        reason = "补件单已发出，等待新影像入件"
        resume_contract = ["event_id", "action", "task_id", "page"]
        event_type = "SUPPLEMENT_WAITING"
    else:
        action = "REQUEST_SUPPLEMENT" if task["status"] == "MISSING" else "REVIEW_IMAGE"
        request_type = "MATERIAL_HITL"
        title = "发起缺件补件" if action == "REQUEST_SUPPLEMENT" else "复核不可读影像"
        reason = "规则已确认当前进件未匹配到必需材料" if action == "REQUEST_SUPPLEMENT" else "机器恢复仍未形成可读证据"
        resume_contract = ["event_id", "action", "task_id"]
        event_type = "DETERMINISTIC_HITL_PREPARED"
    request = {
        "type": request_type,
        "action": action,
        "task_id": task["task_id"],
        "person_id": task["person_id"],
        "material_type": task["material_type"],
        "requirement_id": task["requirement_id"],
        "title": title,
        "reason": reason,
        "requirement_grounding": grounding,
        "resume_contract": resume_contract,
    }
    patch = {
        "pending_human_request": request,
        "active_node": "prepare_problem_human",
        "current_task_id": task["task_id"],
    }
    event = _event(
        state, patch, event_type=event_type, node="prepare_problem_human", actor="workflow",
        task_id=task["task_id"], action=action, observation=request,
        evidence=[grounding["evidence_id"]] if grounding else [],
    )
    return {**patch, "pending_events": [event]}


def prepare(state: AuditState) -> dict[str, Any]:
    """在 interrupt 前只做可重放的状态更新，不产生外部非幂等副作用。"""

    request = deepcopy(state.get("pending_human_request") or {})
    waiting_status = "WAITING_SUPPLEMENT" if request.get("action") == "SUPPLEMENT_RECEIVED" else "WAITING_HUMAN"
    tasks = deepcopy(state.get("audit_plan", []))
    for task in tasks:
        if task["task_id"] == request.get("task_id"):
            task["status"] = "WAITING_HUMAN" if waiting_status == "WAITING_HUMAN" else "MISSING"
    patch = {
        "audit_plan": tasks,
        "status": waiting_status,
        "completeness_status": waiting_status,
        "active_node": "prepare_human",
        "current_task_id": request.get("task_id"),
    }
    event = _event(
        state, patch, event_type="HITL_REQUESTED", node="prepare_human", actor="workflow",
        task_id=request.get("task_id"), action="DURABLE_INTERRUPT",
        observation=request, details={"thread_id": state.get("thread_id")},
    )
    return {**patch, "pending_events": [event]}


def await_resume(state: AuditState) -> dict[str, Any]:
    """用 LangGraph interrupt 暂停；恢复必须使用同 thread_id 的 Command(resume)。"""

    from langgraph.types import interrupt

    command = interrupt(deepcopy(state.get("pending_human_request") or {}))
    if not isinstance(command, dict):
        raise ValueError("resume command must be a structured object")
    resume_context = deepcopy(command.get("_resume_context") or {})
    public_command = {key: deepcopy(value) for key, value in command.items() if not key.startswith("_")}
    patch = {"resume_event": public_command, "status": "RUNNING", "active_node": "await_human"}
    event = _event(
        state, patch, event_type="CHECKPOINT_RESUMED", node="await_human", actor="workflow",
        task_id=command.get("task_id"), action="COMMAND_RESUME", observation=resume_context,
        details={"same_thread": resume_context.get("thread_id") == state.get("thread_id")},
    )
    return {**patch, "pending_events": [event]}


def apply_command(state: AuditState) -> dict[str, Any]:
    """校验恢复命令并应用人工修改；版本递增后交给 Reconciliation。"""

    command = deepcopy(state.get("resume_event") or {})
    request = deepcopy(state.get("pending_human_request") or {})
    if command.get("task_id") != request.get("task_id"):
        raise ValueError("resume task_id does not match the active human task")
    action = command.get("action")
    if action != request.get("action"):
        raise ValueError("resume action does not match the active human task")
    tasks = deepcopy(state.get("audit_plan", []))
    pages = deepcopy(state.get("pages", []))
    human_tasks = deepcopy(state.get("human_tasks", []))
    supplements = deepcopy(state.get("supplement_requests", []))
    task = next(item for item in tasks if item["task_id"] == command["task_id"])
    changed: list[str] = []
    observation: dict[str, Any] = {
        "action": action,
        "task_id": task["task_id"],
        "reason_code": str(command.get("reason_code") or "HUMAN_CONFIRMED"),
        "operator_id": str(command.get("operator_id") or "UNKNOWN"),
    }

    if action in {"CONFIRM_OWNER", "REVIEW_IMAGE"}:
        page_id = str(command.get("page_id") or (task.get("matched_page_ids") or [""])[0])
        page = next((item for item in pages if item["page_id"] == page_id), None)
        if page is None:
            raise ValueError("page_id is required and must reference a case page")
        owner = str(command.get("person_id") or task["person_id"])
        page["owner_person_id"] = owner
        page["material_type"] = str(command.get("material_type") or task["material_type"])
        page["status"] = "VERIFIED"
        page["confidence"] = 1.0
        changed.extend([f"page:{page_id}", f"person:{owner}", f"material:{page['material_type']}"])
        candidate_options = list(request.get("candidate_options") or [])
        selected_candidate_id = command.get("selected_candidate_id")
        if selected_candidate_id and selected_candidate_id not in {
            item.get("candidate_id") for item in candidate_options
        }:
            raise ValueError("selected_candidate_id is outside the active candidate set")
        if not selected_candidate_id:
            matching = [
                item for item in candidate_options
                if page_id in item.get("page_ids", [])
                and owner == item.get("proposed_person_id")
                and page["material_type"] == item.get("proposed_material_type")
            ]
            if len(matching) == 1:
                selected_candidate_id = matching[0].get("candidate_id")
        observation.update({
            "page_id": page_id,
            "person_id": owner,
            "material_type": page["material_type"],
            "selected_candidate_id": selected_candidate_id,
        })
    elif action == "REQUEST_SUPPLEMENT":
        if not any(item["task_id"] == task["task_id"] and item.get("status") == "OPEN" for item in supplements):
            supplements.append(asdict(SupplementRequest(
                request_id=f"SUP-{uuid4().hex[:8].upper()}",
                task_id=task["task_id"],
                requirement_id=task["requirement_id"],
                person_id=task["person_id"],
                material_type=task["material_type"],
            )))
        changed.append(f"supplement:{task['task_id']}")
    elif action == "SUPPLEMENT_RECEIVED":
        page_payload = command.get("page")
        if not isinstance(page_payload, dict) or not page_payload.get("page_id"):
            raise ValueError("SUPPLEMENT_RECEIVED requires page.page_id")
        page = asdict(PageAsset(
            page_id=str(page_payload["page_id"]),
            bundle_id=str(page_payload.get("bundle_id", "SUPPLEMENT")),
            page_number=int(page_payload.get("page_number", len(pages) + 1)),
            domain=str(page_payload.get("domain", "声明、附件与其他材料")),
            material_type=task["material_type"],
            owner_person_id=task["person_id"],
            status="VERIFIED",
            confidence=float(page_payload.get("confidence", 1.0)),
            thumbnail_url=page_payload.get("thumbnail_url"),
            preview_url=page_payload.get("preview_url"),
            extracted_fields=deepcopy(page_payload.get("extracted_fields", {})),
            evidence_refs=[str(item) for item in page_payload.get("evidence_refs", [f"EV-{page_payload['page_id']}"])],
        ))
        if not any(item["page_id"] == page["page_id"] for item in pages):
            pages.append(page)
        for supplement in supplements:
            if supplement["task_id"] == task["task_id"] and supplement.get("status") == "OPEN":
                supplement["status"] = "RECEIVED"
                supplement["received_page_ids"] = [page["page_id"]]
        changed.extend([
            f"page:{page['page_id']}", f"person:{task['person_id']}", f"material:{task['material_type']}",
        ])
        observation["page_id"] = page["page_id"]
    else:
        raise ValueError(f"unsupported resume action: {action}")

    for human_task in human_tasks:
        if human_task.get("human_task_id") == request.get("human_task_id"):
            human_task["status"] = "RESOLVED"
            human_task["resolution"] = command
    next_version = int(state.get("case_version", 1)) + 1
    patch = {
        "pages": pages,
        "human_tasks": human_tasks,
        "supplement_requests": supplements,
        "changed_facts": changed,
        "case_version": next_version,
        "pending_human_request": None,
        "active_node": "apply_human_command",
        "current_task_id": task["task_id"],
    }
    event = _event(
        state, patch,
        event_type="SUPPLEMENT_RECEIVED" if action == "SUPPLEMENT_RECEIVED" else "HUMAN_DECISION_APPLIED",
        node="apply_human_command", actor="human", task_id=task["task_id"],
        action=action, observation=observation,
        state_diff={"case_version": [state.get("case_version", 1), next_version], "changed_facts": changed},
    )
    return {**patch, "pending_events": [event]}


__all__ = ["apply_command", "await_resume", "prepare", "prepare_problem"]
