from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TaskStatus = Literal[
    "PENDING", "RUNNING", "SUCCESS", "FAILED", "WAITING_HUMAN",
    "DIRTY", "INVALIDATED", "SKIPPED",
]


@dataclass(slots=True)
class Evidence:
    evidence_id: str
    source_type: str
    source_id: str
    value: str
    document_id: str | None = None
    page: int | None = None
    field: str | None = None
    rule_id: str | None = None
    confidence: float | None = None


@dataclass(slots=True)
class AuditResult:
    task_id: str
    status: str
    conclusion: str
    confidence: float
    evidence_refs: list[str]
    rule_refs: list[str]
    case_version: int
    plan_version: int


@dataclass(slots=True)
class AuditTask:
    task_id: str
    task_type: str
    status: TaskStatus = "PENDING"
    depends_on: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    required_entities: list[str] = field(default_factory=list)
    result: AuditResult | None = None


@dataclass(slots=True)
class Event:
    event_id: str
    seq: int
    case_id: str
    actor: str
    event_type: str
    timestamp: str
    case_version: int
    plan_version: int
    payload: dict[str, Any]
    checkpoint_id: str | None = None


@dataclass(slots=True)
class CaseState:
    case_id: str
    case_version: int = 1
    plan_version: int = 1
    documents: list[dict[str, Any]] = field(default_factory=list)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)
    business_fields: dict[str, Any] = field(default_factory=dict)
    audit_plan: list[AuditTask] = field(default_factory=list)
    task_results: dict[str, AuditResult] = field(default_factory=dict)
    evidence_ledger: list[Evidence] = field(default_factory=list)
    changed_facts: list[str] = field(default_factory=list)
    dirty_tasks: list[str] = field(default_factory=list)
    invalidated_tasks: list[str] = field(default_factory=list)
    pending_human_request: dict[str, Any] | None = None
    resume_event: dict[str, Any] | None = None
    current_task_id: str | None = None
    active_node: str | None = None
    exception_context: dict[str, Any] | None = None
    rag_trace: dict[str, Any] | None = None
    replan_decisions: list[dict[str, Any]] = field(default_factory=list)
    status: str = "READY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
