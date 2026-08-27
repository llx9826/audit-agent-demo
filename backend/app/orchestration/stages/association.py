"""人员、角色和材料归属阶段。

本模块是主 Pipeline 的真实实现：分类影像只先产生页级信号，
Case Association Agent 对封闭候选做结构化提议，最终只由 Gate 写入主状态。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from ...agents.case_association import (
    AssociationCandidate,
    CaseAssociationAgent,
    CaseAssociationAssignment,
)
from ...agents.exception_recovery import ExceptionRecoveryAgent, ExceptionTask
from ...domain.models import HumanTask
from ...graph.common import _event
from ...graph.state import AuditState
from ..association_evidence import AssociationEvidenceExtractor, AssociationPageObservation


def _evidence_refs(page: dict[str, Any]) -> list[str]:
    refs = [str(item) for item in page.get("evidence_refs", [])]
    return refs or [f"EV-{page['page_id']}"]


_ASSOCIATION_MATERIAL_TYPES = {
    "identity_document", "marriage_certificate", "property_certificate",
    "spouse_consent", "business_license",
}

# 六分类只是上游粗粒度结果。仅选择可能承载姓名、证件号、签署身份或权属人的
# 材料域，避免把 200+ 页全部送入 VLM，同时不能要求上游预先给出人员/角色。
_ASSOCIATION_DOMAINS = {
    "身份与主体证明",
    "婚姻与家庭关系",
    "房产权属与抵押相关材料",
    "经营主体与经营材料",
    "合同、申请与授权文件",
}


def _association_relevant(page: dict[str, Any]) -> bool:
    fields = page.get("extracted_fields") or {}
    has_upstream_signal = bool(
        fields.get("person_id")
        or fields.get("person_name")
        or fields.get("role_signals")
        or fields.get("identity_key")
    )
    return (
        has_upstream_signal
        or page.get("material_type") in _ASSOCIATION_MATERIAL_TYPES
        or page.get("domain") in _ASSOCIATION_DOMAINS
    )


def select_pages(state: AuditState) -> dict[str, Any]:
    """只选择可能承载身份、角色或所属人的分类页，避免把 200+ 页全送 VLM。"""

    selected_ids = sorted(
        page["page_id"] for page in state.get("pages", []) if _association_relevant(page)
    )
    if not selected_ids:
        raise ValueError("no classified pages are eligible for case association evidence extraction")
    material = "|".join([
        str(state.get("case_id", "")),
        str(state.get("case_version", 1)),
        *selected_ids,
    ])
    dispatch_id = f"ASSOC-EV-{sha256(material.encode('utf-8')).hexdigest()[:12].upper()}"
    patch = {
        "association_page_ids": selected_ids,
        "association_evidence_dispatch_id": dispatch_id,
        "active_node": "select_association_pages",
        "current_task_id": None,
    }
    event = _event(
        state,
        patch,
        event_type="ASSOCIATION_PAGES_SELECTED",
        node="select_association_pages",
        actor="workflow",
        action="SELECT_IDENTITY_ROLE_PAGES",
        observation={
            "dispatch_id": dispatch_id,
            "selected_page_ids": selected_ids,
            "selected_count": len(selected_ids),
            "total_page_count": len(state.get("pages", [])),
        },
        details={"selection_authority": "DETERMINISTIC_CLASSIFICATION_FILTER"},
    )
    return {**patch, "pending_events": [event]}


def dispatch_pages(state: AuditState) -> list[Any]:
    """用 Send 为每个相关页创建独立 Evidence Worker。"""

    from langgraph.types import Send

    pages = {page["page_id"]: page for page in state.get("pages", [])}
    return [
        Send("extract_association_page", {
            "case_id": state.get("case_id"),
            "thread_id": state.get("thread_id"),
            "case_version": state.get("case_version", 1),
            "plan_version": state.get("plan_version", 1),
            "association_evidence_dispatch_id": state.get("association_evidence_dispatch_id"),
            "association_worker_page": deepcopy(pages[page_id]),
        })
        for page_id in state.get("association_page_ids", [])
    ]


def extract_page(
    state: AuditState,
    *,
    evidence_extractor: AssociationEvidenceExtractor,
) -> dict[str, Any]:
    """读取一个页面的结构化 Observation；Worker 没有主 State 写权限。"""

    page = state["association_worker_page"]
    try:
        observation = evidence_extractor.extract(
            case_id=str(state.get("case_id", "")),
            page=page,
        )
    except Exception as exc:  # Worker 失败必须汇聚到 Gate，不能击穿整个 LangGraph Run。
        observation = AssociationPageObservation(
            evidence_refs=_evidence_refs(page),
            provider="ASSOCIATION_EVIDENCE_BOUNDARY",
            status="FAILED",
            error_code=type(exc).__name__,
        )
    result = {
        "dispatch_id": state.get("association_evidence_dispatch_id"),
        "page_id": page["page_id"],
        "observation": observation.model_dump(mode="json"),
    }
    event = _event(
        state,
        {},
        event_type="ASSOCIATION_PAGE_EVIDENCE_EXTRACTED",
        node="extract_association_page",
        actor="association_evidence_worker",
        task_id=f"ASSOC-PAGE-{page['page_id']}",
        action="EXTRACT_PAGE_IDENTITY_ROLE_OWNER",
        observation={
            "page_id": page["page_id"],
            "provider": observation.provider,
            "has_person": bool(observation.person_id and observation.person_name),
            "role_signal_count": len(observation.role_signals),
            "has_owner": bool(observation.owner_person_id),
            "status": observation.status,
            "error_code": observation.error_code,
        },
        evidence=observation.evidence_refs,
        details={"write_authority": "NONE", "worker_context": "PAGE_SCOPED"},
    )
    return {"association_evidence_results": [result], "pending_events": [event]}


def extract_evidence(state: AuditState) -> dict[str, Any]:
    """Fan-in 页级 Observation，构建 Mention/Signal，但不做关联结论。"""

    dispatch_id = state.get("association_evidence_dispatch_id")
    page_ids = set(state.get("association_page_ids", []))
    results = {
        item["page_id"]: item
        for item in state.get("association_evidence_results", [])
        if item.get("dispatch_id") == dispatch_id and item.get("page_id") in page_ids
    }
    seeds = {item["person_id"]: item for item in state.get("persons", [])}
    mentions: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    owner_signals: list[dict[str, Any]] = []
    for page_id, result in sorted(results.items()):
        observation = result.get("observation") or {}
        person_id = str(observation.get("person_id") or observation.get("owner_person_id") or "").strip()
        seed = seeds.get(person_id, {})
        display_name = str(observation.get("person_name") or seed.get("name") or "").strip()
        confidence = float(observation.get("confidence") or 0)
        refs = [str(item) for item in observation.get("evidence_refs", [])] or [f"EV-{page_id}"]
        if person_id and display_name:
            mentions.append({
                "mention_id": f"MENTION-{page_id}-{person_id}",
                "page_id": page_id,
                "display_name": display_name,
                "person_id": person_id,
                "identity_key": observation.get("identity_key"),
                "confidence": confidence,
                "evidence_refs": refs,
            })
            # 角色必须来自页级 Observation；Seed.roles 不会自动升格。
            for role in observation.get("role_signals", []):
                signals.append({
                    "signal_id": f"ROLE-{page_id}-{person_id}-{role}",
                    "page_id": page_id,
                    "person_id": person_id,
                    "role": str(role),
                    "confidence": confidence,
                    "evidence_refs": refs,
                })
        owner_person_id = str(observation.get("owner_person_id") or "").strip()
        if owner_person_id:
            owner_signals.append({
                "signal_id": f"OWNER-{page_id}-{owner_person_id}",
                "page_id": page_id,
                "person_id": owner_person_id,
                "confidence": confidence,
                "evidence_refs": refs,
            })
    mentions = list({item["mention_id"]: item for item in mentions}.values())
    signals = list({item["signal_id"]: item for item in signals}.values())
    owner_signals = list({item["signal_id"]: item for item in owner_signals}.values())
    failed_pages = [
        page_id for page_id, result in results.items()
        if (result.get("observation") or {}).get("status") != "SUCCESS"
    ]
    evidence_sufficient = bool(mentions and signals)
    recovery_attempts = int(state.get("association_recovery_attempts", 0))
    recovery_request = None
    if not evidence_sufficient:
        recovery_request = {
            "source": "ASSOCIATION_EVIDENCE_GATE",
            "source_task_id": f"ASSOC-{state.get('case_id')}-C{state.get('case_version', 1)}",
            "exception_type": (
                "TOOL_FAILURE" if failed_pages else "OWNER_ASSIGNMENT_AMBIGUOUS"
            ),
            "problem": "页级证据尚未形成可闭合的人员实体与业务角色，需要新的独立 Observation。",
            "page_ids": sorted(page_ids),
            "failed_page_ids": sorted(failed_pages),
            "evidence_refs": list(dict.fromkeys(
                ref for item in results.values()
                for ref in (item.get("observation") or {}).get("evidence_refs", [])
            )),
            "attempt": recovery_attempts + 1,
        }
    patch = {
        "identity_mentions": mentions,
        "role_signals": signals,
        "material_owner_signals": owner_signals,
        "active_node": "extract_association_evidence",
        "current_task_id": None,
        "association_evidence_gate": {
            "accepted": evidence_sufficient,
            "outcome": "CANDIDATES_READY" if evidence_sufficient else "RECOVERY_REQUIRED",
        },
        "association_recovery_request": recovery_request,
    }
    all_signals = [*mentions, *signals, *owner_signals]
    event = _event(
        state, patch,
        event_type="ASSOCIATION_EVIDENCE_EXTRACTED",
        node="extract_association_evidence",
        actor="association_evidence_gate",
        action="FAN_IN_IDENTITY_ROLE_SIGNALS",
        observation={
            "dispatch_id": dispatch_id,
            "worker_count": len(results),
            "mention_count": len(mentions),
            "role_signal_count": len(signals),
            "owner_signal_count": len(owner_signals),
            "source_page_ids": sorted({item["page_id"] for item in all_signals}),
            "failed_page_ids": sorted(failed_pages),
            "outcome": "CANDIDATES_READY" if evidence_sufficient else "RECOVERY_REQUIRED",
        },
        evidence=list(dict.fromkeys(ref for item in all_signals for ref in item["evidence_refs"])),
        details={"write_authority": "ASSOCIATION_EVIDENCE_GATE"},
    )
    return {**patch, "pending_events": [event]}


def _build_candidates(state: AuditState) -> list[AssociationCandidate]:
    seeds = {item["person_id"]: item for item in state.get("persons", [])}
    mentions_by_person: dict[str, list[dict[str, Any]]] = {}
    for mention in state.get("identity_mentions", []):
        person_id = str(mention.get("person_id") or mention["mention_id"].rsplit("-", 1)[-1])
        mentions_by_person.setdefault(person_id, []).append(mention)

    candidates: list[AssociationCandidate] = []
    for person_id, mentions in sorted(mentions_by_person.items()):
        seed = seeds.get(person_id, {})
        refs = list(dict.fromkeys(ref for item in mentions for ref in item.get("evidence_refs", [])))
        candidates.append(AssociationCandidate(
            candidate_id=f"ASSOC-PERSON-{person_id}",
            candidate_type="PERSON_ENTITY",
            person_id=person_id,
            display_name=str(seed.get("name") or mentions[0]["display_name"]),
            mention_ids=[item["mention_id"] for item in mentions],
            evidence_refs=refs,
            workflow_score=max(float(item.get("confidence") or 0) for item in mentions),
            observations={"source": "PAGE_IDENTITY_MENTIONS"},
        ))
    for signal in state.get("role_signals", []):
        person_id = signal["person_id"]
        seed = seeds.get(person_id, {})
        mentions = mentions_by_person.get(person_id, [])
        display_name = str(seed.get("name") or (mentions[0]["display_name"] if mentions else person_id))
        candidates.append(AssociationCandidate(
            candidate_id=f"ASSOC-ROLE-{person_id}-{signal['role']}",
            candidate_type="PERSON_ROLE",
            person_id=person_id,
            display_name=display_name,
            role=signal["role"],
            page_id=signal["page_id"],
            evidence_refs=list(signal.get("evidence_refs", [])),
            workflow_score=float(signal.get("confidence") or 0),
            observations={"source": "PAGE_ROLE_SIGNAL"},
        ))
    pages = {page["page_id"]: page for page in state.get("pages", [])}
    for owner_signal in state.get("material_owner_signals", []):
        person_id = owner_signal.get("person_id")
        if not person_id or person_id not in mentions_by_person:
            continue
        page_id = str(owner_signal["page_id"])
        page = pages[page_id]
        seed = seeds.get(person_id, {})
        mentions = mentions_by_person[person_id]
        candidates.append(AssociationCandidate(
            candidate_id=f"ASSOC-OWNER-{page_id}-{person_id}",
            candidate_type="MATERIAL_OWNER",
            person_id=str(person_id),
            display_name=str(seed.get("name") or mentions[0]["display_name"]),
            page_id=page_id,
            evidence_refs=list(owner_signal.get("evidence_refs", [])),
            workflow_score=float(owner_signal.get("confidence") or 0),
            observations={"material_type": page.get("material_type"), "source": "PAGE_OWNER_SIGNAL"},
        ))
    # Candidate ID 是业务幂等键，多页同角色保留最高置信度候选。
    unique: dict[str, AssociationCandidate] = {}
    for candidate in candidates:
        current = unique.get(candidate.candidate_id)
        if current is None or candidate.workflow_score > current.workflow_score:
            unique[candidate.candidate_id] = candidate
    # 稳定排序保证同一 Checkpoint 重放得到同一候选顺序，避免模型上下文漂移。
    return sorted(
        unique.values(),
        key=lambda item: (item.candidate_type, item.person_id, item.page_id or "", item.candidate_id),
    )


def build_candidates(state: AuditState) -> dict[str, Any]:
    """由 Workflow 先持久化封闭候选，让慢模型调用之前已有可观测控制点。"""

    candidates = _build_candidates(state)
    if not candidates:
        raise ValueError("no evidence-backed person/role association candidates")
    assignment = CaseAssociationAssignment(
        assignment_id=f"ASSOC-{state.get('case_id')}-C{state.get('case_version', 1)}",
        case_id=str(state.get("case_id")),
        thread_id=str(state.get("thread_id") or state.get("case_id")),
        case_version=int(state.get("case_version", 1)),
        objective="从页级证据候选中确认人员实体、业务角色和材料所属人",
        candidates=candidates,
        allowed_actions=["APPLY_CANDIDATES", "REQUEST_HUMAN", "REQUEST_RECOVERY"],
    )
    patch = {
        "association_assignment": assignment.model_dump(mode="json"),
        "active_node": "build_association_candidates",
        "current_task_id": assignment.assignment_id,
    }
    event = _event(
        state, patch,
        event_type="ASSOCIATION_CANDIDATES_BUILT",
        node="build_association_candidates",
        actor="workflow",
        action="BUILD_CLOSED_ASSOCIATION_CANDIDATES",
        observation={
            "assignment_id": assignment.assignment_id,
            "candidate_count": len(candidates),
            "candidate_types": sorted({item.candidate_type for item in candidates}),
        },
        evidence=list(dict.fromkeys(ref for item in candidates for ref in item.evidence_refs)),
    )
    return {**patch, "pending_events": [event]}


def review(state: AuditState, *, association_agent: CaseAssociationAgent) -> dict[str, Any]:
    """Agent 只看已持久化的最小封闭候选，不读取 200+ 页原文。"""

    assignment = CaseAssociationAssignment.model_validate(state.get("association_assignment") or {})
    run = association_agent.decide(assignment)
    patch = {
        "association_assignment": assignment.model_dump(mode="json"),
        "association_decision": run.decision.model_dump(mode="json"),
        "active_node": "case_association_agent",
        "current_task_id": assignment.assignment_id,
    }
    events = [
        _event(
            state, patch,
            event_type="ASSOCIATION_DECISION_PROPOSED",
            node="case_association_agent",
            actor="association_agent",
            action=run.decision.action,
            observation=run.decision.model_dump(mode="json"),
            evidence=run.decision.evidence_refs,
            details={"prompt": run.prompt.model_dump(mode="json"), "model_trace": run.model_trace, "write_authority": "NONE"},
        ),
    ]
    return {**patch, "pending_events": events}


def _apply_selected(
    state: AuditState,
    selected_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    assignment = state.get("association_assignment") or {}
    selected = [item for item in assignment.get("candidates", []) if item["candidate_id"] in selected_ids]
    entity_candidates = [item for item in selected if item["candidate_type"] == "PERSON_ENTITY"]
    role_candidates = [item for item in selected if item["candidate_type"] == "PERSON_ROLE"]
    owner_candidates = [item for item in selected if item["candidate_type"] == "MATERIAL_OWNER"]
    entities = [{
        "person_id": item["person_id"],
        "display_name": item["display_name"],
        "identity_key": None,
        "mention_ids": list(item.get("mention_ids", [])),
        "status": "CONFIRMED",
        "evidence_refs": list(item.get("evidence_refs", [])),
    } for item in entity_candidates]
    bindings = [{
        "binding_id": f"BIND-ROLE-{item['person_id']}-{item['role']}",
        "person_id": item["person_id"],
        "role": item["role"],
        "status": "CONFIRMED",
        "confidence": item["workflow_score"],
        "evidence_refs": list(item.get("evidence_refs", [])),
        "decided_by": "ASSOCIATION_GATE",
    } for item in role_candidates]
    owner_bindings = [{
        "binding_id": f"BIND-OWNER-{item['page_id']}-{item['person_id']}",
        "page_id": item["page_id"],
        "person_id": item["person_id"],
        "status": "CONFIRMED",
        "confidence": item["workflow_score"],
        "evidence_refs": list(item.get("evidence_refs", [])),
        "decided_by": "ASSOCIATION_GATE",
    } for item in owner_candidates]
    roles_by_person: dict[str, list[str]] = {}
    for item in bindings:
        roles_by_person.setdefault(item["person_id"], []).append(item["role"])
    people = [{
        "person_id": item["person_id"],
        "name": item["display_name"],
        "roles": sorted(set(roles_by_person.get(item["person_id"], []))),
        "confirmed": True,
        "source": "ASSOCIATION_GATE",
    } for item in entities if roles_by_person.get(item["person_id"])]
    pages = deepcopy(state.get("pages", []))
    owner_by_page = {item["page_id"]: item["person_id"] for item in owner_bindings}
    for page in pages:
        if page["page_id"] in owner_by_page:
            page["owner_person_id"] = owner_by_page[page["page_id"]]
    return people, entities, bindings, owner_bindings, pages


def gate(state: AuditState) -> dict[str, Any]:
    """校验 Agent 提议后写入 Confirmed Projection；失败时仅能转 HITL。"""

    assignment = deepcopy(state.get("association_assignment") or {})
    decision = deepcopy(state.get("association_decision") or {})
    candidates = {item["candidate_id"]: item for item in assignment.get("candidates", [])}
    selected_ids = list(dict.fromkeys(decision.get("selected_candidate_ids", [])))
    selected = [candidates[item] for item in selected_ids if item in candidates]
    allowed_evidence = {ref for item in selected for ref in item.get("evidence_refs", [])}
    selected_people = {item["person_id"] for item in selected if item["candidate_type"] == "PERSON_ENTITY"}
    role_people = {item["person_id"] for item in selected if item["candidate_type"] == "PERSON_ROLE"}
    owner_people = {item["person_id"] for item in selected if item["candidate_type"] == "MATERIAL_OWNER"}
    case_page_ids = {str(page["page_id"]) for page in state.get("pages", [])}
    owner_page_ids = {
        str(item["page_id"])
        for item in selected
        if item["candidate_type"] == "MATERIAL_OWNER" and item.get("page_id")
    }
    action = decision.get("action")
    assignment_is_current = bool(
        assignment.get("case_id") == state.get("case_id")
        and int(assignment.get("case_version", -1)) == int(state.get("case_version", 1))
        and assignment.get("thread_id") == (state.get("thread_id") or state.get("case_id"))
    )
    evidence_is_bounded = set(decision.get("evidence_refs", [])).issubset(
        {ref for item in candidates.values() for ref in item.get("evidence_refs", [])}
    )
    accepted = bool(
        action == "APPLY_CANDIDATES"
        and action in assignment.get("allowed_actions", [])
        and assignment_is_current
        and len(selected) == len(selected_ids)
        and selected_people
        and role_people.issubset(selected_people)
        and role_people == selected_people
        and owner_people.issubset(selected_people)
        and owner_page_ids.issubset(case_page_ids)
        and set(decision.get("evidence_refs", [])).issubset(allowed_evidence)
    )
    recovery_accepted = bool(
        action == "REQUEST_RECOVERY"
        and action in assignment.get("allowed_actions", [])
        and assignment_is_current
        and evidence_is_bounded
        and decision.get("missing_observations")
    )
    human_tasks = deepcopy(state.get("human_tasks", []))
    pending: dict[str, Any] | None = None
    people: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    owner_bindings: list[dict[str, Any]] = []
    pages = deepcopy(state.get("pages", []))
    recovery_request: dict[str, Any] | None = None
    if accepted:
        people, entities, bindings, owner_bindings, pages = _apply_selected(state, selected_ids)
        outcome = "CONFIRMED"
    elif recovery_accepted:
        exception_type = {
            "IDENTITY_EVIDENCE_INSUFFICIENT": "OWNER_ASSIGNMENT_AMBIGUOUS",
            "ROLE_EVIDENCE_INSUFFICIENT": "OWNER_ASSIGNMENT_AMBIGUOUS",
            "OWNER_EVIDENCE_INSUFFICIENT": "OWNER_ASSIGNMENT_AMBIGUOUS",
            "CROSS_PAGE_EVIDENCE_CONFLICT": "CROSS_PAGE_CONFLICT",
        }[str(decision["exception_type"])]
        candidate_page_ids = list(dict.fromkeys(
            str(item["page_id"])
            for item in candidates.values()
            if item.get("page_id")
        ))
        if not candidate_page_ids:
            candidate_page_ids = list(state.get("association_page_ids", []))
        recovery_request = {
            "source": "CASE_ASSOCIATION_AGENT",
            "source_task_id": assignment.get("assignment_id"),
            "exception_type": exception_type,
            "problem": decision.get("rationale_summary"),
            "page_ids": candidate_page_ids,
            "failed_page_ids": [],
            "evidence_refs": list(decision.get("evidence_refs", [])),
            "missing_observations": list(decision.get("missing_observations", [])),
            "attempt": int(state.get("association_recovery_attempts", 0)) + 1,
        }
        outcome = "RECOVERY_REQUIRED"
    else:
        human_task_id = f"HUMAN-{uuid4().hex[:8].upper()}"
        pending = {
            "type": "ASSOCIATION_HITL",
            "action": "CONFIRM_ASSOCIATION",
            "task_id": assignment.get("assignment_id"),
            "human_task_id": human_task_id,
            "title": "确认人员、角色与材料归属",
            "reason": decision.get("rationale_summary") or "关键关联证据未形成唯一闭合。",
            "candidate_options": list(candidates.values()),
            "resume_contract": ["event_id", "action", "task_id", "selected_candidate_ids"],
        }
        human_tasks.append(asdict(HumanTask(
            human_task_id=human_task_id,
            task_type="CONFIRM_ASSOCIATION",
            title=pending["title"],
            reason=pending["reason"],
            task_id=assignment.get("assignment_id"),
            candidate_options=list(candidates.values()),
            evidence_refs=list(decision.get("evidence_refs", [])),
            expected_case_version=int(state.get("case_version", 1)),
        )))
        outcome = "HITL_REQUIRED"
    patch = {
        "persons": people if accepted else state.get("persons", []),
        "person_entities": entities,
        "role_bindings": bindings,
        "material_owner_bindings": owner_bindings,
        "pages": pages,
        "human_tasks": human_tasks,
        "pending_human_request": pending,
        "association_gate": {"accepted": accepted, "outcome": outcome},
        "association_recovery_request": recovery_request,
        "active_node": "association_gate",
    }
    event = _event(
        state, patch,
        event_type="ASSOCIATION_GATE_EVALUATED",
        node="association_gate",
        actor="association_gate",
        action="VALIDATE_AND_COMMIT_ASSOCIATIONS",
        observation={
            "accepted": accepted,
            "outcome": outcome,
            "confirmed_person_count": len(people),
            "confirmed_role_count": len(bindings),
            "confirmed_owner_count": len(owner_bindings),
            "checks": {
                "assignment_is_current": assignment_is_current,
                "candidate_membership": len(selected) == len(selected_ids),
                "role_people_are_confirmed": role_people == selected_people,
                "owner_people_are_confirmed": owner_people.issubset(selected_people),
                "owner_pages_are_scoped": owner_page_ids.issubset(case_page_ids),
                "evidence_is_bounded": evidence_is_bounded,
            },
        },
        evidence=list(decision.get("evidence_refs", [])),
        details={"write_authority": "ASSOCIATION_GATE_ONLY"},
    )
    return {**patch, "pending_events": [event]}


def route_after_evidence(state: AuditState) -> Literal["recover", "candidates"]:
    """证据 Gate 决定构造候选还是先委托 Exception Agent。"""

    outcome = (state.get("association_evidence_gate") or {}).get("outcome")
    return "recover" if outcome == "RECOVERY_REQUIRED" else "candidates"


def route_after_gate(state: AuditState) -> Literal["human", "recover", "requirements"]:
    if state.get("pending_human_request"):
        return "human"
    if (state.get("association_gate") or {}).get("outcome") == "RECOVERY_REQUIRED":
        return "recover"
    return "requirements"


def recover(state: AuditState, *, exception_agent: ExceptionRecoveryAgent) -> dict[str, Any]:
    """执行人员关联专用的受控 Exception Tool Loop。

    Exception Agent 只产生新的 Observation；本节点核验结构化字段或封闭候选成员后，
    才把 Observation 写回页级投影并重新进入 Association Evidence Fan-out。
    """

    request = deepcopy(state.get("association_recovery_request") or {})
    attempts = int(state.get("association_recovery_attempts", 0)) + 1
    pages = deepcopy(state.get("pages", []))
    page_by_id = {page["page_id"]: page for page in pages}
    requested_ids = [str(item) for item in request.get("page_ids", []) if str(item) in page_by_id]
    if not requested_ids:
        requested_ids = list(state.get("association_page_ids", []))
    if not requested_ids:
        raise ValueError("association recovery requires at least one scoped page")

    # 优先恢复明确失败的页，其次选择当前封闭范围内的第一个身份/角色承载页。
    failed_ids = [str(item) for item in request.get("failed_page_ids", []) if str(item) in page_by_id]
    page = page_by_id[(failed_ids or requested_ids)[0]]
    assignment = state.get("association_assignment") or {}
    candidate_people = list(dict.fromkeys(
        str(item["person_id"])
        for item in assignment.get("candidates", [])
        if item.get("person_id")
    ))
    recovery_values = [
        str(item) for item in (page.get("extracted_fields") or {}).get("recovery_values", [])
    ]
    proposed_value = recovery_values[0] if recovery_values else (candidate_people[0] if candidate_people else "UNKNOWN")
    trusted_value = recovery_values[1] if len(recovery_values) > 1 else proposed_value
    result = exception_agent.resolve(
        ExceptionTask(
            exception_type=str(request.get("exception_type") or "OWNER_ASSIGNMENT_AMBIGUOUS"),
            source_task_id=str(request.get("source_task_id") or "ASSOCIATION-RECOVERY"),
            problem=str(request.get("problem") or "人员、角色或材料归属证据不足"),
            evidence_refs=list(request.get("evidence_refs", [])),
            context_refs=requested_ids,
        ),
        vlm_value=proposed_value,
        trusted_document_value=trusted_value,
        tool_context={
            "page": deepcopy(page),
            "scoped_page_ids": requested_ids,
            "missing_observations": list(request.get("missing_observations", [])),
        },
    )

    # Tool 的结构化 metadata 不进入模型 Context，只供父 Workflow Gate 校验。
    recovered_fields: dict[str, Any] = {}
    for action in result.actions:
        fields = (action.get("observation_metadata") or {}).get("fields")
        if isinstance(fields, dict):
            recovered_fields.update({key: value for key, value in fields.items() if value not in (None, "", [])})

    normalized = [
        str(action["normalized_value"])
        for action in result.actions
        if action.get("normalized_value") is not None
    ]
    consensus = next(
        (value for value in dict.fromkeys(normalized) if normalized.count(value) >= 2),
        None,
    )
    person_id = str(recovered_fields.get("person_id") or recovered_fields.get("owner_person_id") or consensus or "").strip()
    candidate_rows = [
        item for item in assignment.get("candidates", []) if item.get("person_id") == person_id
    ]
    person_name = str(recovered_fields.get("person_name") or (
        candidate_rows[0].get("display_name") if candidate_rows else ""
    )).strip()
    roles = [str(item) for item in recovered_fields.get("role_signals", [])]
    if not roles:
        roles = list(dict.fromkeys(
            str(item["role"]) for item in candidate_rows if item.get("role")
        ))

    reload_succeeded = any(
        action.get("tool") == "document_reload" and action.get("executed") and action.get("state_changed")
        for action in result.actions
    )
    can_retry_extraction = bool(
        attempts <= 2
        and (
            (result.status == "RESOLVED" and person_id and person_name and roles)
            or (request.get("exception_type") == "TOOL_FAILURE" and reload_succeeded)
        )
    )
    pending: dict[str, Any] | None = None
    human_tasks = deepcopy(state.get("human_tasks", []))
    if can_retry_extraction:
        if person_id and person_name and roles:
            fields = deepcopy(page.get("extracted_fields") or {})
            fields.update(recovered_fields)
            fields.update({
                "person_id": person_id,
                "person_name": person_name,
                "role_signals": roles,
            })
            if recovered_fields.get("owner_person_id") or consensus:
                fields["owner_person_id"] = str(
                    recovered_fields.get("owner_person_id") or consensus
                )
            page["extracted_fields"] = fields
            page["evidence_refs"] = list(dict.fromkeys([
                *page.get("evidence_refs", []), *result.evidence_refs,
            ]))
        outcome = "RETRY_ASSOCIATION_EVIDENCE"
    else:
        human_task_id = f"HUMAN-{uuid4().hex[:8].upper()}"
        has_candidates = bool(assignment.get("candidates"))
        pending = {
            "type": "ASSOCIATION_HITL",
            "action": "CONFIRM_ASSOCIATION" if has_candidates else "RESOLVE_ASSOCIATION_EVIDENCE",
            "task_id": request.get("source_task_id"),
            "human_task_id": human_task_id,
            "page_id": page["page_id"],
            "title": "补充人员、角色与材料归属证据",
            "reason": result.conclusion or "受控恢复未形成可闭合的人员与角色 Observation。",
            "candidate_options": list(assignment.get("candidates", [])),
            "resume_contract": (
                ["event_id", "action", "task_id", "selected_candidate_ids"]
                if has_candidates else
                ["event_id", "action", "task_id", "page_id", "person_id", "person_name", "roles"]
            ),
        }
        human_tasks.append(asdict(HumanTask(
            human_task_id=human_task_id,
            task_type=pending["action"],
            title=pending["title"],
            reason=pending["reason"],
            task_id=str(request.get("source_task_id") or "ASSOCIATION-RECOVERY"),
            candidate_options=list(assignment.get("candidates", [])),
            evidence_refs=list(result.evidence_refs),
            expected_case_version=int(state.get("case_version", 1)),
        )))
        outcome = "HITL_REQUIRED"

    patch = {
        "pages": pages,
        "association_recovery_attempts": attempts,
        "association_recovery_request": None,
        "association_evidence_results": [],
        "pending_human_request": pending,
        "human_tasks": human_tasks,
        "association_gate": {
            "accepted": can_retry_extraction,
            "outcome": outcome,
        },
        "exception_context": {
            "source_task_id": request.get("source_task_id"),
            "page_id": page["page_id"],
            "handoff_source": request.get("source"),
            "exception_type": request.get("exception_type"),
            "status": result.status,
            "stop_reason": result.stop_reason,
            "steps_used": result.steps_used,
            "step_budget": result.step_budget,
            "tool_trace": result.actions,
            "decision_trace": result.decision_trace,
            "loop_guard_triggered": result.loop_guard_triggered,
        },
        "active_node": "association_exception_recovery",
        "current_task_id": request.get("source_task_id"),
    }
    events = [
        _event(
            state, patch,
            event_type="HANDOFF_CREATED",
            node="association_exception_recovery",
            actor="workflow",
            task_id=request.get("source_task_id"),
            action="DELEGATE_ASSOCIATION_TO_EXCEPTION_AGENT",
            observation={
                "exception_type": request.get("exception_type"),
                "scoped_page_ids": requested_ids,
                "attempt": attempts,
            },
            evidence=list(request.get("evidence_refs", [])),
            details={"context_isolation": True, "allowed_tools": result.allowed_tools},
        ),
        *[
            _event(
                state, patch,
                event_type="AGENT_TOOL_FINISHED",
                node="association_exception_recovery",
                actor="exception_agent",
                task_id=request.get("source_task_id"),
                action="BOUNDED_TOOL_LOOP",
                tool=action["tool"],
                observation={
                    "step": action["step"],
                    "result": action.get("result"),
                    "state_changed": action.get("state_changed", False),
                },
                evidence=list(action.get("evidence_refs", [])),
            )
            for action in result.actions
        ],
        _event(
            state, patch,
            event_type="ASSOCIATION_RECOVERY_COMPLETED",
            node="association_exception_recovery",
            actor="association_gate",
            task_id=request.get("source_task_id"),
            action="VALIDATE_RECOVERED_OBSERVATIONS",
            observation={"outcome": outcome, "attempt": attempts},
            evidence=list(result.evidence_refs),
            details={"write_authority": "ASSOCIATION_GATE_ONLY"},
        ),
    ]
    return {**patch, "pending_events": events}


def route_after_recovery(state: AuditState) -> Literal["retry", "human"]:
    return "human" if state.get("pending_human_request") else "retry"


def prepare_human(state: AuditState) -> dict[str, Any]:
    request = deepcopy(state.get("pending_human_request") or {})
    patch = {
        "status": "WAITING_HUMAN",
        "active_node": "prepare_association_human",
        "current_task_id": request.get("task_id"),
    }
    event = _event(
        state, patch,
        event_type="HITL_REQUESTED",
        node="prepare_association_human",
        actor="workflow",
        task_id=request.get("task_id"),
        action="DURABLE_ASSOCIATION_INTERRUPT",
        observation=request,
        details={"thread_id": state.get("thread_id")},
    )
    return {**patch, "pending_events": [event]}


def await_human(state: AuditState) -> dict[str, Any]:
    from langgraph.types import interrupt

    command = interrupt(deepcopy(state.get("pending_human_request") or {}))
    if not isinstance(command, dict):
        raise ValueError("association resume command must be a structured object")
    resume_context = deepcopy(command.get("_resume_context") or {})
    public_command = {key: deepcopy(value) for key, value in command.items() if not key.startswith("_")}
    patch = {"resume_event": public_command, "status": "RUNNING", "active_node": "await_association_human"}
    event = _event(
        state, patch,
        event_type="CHECKPOINT_RESUMED",
        node="await_association_human",
        actor="workflow",
        task_id=command.get("task_id"),
        action="COMMAND_RESUME",
        observation=resume_context,
        details={"same_thread": resume_context.get("thread_id") == state.get("thread_id")},
    )
    return {**patch, "pending_events": [event]}


def apply_human(state: AuditState) -> dict[str, Any]:
    command = deepcopy(state.get("resume_event") or {})
    request = deepcopy(state.get("pending_human_request") or {})
    if command.get("task_id") != request.get("task_id"):
        raise ValueError("association resume command does not match the active task")
    if command.get("action") == "RESOLVE_ASSOCIATION_EVIDENCE":
        page_id = str(command.get("page_id") or "")
        pages = deepcopy(state.get("pages", []))
        page = next((item for item in pages if item["page_id"] == page_id), None)
        if page is None:
            raise ValueError("association evidence resume page is outside the case")
        fields = deepcopy(page.get("extracted_fields") or {})
        fields.update({
            "person_id": str(command.get("person_id")),
            "person_name": str(command.get("person_name")),
            "role_signals": [str(item) for item in command.get("roles", [])],
            "owner_person_id": str(command.get("person_id")),
        })
        page["extracted_fields"] = fields
        next_version = int(state.get("case_version", 1)) + 1
        patch = {
            "pages": pages,
            "pending_human_request": None,
            "case_version": next_version,
            "changed_facts": ["association:evidence"],
            "association_assignment": None,
            "association_decision": None,
            "association_evidence_results": [],
            "active_node": "apply_association_human",
        }
        event = _event(
            state, patch,
            event_type="HUMAN_ASSOCIATION_EVIDENCE_APPLIED",
            node="apply_association_human",
            actor="human",
            task_id=request.get("task_id"),
            action="RESOLVE_ASSOCIATION_EVIDENCE",
            observation={"page_id": page_id, "roles": command.get("roles", [])},
            evidence=_evidence_refs(page),
            state_diff={"case_version": [state.get("case_version", 1), next_version]},
        )
        return {**patch, "pending_events": [event]}
    if command.get("action") != "CONFIRM_ASSOCIATION":
        raise ValueError("association resume action does not match the active task")
    selected_ids = [str(item) for item in command.get("selected_candidate_ids", [])]
    if not selected_ids:
        raise ValueError("CONFIRM_ASSOCIATION requires selected_candidate_ids")
    next_version = int(state.get("case_version", 1)) + 1
    # 人工确认本身会产生新的 Case 版本；候选集来自当前被 interrupt 固定的
    # Checkpoint，因此需要与人工决策一起原子地换基线。若只升级 Case 而保留
    # 旧 assignment 版本，Association Gate 会把同一批候选误判为 stale，
    # 再次创建完全相同的 HITL 任务，页面表现为“归属待确认”无法结束。
    rebased_assignment = deepcopy(state.get("association_assignment") or {})
    rebased_assignment["case_version"] = next_version
    patch = {
        "association_assignment": rebased_assignment,
        "association_decision": {
            "action": "APPLY_CANDIDATES",
            "selected_candidate_ids": selected_ids,
            "reason_code": "HUMAN_CONFIRMED_ASSOCIATION",
            "rationale_summary": "人工已在封闭候选中确认关联。",
            "evidence_refs": list(dict.fromkeys(
                ref
                for item in (state.get("association_assignment") or {}).get("candidates", [])
                if item.get("candidate_id") in selected_ids
                for ref in item.get("evidence_refs", [])
            )),
            "confidence": 1.0,
            "requires_human": False,
        },
        "pending_human_request": None,
        "case_version": next_version,
        "changed_facts": ["association:person_role_owner"],
        "active_node": "apply_association_human",
    }
    event = _event(
        state, patch,
        event_type="HUMAN_ASSOCIATION_APPLIED",
        node="apply_association_human",
        actor="human",
        task_id=request.get("task_id"),
        action="CONFIRM_ASSOCIATION",
        observation={"selected_candidate_ids": selected_ids},
        state_diff={"case_version": [state.get("case_version", 1), next_version]},
    )
    return {**patch, "pending_events": [event]}


def route_after_human(state: AuditState) -> Literal["gate", "retry"]:
    """候选确认回 Gate；人工补充的新 Observation 重新走页级证据链。"""

    return "gate" if state.get("association_decision") else "retry"


__all__ = [
    "apply_human", "await_human", "extract_evidence", "gate", "prepare_human",
    "recover", "review", "route_after_evidence", "route_after_gate",
    "route_after_human", "route_after_recovery",
]
