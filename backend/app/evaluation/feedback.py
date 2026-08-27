"""从审计事件生成候选曝光、人工反馈和可版本化 Hard Case。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


@dataclass(slots=True)
class CandidateImpression:
    case_id: str
    task_id: str
    event_id: str
    candidate_id: str
    rank_position: int
    raw_score: float
    features: dict[str, Any]


@dataclass(slots=True)
class HumanFeedback:
    case_id: str
    task_id: str
    event_id: str
    action: str
    selected_candidate_id: str | None
    reason_code: str
    operator_id: str


def _candidate_id_from_observation(
    candidates: list[dict[str, Any]],
    observation: Mapping[str, Any],
) -> str | None:
    explicit = observation.get("selected_candidate_id")
    if explicit:
        return str(explicit)
    matches = [
        candidate for candidate in candidates
        if (not observation.get("page_id") or observation["page_id"] in candidate.get("page_ids", []))
        and (
            not observation.get("person_id")
            or observation["person_id"] == candidate.get("proposed_person_id")
        )
        and (
            not observation.get("material_type")
            or observation["material_type"] == candidate.get("proposed_material_type")
        )
    ]
    return str(matches[0]["candidate_id"]) if len(matches) == 1 else None


def project_feedback(events: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """事件日志是事实源；本函数生成可重建的训练/标注读模型。"""

    ordered = sorted(events, key=lambda item: int(item.get("seq", 0)))
    candidate_sets: dict[str, list[dict[str, Any]]] = {}
    impressions: list[CandidateImpression] = []
    feedback: list[HumanFeedback] = []
    decisions: dict[str, dict[str, Any]] = {}

    for event in ordered:
        payload = event.get("payload", {})
        task_id = str(payload.get("task_id") or "")
        if event.get("event_type") == "AUDIT_CANDIDATES_BUILT":
            candidates = list((payload.get("observation") or {}).get("candidates", []))
            candidate_sets[task_id] = deepcopy(candidates)
            for rank, candidate in enumerate(candidates, start=1):
                impressions.append(CandidateImpression(
                    case_id=str(event.get("case_id")),
                    task_id=task_id,
                    event_id=str(event.get("event_id")),
                    candidate_id=str(candidate["candidate_id"]),
                    rank_position=rank,
                    raw_score=float(candidate.get("workflow_score", 0.0)),
                    features={
                        "page_ids": list(candidate.get("page_ids", [])),
                        "proposed_person_id": candidate.get("proposed_person_id"),
                        "proposed_material_type": candidate.get("proposed_material_type"),
                        "proposed_requirement_id": candidate.get("proposed_requirement_id"),
                        "proposed_bundle_id": candidate.get("proposed_bundle_id"),
                        "evidence_count": len(candidate.get("evidence_refs", [])),
                        "observations": deepcopy(candidate.get("observations", {})),
                    },
                ))
        elif event.get("event_type") == "AUDIT_DECISION_PROPOSED":
            decisions[task_id] = deepcopy(payload.get("observation") or {})
        elif event.get("event_type") == "HUMAN_DECISION_APPLIED":
            observation = payload.get("observation") or {}
            feedback.append(HumanFeedback(
                case_id=str(event.get("case_id")),
                task_id=task_id,
                event_id=str(event.get("event_id")),
                action=str(observation.get("action") or payload.get("action") or "UNKNOWN"),
                selected_candidate_id=_candidate_id_from_observation(
                    candidate_sets.get(task_id, []), observation,
                ),
                reason_code=str(observation.get("reason_code") or "HUMAN_CONFIRMED"),
                operator_id=str(observation.get("operator_id") or "UNKNOWN"),
            ))

    hard_cases: list[dict[str, Any]] = []
    for item in feedback:
        candidates = candidate_sets.get(item.task_id, [])
        proposed = decisions.get(item.task_id, {})
        if not candidates or item.action not in {"CONFIRM_OWNER", "REVIEW_IMAGE"}:
            continue
        hard_cases.append({
            "id": f"feedback-{item.case_id}-{item.task_id}-{item.event_id}",
            "input": {
                "case_id": item.case_id,
                "task_id": item.task_id,
                "candidates": deepcopy(candidates),
                "agent_decision": deepcopy(proposed),
            },
            "expected": {
                "action": item.action,
                "selected_candidate_id": item.selected_candidate_id,
            },
            "meta": {
                "failure_mode": "human_override_or_abstention",
                "source": "human_confirmed_event",
                "reason_code": item.reason_code,
                "operator_id": item.operator_id,
            },
        })

    return {
        "candidate_impressions": [asdict(item) for item in impressions],
        "human_feedback": [asdict(item) for item in feedback],
        "hard_cases": hard_cases,
    }
