from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from ..adapters import LexicalCrossEncoderReranker, LocalDenseBM25Channels, RetrievalScope
from .models import AtomicRequirementRecord


class HybridRequirementRetriever:
    """Requirement retrieval with explicit applicability before RRF/rerank."""

    def __init__(
        self,
        requirements: Sequence[AtomicRequirementRecord],
        *,
        channel_retriever: Any | None = None,
        reranker: Any | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.requirements = list(requirements)
        self.channel_retriever = channel_retriever or LocalDenseBM25Channels()
        self.reranker = reranker or LexicalCrossEncoderReranker()
        self.rrf_k = rrf_k

    def trace(
        self,
        *,
        product: str,
        channel: str,
        case_date: date,
        person_roles: list[str],
        query: str,
        top_k: int = 20,
        required_requirement_ids: list[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        rewritten_query: str | None = None,
        rewrite_strategy: str = "DETERMINISTIC_SCOPE_ENRICHMENT",
        rewrite_trace: dict[str, Any] | None = None,
        stage_callback: Any | None = None,
    ) -> dict[str, Any]:
        def emit(stage: str, payload: dict[str, Any]) -> None:
            if stage_callback is not None:
                stage_callback(stage, payload)

        rewritten = rewritten_query or " ".join([product, channel, *person_roles, query])
        allowed_ids = set(required_requirement_ids or [])
        scope_filters = metadata_filters or {}
        eligibility: dict[str, list[str]] = {}
        eligible_records: list[AtomicRequirementRecord] = []
        role_set = set(person_roles)
        for requirement in self.requirements:
            reasons: list[str] = []
            if requirement.product != product:
                reasons.append("PRODUCT_MISMATCH")
            if requirement.channel not in {channel, "ALL"}:
                reasons.append("CHANNEL_MISMATCH")
            if requirement.person_role not in role_set:
                reasons.append("ROLE_MISMATCH")
            if requirement.status != "ACTIVE":
                reasons.append("VERSION_INACTIVE")
            if requirement.effective_from > case_date:
                reasons.append("NOT_YET_EFFECTIVE")
            if requirement.effective_to and requirement.effective_to < case_date:
                reasons.append("EXPIRED")
            if allowed_ids and requirement.requirement_id not in allowed_ids:
                reasons.append("PROBLEM_SCOPE_MISMATCH")
            for key, expected in scope_filters.items():
                actual = requirement.metadata.get(key)
                expected_values = set(expected) if isinstance(expected, (list, tuple, set, frozenset)) else {expected}
                if actual != "ALL" and actual not in expected_values:
                    reasons.append(f"{key.upper()}_MISMATCH")
            eligibility[requirement.requirement_id] = reasons
            if not reasons:
                eligible_records.append(requirement)

        # Applicability and issue scope are resolved before expensive retrieval.
        retrieval_scope = RetrievalScope(
            allowed_document_ids=tuple(item.requirement_id for item in eligible_records),
            metadata={
                "product": product,
                "channel": channel,
                "person_roles": tuple(person_roles),
                "case_date": case_date.isoformat(),
                **scope_filters,
            },
        )
        emit("METADATA_FILTER", {
            "eligible_count": len(eligible_records),
            "filters": dict(retrieval_scope.metadata),
        })
        signals = self.channel_retriever.score(
            rewritten,
            eligible_records,
            metadata_filter=retrieval_scope,
        )
        emit("DENSE_BM25_RETRIEVAL", {
            "eligible_count": len(eligible_records),
            "dense_hit_count": sum(1 for signal in signals.values() if signal.dense_rank is not None),
            "bm25_hit_count": sum(1 for signal in signals.values() if signal.bm25_rank is not None),
            "backend": self.channel_retriever.backend_name,
        })
        candidates: list[dict[str, Any]] = []
        for requirement in self.requirements:
            signal = signals.get(requirement.requirement_id)
            reasons = eligibility[requirement.requirement_id]
            candidates.append({
                "requirement_id": requirement.requirement_id,
                "title": requirement.title,
                "person_role": requirement.person_role,
                "material_type": requirement.material_type,
                "checklist_version": requirement.checklist_version,
                "source_document": requirement.source_document,
                "source_section": requirement.source_section,
                "atomic_requirement": requirement.atomic_requirement,
                "effective_from": requirement.effective_from.isoformat(),
                "dense_score": round(signal.dense_score, 6) if signal else 0.0,
                "dense_rank": signal.dense_rank if signal else None,
                "bm25_score": round(signal.bm25_score, 6) if signal else 0.0,
                "bm25_rank": signal.bm25_rank if signal else None,
                "eligible": not reasons,
                "filter_reasons": reasons,
                "filter_reason": reasons[0] if reasons else None,
                "evidence_id": requirement.evidence_id,
                "child_chunk_id": requirement.child_chunk_id,
                "metadata": dict(requirement.metadata),
            })

        eligible = [candidate for candidate in candidates if candidate["eligible"]]
        # 使用底层通道的真实命中 rank；未命中通道对 RRF 贡献为 0。
        retrieved = [
            candidate for candidate in eligible
            if candidate["dense_rank"] is not None or candidate["bm25_rank"] is not None
        ]
        for candidate in retrieved:
            dense_component = (
                1 / (self.rrf_k + int(candidate["dense_rank"]))
                if candidate["dense_rank"] is not None else 0.0
            )
            bm25_component = (
                1 / (self.rrf_k + int(candidate["bm25_rank"]))
                if candidate["bm25_rank"] is not None else 0.0
            )
            candidate["rrf_score"] = round(dense_component + bm25_component, 9)
        retrieved.sort(key=lambda item: (-item["rrf_score"], item["requirement_id"]))
        for index, candidate in enumerate(retrieved, start=1):
            candidate["rrf_rank"] = index
        emit("RRF", {"fused_hit_count": len(retrieved), "rrf_k": self.rrf_k})

        by_id = {record.requirement_id: record for record in self.requirements}
        pool = retrieved[: max(top_k, 20)]
        scores = self.reranker.score(
            rewritten,
            [by_id[item["requirement_id"]].retrieval_text for item in pool],
        ) if pool else []
        for candidate, score in zip(pool, scores, strict=True):
            candidate["rerank_score"] = round(float(score), 6)
        pool.sort(key=lambda item: (-item["rerank_score"], -item["rrf_score"], item["requirement_id"]))
        for index, candidate in enumerate(pool, start=1):
            candidate["rerank_rank"] = index
            candidate["selected"] = index <= top_k
        emit("CROSS_ENCODER_RERANK", {
            "candidate_count": len(pool),
            "selected_count": min(top_k, len(pool)),
            "model": self.reranker.model_name,
        })

        selected = pool[:top_k]
        emit("REQUIREMENT_GROUNDING", {
            "requirement_ids": [item["requirement_id"] for item in selected],
        })
        for candidate in candidates:
            candidate.setdefault("rrf_score", 0.0)
            candidate.setdefault("rrf_rank", None)
            candidate.setdefault("rerank_score", None)
            candidate.setdefault("rerank_rank", None)
            candidate.setdefault("selected", False)
        return {
            "original_query": query,
            "rewritten_query": rewritten,
            "retrieval": {
                "strategy": "METADATA_FILTER_DENSE_BM25_RRF_CROSS_ENCODER",
                "channel_backend": self.channel_retriever.backend_name,
                "reranker": self.reranker.model_name,
                "candidate_count": len(candidates),
                "eligible_count": len(eligible),
                "dense_hit_count": sum(1 for item in eligible if item["dense_rank"] is not None),
                "bm25_hit_count": sum(1 for item in eligible if item["bm25_rank"] is not None),
                "fused_hit_count": len(retrieved),
            },
            "pipeline": [
                {"stage": "QUERY_REWRITE", "output": rewritten, "strategy": rewrite_strategy, "model_trace": rewrite_trace},
                {
                    "stage": "METADATA_FILTER",
                    "eligible_count": len(eligible),
                    "filters": {
                        "product": product,
                        "channel": channel,
                        "person_roles": person_roles,
                        "case_date": case_date.isoformat(),
                        "requirement_ids": required_requirement_ids or [],
                        **scope_filters,
                    },
                },
                {
                    "stage": "DENSE_BM25_RETRIEVAL",
                    "candidate_count": len(eligible),
                    "dense_hit_count": sum(1 for item in eligible if item["dense_rank"] is not None),
                    "bm25_hit_count": sum(1 for item in eligible if item["bm25_rank"] is not None),
                },
                {"stage": "RRF", "candidate_count": len(retrieved)},
                {"stage": "CROSS_ENCODER_RERANK", "candidate_count": len(pool)},
                {"stage": "REQUIREMENT_GROUNDING", "requirement_ids": [item["requirement_id"] for item in selected]},
            ],
            "candidates": candidates,
            "selected": selected,
            "final_requirements": [item["requirement_id"] for item in selected],
        }
