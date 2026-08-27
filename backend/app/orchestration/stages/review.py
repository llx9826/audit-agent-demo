"""材料语义仲裁阶段。

Workflow 先依据已确认人员、当前 Audit Plan 和页级 Observation 构造封闭候选；
Material Audit Agent 只做一次结构化选择；Plan Gate 再校验版本、作用域和证据后
写入主状态。Agent 没有 Tool，也不能绕过 Gate 修改 Case。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from itertools import product
from typing import Any, Literal
from uuid import uuid4

from ...agents.contracts import MaterialAuditAssignment, MaterialCandidate, MaterialIssue
from ...domain.models import HumanTask
from ...graph.common import _event
from ...graph.state import AuditState
from ..dependencies import MaterialSemanticDecider


_SEMANTIC_ISSUE_BY_PAGE_STATUS = {
    "TYPE_AMBIGUOUS": "TYPE_AMBIGUOUS",
    "BUNDLE_AMBIGUOUS": "BUNDLE_AMBIGUOUS",
    "REQUIREMENT_MATCH_AMBIGUOUS": "REQUIREMENT_MATCH_AMBIGUOUS",
}


def _active_ambiguous_task(state: AuditState) -> dict[str, Any]:
    """选择稳定的当前歧义 Task，避免依赖列表偶然顺序。"""

    tasks = sorted(
        (item for item in state.get("audit_plan", []) if item.get("status") == "AMBIGUOUS"),
        key=lambda item: item["task_id"],
    )
    if not tasks:
        raise ValueError("material semantic review requires an AMBIGUOUS task")
    return tasks[0]


def _confirmed_people(state: AuditState) -> set[str]:
    return {
        str(person["person_id"])
        for person in state.get("persons", [])
        if person.get("confirmed", True)
    }


def _confirmed_owner_by_page(state: AuditState) -> dict[str, str]:
    """Association Gate 的已确认归属是高优先级事实，仲裁 Agent 无权覆盖。"""

    return {
        str(binding["page_id"]): str(binding["person_id"])
        for binding in state.get("material_owner_bindings", [])
        if binding.get("status") == "CONFIRMED"
    }


def _candidate_scope(state: AuditState) -> set[tuple[str, str, str]]:
    """返回当前 Plan 已存在的 person/material/requirement 组合。"""

    return {
        (str(task["person_id"]), str(task["material_type"]), str(task["requirement_id"]))
        for task in state.get("audit_plan", [])
    }


def _build_candidates(
    state: AuditState,
    task: dict[str, Any],
    candidate_pages: list[dict[str, Any]],
) -> tuple[list[MaterialCandidate], list[dict[str, Any]]]:
    """过滤越界组合，再稳定排序选出最多八个可重放候选。"""

    confirmed_people = _confirmed_people(state)
    confirmed_owner = _confirmed_owner_by_page(state)
    scoped_combinations = _candidate_scope(state)
    candidates_by_key: dict[tuple[str, str, str, str, str | None], MaterialCandidate] = {}
    pruned: list[dict[str, Any]] = []

    for page in sorted(candidate_pages, key=lambda item: str(item["page_id"])):
        page_id = str(page["page_id"])
        fields = page.get("extracted_fields") or {}
        observed_owners = [str(value) for value in fields.get("owner_candidates") or [task["person_id"]]]
        authoritative_owner = confirmed_owner.get(page_id)
        if authoritative_owner:
            observed_owners = [authoritative_owner]
        owners = sorted(set(observed_owners).intersection(confirmed_people))
        material_types = sorted(set(
            str(value)
            for value in fields.get("candidate_material_types") or [task["material_type"]]
        ))
        requirement_ids = sorted(set(
            str(value)
            for value in fields.get("candidate_requirement_ids") or [task["requirement_id"]]
        ))
        raw_bundles = fields.get("candidate_bundle_ids") or [page.get("bundle_id")]
        bundle_ids = sorted({str(value) for value in raw_bundles if value}) or [None]

        if not owners:
            pruned.append({"page_id": page_id, "reason": "NO_CONFIRMED_PERSON_CANDIDATE"})
            continue

        for person_id, material_type, requirement_id, bundle_id in product(
            owners, material_types, requirement_ids, bundle_ids,
        ):
            if (person_id, material_type, requirement_id) not in scoped_combinations:
                pruned.append({
                    "page_id": page_id,
                    "reason": "OUTSIDE_CURRENT_PLAN",
                    "person_id": person_id,
                    "material_type": material_type,
                    "requirement_id": requirement_id,
                })
                continue
            score = float(page.get("confidence") or 0.0)
            score += 0.08 if authoritative_owner == person_id else 0.0
            score += 0.04 if person_id == str(task["person_id"]) else 0.0
            score += 0.04 if material_type == str(task["material_type"]) else 0.0
            score += 0.04 if requirement_id == str(task["requirement_id"]) else 0.0
            key = (page_id, person_id, material_type, requirement_id, bundle_id)
            candidate = MaterialCandidate(
                candidate_id=(
                    f"CAND-{task['task_id']}-{page_id}-{person_id}-"
                    f"{material_type}-{requirement_id}-{bundle_id or 'NO_BUNDLE'}"
                ),
                page_ids=[page_id],
                proposed_person_id=person_id,
                proposed_material_type=material_type,
                proposed_requirement_id=requirement_id,
                proposed_bundle_id=bundle_id,
                evidence_refs=list(dict.fromkeys(page.get("evidence_refs", []))),
                observations={
                    "page_status": page.get("status"),
                    "authoritative_owner": authoritative_owner,
                    "source": "WORKFLOW_MATCHER",
                },
                workflow_score=min(score, 1.0),
            )
            previous = candidates_by_key.get(key)
            if previous is None or candidate.workflow_score > previous.workflow_score:
                candidates_by_key[key] = candidate

    ordered = sorted(
        candidates_by_key.values(),
        key=lambda item: (-item.workflow_score, item.candidate_id),
    )
    if len(ordered) > 8:
        pruned.append({"reason": "TOP_K_LIMIT", "removed_count": len(ordered) - 8})
    return ordered[:8], pruned


def run(state: AuditState, *, material_agent: MaterialSemanticDecider) -> dict[str, Any]:
    """建立最小委托合同并调用一次材料语义仲裁 Agent。"""

    task = _active_ambiguous_task(state)
    pages_by_id = {str(page["page_id"]): page for page in state.get("pages", [])}
    candidate_pages = [
        pages_by_id[page_id]
        for page_id in sorted(set(task.get("matched_page_ids", [])))
        if page_id in pages_by_id
    ]
    candidates, pruned = _build_candidates(state, task, candidate_pages)
    if not candidates:
        raise ValueError("workflow could not build a valid material semantic candidate")

    issue_type = next(
        (
            _SEMANTIC_ISSUE_BY_PAGE_STATUS[page.get("status")]
            for page in candidate_pages
            if page.get("status") in _SEMANTIC_ISSUE_BY_PAGE_STATUS
        ),
        "OWNER_AMBIGUOUS",
    )
    issue = MaterialIssue(
        task_id=task["task_id"],
        issue_type=issue_type,
        person_id=task["person_id"],
        material_type=task["material_type"],
        candidate_page_ids=list(task.get("matched_page_ids", [])),
        evidence_refs=list(task.get("evidence_refs", [])),
        confidence=next(
            (
                float(match.get("confidence", 0.0))
                for match in state.get("material_matches", [])
                if match["task_id"] == task["task_id"]
            ),
            0.0,
        ),
    )
    assignment = MaterialAuditAssignment(
        assignment_id=(
            f"MA-{state.get('case_id')}-C{state.get('case_version', 1)}-"
            f"P{state.get('plan_version', 1)}-{task['task_id']}"
        ),
        case_id=str(state.get("case_id")),
        thread_id=str(state.get("thread_id") or state.get("case_id")),
        case_version=int(state.get("case_version", 1)),
        plan_version=int(state.get("plan_version", 1)),
        objective="在 Workflow 给出的封闭候选集中消解材料语义归属",
        issue=issue,
        candidates=candidates,
        allowed_actions=["APPLY_CANDIDATE", "REQUEST_HUMAN", "REQUEST_RECOVERY"],
    )
    agent_run = material_agent.decide(assignment)
    patch = {
        "audit_assignment": assignment.model_dump(mode="json"),
        "audit_decision": agent_run.decision.model_dump(mode="json"),
        "active_node": "material_agent_review",
        "current_task_id": task["task_id"],
    }
    events = [
        _event(
            state, patch, event_type="AUDIT_CANDIDATES_BUILT", node="material_agent_review",
            actor="workflow", task_id=task["task_id"], action="BUILD_CLOSED_CANDIDATE_SET",
            observation={
                "issue": issue.model_dump(mode="json"),
                "candidates": [item.model_dump(mode="json") for item in candidates],
                "pruned": pruned,
            },
        ),
        _event(
            state, patch, event_type="PROMPT_RENDERED", node="material_agent_review",
            actor="audit_agent", task_id=task["task_id"], action="RENDER_VERSIONED_PROMPT",
            observation=agent_run.prompt.model_dump(mode="json"),
            details={"model_trace": agent_run.model_trace},
        ),
        _event(
            state, patch, event_type="AUDIT_DECISION_PROPOSED", node="material_agent_review",
            actor="audit_agent", task_id=task["task_id"], action=agent_run.decision.action,
            observation=agent_run.decision.model_dump(mode="json"),
            evidence=agent_run.decision.evidence_refs, details={"write_authority": "NONE"},
        ),
    ]
    return {**patch, "pending_events": events}


def _append_owner_binding(
    state: AuditState,
    *,
    selected: dict[str, Any],
) -> list[dict[str, Any]]:
    """把 Gate 批准的归属写成显式事实，供后续候选构造和 Replan 复用。"""

    bindings = deepcopy(state.get("material_owner_bindings", []))
    selected_pages = set(selected["page_ids"])
    bindings = [item for item in bindings if str(item.get("page_id")) not in selected_pages]
    for page_id in sorted(selected_pages):
        bindings.append({
            "binding_id": f"BIND-OWNER-{page_id}-{selected['proposed_person_id']}",
            "page_id": page_id,
            "person_id": selected["proposed_person_id"],
            "status": "CONFIRMED",
            "confidence": float(selected.get("workflow_score") or 0.0),
            "evidence_refs": list(selected.get("evidence_refs", [])),
            "decided_by": "WORKFLOW_PLAN_GATE",
        })
    return bindings


def plan_gate(state: AuditState) -> dict[str, Any]:
    """验证 Agent 提议；只有本 Gate 可以提交材料语义事实。"""

    assignment = deepcopy(state.get("audit_assignment") or {})
    decision = deepcopy(state.get("audit_decision") or {})
    candidates = {item["candidate_id"]: item for item in assignment.get("candidates", [])}
    selected = candidates.get(decision.get("selected_candidate_id"))
    case_page_ids = {str(page["page_id"]) for page in state.get("pages", [])}
    issue_page_ids = set(assignment.get("issue", {}).get("candidate_page_ids", []))
    confirmed_people = _confirmed_people(state)
    scoped_combinations = _candidate_scope(state)
    confirmed_owner = _confirmed_owner_by_page(state)
    allowed_evidence = {
        ref for item in assignment.get("candidates", []) for ref in item.get("evidence_refs", [])
    }
    action = decision.get("action")
    assignment_is_current = bool(
        assignment.get("case_id") == state.get("case_id")
        and assignment.get("thread_id") == (state.get("thread_id") or state.get("case_id"))
        and int(assignment.get("case_version", -1)) == int(state.get("case_version", 1))
        and int(assignment.get("plan_version", -1)) == int(state.get("plan_version", 1))
        and any(
            task.get("task_id") == assignment.get("issue", {}).get("task_id")
            and task.get("status") == "AMBIGUOUS"
            for task in state.get("audit_plan", [])
        )
    )
    selected_is_scoped = bool(
        selected
        and set(selected.get("page_ids", [])).issubset(case_page_ids)
        and set(selected.get("page_ids", [])).issubset(issue_page_ids)
        and selected.get("proposed_person_id") in confirmed_people
        and (
            selected.get("proposed_person_id"),
            selected.get("proposed_material_type"),
            selected.get("proposed_requirement_id"),
        ) in scoped_combinations
        and all(
            confirmed_owner.get(page_id) in {None, selected.get("proposed_person_id")}
            for page_id in selected.get("page_ids", [])
        )
    )
    evidence_is_bounded = set(decision.get("evidence_refs", [])).issubset(allowed_evidence)
    accepted = bool(
        assignment_is_current
        and action in assignment.get("allowed_actions", [])
        and evidence_is_bounded
        and (
            (action == "APPLY_CANDIDATE" and selected_is_scoped)
            or action in {"REQUEST_RECOVERY", "REQUEST_HUMAN"}
        )
    )

    pages = deepcopy(state.get("pages", []))
    owner_bindings = deepcopy(state.get("material_owner_bindings", []))
    pending_request: dict[str, Any] | None = None
    human_tasks = deepcopy(state.get("human_tasks", []))
    gate_outcome = "REJECTED_TO_HITL"
    if accepted and action == "APPLY_CANDIDATE" and selected:
        for page in pages:
            if page["page_id"] in selected["page_ids"]:
                page["owner_person_id"] = selected["proposed_person_id"]
                page["material_type"] = selected["proposed_material_type"]
                if selected.get("proposed_bundle_id"):
                    page["bundle_id"] = selected["proposed_bundle_id"]
                fields = deepcopy(page.get("extracted_fields") or {})
                fields["matched_requirement_id"] = selected["proposed_requirement_id"]
                page["extracted_fields"] = fields
                page["status"] = "VERIFIED"
        owner_bindings = _append_owner_binding(state, selected=selected)
        gate_outcome = "APPLIED_AND_REMATCH"
    elif accepted and action == "REQUEST_RECOVERY":
        gate_outcome = "RECOVERY_REQUIRED"
    else:
        task = next(
            item for item in state.get("audit_plan", [])
            if item["task_id"] == assignment.get("issue", {}).get("task_id")
        )
        human_task_id = f"HUMAN-{uuid4().hex[:8].upper()}"
        pending_request = {
            "type": "MATERIAL_HITL",
            "action": (
                "CONFIRM_OWNER"
                if assignment.get("issue", {}).get("issue_type") == "OWNER_AMBIGUOUS"
                else "REVIEW_IMAGE"
            ),
            "task_id": task["task_id"],
            "human_task_id": human_task_id,
            "person_id": task["person_id"],
            "material_type": task["material_type"],
            "requirement_id": task["requirement_id"],
            "title": "确认材料语义归属",
            "reason": decision.get("rationale_summary") or "Agent 提议未通过 Plan Gate，转人工确认。",
            "reason_code": decision.get("reason_code", "AUDIT_GATE_HITL"),
            "candidate_page_ids": sorted(issue_page_ids),
            "candidate_options": assignment.get("candidates", []),
            "resume_contract": [
                "event_id", "action", "task_id", "page_id", "person_id",
                "selected_candidate_id", "reason_code", "operator_id",
            ],
        }
        human_tasks.append(asdict(HumanTask(
            human_task_id=human_task_id,
            task_type=pending_request["action"],
            title=pending_request["title"],
            reason=pending_request["reason"],
            task_id=task["task_id"],
            candidate_options=assignment.get("candidates", []),
            evidence_refs=list(decision.get("evidence_refs", [])),
            expected_case_version=int(state.get("case_version", 1)),
        )))
        gate_outcome = "HITL_REQUIRED" if accepted else "REJECTED_TO_HITL"

    patch = {
        "pages": pages,
        "material_owner_bindings": owner_bindings,
        "pending_human_request": pending_request,
        "human_tasks": human_tasks,
        "audit_gate": {"accepted": accepted, "outcome": gate_outcome},
        "active_node": "audit_plan_gate",
    }
    event = _event(
        state, patch, event_type="AUDIT_PLAN_GATE_EVALUATED", node="audit_plan_gate",
        actor="plan_gate", task_id=assignment.get("issue", {}).get("task_id"),
        action="VALIDATE_BOUNDED_PROPOSAL",
        observation={
            "accepted": accepted,
            "outcome": gate_outcome,
            "checks": {
                "assignment_is_current": assignment_is_current,
                "allowed_action": action in assignment.get("allowed_actions", []),
                "candidate_is_scoped": selected_is_scoped if action == "APPLY_CANDIDATE" else None,
                "evidence_is_bounded": evidence_is_bounded,
            },
        },
        details={"write_authority": "WORKFLOW_PLAN_GATE"},
    )
    return {**patch, "pending_events": [event]}


def route_after_gate(state: AuditState) -> Literal["human", "recover", "rematch"]:
    if state.get("pending_human_request"):
        return "human"
    if (state.get("audit_gate") or {}).get("outcome") == "RECOVERY_REQUIRED":
        return "recover"
    return "rematch"


__all__ = ["plan_gate", "route_after_gate", "run"]
