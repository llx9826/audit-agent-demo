"""人员、角色与材料归属的受控关联合同。

Workflow 先生成封闭候选，Agent 只能选择候选或请求人工，
不能新增人员、角色、页面或证据。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from ..contracts import PromptMetadata, RenderedPrompt, StrictModel


AssociationCandidateType = Literal["PERSON_ENTITY", "PERSON_ROLE", "MATERIAL_OWNER"]


class AssociationCandidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    candidate_type: AssociationCandidateType
    person_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    role: str | None = None
    page_id: str | None = None
    mention_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)
    workflow_score: float = Field(ge=0, le=1)
    observations: dict[str, Any] = Field(default_factory=dict)


AssociationAction = Literal["APPLY_CANDIDATES", "REQUEST_HUMAN", "REQUEST_RECOVERY"]


class CaseAssociationAssignment(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    assignment_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    objective: str = Field(min_length=1)
    candidates: list[AssociationCandidate] = Field(min_length=1, max_length=64)
    allowed_actions: list[AssociationAction]


class ApplyAssociationCandidates(StrictModel):
    action: Literal["APPLY_CANDIDATES"]
    selected_candidate_ids: list[str] = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    requires_human: Literal[False] = False


class RequestAssociationHuman(StrictModel):
    action: Literal["REQUEST_HUMAN"]
    selected_candidate_ids: list[str] = Field(default_factory=list)
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    requires_human: Literal[True] = True


class RequestAssociationRecovery(StrictModel):
    """请求 Workflow 委托 Exception Agent 获取新的关联 Observation。"""

    action: Literal["REQUEST_RECOVERY"]
    selected_candidate_ids: list[str] = Field(default_factory=list)
    exception_type: Literal[
        "IDENTITY_EVIDENCE_INSUFFICIENT",
        "ROLE_EVIDENCE_INSUFFICIENT",
        "OWNER_EVIDENCE_INSUFFICIENT",
        "CROSS_PAGE_EVIDENCE_CONFLICT",
    ]
    missing_observations: list[str] = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    rationale_summary: str = Field(min_length=1, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    requires_human: Literal[False] = False


CaseAssociationDecision = Annotated[
    Union[ApplyAssociationCandidates, RequestAssociationHuman, RequestAssociationRecovery],
    Field(discriminator="action"),
]


class CaseAssociationRun(StrictModel):
    prompt: PromptMetadata
    decision: CaseAssociationDecision
    model_trace: dict[str, Any] | None = None


class CaseAssociationDecisionAdapter:
    """只用于类型标注；运行时 Adapter 由 Agent 通过 Protocol 调用。"""

    def decide_association(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: CaseAssociationAssignment,
    ) -> CaseAssociationDecision:  # pragma: no cover - documentation contract
        raise NotImplementedError
