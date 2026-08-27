"""Typed contracts for scoped knowledge intent routing and grounded answers."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..agents.contracts import RenderedPrompt
from .taxonomy import KnowledgeMaterialDomain


KnowledgeIntent = Literal[
    "MATERIAL_REQUIREMENT",
    "SOURCE_TRACE",
]

KnowledgeQueryMode = Literal[
    "LOOKUP",
    "APPLICABILITY",
    "WAIVER_OR_SUBSTITUTE",
    "REGION_COMPARISON",
    "SUPPLEMENT",
]

KnowledgeReasonCode = Literal[
    "MATERIAL_KNOWLEDGE_QUERY",
    "SOURCE_TRACE_QUERY",
    "PRODUCT_REQUIRED",
    "REGION_REQUIRED",
    "PERSON_STATE_REQUIRED",
    "MATERIAL_SCOPE_REQUIRED",
    "OUT_OF_SCOPE",
    "UNSAFE_OR_UNSUPPORTED",
]

KnowledgeProduct = Literal[
    "住房公积金个人住房贷款",
    "个人经营抵押贷款",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeEntities(StrictModel):
    regions: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    marriage_statuses: list[str] = Field(default_factory=list)
    person_roles: list[str] = Field(default_factory=lambda: ["APPLICANT"])
    # Product 直接约束为索引 Metadata 枚举，防止 LLM 用俗称造成零召回。
    product: KnowledgeProduct | None = None
    # 检索只使用受控领域编码；material_domain 保留模型抽取的可读原文和兼容性。
    material_domain_code: KnowledgeMaterialDomain | None = None
    material_domain: str | None = None
    material_type: str | None = None
    case_date: str | None = None


class KnowledgeIntentDecision(StrictModel):
    route: Literal["ACCEPT", "CLARIFY", "REFUSE"]
    primary_intent: KnowledgeIntent | None = None
    answer_modes: list[Literal["ANSWER_REQUIREMENT", "TRACE_SOURCE"]] = Field(default_factory=list)
    query_modes: list[KnowledgeQueryMode] = Field(default_factory=list)
    entities: KnowledgeEntities = Field(default_factory=KnowledgeEntities)
    confidence: float = Field(ge=0, le=1)
    # 受控原因码既让在线路由可观测，也把模型的 null/空字符串挡在检索之前。
    reason_code: KnowledgeReasonCode
    user_message: str = Field(min_length=1, max_length=240)
    router: str


class KnowledgeCitationContext(StrictModel):
    child_chunk_id: str
    parent_chunk_id: str | None = None
    title: str
    atomic_requirement: str
    parent_text: str | None = None
    source_document: str
    source_section: str
    source_url: str | None = None
    region: str | None = None
    branch: str | None = None


class GroundedAnswerDecision(StrictModel):
    status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE"]
    answer: str = Field(min_length=1, max_length=1200)
    cited_chunk_ids: list[str] = Field(default_factory=list)


class QueryRewriteDecision(StrictModel):
    """受 Metadata 约束的检索改写；禁止补造地区、产品或材料要求。"""

    rewritten_query: str = Field(min_length=2, max_length=500)
    changed: bool
    reason_code: str = Field(min_length=1)


class KnowledgeIntentAdapter(Protocol):
    def classify_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
    ) -> KnowledgeIntentDecision: ...


class KnowledgeAnswerAdapter(Protocol):
    def answer_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
        citations: list[KnowledgeCitationContext],
    ) -> GroundedAnswerDecision: ...
