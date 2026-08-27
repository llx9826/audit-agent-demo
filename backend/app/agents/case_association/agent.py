"""受控 Case Association Agent。"""
from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from typing import Protocol

from ..contracts import RenderedPrompt
from ...prompting.registry import PromptRegistry
from .contracts import (
    ApplyAssociationCandidates,
    CaseAssociationAssignment,
    CaseAssociationDecision,
    CaseAssociationRun,
    RequestAssociationHuman,
    RequestAssociationRecovery,
)


class AssociationModelAdapter(Protocol):
    def decide_association(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: CaseAssociationAssignment,
    ) -> CaseAssociationDecision: ...


class DeterministicAssociationAdapter:
    """本地单测适配器；生产由 Composition Root 注入 ModelGateway。"""

    def decide_association(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: CaseAssociationAssignment,
    ) -> CaseAssociationDecision:
        del prompt
        observations = [item.observations for item in assignment.candidates]
        evidence = list(dict.fromkeys(
            ref for item in assignment.candidates for ref in item.evidence_refs
        ))
        if any(item.get("recovery_exhausted") for item in observations):
            return RequestAssociationHuman(
                action="REQUEST_HUMAN",
                selected_candidate_ids=[],
                reason_code="ASSOCIATION_RECOVERY_EXHAUSTED",
                rationale_summary="机器恢复已耗尽，候选仍不能唯一确认。",
                evidence_refs=evidence,
                confidence=max((item.workflow_score for item in assignment.candidates), default=0.0),
                requires_human=True,
            )
        if any(item.get("cross_page_conflict") for item in observations):
            return RequestAssociationRecovery(
                action="REQUEST_RECOVERY",
                selected_candidate_ids=[],
                exception_type="CROSS_PAGE_EVIDENCE_CONFLICT",
                missing_observations=["INDEPENDENT_IDENTITY_OBSERVATION"],
                reason_code="CROSS_PAGE_EVIDENCE_CONFLICT",
                rationale_summary="跨页身份信号冲突，需要独立材料观察。",
                evidence_refs=evidence,
                confidence=max((item.workflow_score for item in assignment.candidates), default=0.0),
                requires_human=False,
            )
        if any(item.get("missing_observations") for item in observations):
            return RequestAssociationRecovery(
                action="REQUEST_RECOVERY",
                selected_candidate_ids=[],
                exception_type="ROLE_EVIDENCE_INSUFFICIENT",
                missing_observations=list(dict.fromkeys(
                    value
                    for item in observations
                    for value in item.get("missing_observations", [])
                )),
                reason_code="ROLE_EVIDENCE_INSUFFICIENT",
                rationale_summary="角色候选缺少独立材料观察，需要先恢复证据。",
                evidence_refs=evidence,
                confidence=max((item.workflow_score for item in assignment.candidates), default=0.0),
                requires_human=False,
            )
        selected = [item for item in assignment.candidates if item.evidence_refs]
        return ApplyAssociationCandidates(
            action="APPLY_CANDIDATES",
            selected_candidate_ids=[item.candidate_id for item in selected],
            reason_code="EVIDENCE_BACKED_CANDIDATES",
            rationale_summary="候选均绑定页级证据，可交由 Association Gate 校验并写入。",
            evidence_refs=evidence,
            confidence=min((item.workflow_score for item in selected), default=.0),
        )


class CaseAssociationAgent:
    """对 Workflow 的封闭关联候选做一次结构化判断。"""

    def __init__(
        self,
        *,
        model_adapter: AssociationModelAdapter | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self.model_adapter = model_adapter or DeterministicAssociationAdapter()
        self.prompt_registry = prompt_registry or PromptRegistry()

    def decide(self, assignment: CaseAssociationAssignment) -> CaseAssociationRun:
        prompt = self.prompt_registry.render_case_association(assignment)
        try:
            raw = self.model_adapter.decide_association(
                prompt=prompt,
                assignment=assignment,
            )
            # 即使 Provider 声明了 Structured Output，仍在 Agent 边界执行本地校验；
            # 业务 Gate 只接收已通过判别联合合同的对象。
            decision = TypeAdapter(CaseAssociationDecision).validate_python(raw)
        except (RuntimeError, ValidationError, TypeError, ValueError) as exc:
            # 关联阶段是所有后续 Task 的事实入口。模型路由耗尽或结构化输出无效时
            # 不能猜测关系，也不能击穿整笔进件，安全降级为持久化 HITL。
            decision = RequestAssociationHuman(
                action="REQUEST_HUMAN",
                selected_candidate_ids=[],
                reason_code="ASSOCIATION_MODEL_UNAVAILABLE",
                rationale_summary="关联模型暂不可用，需人工在证据候选中确认人员与角色。",
                evidence_refs=[],
                confidence=0.0,
                requires_human=True,
            )
            trace = getattr(exc, "trace", None)
        else:
            trace = getattr(self.model_adapter, "last_trace", None)
        return CaseAssociationRun(
            prompt=prompt.metadata,
            decision=decision,
            model_trace=trace,
        )
