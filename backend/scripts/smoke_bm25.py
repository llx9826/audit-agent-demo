"""中文 BM25 真实索引冒烟：校验稀疏通道有命中且未命中无伪 rank。"""
from __future__ import annotations

from datetime import date
import json

from app.bootstrap.settings import settings_from_env
from app.rag.requirements.runtime import get_requirement_retriever


def main() -> int:
    settings_from_env(profile="real")
    trace = get_requirement_retriever().trace(
        product="住房公积金个人住房贷款",
        channel="PUBLIC",
        case_date=date(2026, 8, 17),
        person_roles=["APPLICANT"],
        query="北京 婚姻电子证照 免交纸质件",
        top_k=5,
        metadata_filters={"region": "北京", "domain": ["婚姻与家庭关系"]},
    )
    bm25_hits = sorted(
        [item for item in trace["candidates"] if item["eligible"] and item["bm25_rank"] is not None],
        key=lambda item: item["bm25_rank"],
    )
    false_ranks = [
        item["requirement_id"] for item in trace["candidates"]
        if item["bm25_score"] == 0 and item["bm25_rank"] is not None
    ]
    summary = {
        "backend": trace["retrieval"]["channel_backend"],
        "bm25_hit_count": trace["retrieval"]["bm25_hit_count"],
        "false_zero_score_ranks": false_ranks,
        "top_bm25": [{
            "requirement_id": item["requirement_id"],
            "score": item["bm25_score"],
            "rank": item["bm25_rank"],
        } for item in bm25_hits[:5]],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if bm25_hits and not false_ranks else 1


if __name__ == "__main__":
    raise SystemExit(main())
