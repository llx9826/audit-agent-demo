"""Problem-triggered RAG that grounds supplement and HITL explanations."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..online import ConfirmedWorkflowQuery, OnlineRequirementRag, OnlineRetrievalRequest
from .hybrid import HybridRequirementRetriever
from .runtime import get_requirement_retriever


class RequirementEvidenceRAG:
    def __init__(self, retriever: HybridRequirementRetriever | None = None) -> None:
        self.retriever = retriever or get_requirement_retriever()
        # 规则引擎已经给出 requirement_id，这个入口不再做 LLM 意图猜测。
        self.online_rag = OnlineRequirementRag(
            self.retriever,
            query_rewriter=ConfirmedWorkflowQuery(),
        )

    def ground(
        self,
        *,
        product: str,
        channel: str,
        case_date: date,
        person_roles: list[str],
        problem_tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        requirement_ids = list(dict.fromkeys(
            str(task["requirement_id"]) for task in problem_tasks if task.get("requirement_id")
        ))
        issue_terms = " ".join(
            f"{task.get('person_role', '')} {task.get('material_type', '')} {task.get('status', '')}"
            for task in problem_tasks
        )
        query = f"当前材料齐套校验发现 {issue_terms}，请召回补件或人工处理所需的原子要求依据。"
        trace = self.online_rag.retrieve(OnlineRetrievalRequest(
            product=product,
            channel=channel,
            case_date=case_date,
            person_roles=person_roles,
            query=query,
            top_k=max(1, len(requirement_ids)),
            required_requirement_ids=requirement_ids,
        ))
        selected = {item["requirement_id"]: item for item in trace["selected"]}
        groundings: list[dict[str, Any]] = []
        for task in problem_tasks:
            item = selected.get(str(task.get("requirement_id")))
            if item is None:
                continue
            groundings.append({
                "task_id": task["task_id"],
                "requirement_id": item["requirement_id"],
                "issue_status": task.get("status"),
                "evidence_id": item["evidence_id"],
                "child_chunk_id": item["child_chunk_id"],
                "parent_chunk_id": item.get("metadata", {}).get("parent_chunk_id"),
                "source_document": item["source_document"],
                "source_section": item["source_section"],
                "source_url": item.get("metadata", {}).get("source_url"),
                "atomic_requirement": item["atomic_requirement"],
                "retrieval_scores": {
                    "dense": item["dense_score"],
                    "bm25": item["bm25_score"],
                    "rrf": item["rrf_score"],
                    "rerank": item["rerank_score"],
                },
            })
        trace.update({
            "trace_type": "REQUIREMENT_EVIDENCE_RAG",
            "trigger": "COMPLETENESS_PROBLEM",
            "problem_task_ids": [task["task_id"] for task in problem_tasks],
            "groundings": groundings,
        })
        return trace
