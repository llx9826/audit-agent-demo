"""缺件依据阶段：确定性缺件后才检索适用条款并绑定稳定证据。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from ...graph.common import _event
from ...graph.state import AuditState
from ..dependencies import RequirementEvidenceGrounder


def run(
    state: AuditState,
    *,
    requirement_evidence_rag: RequirementEvidenceGrounder,
) -> dict[str, Any]:
    """为已判定的缺件/不可读 Task 提供“为什么需要该材料”的制度依据。"""

    fields = state.get("business_fields", {})
    roles = sorted({role for person in state.get("persons", []) for role in person.get("roles", [])})
    trace = requirement_evidence_rag.ground(
        product=str(fields.get("product_type", "")),
        channel=str(fields.get("channel", "ALL")),
        case_date=date.fromisoformat(str(fields.get("case_date"))),
        person_roles=roles,
        problem_tasks=state.get("problem_tasks", []),
    )
    groundings = trace["groundings"]
    grounding_by_task = {item["task_id"]: item for item in groundings}
    tasks = deepcopy(state.get("audit_plan", []))
    matches = deepcopy(state.get("material_matches", []))
    for task in tasks:
        grounding = grounding_by_task.get(task["task_id"])
        if grounding:
            task["evidence_refs"] = list(dict.fromkeys([
                *task.get("evidence_refs", []), grounding["evidence_id"],
            ]))
    for match in matches:
        grounding = grounding_by_task.get(match["task_id"])
        if grounding:
            match["evidence_refs"] = list(dict.fromkeys([
                *match.get("evidence_refs", []), grounding["evidence_id"],
            ]))

    ledger = deepcopy(state.get("evidence_ledger", []))
    known = {item["evidence_id"] for item in ledger}
    for grounding in groundings:
        if grounding["evidence_id"] in known:
            continue
        ledger.append({
            "evidence_id": grounding["evidence_id"],
            "source_type": "ATOMIC_REQUIREMENT",
            "source_id": grounding["requirement_id"],
            "value": grounding["atomic_requirement"],
            "document_id": grounding["source_document"],
            "page": None,
            "field": grounding["source_section"],
            "requirement_id": grounding["requirement_id"],
            "confidence": grounding["retrieval_scores"].get("rerank"),
        })
        known.add(grounding["evidence_id"])

    patch = {
        "audit_plan": tasks,
        "material_matches": matches,
        "evidence_ledger": ledger,
        "supplement_groundings": groundings,
        "rag_trace": trace,
        "active_node": "ground_requirement_evidence",
    }
    events = [
        _event(
            state, patch, event_type="EVIDENCE_RAG_STARTED", node="ground_requirement_evidence",
            actor="retriever", action="GROUND_COMPLETENESS_PROBLEMS",
            tool="requirement_evidence_search",
            observation={"trigger": trace["trigger"], "problem_task_ids": trace["problem_task_ids"]},
            details={"pipeline": trace["pipeline"]},
        ),
        _event(
            state, patch, event_type="EVIDENCE_RAG_COMPLETED", node="ground_requirement_evidence",
            actor="retriever", action="DENSE_BM25_RRF_RERANK", tool="hybrid_retriever",
            observation={
                "eligible_count": trace["retrieval"]["eligible_count"],
                "grounded_requirement_ids": trace["final_requirements"],
            },
            evidence=[item["evidence_id"] for item in groundings],
            details={"rag_trace": trace},
        ),
    ]
    return {**patch, "pending_events": events}


__all__ = ["run"]
