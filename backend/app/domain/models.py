from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


TaskStatus = Literal[
    "PENDING", "RUNNING", "MATCHED", "MISSING", "UNREADABLE", "AMBIGUOUS",
    "WAITING_HUMAN", "DIRTY", "INVALIDATED", "SKIPPED", "SUCCESS", "FAILED",
]

HumanTaskStatus = Literal["OPEN", "RESOLVED", "CANCELLED"]


@dataclass(slots=True)
class PersonRole:
    person_id: str
    name: str
    roles: list[str]
    confirmed: bool = True
    source: str = "CASE_INPUT"


@dataclass(slots=True)
class IdentityMention:
    """从已分类影像中抽取的人员提及；本身不等于已确认人员。"""

    mention_id: str
    page_id: str
    display_name: str
    person_id: str | None = None
    identity_key: str | None = None
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoleSignal:
    """页级角色信号，必须经 Association Gate 才能变成业务角色。"""

    signal_id: str
    page_id: str
    person_id: str
    role: str
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PersonEntity:
    """跨页归并后的人员实体，仅保存可展示的脱敏标识。"""

    person_id: str
    display_name: str
    identity_key: str | None = None
    mention_ids: list[str] = field(default_factory=list)
    status: str = "CANDIDATE"
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoleBinding:
    """人员与业务角色的可追溯绑定。"""

    binding_id: str
    person_id: str
    role: str
    status: str
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    decided_by: str = "ASSOCIATION_GATE"


@dataclass(slots=True)
class MaterialOwnerBinding:
    """影像页与人员的归属绑定；Agent 只能提议，Gate 拥有写入权。"""

    binding_id: str
    page_id: str
    person_id: str
    status: str
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    decided_by: str = "ASSOCIATION_GATE"


@dataclass(slots=True)
class PageAsset:
    page_id: str
    bundle_id: str
    page_number: int
    domain: str
    material_type: str | None = None
    owner_person_id: str | None = None
    status: str = "PROCESSING"
    thumbnail_url: str | None = None
    preview_url: str | None = None
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AtomicRequirement:
    requirement_id: str
    title: str
    product: str
    channel: str
    checklist_version: int
    effective_from: str
    person_role: str
    material_type: str
    source_document: str
    source_section: str
    atomic_requirement: str = ""
    condition_expression: str = "always"
    required_pages: int = 1
    effective_to: str | None = None
    evidence_id: str | None = None


@dataclass(slots=True)
class Evidence:
    evidence_id: str
    source_type: str
    source_id: str
    value: str
    document_id: str | None = None
    page: int | None = None
    field: str | None = None
    requirement_id: str | None = None
    confidence: float | None = None


@dataclass(slots=True)
class AuditResult:
    """Versioned task result retained as the stable persistence contract."""

    task_id: str
    status: str
    conclusion: str
    confidence: float
    evidence_refs: list[str]
    requirement_refs: list[str]
    case_version: int
    plan_version: int
    # Result Version 与 Case/Plan Version 分离，便于 Fan-in Gate 拒绝陈旧 Worker 结果。
    result_version: int = 1


@dataclass(slots=True)
class RequiredMaterialTask:
    task_id: str
    task_type: str = "required_material"
    status: TaskStatus = "PENDING"
    # depends_on 保留为兼容投影；新编排器分别解析事实依赖和 Task 依赖。
    depends_on: list[str] = field(default_factory=list)
    fact_dependencies: list[str] = field(default_factory=list)
    task_dependencies: list[str] = field(default_factory=list)
    conflict_keys: list[str] = field(default_factory=list)
    requirement_refs: list[str] = field(default_factory=list)
    executor: str = "MATERIAL_MATCH_WORKER"
    execution_group: str = "MATERIAL_MATCH"
    result_version: int = 0
    requirement_id: str | None = None
    person_id: str | None = None
    person_role: str | None = None
    material_type: str | None = None
    matched_page_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    result: AuditResult | None = None


# Stable service-layer alias: every task now represents one person × requirement.
AuditTask = RequiredMaterialTask


@dataclass(slots=True)
class MaterialMatch:
    match_id: str
    task_id: str
    requirement_id: str
    person_id: str
    page_ids: list[str]
    status: str
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    decided_by: str = "WORKFLOW"


@dataclass(slots=True)
class HumanTask:
    human_task_id: str
    task_type: str
    title: str
    reason: str
    status: HumanTaskStatus = "OPEN"
    task_id: str | None = None
    candidate_options: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    checkpoint_id: str | None = None
    expected_case_version: int = 1
    resolution: dict[str, Any] | None = None


@dataclass(slots=True)
class SupplementRequest:
    request_id: str
    task_id: str
    requirement_id: str
    person_id: str
    material_type: str
    status: str = "OPEN"
    requested_at: str | None = None
    received_page_ids: list[str] = field(default_factory=list)


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
    thread_id: str | None = None
    run_id: str | None = None
    namespace: str | None = None


@dataclass(slots=True)
class CaseState:
    case_id: str
    thread_id: str = ""
    case_version: int = 1
    plan_version: int = 1

    # Material-completeness domain.
    persons: list[PersonRole] = field(default_factory=list)
    person_entities: list[PersonEntity] = field(default_factory=list)
    identity_mentions: list[IdentityMention] = field(default_factory=list)
    role_signals: list[RoleSignal] = field(default_factory=list)
    role_bindings: list[RoleBinding] = field(default_factory=list)
    material_owner_bindings: list[MaterialOwnerBinding] = field(default_factory=list)
    pages: list[PageAsset] = field(default_factory=list)
    requirements: list[AtomicRequirement] = field(default_factory=list)
    audit_plan: list[RequiredMaterialTask] = field(default_factory=list)
    material_matches: list[MaterialMatch] = field(default_factory=list)
    human_tasks: list[HumanTask] = field(default_factory=list)
    supplement_requests: list[SupplementRequest] = field(default_factory=list)
    completeness_status: str = "NOT_STARTED"

    business_fields: dict[str, Any] = field(default_factory=dict)
    evidence_ledger: list[Evidence] = field(default_factory=list)
    changed_facts: list[str] = field(default_factory=list)
    dirty_tasks: list[str] = field(default_factory=list)
    invalidated_tasks: list[str] = field(default_factory=list)
    pending_human_request: dict[str, Any] | None = None
    resume_event: dict[str, Any] | None = None
    current_task_id: str | None = None
    active_node: str | None = None
    exception_context: dict[str, Any] | None = None
    association_assignment: dict[str, Any] | None = None
    association_decision: dict[str, Any] | None = None
    association_gate: dict[str, Any] | None = None
    material_owner_signals: list[dict[str, Any]] = field(default_factory=list)
    audit_assignment: dict[str, Any] | None = None
    audit_decision: dict[str, Any] | None = None
    audit_gate: dict[str, Any] | None = None
    problem_tasks: list[dict[str, Any]] = field(default_factory=list)
    supplement_groundings: list[dict[str, Any]] = field(default_factory=list)
    rag_trace: dict[str, Any] | None = None
    replan_decisions: list[dict[str, Any]] = field(default_factory=list)
    status: str = "READY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
