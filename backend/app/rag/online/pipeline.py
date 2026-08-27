"""Explicit real-time boundary around the already-indexed requirement corpus.

No crawling, parsing, chunking or embedding-document writes are allowed here.
Those operations belong to ``app.rag.offline`` and deployment-time index jobs.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..requirements.hybrid import HybridRequirementRetriever
from ..requirements.runtime import get_requirement_retriever


class OnlineRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str
    channel: str
    case_date: date
    person_roles: list[str] = Field(min_length=1)
    query: str = Field(min_length=2)
    top_k: int = Field(default=6, ge=1, le=20)
    required_requirement_ids: list[str] | None = None
    # 地区/分行通常是单值；领域由受控 domain_family 展开为多个兼容索引标签。
    metadata_filters: dict[str, str | list[str]] = Field(default_factory=dict)


class OnlineRequirementRag:
    """Query-time-only adapter: filter → dense/BM25 → RRF → rerank."""

    def __init__(self, retriever: HybridRequirementRetriever | None = None, *, query_rewriter: "QueryRewriter | None" = None) -> None:
        self.retriever = retriever or get_requirement_retriever()
        if query_rewriter is None:
            raise ValueError(
                "OnlineRequirementRag requires an explicit query strategy: "
                "LLM rewrite for knowledge Q&A or confirmed-workflow pass-through"
            )
        self.query_rewriter = query_rewriter

    def retrieve(
        self,
        request: OnlineRetrievalRequest,
        *,
        stage_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        rewrite = self.query_rewriter.rewrite(request)
        if stage_callback is not None:
            stage_callback("QUERY_REWRITE", {
                "strategy": rewrite["strategy"],
                "rewritten_query": rewrite["query"],
                "model_trace": rewrite.get("model_trace"),
                "degraded": bool(rewrite.get("degraded", False)),
                "failure_code": rewrite.get("failure_code"),
            })
        return self.retriever.trace(
            **request.model_dump(),
            rewritten_query=rewrite["query"],
            rewrite_strategy=rewrite["strategy"],
            rewrite_trace=rewrite.get("model_trace"),
            stage_callback=stage_callback,
        )


class QueryRewriter(Protocol):
    def rewrite(self, request: OnlineRetrievalRequest) -> dict[str, Any]: ...


class ConfirmedWorkflowQuery:
    """已由 Workflow 确认任务意图的查询边界。

    这不是模型基线：缺件任务已由规则引擎给出 requirement_id，
    再做意图识别或 HyDE 会引入新的不确定性。知识库自然语言入口
    必须使用 GatewayQueryRewriter。
    """

    def rewrite(self, request: OnlineRetrievalRequest) -> dict[str, Any]:
        return {
            "query": request.query,
            "strategy": "CONFIRMED_WORKFLOW_QUERY",
        }
