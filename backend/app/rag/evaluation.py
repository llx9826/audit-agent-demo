from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import log2
from typing import Mapping, Sequence

from .requirements.hybrid import HybridRequirementRetriever


Relevance = Sequence[str] | Mapping[str, float]


def _relevance_map(relevant: Relevance) -> dict[str, float]:
    if isinstance(relevant, Mapping):
        return {str(requirement_id): float(score) for requirement_id, score in relevant.items()}
    return {str(requirement_id): 1.0 for requirement_id in relevant}


def hit_rate_at_k(ranked_requirement_ids: Sequence[str], relevant: Relevance, k: int) -> float:
    relevant_ids = set(_relevance_map(relevant))
    return float(any(requirement_id in relevant_ids for requirement_id in ranked_requirement_ids[:k]))


def recall_at_k(ranked_requirement_ids: Sequence[str], relevant: Relevance, k: int) -> float:
    relevant_ids = set(_relevance_map(relevant))
    if not relevant_ids:
        return 0.0
    return len(relevant_ids.intersection(ranked_requirement_ids[:k])) / len(relevant_ids)


def reciprocal_rank(ranked_requirement_ids: Sequence[str], relevant: Relevance) -> float:
    relevant_ids = set(_relevance_map(relevant))
    for rank, requirement_id in enumerate(ranked_requirement_ids, start=1):
        if requirement_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_requirement_ids: Sequence[str], relevant: Relevance, k: int) -> float:
    relevance = _relevance_map(relevant)
    gains = [relevance.get(requirement_id, 0.0) for requirement_id in ranked_requirement_ids[:k]]
    dcg = sum((2**gain - 1) / log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2**gain - 1) / log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    query: str
    case_date: date
    product: str
    channel: str
    person_roles: list[str]
    relevant_requirement_ids: Relevance
    metadata_filters: dict[str, str] = field(default_factory=dict)
    case_id: str = ""
    failure_mode: str = "retrieval_relevance"
    source: str = "hand_labeled_project_case"


def evaluate_retriever_cases(
    retriever: HybridRequirementRetriever,
    cases: Sequence[RetrievalEvalCase],
    *,
    k: int = 5,
) -> list[dict[str, float | str]]:
    """保留逐 Case 指标，供 Failure Mode 切片和成对 Bootstrap 使用。"""

    rows: list[dict[str, float | str]] = []
    for index, case in enumerate(cases, start=1):
        trace = retriever.trace(
            query=case.query,
            case_date=case.case_date,
            product=case.product,
            channel=case.channel,
            person_roles=case.person_roles,
            metadata_filters=case.metadata_filters,
            top_k=max(k, len(retriever.requirements)),
        )
        ranked = [str(item["requirement_id"]) for item in trace["selected"]]
        rows.append({
            "case_id": case.case_id or f"CASE-{index:03d}",
            "failure_mode": case.failure_mode,
            f"hit_rate@{k}": hit_rate_at_k(ranked, case.relevant_requirement_ids, k),
            f"recall@{k}": recall_at_k(ranked, case.relevant_requirement_ids, k),
            "mrr": reciprocal_rank(ranked, case.relevant_requirement_ids),
            f"ndcg@{k}": ndcg_at_k(ranked, case.relevant_requirement_ids, k),
        })
    return rows


def evaluate_retriever(
    retriever: HybridRequirementRetriever,
    cases: Sequence[RetrievalEvalCase],
    *,
    k: int = 5,
) -> dict[str, float | int]:
    if not cases:
        return {"case_count": 0, f"hit_rate@{k}": 0.0, f"recall@{k}": 0.0, "mrr": 0.0, f"ndcg@{k}": 0.0}
    rows = evaluate_retriever_cases(retriever, cases, k=k)
    count = len(rows)
    return {
        "case_count": count,
        f"hit_rate@{k}": sum(float(row[f"hit_rate@{k}"]) for row in rows) / count,
        f"recall@{k}": sum(float(row[f"recall@{k}"]) for row in rows) / count,
        "mrr": sum(float(row["mrr"]) for row in rows) / count,
        f"ndcg@{k}": sum(float(row[f"ndcg@{k}"]) for row in rows) / count,
    }
