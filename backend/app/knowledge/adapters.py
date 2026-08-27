"""Provider adapters for the knowledge RAG contracts."""
from __future__ import annotations

from pydantic import TypeAdapter

from ..agents.contracts import RenderedPrompt
from ..providers.decision_adapters import QwenVllmAdapter
from ..providers import Message, ModelGateway
from ..providers.contracts import GatewayExhaustedError
from ..rag.online import OnlineRetrievalRequest
from ..prompting import PromptRegistry
from .contracts import (
    GroundedAnswerDecision,
    KnowledgeCitationContext,
    KnowledgeIntentDecision,
    QueryRewriteDecision,
)


class KnowledgeModelRouteError(RuntimeError):
    """携带脱敏 Gateway Trace 的知识模型错误；不包含 Key、URL 或原始 Prompt。"""

    def __init__(self, role: str, trace: dict) -> None:
        super().__init__(f"knowledge model route {role} exhausted")
        self.role = role
        self.trace = trace


class QwenKnowledgeAdapter:
    """Use the same Qwen/vLLM transport with separate strict schemas."""

    def __init__(self, client: QwenVllmAdapter) -> None:
        self.client = client
        self._intent = TypeAdapter(KnowledgeIntentDecision)
        self._answer = TypeAdapter(GroundedAnswerDecision)

    def classify_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
    ) -> KnowledgeIntentDecision:
        del question
        return self.client.invoke_structured(
            prompt=prompt,
            decision_adapter=self._intent,
            schema_name="knowledge_intent_decision",
        )

    def answer_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
        citations: list[KnowledgeCitationContext],
    ) -> GroundedAnswerDecision:
        del question, citations
        return self.client.invoke_structured(
            prompt=prompt,
            decision_adapter=self._answer,
            schema_name="knowledge_grounded_answer",
        )


class GatewayKnowledgeAdapter:
    """知识意图和 Grounding 共用统一 Gateway，但使用独立任务 Route。"""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self._intent = TypeAdapter(KnowledgeIntentDecision)
        self._answer = TypeAdapter(GroundedAnswerDecision)
        self.last_trace: dict | None = None

    def _invoke(self, *, role: str, prompt: RenderedPrompt, adapter: TypeAdapter, schema_name: str):
        try:
            result = self.gateway.structured_sync(
                role=role,
                messages=[
                    Message(role="system", content=prompt.system),
                    Message(role="user", content=prompt.user),
                ],
                schema=adapter,
                schema_name=schema_name,
            )
        except GatewayExhaustedError as exc:
            self.last_trace = exc.trace.to_public_dict()
            raise KnowledgeModelRouteError(role, self.last_trace) from exc
        self.last_trace = result.trace.to_public_dict()
        return result.value

    def classify_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
    ) -> KnowledgeIntentDecision:
        del question
        return self._invoke(
            role="knowledge_intent",
            prompt=prompt,
            adapter=self._intent,
            schema_name="knowledge_intent_decision",
        )

    def answer_knowledge(
        self,
        *,
        prompt: RenderedPrompt,
        question: str,
        citations: list[KnowledgeCitationContext],
    ) -> GroundedAnswerDecision:
        del question, citations
        return self._invoke(
            role="knowledge_grounding",
            prompt=prompt,
            adapter=self._answer,
            schema_name="knowledge_grounded_answer",
        )

    def rewrite_knowledge_query(self, *, prompt: RenderedPrompt) -> tuple[QueryRewriteDecision, dict | None]:
        decision = self._invoke(
            role="query_rewrite",
            prompt=prompt,
            adapter=TypeAdapter(QueryRewriteDecision),
            schema_name="knowledge_query_rewrite",
        )
        return decision, self.last_trace


class GatewayQueryRewriter:
    """真实知识检索使用模型改写；所有实体仍来自已验证 Metadata Scope。"""

    def __init__(self, adapter: GatewayKnowledgeAdapter, prompt_registry: PromptRegistry | None = None) -> None:
        self.adapter = adapter
        self.prompt_registry = prompt_registry or PromptRegistry()

    def rewrite(self, request: OnlineRetrievalRequest) -> dict:
        prompt = self.prompt_registry.render_query_rewrite(
            question=request.query,
            entities={
                "product": request.product,
                "channel": request.channel,
                "person_roles": request.person_roles,
                **request.metadata_filters,
            },
        )
        try:
            decision, trace = self.adapter.rewrite_knowledge_query(prompt=prompt)
        except KnowledgeModelRouteError as exc:
            # Query Rewrite 是召回增强而不是安全边界。模型网络抖动时保留原始
            # Query 继续真实 Metadata Filter + Hybrid Retrieval，不能让可选增强
            # 拖垮整条知识链路，也不能改用脚本化答案。
            return {
                "query": request.query,
                "strategy": "MODEL_REWRITE_UNAVAILABLE_ORIGINAL_QUERY",
                "model_trace": exc.trace,
                "degraded": True,
                "failure_code": "QUERY_REWRITE_MODEL_UNAVAILABLE",
            }
        return {
            "query": decision.rewritten_query,
            "strategy": "MODEL_CONSTRAINED_REWRITE",
            "model_trace": trace,
            "degraded": False,
        }
