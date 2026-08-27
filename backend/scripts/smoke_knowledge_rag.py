"""真实在线 Knowledge RAG Smoke：模型意图/改写/回答 + Milvus 混合召回。"""
from __future__ import annotations

import argparse
import json

from app.bootstrap.container import ApplicationContainer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="南京公积金贷款，离婚需要什么婚姻证明？有依据吗？")
    args = parser.parse_args()
    container = ApplicationContainer.build(profile="real")
    try:
        result = container.knowledge_service.query(args.question)
        trace = result["trace"]
        summary = {
            "status": result["status"],
            "intent": result["intent"],
            "applied_filters": result["applied_filters"],
            "answer": result["answer"],
            "citation_ids": [item["child_chunk_id"] for item in result["citations"]],
            "retrieval": trace["retrieval"],
            "pipeline": [item["stage"] for item in trace["pipeline"]],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ANSWERED" else 1
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
