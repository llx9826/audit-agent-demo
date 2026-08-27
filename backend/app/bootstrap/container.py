"""唯一 Composition Root：集中组装模型、Agent、RAG、Graph 与运行时。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from ..agents.exception_recovery import ExceptionRecoveryAgent
from ..agents.case_association import CaseAssociationAgent
from ..agents.material_audit import MaterialAuditAgent
from ..providers.decision_adapters import GatewayDecisionAdapter
from ..knowledge import KnowledgeRunManager, KnowledgeService
from ..knowledge.adapters import GatewayKnowledgeAdapter, GatewayQueryRewriter
from ..providers import ModelGateway, gateway_from_settings
from ..orchestration import AuditOrchestrator, AuditPipelineDependencies
from ..orchestration.association_evidence import (
    PageFieldAssociationEvidenceExtractor,
    ToolAssociationEvidenceExtractor,
)
from ..rag.requirements.evidence import RequirementEvidenceRAG
from ..rag.cache import build_rag_cache
from ..rag.requirements.rule_engine import RequirementRuleEngine
from ..runtime.run_manager import RunManager
from ..service import AuditService
from ..tools.local import build_local_tool_registry
from .settings import AppSettings, settings_from_env


@dataclass(slots=True)
class ApplicationContainer:
    """进程级依赖容器；业务 Node 不允许自行构造这些能力。"""

    settings: AppSettings
    audit_service: AuditService
    knowledge_service: KnowledgeService
    knowledge_run_manager: KnowledgeRunManager
    run_manager: RunManager
    audit_orchestrator: AuditOrchestrator
    model_gateway: ModelGateway | None = None

    @staticmethod
    def _knowledge_model_signature(settings: AppSettings) -> str:
        """生成不含 Key/Base URL 的稳定签名，用于隔离不同模型缓存。"""

        if settings.model is None:
            return "unconfigured-model"
        endpoint_map = settings.model.endpoint_map()
        material = {
            "routes": {
                role: [
                    {
                        "provider": endpoint_map[name].provider,
                        "model": endpoint_map[name].model,
                        "structured_mode": endpoint_map[name].structured_mode,
                    }
                    for name in settings.model.routes.get(role, ())
                ]
                for role in ("knowledge_intent", "query_rewrite", "knowledge_grounding")
            },
            "max_tokens": settings.model.max_tokens,
            "role_max_tokens": {
                role: settings.model.max_tokens_for(role)
                for role in ("knowledge_intent", "query_rewrite", "knowledge_grounding")
            },
        }
        canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def build(cls, *, profile: str | None = None) -> "ApplicationContainer":
        settings = settings_from_env(profile=profile)
        if settings.model is None:
            raise ValueError(
                "RAG requires LLM_BASE_URL and LLM_MODEL; demo only fixes Case/Tool data, "
                "it does not replace the real knowledge model"
            )
        gateway = gateway_from_settings(settings.model)
        knowledge_adapter = GatewayKnowledgeAdapter(gateway)
        rag_cache = build_rag_cache(
            backend=settings.rag_cache_backend,
            redis_url=settings.rag_cache_redis_url.get_secret_value(),
            verify_write=settings.rag_cache_verify_write,
        )
        if settings.profile == "demo":
            from demo.providers import DEMO_KNOWLEDGE_QUESTIONS, build_demo_tool_registry

            # Demo 只固定案件和 Tool 返回值；Audit/Exception 的每一步决策仍走
            # 与知识库相同的 ModelGateway，避免演示轨迹与真实 Agent 代码分叉。
            exception_agent = ExceptionRecoveryAgent(
                max_steps=4,
                registry=build_demo_tool_registry(),
                model_adapter=GatewayDecisionAdapter(gateway),
            )
            material_agent = MaterialAuditAgent(
                model_adapter=GatewayDecisionAdapter(gateway),
            )
            association_agent = CaseAssociationAgent(
                model_adapter=GatewayDecisionAdapter(gateway),
            )
            audit_service = AuditService(
                pipeline_dependencies=AuditPipelineDependencies(
                    requirement_resolver=RequirementRuleEngine(),
                    requirement_evidence_rag=RequirementEvidenceRAG(),
                    association_evidence_extractor=PageFieldAssociationEvidenceExtractor(),
                    case_association_agent=association_agent,
                    exception_agent=exception_agent,
                    material_audit_agent=material_agent,
                ),
                max_task_concurrency=settings.task_worker_max_concurrency,
                graph_recursion_limit=settings.audit_graph_recursion_limit,
            )
            run_manager = RunManager(audit_service)
            knowledge_service = KnowledgeService(
                intent_adapter=knowledge_adapter,
                answer_adapter=knowledge_adapter,
                query_rewriter=GatewayQueryRewriter(knowledge_adapter),
                suggested_questions=DEMO_KNOWLEDGE_QUESTIONS,
                cache=rag_cache,
                cache_ttl_seconds=settings.rag_cache_ttl_seconds,
                cache_version=settings.rag_cache_version,
                cache_model_signature=cls._knowledge_model_signature(settings),
            )
            return cls(
                settings=settings,
                model_gateway=gateway,
                audit_service=audit_service,
                knowledge_service=knowledge_service,
                knowledge_run_manager=KnowledgeRunManager(knowledge_service),
                run_manager=run_manager,
                audit_orchestrator=AuditOrchestrator(audit_service, run_manager),
            )

        local_tools = build_local_tool_registry()
        exception_agent = ExceptionRecoveryAgent(
            max_steps=4,
            registry=local_tools,
            model_adapter=GatewayDecisionAdapter(gateway),
        )
        material_agent = MaterialAuditAgent(
            model_adapter=GatewayDecisionAdapter(gateway),
        )
        association_agent = CaseAssociationAgent(
            model_adapter=GatewayDecisionAdapter(gateway),
        )
        audit_service = AuditService(
            pipeline_dependencies=AuditPipelineDependencies(
                requirement_resolver=RequirementRuleEngine(),
                requirement_evidence_rag=RequirementEvidenceRAG(),
                association_evidence_extractor=ToolAssociationEvidenceExtractor(local_tools),
                case_association_agent=association_agent,
                exception_agent=exception_agent,
                material_audit_agent=material_agent,
            ),
            max_task_concurrency=settings.task_worker_max_concurrency,
            graph_recursion_limit=settings.audit_graph_recursion_limit,
        )
        run_manager = RunManager(audit_service)
        knowledge_service = KnowledgeService(
            intent_adapter=knowledge_adapter,
            answer_adapter=knowledge_adapter,
            query_rewriter=GatewayQueryRewriter(knowledge_adapter),
            suggested_questions=[],
            cache=rag_cache,
            cache_ttl_seconds=settings.rag_cache_ttl_seconds,
            cache_version=settings.rag_cache_version,
            cache_model_signature=cls._knowledge_model_signature(settings),
        )
        return cls(
            settings=settings,
            model_gateway=gateway,
            audit_service=audit_service,
            knowledge_service=knowledge_service,
            knowledge_run_manager=KnowledgeRunManager(knowledge_service),
            run_manager=run_manager,
            audit_orchestrator=AuditOrchestrator(audit_service, run_manager),
        )

    def close(self) -> None:
        self.knowledge_run_manager.close()
        self.run_manager.close()
        self.audit_service.close()
