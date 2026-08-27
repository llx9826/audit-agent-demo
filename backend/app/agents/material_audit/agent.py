"""材料语义候选消歧 Agent。

输入必须是 Workflow 生成的封闭候选集；本 Agent 只返回一次结构化提议，
不拥有 Tool、不修改 Case State，最终写入权属于主图中的 Plan Gate。
"""
from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from ..contracts import (
    MaterialAuditAssignment,
    MaterialAuditDecision,
    MaterialAuditRun,
    RequestHumanDecision,
)
from ...providers.decision_adapters import (
    MaterialAuditDecisionAdapter,
    ModelAdapterError,
    material_audit_model_adapter_from_env,
)
from ...prompting import PromptRegistry


class MaterialAuditAgent:
    """在封闭候选内做一次语义消歧；失败时确定性降级到 HITL。"""

    def __init__(
        self,
        *,
        model_adapter: MaterialAuditDecisionAdapter | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self.model_adapter = model_adapter or material_audit_model_adapter_from_env()
        self.prompt_registry = prompt_registry or PromptRegistry()

    def decide(self, assignment: MaterialAuditAssignment) -> MaterialAuditRun:
        prompt = self.prompt_registry.render_material_audit(assignment)
        try:
            raw = self.model_adapter.decide_material(prompt=prompt, assignment=assignment)
            decision = TypeAdapter(MaterialAuditDecision).validate_python(raw)
        except (ModelAdapterError, ValidationError, TypeError, ValueError):
            decision = RequestHumanDecision(
                action="REQUEST_HUMAN",
                reason_code="INVALID_STRUCTURED_OUTPUT",
                rationale_summary="Agent 结构化输出无效，已按安全策略升级人工处理。",
                evidence_refs=assignment.issue.evidence_refs,
                confidence=0.0,
                requires_human=True,
            )
        return MaterialAuditRun(
            prompt=prompt.metadata,
            decision=decision,
            model_trace=getattr(self.model_adapter, "last_trace", None),
        )
