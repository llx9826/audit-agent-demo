from operator import add
from typing import Annotated, Any, TypedDict


class AuditState(TypedDict, total=False):
    case_id: str
    thread_id: str
    case_version: int
    plan_version: int
    persons: list[dict[str, Any]]
    person_entities: list[dict[str, Any]]
    identity_mentions: list[dict[str, Any]]
    role_signals: list[dict[str, Any]]
    role_bindings: list[dict[str, Any]]
    material_owner_bindings: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    audit_plan: list[dict[str, Any]]
    material_matches: list[dict[str, Any]]
    human_tasks: list[dict[str, Any]]
    supplement_requests: list[dict[str, Any]]
    completeness_status: str

    business_fields: dict[str, Any]
    evidence_ledger: list[dict[str, Any]]
    changed_facts: list[str]
    dirty_tasks: list[str]
    invalidated_tasks: list[str]
    pending_human_request: dict[str, Any] | None
    resume_event: dict[str, Any] | None
    current_task_id: str | None
    active_node: str | None
    exception_context: dict[str, Any] | None
    exception_handoff: dict[str, Any] | None
    exception_result_gate: dict[str, Any] | None
    association_assignment: dict[str, Any] | None
    association_decision: dict[str, Any] | None
    association_gate: dict[str, Any] | None
    association_evidence_gate: dict[str, Any] | None
    association_recovery_request: dict[str, Any] | None
    association_recovery_attempts: int
    association_page_ids: list[str]
    association_evidence_dispatch_id: str | None
    association_evidence_results: Annotated[list[dict[str, Any]], add]
    material_owner_signals: list[dict[str, Any]]
    association_worker_page: dict[str, Any]
    audit_assignment: dict[str, Any] | None
    audit_decision: dict[str, Any] | None
    audit_gate: dict[str, Any] | None
    problem_tasks: list[dict[str, Any]]
    supplement_groundings: list[dict[str, Any]]
    rag_trace: dict[str, Any] | None
    replan_decisions: list[dict[str, Any]]
    # Orchestrator/Worker 临时状态：Worker 结果用 reducer 聚合，业务写入仍由 Fan-in Gate 完成。
    ready_task_ids: list[str]
    task_dispatch_id: str | None
    task_worker_results: Annotated[list[dict[str, Any]], add]
    worker_task: dict[str, Any]
    worker_requirement: dict[str, Any]
    worker_pages: list[dict[str, Any]]
    worker_case_version: int
    worker_plan_version: int
    pending_events: Annotated[list[dict[str, Any]], add]
    status: str
