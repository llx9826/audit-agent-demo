"""Task Orchestrator 与并行材料匹配 Worker。

这个阶段只做确定性材料齐套匹配：Orchestrator 解析依赖并用 ``Send`` 分发
彼此独立的 Task，Worker 只返回候选结果，最后由单一 Fan-in Gate 校验版本并
写回 ``audit_plan``。Agent、Tool 与 HITL 的分支仍由主 Pipeline 统一编排。
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any, Literal

from ...graph.common import _event
from ...graph.state import AuditState


def _ready(
    task: dict[str, Any],
    task_by_id: dict[str, dict[str, Any]],
    *,
    current_task_id: str | None,
    dirty_task_ids: set[str],
) -> bool:
    """只有 Task 依赖均已提交时才可分发；事实依赖已由上游 Gate 确认。"""

    if task.get("status") == "MATCHED" and task.get("result"):
        return False
    selected_for_execution = (
        task.get("status") in {"PENDING", "DIRTY", "INVALIDATED", "FAILED"}
        or task["task_id"] == current_task_id
        or task["task_id"] in dirty_task_ids
    )
    if not selected_for_execution:
        return False
    return all(
        task_by_id.get(dependency, {}).get("status") in {"MATCHED", "SUCCESS"}
        for dependency in task.get("task_dependencies", [])
    )


def resolve_ready_tasks(state: AuditState) -> dict[str, Any]:
    """解析 Ready Batch，并生成可重放的 Dispatch ID。"""

    tasks = state.get("audit_plan", [])
    task_by_id = {task["task_id"]: task for task in tasks}
    current_task_id = state.get("current_task_id")
    dirty_task_ids = set(state.get("dirty_tasks", []))
    ready_ids = sorted(
        task["task_id"] for task in tasks
        if _ready(
            task,
            task_by_id,
            current_task_id=current_task_id,
            dirty_task_ids=dirty_task_ids,
        )
    )
    dispatch_material = "|".join([
        str(state.get("case_id", "")),
        str(state.get("case_version", 1)),
        str(state.get("plan_version", 1)),
        *ready_ids,
    ])
    dispatch_id = f"DISPATCH-{sha256(dispatch_material.encode('utf-8')).hexdigest()[:12].upper()}"
    blocked = [
        task["task_id"] for task in tasks
        if task["task_id"] not in ready_ids
        and not (task.get("status") == "MATCHED" and task.get("result"))
    ]
    patch = {
        "ready_task_ids": ready_ids,
        "task_dispatch_id": dispatch_id,
        "active_node": "resolve_ready_tasks",
        "current_task_id": None,
    }
    event = _event(
        state,
        patch,
        event_type="READY_TASKS_DISPATCHED",
        node="resolve_ready_tasks",
        actor="task_orchestrator",
        action="RESOLVE_DEPENDENCIES_AND_DISPATCH",
        observation={
            "dispatch_id": dispatch_id,
            "ready_task_ids": ready_ids,
            "blocked_task_ids": blocked,
            "dispatch_mode": "LANGGRAPH_SEND",
        },
        details={"worker": "match_task_worker", "fan_in_gate": "match_materials"},
    )
    return {**patch, "pending_events": [event]}


def dispatch_ready_tasks(state: AuditState) -> list[Any] | Literal["match_materials"]:
    """用 LangGraph Send 发送最小 Worker Context，而不是复制整笔 Case。"""

    from langgraph.types import Send

    ready_ids = state.get("ready_task_ids", [])
    if not ready_ids:
        return "match_materials"
    tasks = {task["task_id"]: task for task in state.get("audit_plan", [])}
    requirements = {
        requirement["requirement_id"]: requirement
        for requirement in state.get("requirements", [])
    }
    common = {
        "case_id": state.get("case_id"),
        "thread_id": state.get("thread_id"),
        "case_version": state.get("case_version", 1),
        "plan_version": state.get("plan_version", 1),
        "task_dispatch_id": state.get("task_dispatch_id"),
    }
    return [
        Send(
            "match_task_worker",
            {
                **common,
                "worker_task": deepcopy(tasks[task_id]),
                "worker_requirement": deepcopy(requirements[tasks[task_id]["requirement_id"]]),
                # Worker 只读页级投影；没有 Case、Prompt、Agent 或持久化写权限。
                "worker_pages": deepcopy(state.get("pages", [])),
                "worker_case_version": int(state.get("case_version", 1)),
                "worker_plan_version": int(state.get("plan_version", 1)),
            },
        )
        for task_id in ready_ids
    ]


def match_task_worker(state: AuditState) -> dict[str, Any]:
    """独立计算一个人员 × Requirement Task，不直接修改主 State。"""

    task = deepcopy(state["worker_task"])
    requirement = state["worker_requirement"]
    pages = state.get("worker_pages", [])
    required_pages = int(requirement.get("required_pages", 1))
    same_material = [page for page in pages if page.get("material_type") == task["material_type"]]
    exact = [page for page in same_material if page.get("owner_person_id") == task["person_id"]]
    verified = [page for page in exact if page.get("status") == "VERIFIED"]
    low_confidence = [
        page for page in exact
        if page.get("status") in {"LOW_CONFIDENCE", "RECOVERY_EXHAUSTED"}
    ]
    ambiguity_statuses = {
        "OWNER_AMBIGUOUS",
        "TYPE_AMBIGUOUS",
        "BUNDLE_AMBIGUOUS",
        "REQUIREMENT_MATCH_AMBIGUOUS",
        "PAGE_INTEGRITY_AMBIGUOUS",
        "TOOL_FAILURE",
    }
    ambiguous = [page for page in same_material if page.get("status") in ambiguity_statuses]
    # 类型/Requirement 尚未确定的页面不会出现在 same_material 中，必须通过上游
    # VLM 给出的封闭候选字段参与当前 Task 的匹配，不能靠 Agent 新造候选。
    ambiguous.extend(
        page for page in pages
        if page.get("status") in ambiguity_statuses
        and page not in ambiguous
        and (
            task["material_type"] in (page.get("extracted_fields") or {}).get("candidate_material_types", [])
            or task["requirement_id"] in (page.get("extracted_fields") or {}).get("candidate_requirement_ids", [])
        )
    )

    if len(verified) >= required_pages:
        selected = verified[:required_pages]
        status = "MATCHED"
        confidence = min(float(page.get("confidence") or 0) for page in selected)
    elif low_confidence:
        selected = [*verified, *low_confidence]
        status = "UNREADABLE"
        confidence = min(float(page.get("confidence") or 0) for page in selected)
    elif ambiguous:
        selected = ambiguous
        status = "AMBIGUOUS"
        confidence = max(float(page.get("confidence") or 0) for page in selected)
    else:
        selected = verified
        status = "MISSING"
        confidence = 0.0

    page_ids = [page["page_id"] for page in selected]
    evidence_refs = list(dict.fromkeys(
        ref for page in selected for ref in page.get("evidence_refs", [])
    ))
    result_version = int(task.get("result_version", 0)) + 1
    task.update({
        "status": status,
        "matched_page_ids": page_ids,
        "evidence_refs": evidence_refs,
        "result_version": result_version,
    })
    if status == "MATCHED":
        task["result"] = {
            "task_id": task["task_id"],
            "status": "PASS",
            "conclusion": "已匹配应提供材料影像",
            "confidence": confidence,
            "evidence_refs": evidence_refs,
            "requirement_refs": list(task.get("requirement_refs") or [task["requirement_id"]]),
            "case_version": state.get("worker_case_version", 1),
            "plan_version": state.get("worker_plan_version", 1),
            "result_version": result_version,
        }
    else:
        task["result"] = None

    match = {
        "match_id": f"MATCH-{task['task_id']}",
        "task_id": task["task_id"],
        "requirement_id": task["requirement_id"],
        "person_id": task["person_id"],
        "page_ids": page_ids,
        "status": status,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "decided_by": "WORKFLOW_SEND_WORKER",
    }
    worker_result = {
        "dispatch_id": state.get("task_dispatch_id"),
        "task_id": task["task_id"],
        "expected_case_version": state.get("worker_case_version", 1),
        "expected_plan_version": state.get("worker_plan_version", 1),
        "conflict_keys": list(task.get("conflict_keys", [])),
        "task": task,
        "match": match,
        "required_pages": required_pages,
    }
    event = _event(
        state,
        {},
        event_type="TASK_WORKER_COMPLETED",
        node="match_task_worker",
        actor="material_match_worker",
        task_id=task["task_id"],
        action="MATCH_REQUIREMENT_TO_PAGES",
        observation={
            "dispatch_id": state.get("task_dispatch_id"),
            "status": status,
            "page_ids": page_ids,
            "required_pages": required_pages,
            "result_version": result_version,
        },
        evidence=evidence_refs,
        details={"write_authority": "NONE", "worker_context": "TASK_SCOPED"},
    )
    return {"task_worker_results": [worker_result], "pending_events": [event]}


def match_materials(state: AuditState) -> dict[str, Any]:
    """Fan-in Gate：串行校验 Worker 版本、冲突组并提交 Task Result。"""

    dispatch_id = state.get("task_dispatch_id")
    ready_ids = set(state.get("ready_task_ids", []))
    candidates = [
        result for result in state.get("task_worker_results", [])
        if result.get("dispatch_id") == dispatch_id and result.get("task_id") in ready_ids
    ]
    # Checkpoint 重放可能留下同一 Dispatch 的重复结果；按 task_id 做幂等收敛。
    result_by_task = {result["task_id"]: result for result in candidates}
    tasks = deepcopy(state.get("audit_plan", []))
    matches_by_task = {
        match["task_id"]: deepcopy(match) for match in state.get("material_matches", [])
    }
    events: list[dict[str, Any]] = []
    stale_ids: list[str] = []
    committed_ids: list[str] = []
    conflict_groups: dict[str, list[str]] = {}

    for task in sorted(tasks, key=lambda item: item["task_id"]):
        if task["task_id"] not in ready_ids:
            continue
        worker = result_by_task.get(task["task_id"])
        if worker is None:
            task.update({"status": "FAILED", "result": None})
            stale_ids.append(task["task_id"])
            continue
        version_matches = (
            int(worker.get("expected_case_version", -1)) == int(state.get("case_version", 1))
            and int(worker.get("expected_plan_version", -1)) == int(state.get("plan_version", 1))
        )
        if not version_matches:
            task.update({"status": "INVALIDATED", "result": None})
            stale_ids.append(task["task_id"])
            events.append(_event(
                state,
                {},
                event_type="STALE_TASK_RESULT_REJECTED",
                node="match_materials",
                actor="plan_gate",
                task_id=task["task_id"],
                action="REJECT_VERSION_MISMATCH",
                observation={
                    "expected_case_version": worker.get("expected_case_version"),
                    "current_case_version": state.get("case_version", 1),
                    "expected_plan_version": worker.get("expected_plan_version"),
                    "current_plan_version": state.get("plan_version", 1),
                },
            ))
            continue
        task.clear()
        task.update(deepcopy(worker["task"]))
        matches_by_task[task["task_id"]] = deepcopy(worker["match"])
        committed_ids.append(task["task_id"])
        for key in worker.get("conflict_keys", []):
            conflict_groups.setdefault(key, []).append(task["task_id"])
        events.append(_event(
            state,
            {},
            event_type="MATERIAL_TASK_EVALUATED",
            node="match_materials",
            actor="plan_gate",
            task_id=task["task_id"],
            action="COMMIT_WORKER_RESULT",
            observation={
                "status": task["status"],
                "person_id": task["person_id"],
                "material_type": task["material_type"],
                "page_ids": task.get("matched_page_ids", []),
                "result_version": task.get("result_version", 0),
            },
            evidence=task.get("evidence_refs", []),
            details={"write_authority": "WORKFLOW_FAN_IN_GATE"},
        ))

    serialized_conflicts = {
        key: task_ids for key, task_ids in conflict_groups.items() if len(task_ids) > 1
    }
    patch = {
        "audit_plan": tasks,
        "material_matches": [matches_by_task[key] for key in sorted(matches_by_task)],
        "ready_task_ids": [],
        "active_node": "match_materials",
        "current_task_id": None,
    }
    events.append(_event(
        state,
        patch,
        event_type="TASK_FAN_IN_COMMITTED",
        node="match_materials",
        actor="plan_gate",
        action="VALIDATE_AND_COMMIT_READY_BATCH",
        observation={
            "dispatch_id": dispatch_id,
            "expected_count": len(ready_ids),
            "committed_task_ids": committed_ids,
            "rejected_task_ids": stale_ids,
            "serialized_conflict_groups": serialized_conflicts,
        },
        details={"write_authority": "WORKFLOW_FAN_IN_GATE"},
    ))
    return {**patch, "pending_events": events}


def recovery_route(state: AuditState) -> Literal["recover", "validate"]:
    """机器 Observation 故障直接进入 Exception，不伪装成语义歧义。"""

    recovery_page_ids = {
        page["page_id"] for page in state.get("pages", [])
        if page.get("status") in {"LOW_CONFIDENCE", "TOOL_FAILURE", "PAGE_INTEGRITY_AMBIGUOUS"}
    }
    requires_recovery = any(
        task.get("status") in {"UNREADABLE", "AMBIGUOUS"}
        and recovery_page_ids.intersection(task.get("matched_page_ids", []))
        for task in state.get("audit_plan", [])
    )
    return "recover" if requires_recovery else "validate"


def validate_completeness(state: AuditState) -> dict[str, Any]:
    """汇总 Task 结果，保持“缺件由 Workflow 判定”的业务边界。"""

    problem_tasks = [
        {
            "task_id": task["task_id"],
            "requirement_id": task.get("requirement_id"),
            "person_id": task.get("person_id"),
            "person_role": task.get("person_role"),
            "material_type": task.get("material_type"),
            "status": task.get("status"),
            "matched_page_ids": list(task.get("matched_page_ids", [])),
        }
        for task in state.get("audit_plan", [])
        if task.get("status") != "MATCHED"
    ]
    patch = {
        "problem_tasks": problem_tasks,
        "completeness_status": "INCOMPLETE" if problem_tasks else "COMPLETE",
        "active_node": "validate_completeness",
        "current_task_id": problem_tasks[0]["task_id"] if problem_tasks else None,
    }
    event = _event(
        state,
        patch,
        event_type="COMPLETENESS_CHECKED",
        node="validate_completeness",
        actor="validator",
        action="CHECK_ALL_REQUIREMENT_PERSON_TASKS",
        observation={
            "result": "PROBLEM_FOUND" if problem_tasks else "ALL_MATERIALS_COMPLETE",
            "problem_count": len(problem_tasks),
            "problems": problem_tasks,
        },
        details={"next": "TASK_OUTCOME_ROUTER" if problem_tasks else "FINAL_VALIDATOR"},
    )
    return {**patch, "pending_events": [event]}


def issue_route(state: AuditState) -> Literal["audit", "ground", "complete"]:
    """Task Outcome Router：按结果确定性选择 Agent、RAG 或完成。"""

    statuses = {item.get("status") for item in state.get("problem_tasks", [])}
    if "AMBIGUOUS" in statuses:
        return "audit"
    if statuses.intersection({"MISSING", "UNREADABLE"}):
        return "ground"
    return "complete"


__all__ = [
    "dispatch_ready_tasks",
    "issue_route",
    "match_materials",
    "match_task_worker",
    "recovery_route",
    "resolve_ready_tasks",
    "validate_completeness",
]
