from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from hashlib import blake2b
from math import log, sqrt
import re
from typing import Any


DOMAIN_TERMS = (
    "个人经营贷款", "综合融资成本", "年化综合融资成本", "客户确认", "贷款用途",
    "第一还款来源", "受托支付", "抵押物", "抵押登记", "生效日期", "配偶",
    "经营真实性", "费用构成", "签订合同前", "宅抵贷",
)


@dataclass(frozen=True, slots=True)
class PolicyRule:
    rule_id: str
    title: str
    version: int
    effective_date: date
    product: str
    text: str
    dense_score: float
    bm25_score: float
    status: str = "ACTIVE"
    valid_until: date | None = None
    issuer: str = ""
    article: str = ""
    source_url: str = ""
    source_type: str = "OFFICIAL"


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    result = re.findall(r"[a-z0-9_-]+", normalized)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    for run in chinese_runs:
        result.extend(run[index:index + 2] for index in range(max(1, len(run) - 1)))
    result.extend(term for term in DOMAIN_TERMS if term in normalized)
    return result


def _hashed_vector(tokens: list[str], dimensions: int = 128) -> list[float]:
    vector = [0.0] * dimensions
    for token, count in Counter(tokens).items():
        digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + log(count))
    norm = sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return max(0.0, sum(a * b for a, b in zip(left, right, strict=True)))


def _bm25(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    if not documents:
        return []
    document_count = len(documents)
    average_length = sum(len(document) for document in documents) / document_count or 1.0
    document_frequency = Counter(token for token in set(query_tokens) for document in documents if token in document)
    scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        score = 0.0
        for token in set(query_tokens):
            frequency = frequencies[token]
            if not frequency:
                continue
            idf = log(1 + (document_count - document_frequency[token] + .5) / (document_frequency[token] + .5))
            denominator = frequency + 1.5 * (1 - .75 + .75 * len(document) / average_length)
            score += idf * frequency * 2.5 / denominator
        scores.append(score)
    maximum = max(scores) or 1.0
    return [score / maximum for score in scores]


class HybridPolicyRetriever:
    """Local hybrid retriever with runtime scoring, RRF and metadata gates.

    The dense channel uses a deterministic feature-hashing vector over the local
    corpus. It is intentionally small and offline, but unlike a fixture it
    computes both channels from the actual query and policy text at runtime.
    """

    def __init__(self, rules: list[PolicyRule]) -> None:
        self.rules = rules

    def _runtime_scores(self, query: str) -> dict[str, tuple[float, float]]:
        query_tokens = _tokens(query)
        document_tokens = [_tokens(f"{rule.title}{rule.article}{rule.text}") for rule in self.rules]
        query_vector = _hashed_vector(query_tokens)
        dense = [_cosine(query_vector, _hashed_vector(tokens)) for tokens in document_tokens]
        sparse = _bm25(query_tokens, document_tokens)
        return {
            rule.rule_id: (round(dense[index], 6), round(sparse[index], 6))
            for index, rule in enumerate(self.rules)
        }

    def search(
        self, *, case_date: date, product: str, top_k: int = 5, query: str | None = None,
    ) -> list[dict[str, object]]:
        trace = self.trace(case_date=case_date, product=product, top_k=top_k, query=query)
        selected_ids = [item["rule_id"] for item in trace["selected"]]
        by_id = {rule.rule_id: rule for rule in self.rules}
        return [
            {"rule": by_id[rule_id], "rrf_score": item["rrf_score"]}
            for rule_id, item in zip(selected_ids, trace["selected"], strict=True)
        ]

    def trace(
        self, *, case_date: date, product: str, top_k: int = 5, query: str | None = None,
    ) -> dict[str, Any]:
        scores = self._runtime_scores(query) if query else {
            rule.rule_id: (rule.dense_score, rule.bm25_score) for rule in self.rules
        }
        candidates: list[dict[str, Any]] = []
        applicable: list[PolicyRule] = []
        for rule in self.rules:
            reasons: list[str] = []
            if rule.product not in {product, "个人贷款"}:
                reasons.append("PRODUCT_MISMATCH")
            if rule.status != "ACTIVE":
                reasons.append("VERSION_INACTIVE")
            if rule.effective_date > case_date:
                reasons.append("NOT_YET_EFFECTIVE")
            if rule.valid_until and rule.valid_until < case_date:
                reasons.append("EXPIRED")
            if not reasons:
                applicable.append(rule)
            dense_score, bm25_score = scores[rule.rule_id]
            candidates.append({
                "rule_id": rule.rule_id,
                "title": rule.title,
                "version": rule.version,
                "status": rule.status,
                "effective_date": rule.effective_date.isoformat(),
                "valid_until": rule.valid_until.isoformat() if rule.valid_until else None,
                "product": rule.product,
                "issuer": rule.issuer,
                "article": rule.article,
                "source_url": rule.source_url,
                "source_type": rule.source_type,
                "dense_score": dense_score,
                "bm25_score": bm25_score,
                "score": dense_score,
                "eligible": not reasons,
                "valid": not reasons,
                "filter_reason": reasons[0] if reasons else None,
                "reason": "；".join(reasons) if reasons else "产品、状态与生效日期均匹配",
                "filter_reasons": reasons,
            })

        latest_by_title: dict[str, PolicyRule] = {}
        for rule in applicable:
            current = latest_by_title.get(rule.title)
            if current is None or rule.version > current.version:
                latest_by_title[rule.title] = rule
        latest_ids = {rule.rule_id for rule in latest_by_title.values()}
        for candidate in candidates:
            if candidate["eligible"] and candidate["rule_id"] not in latest_ids:
                candidate["eligible"] = False
                candidate["valid"] = False
                candidate["filter_reason"] = "VERSION_SUPERSEDED"
                candidate["reason"] = "VERSION_SUPERSEDED"
                candidate["filter_reasons"] = ["VERSION_SUPERSEDED"]

        valid = list(latest_by_title.values())
        dense_rank = {
            rule.rule_id: index + 1
            for index, rule in enumerate(sorted(valid, key=lambda item: scores[item.rule_id][0], reverse=True))
        }
        sparse_rank = {
            rule.rule_id: index + 1
            for index, rule in enumerate(sorted(valid, key=lambda item: scores[item.rule_id][1], reverse=True))
        }
        ranked = sorted(
            ({
                "rule": rule,
                "rrf_score": 1 / (60 + dense_rank[rule.rule_id]) + 1 / (60 + sparse_rank[rule.rule_id]),
            } for rule in valid),
            key=lambda item: float(item["rrf_score"]),
            reverse=True,
        )[:top_k]
        selected = [{
            "rule_id": item["rule"].rule_id,
            "rrf_score": item["rrf_score"],
            "selection_reason": "PRODUCT_STATUS_EFFECTIVE_DATE_MATCH",
        } for item in ranked]
        selected_scores = {item["rule_id"]: item["rrf_score"] for item in selected}
        for candidate in candidates:
            candidate["rrf_score"] = selected_scores.get(candidate["rule_id"], 0.0)
        return {"candidates": candidates, "selected": selected}


def demo_policy_trace() -> dict[str, Any]:
    query = "CASE-ZD-042 2026-08-15 宅抵贷 签订合同前 综合融资成本 明示 费用构成 客户确认"
    rules = [
        PolicyRule(
            "DEMO-COST-2025", "综合融资成本明示策略（演示旧版）", 1,
            date(2025, 1, 1), "宅抵贷",
            query, .99, .99, status="RETIRED", valid_until=date(2026, 7, 31),
            issuer="演示策略", article="旧版演示策略", source_type="DEMO_POLICY",
        ),
        PolicyRule(
            "NFRA-2024-PERSONAL-LOAN", "个人贷款管理办法", 1,
            date(2024, 7, 1), "个人贷款",
            "贷款调查应包括借款人基本情况、收入、贷款用途、经营情况、第一还款来源，以及抵押物权属、价值和变现能力。",
            .0, .0, issuer="国家金融监督管理总局", article="贷款调查与风险评价",
            source_url="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1151064&itemId=861",
        ),
        PolicyRule(
            "NFRA-2026-COST-01", "个人贷款业务明示综合融资成本规定", 1,
            date(2026, 8, 1), "个人贷款",
            "金融机构应当在签订个人贷款合同前，向借款人明示年化综合融资成本、费用构成、收取主体，并由借款人确认。",
            .0, .0, issuer="国家金融监督管理总局", article="综合融资成本明示与确认",
            source_url="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1251479&itemId=928",
        ),
    ]
    retriever = HybridPolicyRetriever(rules)
    trace = retriever.trace(case_date=date(2026, 8, 15), product="宅抵贷", query=query)
    return {
        "original_query": "案例日期为 2026-08-15，签约前需要新增哪些费用明示与客户确认任务？",
        "rewritten_query": query,
        "retrieval": {
            "strategy": "HASHED_DENSE_BM25_RRF",
            "score_source": "LOCAL_CORPUS_RUNTIME",
            "top_k": 5,
        },
        **trace,
        "final_rule": "NFRA-2026-COST-01",
        "final_evidence_id": "E-RULE-COST-2026",
        "clause": "案例日期晚于 2026-08-01：签约前须明示综合融资成本及费用构成，并取得客户确认。",
        "grounding": {
            "evidence_id": "E-RULE-COST-2026",
            "rule_id": "NFRA-2026-COST-01",
            "version": 1,
            "effective_date": "2026-08-01",
            "product": "个人贷款",
            "issuer": "国家金融监督管理总局",
            "article": "综合融资成本明示与确认",
            "source_url": "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1251479&itemId=928",
            "clause": "签订个人贷款合同前，明示年化综合融资成本、费用构成与收取主体，并由借款人确认。",
        },
    }
