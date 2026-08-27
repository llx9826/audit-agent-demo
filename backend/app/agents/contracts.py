"""Typed contracts shared by prompts, model adapters and the exception graph."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from ..tools.contracts import ToolSpec


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptMetadata(StrictModel):
    prompt_id: str
    version: str
    sha256: str


class RenderedPrompt(StrictModel):
    metadata: PromptMetadata
    system: str
    user: str


MaterialIssueType = Literal[
    "OWNER_AMBIGUOUS",
    "TYPE_AMBIGUOUS",
    "BUNDLE_AMBIGUOUS",
    "REQUIREMENT_MATCH_AMBIGUOUS",
]


MaterialAuditAction = Literal[
    "APPLY_CANDIDATE",
    "REQUEST_HUMAN",
    "REQUEST_RECOVERY",
]


class MaterialIssue(StrictModel):
    task_id: str = Field(min_length=1)
    issue_type: MaterialIssueType
    person_id: str = Field(min_length=1)
    material_type: str = Field(min_length=1)
    candidate_page_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class MaterialCandidate(StrictModel):
    """由 Workflow 构造的封闭候选；模型无权新增页面、人员或材料类型。"""

    candidate_id: str = Field(min_length=1)
    page_ids: list[str] = Field(min_length=1)
    proposed_person_id: str = Field(min_length=1)
    proposed_material_type: str = Field(min_length=1)
    proposed_requirement_id: str = Field(min_length=1)
    proposed_bundle_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    observations: dict[str, Any] = Field(default_factory=dict)
    workflow_score: float = Field(default=0.0, ge=0, le=1)


class MaterialAuditAssignment(StrictModel):
    """Minimal context delegated by the deterministic material Workflow."""

    schema_version: Literal["2.0"] = "2.0"
    assignment_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1)
    issue: MaterialIssue
    candidates: list[MaterialCandidate] = Field(min_length=1, max_length=8)
    allowed_actions: list[MaterialAuditAction] = Field(min_length=1)


class ApplyCandidateDecision(StrictModel):
    action: Literal["APPLY_CANDIDATE"]
    selected_candidate_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    requires_human: Literal[False] = False


class RequestHumanDecision(StrictModel):
    action: Literal["REQUEST_HUMAN"]
    selected_candidate_id: str | None = None
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    requires_human: Literal[True] = True


class RequestRecoveryDecision(StrictModel):
    action: Literal["REQUEST_RECOVERY"]
    exception_type: Literal[
        "OWNER_EVIDENCE_INSUFFICIENT",
        "TYPE_EVIDENCE_INSUFFICIENT",
        "CROSS_PAGE_EVIDENCE_INSUFFICIENT",
        "BUNDLE_EVIDENCE_INSUFFICIENT",
        "REQUIREMENT_EVIDENCE_INSUFFICIENT",
        "PAGE_INTEGRITY_EVIDENCE_INSUFFICIENT",
        "TOOL_RECOVERY_REQUIRED",
    ]
    missing_observations: list[str] = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    requires_human: Literal[False] = False


MaterialAuditDecision = Annotated[
    Union[ApplyCandidateDecision, RequestHumanDecision, RequestRecoveryDecision],
    Field(discriminator="action"),
]


class MaterialAuditRun(StrictModel):
    prompt: PromptMetadata
    decision: MaterialAuditDecision
    model_trace: dict[str, Any] | None = None


class CompletionCondition(StrictModel):
    """Executable completion contract; it deliberately contains no Tool names."""

    condition_type: Literal["NORMALIZED_VALUE_CONSENSUS"] = "NORMALIZED_VALUE_CONSENSUS"
    minimum_independent_sources: int = Field(default=2, ge=2)
    minimum_confidence: float = Field(default=0.9, ge=0, le=1)


class ExceptionEnvelope(StrictModel):
    """Minimal, versioned handoff from the parent Workflow to the sub-agent."""

    schema_version: Literal["1.0"] = "1.0"
    handoff_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    plan_version: int = Field(ge=1)
    source_task_id: str = Field(min_length=1)
    exception_type: str = Field(min_length=1)
    problem_summary: str = Field(min_length=1, max_length=500)
    context_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(min_length=1)
    max_steps: int = Field(ge=1)
    max_retry: int = Field(default=1, ge=0)
    completion_condition: CompletionCondition


class FactPatchProposal(StrictModel):
    path: str = Field(min_length=1)
    operation: Literal["ADD", "REPLACE"]
    value: Any
    evidence_refs: list[str] = Field(min_length=1)


class ExceptionResolutionContract(StrictModel):
    handoff_id: str = Field(min_length=1)
    status: Literal["RESOLVED", "NEED_HUMAN"]
    conclusion: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    patch_proposals: list[FactPatchProposal] = Field(default_factory=list)
    steps_used: int = Field(ge=0)
    stop_reason: str


class AgentToolCall(StrictModel):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class CallToolDecision(StrictModel):
    action: Literal["CALL_TOOL"]
    tool_call: AgentToolCall
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    expected_state_delta: list[str] = Field(default_factory=list)


class ResolveDecision(StrictModel):
    action: Literal["RESOLVE"]
    conclusion: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)


class EscalateDecision(StrictModel):
    action: Literal["ESCALATE"]
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    missing_evidence: list[str] = Field(default_factory=list)


AgentDecision = Annotated[
    Union[CallToolDecision, ResolveDecision, EscalateDecision],
    Field(discriminator="action"),
]


class AgentObservation(StrictModel):
    step: int = Field(ge=1)
    tool: str
    result: str | None = None
    normalized_value: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    error_code: str | None = None
    state_changed: bool = False


class ExceptionDecisionContext(StrictModel):
    exception_type: str
    source_task_id: str
    problem: str
    context_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    allowed_tools: list[str]
    registered_tools: list[str]
    tool_specs: list[ToolSpec] = Field(default_factory=list)
    observations: list[AgentObservation] = Field(default_factory=list)
    normalized_values: dict[str, str] = Field(default_factory=dict)
    steps_used: int = Field(ge=0)
    max_steps: int = Field(ge=1)
    completion_condition: CompletionCondition
