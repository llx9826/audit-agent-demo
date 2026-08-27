"""只属于 Demo Profile 的固定案件提示和可重放 Tool Observation。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.agents.contracts import (
    AgentDecision,
    AgentToolCall,
    CallToolDecision,
    EscalateDecision,
    ExceptionDecisionContext,
    ApplyCandidateDecision,
    MaterialAuditAssignment,
    MaterialAuditDecision,
    RequestHumanDecision,
    RequestRecoveryDecision,
    RenderedPrompt,
)
from app.knowledge.contracts import (
    GroundedAnswerDecision,
    KnowledgeCitationContext,
    KnowledgeEntities,
    KnowledgeIntentDecision,
)
from app.orchestration.association_evidence import PageFieldAssociationEvidenceExtractor
from app.tools import ToolObservation, ToolRegistry, ToolRuntimeContext, ToolSpec


DEMO_KNOWLEDGE_QUESTIONS = [
    "南京公积金贷款，离婚需要什么婚姻证明？有依据吗？",
    "对比南京、北京、广州的公积金贷款婚姻材料",
    "广西建行个人经营抵押贷款需要哪些抵押物材料？",
    "北京公积金贷款婚姻电子证照可以免交纸质件吗？",
]


class DemoExceptionDecisionAdapter:
    """Observation-driven fixture implementing the production decision contract."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def decide(
        self,
        *,
        prompt: RenderedPrompt,
        context: ExceptionDecisionContext,
    ) -> AgentDecision:
        self.invocations.append({
            "prompt": prompt.model_copy(deep=True),
            "context": context.model_copy(deep=True),
        })
        used_tools = [item.tool for item in context.observations]
        available = set(context.allowed_tools)
        if not context.observations:
            if "ocr_retry" in available:
                tool, reason, summary = "ocr_retry", "RETRY_LOW_CONFIDENCE_OCR", "先执行低成本 OCR 重试。"
            elif "vlm_extract" in available:
                tool, reason, summary = "vlm_extract", "OWNER_OBSERVATION_REQUIRED", "先使用 VLM 重识别所属人。"
            else:
                tool, reason, summary = context.allowed_tools[0], "FIRST_AVAILABLE_OBSERVATION", "调用当前最小候选工具。"
        elif "vlm_extract" in available and "vlm_extract" not in used_tools:
            tool, reason, summary = "vlm_extract", "OCR_RETRY_INSUFFICIENT", "OCR 未形成可信变化，改用 VLM 重识别。"
        elif "document_search" in available and "document_search" not in used_tools:
            tool, reason, summary = "document_search", "INDEPENDENT_SOURCE_REQUIRED", "使用进件材料检索完成交叉验证。"
        else:
            return EscalateDecision(
                action="ESCALATE",
                reason_code="EVIDENCE_UNRESOLVED",
                rationale_summary="可用恢复能力已执行，仍未满足完成条件。",
            )
        return CallToolDecision(
            action="CALL_TOOL",
            tool_call=AgentToolCall(name=tool),
            reason_code=reason,
            rationale_summary=summary,
            expected_state_delta=["evidence_refs", "normalized_values"],
        )


class DemoMaterialAuditAdapter:
    """Deterministic fixture implementing the production Material Agent contract."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def decide_material(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: MaterialAuditAssignment,
    ) -> MaterialAuditDecision:
        self.invocations.append({
            "prompt": prompt.model_copy(deep=True),
            "assignment": assignment.model_copy(deep=True),
        })
        issue = assignment.issue
        candidate = max(assignment.candidates, key=lambda item: (item.workflow_score, item.candidate_id))
        # 演示场景刻意保留两个人员候选，真实链路由模型按同一合同决策。
        if issue.issue_type == "OWNER_AMBIGUOUS":
            return RequestHumanDecision(
                action="REQUEST_HUMAN",
                selected_candidate_id=candidate.candidate_id,
                reason_code="OWNER_EVIDENCE_AMBIGUOUS",
                rationale_summary="两个所属人候选均有页面证据，需要人工确认后才能写回。",
                evidence_refs=candidate.evidence_refs,
                confidence=max(issue.confidence, .72),
                requires_human=True,
            )
        recovery_type = {
            "TYPE_AMBIGUOUS": "TYPE_EVIDENCE_INSUFFICIENT",
            "BUNDLE_AMBIGUOUS": "BUNDLE_EVIDENCE_INSUFFICIENT",
            "REQUIREMENT_MATCH_AMBIGUOUS": "REQUIREMENT_EVIDENCE_INSUFFICIENT",
        }.get(issue.issue_type)
        if recovery_type:
            return RequestRecoveryDecision(
                action="REQUEST_RECOVERY",
                exception_type=recovery_type,
                missing_observations=["INDEPENDENT_DISAMBIGUATION_OBSERVATION"],
                reason_code="OBSERVATION_REQUIRED",
                rationale_summary="候选缺少可区分的独立 Observation，需先受控恢复。",
                evidence_refs=candidate.evidence_refs,
                confidence=max(issue.confidence, .7),
                requires_human=False,
            )
        return ApplyCandidateDecision(
            action="APPLY_CANDIDATE",
            selected_candidate_id=candidate.candidate_id,
            reason_code="CANDIDATE_EVIDENCE_DOMINANT",
            rationale_summary="候选证据在封闭候选集中具有唯一支持。",
            evidence_refs=candidate.evidence_refs,
            confidence=max(issue.confidence, .9),
            requires_human=False,
        )


class DemoKnowledgeAdapter:
    """仅供单元测试使用的确定性知识库 Test Double。"""

    suggested_questions = DEMO_KNOWLEDGE_QUESTIONS
    _regions = ("南京", "北京", "广州", "深圳", "武汉", "重庆", "苏州", "天津", "陕西", "广西", "全国")
    _branches = {
        "南京": "南京市", "北京": "北京市", "广州": "广州市", "深圳": "深圳市",
        "武汉": "武汉市", "重庆": "重庆市", "苏州": "苏州市", "天津": "天津市",
        "陕西": "陕西省", "广西": "广西区分行", "全国": "ALL",
    }
    _unsafe = ("伪造", "造假", "绕过", "规避审核")
    _out_of_scope = ("批贷", "审批通过", "额度", "利率", "风险评分", "房产估值", "准入")
    _scope_terms = ("材料", "证明", "证件", "补件", "提供", "提交", "依据", "来源", "电子证照", "免提交")

    def classify_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
    ) -> KnowledgeIntentDecision:
        del prompt
        regions = [region for region in self._regions if region in question]
        if "公积金" in question:
            product = "住房公积金个人住房贷款"
        elif any(term in question for term in ("经营贷", "经营抵押", "抵押快贷", "宅抵贷")):
            product = "个人经营抵押贷款"
        else:
            product = None
        entities = KnowledgeEntities(
            regions=regions,
            branches=[self._branches[region] for region in regions],
            marriage_statuses=[
                status for status in ("已婚", "离婚", "未婚", "单身", "丧偶") if status in question
            ],
            product=product,
            material_domain_code="MARRIAGE_FAMILY" if "婚" in question else None,
            material_domain="婚姻与家庭关系" if "婚" in question else None,
            person_roles=["APPLICANT"],
        )
        if any(term in question for term in self._unsafe):
            return KnowledgeIntentDecision(
                route="REFUSE", confidence=.99, reason_code="UNSAFE_OR_UNSUPPORTED",
                user_message="知识库不能帮助伪造材料、绕过审核或规避材料要求。",
                router="DEMO_STRUCTURED_INTENT_ROUTER_V1", entities=entities,
            )
        if any(term in question for term in self._out_of_scope):
            return KnowledgeIntentDecision(
                route="REFUSE", confidence=.98, reason_code="OUT_OF_SCOPE",
                user_message="该知识库只回答进件材料要求，不判断贷款审批、额度、利率或风险。",
                router="DEMO_STRUCTURED_INTENT_ROUTER_V1", entities=entities,
            )
        if not any(term in question for term in self._scope_terms):
            return KnowledgeIntentDecision(
                route="REFUSE", confidence=.94, reason_code="OUT_OF_SCOPE",
                user_message="请询问贷款进件材料清单、适用范围、来源依据、替代材料或补件方式。",
                router="DEMO_STRUCTURED_INTENT_ROUTER_V1", entities=entities,
            )
        if not entities.product:
            return KnowledgeIntentDecision(
                route="CLARIFY", confidence=.83, reason_code="PRODUCT_REQUIRED",
                user_message="请补充要查询的贷款产品；当前支持住房公积金个人住房贷款和个人经营抵押贷款材料。",
                router="DEMO_STRUCTURED_INTENT_ROUTER_V1", entities=entities,
            )

        query_modes: list[str] = []
        if any(term in question for term in ("对比", "区别", "各地", "不同城市")) or len(regions) > 1:
            query_modes.append("REGION_COMPARISON")
        if any(term in question for term in ("电子证照", "免提交", "替代", "无需提供")):
            query_modes.append("WAIVER_OR_SUBSTITUTE")
        if any(term in question for term in ("补件", "缺件", "怎么补")):
            query_modes.append("SUPPLEMENT")
        if any(term in question for term in ("是否", "能否", "需要吗", "适用")):
            query_modes.append("APPLICABILITY")
        if not query_modes:
            query_modes.append("LOOKUP")
        asks_requirement = any(term in question for term in self._scope_terms if term not in {"依据", "来源"})
        asks_source = any(term in question for term in ("依据", "证据", "来源"))
        primary_intent = "MATERIAL_REQUIREMENT" if asks_requirement else "SOURCE_TRACE"
        answer_modes = ["ANSWER_REQUIREMENT"] if asks_requirement else []
        if asks_source:
            answer_modes.append("TRACE_SOURCE")
        return KnowledgeIntentDecision(
            route="ACCEPT",
            primary_intent=primary_intent,
            answer_modes=answer_modes,
            query_modes=list(dict.fromkeys(query_modes)),
            entities=entities,
            confidence=.97 if regions else .9,
            reason_code="MATERIAL_KNOWLEDGE_QUERY",
            user_message="已识别为材料知识查询。",
            router="DEMO_STRUCTURED_INTENT_ROUTER_V1",
        )

    def answer_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
        citations: list[KnowledgeCitationContext],
    ) -> GroundedAnswerDecision:
        del prompt, question
        if not citations:
            return GroundedAnswerDecision(
                status="INSUFFICIENT_EVIDENCE",
                answer="当前检索范围内没有足够依据。",
            )
        selected: list[KnowledgeCitationContext] = []
        seen_regions: set[str] = set()
        for item in citations:
            scope = item.region or item.child_chunk_id
            if scope in seen_regions:
                continue
            selected.append(item)
            seen_regions.add(scope)
        if len(selected) == 1:
            selected = citations[:3]
        answer = "；".join(
            f"{item.atomic_requirement} [{item.child_chunk_id}]" for item in selected
        )
        return GroundedAnswerDecision(
            status="ANSWERED",
            answer=f"根据检索到的原子要求：{answer}",
            cited_chunk_ids=[item.child_chunk_id for item in selected],
        )


_DEMO_TOOL_INTENTS = {
    "ocr_retry": ["EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE", "EXCEPTION:OCR_FIELD_CONFLICT"],
    "vlm_extract": [
        "EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE", "EXCEPTION:MATERIAL_TYPE_AMBIGUOUS",
        "EXCEPTION:OWNER_ASSIGNMENT_AMBIGUOUS", "EXCEPTION:CROSS_PAGE_CONFLICT",
    ],
    "document_search": [
        "EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE", "EXCEPTION:MATERIAL_TYPE_AMBIGUOUS",
        "EXCEPTION:OWNER_ASSIGNMENT_AMBIGUOUS", "EXCEPTION:CROSS_PAGE_CONFLICT",
    ],
    "neighbor_page_search": ["EXCEPTION:MATERIAL_TYPE_AMBIGUOUS", "EXCEPTION:CROSS_PAGE_CONFLICT"],
    "page_integrity_check": ["EXCEPTION:PAGE_MISSING_OR_DUPLICATE"],
    "document_reload": ["EXCEPTION:PAGE_MISSING_OR_DUPLICATE", "EXCEPTION:TOOL_FAILURE"],
}


def _spec(name: str, description: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="demo-1.0.0",
        description=description,
        provider_type="LOCAL",
        provider_name="demo-material-provider",
        supported_intents=_DEMO_TOOL_INTENTS[name],
        side_effect="STATE_PROPOSAL",
        max_retries=1,
    )


def build_demo_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def ocr_retry(_arguments: BaseModel, _runtime: ToolRuntimeContext) -> ToolObservation:
        return ToolObservation(result="same_value_low_confidence")

    def vlm_extract(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        value = str(runtime.values["vlm_value"])
        return ToolObservation(
            result=value,
            normalized_value=value,
            evidence_refs=["E-VLM-01"],
            confidence=.96,
        )

    def document_search(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        value = str(runtime.values["trusted_document_value"])
        return ToolObservation(
            result=f"material_owner={value}",
            normalized_value=value,
            evidence_refs=["E-DOC-01"],
            confidence=.99,
        )

    def neighbor_page_search(_arguments: BaseModel, _runtime: ToolRuntimeContext) -> ToolObservation:
        return ToolObservation(
            result="adjacent_pages_restored", normalized_value="bundle-restored",
            evidence_refs=["E-NEIGHBOR-01"], confidence=.95,
        )

    def page_integrity_check(_arguments: BaseModel, _runtime: ToolRuntimeContext) -> ToolObservation:
        return ToolObservation(
            result="duplicate_page_detected", normalized_value="duplicate-page-removed",
            evidence_refs=["E-INTEGRITY-01"], confidence=.98,
        )

    def document_reload(_arguments: BaseModel, _runtime: ToolRuntimeContext) -> ToolObservation:
        return ToolObservation(
            result="source_asset_reloaded", normalized_value="asset-ready",
            evidence_refs=["E-RELOAD-01"], confidence=.97,
        )

    registry.register(_spec("ocr_retry", "Demo OCR retry observation."), ocr_retry)
    registry.register(_spec("vlm_extract", "Demo VLM observation."), vlm_extract)
    registry.register(_spec("document_search", "Demo case-material search observation."), document_search)
    registry.register(_spec("neighbor_page_search", "Demo adjacent-page observation."), neighbor_page_search)
    registry.register(_spec("page_integrity_check", "Demo missing/duplicate-page observation."), page_integrity_check)
    registry.register(_spec("document_reload", "Demo source-asset reload observation."), document_reload)
    return registry


def build_demo_agents():
    """Construct providers for injection by the application composition root."""

    from app.agents.exception_recovery import ExceptionRecoveryAgent
    from app.agents.material_audit import MaterialAuditAgent

    return (
        ExceptionRecoveryAgent(
            max_steps=3,
            registry=build_demo_tool_registry(),
            model_adapter=DemoExceptionDecisionAdapter(),
        ),
        MaterialAuditAgent(model_adapter=DemoMaterialAuditAdapter()),
    )


def build_demo_retriever():
    """仅供单元/合同测试的无外部进程 Retriever fixture。"""

    from app.rag.requirements.corpus import load_requirement_corpus
    from app.rag.requirements.hybrid import HybridRequirementRetriever

    return HybridRequirementRetriever(load_requirement_corpus())


def build_demo_pipeline_dependencies():
    """组装测试用 Pipeline；真实 Demo 进程仍由 ApplicationContainer 注入 Milvus。"""

    from app.orchestration import AuditPipelineDependencies
    from app.agents.case_association import CaseAssociationAgent
    from app.rag.requirements.evidence import RequirementEvidenceRAG
    from app.rag.requirements.rule_engine import RequirementRuleEngine

    exception_agent, material_agent = build_demo_agents()
    return AuditPipelineDependencies(
        requirement_resolver=RequirementRuleEngine(),
        requirement_evidence_rag=RequirementEvidenceRAG(build_demo_retriever()),
        association_evidence_extractor=PageFieldAssociationEvidenceExtractor(),
        case_association_agent=CaseAssociationAgent(),
        exception_agent=exception_agent,
        material_audit_agent=material_agent,
    )


def build_demo_knowledge_service():
    from app.knowledge import KnowledgeService

    class DemoQueryRewriter:
        """仅用于单元测试的可重放 Query fixture；应用 Container 不会组装它。"""

        def rewrite(self, request):
            return {
                "query": " ".join([
                    request.product,
                    request.channel,
                    *request.person_roles,
                    request.query,
                ]),
                "strategy": "TEST_FIXTURE_SCOPE_QUERY",
            }

    adapter = DemoKnowledgeAdapter()
    return KnowledgeService(
        intent_adapter=adapter,
        answer_adapter=adapter,
        query_rewriter=DemoQueryRewriter(),
        retriever=build_demo_retriever(),
        suggested_questions=adapter.suggested_questions,
    )
