from operator import add
from typing import Annotated, Any, TypedDict


class AuditState(TypedDict, total=False):
    case_id: str
    case_version: int
    plan_version: int
    documents: list[dict[str, Any]]
    entities: dict[str, dict[str, Any]]
    relations: list[dict[str, Any]]
    business_fields: dict[str, Any]
    audit_plan: list[dict[str, Any]]
    task_results: dict[str, dict[str, Any]]
    evidence_ledger: list[dict[str, Any]]
    changed_facts: list[str]
    dirty_tasks: list[str]
    invalidated_tasks: list[str]
    pending_human_request: dict[str, Any] | None
    resume_event: dict[str, Any] | None
    current_task_id: str | None
    active_node: str | None
    exception_context: dict[str, Any] | None
    rag_trace: dict[str, Any] | None
    replan_decisions: list[dict[str, Any]]
    pending_events: Annotated[list[dict[str, Any]], add]
    status: str
