"""主 Pipeline 显式依赖合同。

主图只依赖能力 Protocol，不依赖具体 Agent 实现。这样代码阅读者从本文件即可
看出“两个受控决策 Agent + 一个共享恢复 Sub-Agent”的边界；模型、Prompt、Tool
和 Profile 的具体组装仍只发生在 Composition Root。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..agents.case_association import CaseAssociationAssignment, CaseAssociationRun
from ..agents.exception_recovery import ExceptionResult, ExceptionTask
from ..agents.material_audit import MaterialAuditAssignment, MaterialAuditRun
from .association_evidence import AssociationEvidenceExtractor


class RequirementResolver(Protocol):
    def resolve(
        self,
        *,
        product: str,
        channel: str,
        case_date: Any,
        person_roles: list[str],
    ) -> dict[str, Any]: ...


class RequirementEvidenceGrounder(Protocol):
    def ground(
        self,
        *,
        product: str,
        channel: str,
        case_date: Any,
        person_roles: list[str],
        problem_tasks: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


class CaseAssociationDecider(Protocol):
    """在 Workflow 构造的封闭候选中判断人员、角色与材料归属。"""

    def decide(self, assignment: CaseAssociationAssignment) -> CaseAssociationRun: ...


class MaterialSemanticDecider(Protocol):
    """对材料所属人、类型、跨页分组或 Requirement 归属做受控仲裁。"""

    def decide(self, assignment: MaterialAuditAssignment) -> MaterialAuditRun: ...


class ExceptionRecoveryCapability(Protocol):
    """为主图提供共享、短生命周期且有界的异常恢复 Tool Loop。"""

    def resolve(
        self,
        task: ExceptionTask,
        vlm_value: str,
        *,
        trusted_document_value: str,
        tool_context: dict[str, Any] | None = None,
    ) -> ExceptionResult: ...


@dataclass(frozen=True, slots=True)
class AuditPipelineDependencies:
    """读完本合同即可定位两个决策 Agent、恢复 Sub-Agent 与确定性能力。"""

    requirement_resolver: RequirementResolver
    requirement_evidence_rag: RequirementEvidenceGrounder
    association_evidence_extractor: AssociationEvidenceExtractor
    case_association_agent: CaseAssociationDecider
    exception_agent: ExceptionRecoveryCapability
    material_audit_agent: MaterialSemanticDecider
