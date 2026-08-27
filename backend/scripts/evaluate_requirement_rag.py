"""在隔离 Milvus 副本上运行 Retrieval Golden Set 与成对回归门禁。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterator

from app.bootstrap.settings import settings_from_env
from app.evaluation import aggregate_case_metrics, paired_bootstrap_gate
from app.rag.evaluation import RetrievalEvalCase, evaluate_retriever_cases
from app.rag.requirements.runtime import get_requirement_retriever, reset_requirement_retriever_cache


EVAL_ROOT = Path(__file__).resolve().parents[1] / "evals"
GOLDEN = EVAL_ROOT / "requirement_retrieval.jsonl"
BASELINE = EVAL_ROOT / "requirement_retrieval_baseline.json"
REPORT = Path(__file__).resolve().parents[2] / ".data" / "eval-reports" / "requirement_rag.json"
METRICS = ("hit_rate@5", "recall@5", "mrr", "ndcg@5")
QUALITY_FLOOR = {"hit_rate@5": .95, "recall@5": .95, "mrr": .60, "ndcg@5": .75}


def load_cases(path: Path = GOLDEN) -> list[RetrievalEvalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        cases.append(RetrievalEvalCase(
            case_id=payload["id"],
            query=payload["query"],
            case_date=date.fromisoformat(payload["case_date"]),
            product=payload["product"],
            channel=payload["channel"],
            person_roles=payload["person_roles"],
            relevant_requirement_ids=payload["relevant_requirement_ids"],
            metadata_filters=payload.get("metadata_filters", {}),
            failure_mode=payload.get("failure_mode", "retrieval_relevance"),
            source=payload.get("source", "hand_labeled_project_case"),
        ))
    if not 30 <= len(cases) <= 200:
        raise ValueError("retrieval Golden Set must contain 30-200 cases")
    return cases


@contextmanager
def isolated_milvus_uri() -> Iterator[str]:
    """Milvus Lite 是单写进程存储；评测使用快照副本避免与 Demo 抢锁。"""

    source = os.getenv("REQUIREMENT_RAG_MILVUS_URI", "").strip()
    if not source:
        raise ValueError("REQUIREMENT_RAG_MILVUS_URI is required")
    if source.startswith(("http://", "https://")):
        yield source
        return
    source_path = Path(source).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Milvus Lite index not found: {source_path}")
    original = source
    with tempfile.TemporaryDirectory(prefix="requirement-rag-eval-") as directory:
        target = Path(directory) / "milvus-snapshot.db"
        shutil.copytree(source_path, target, ignore=shutil.ignore_patterns("LOCK", "*.lock"))
        os.environ["REQUIREMENT_RAG_MILVUS_URI"] = str(target)
        reset_requirement_retriever_cache()
        try:
            yield str(target)
        finally:
            retriever = get_requirement_retriever()
            client = getattr(retriever.channel_retriever, "_client_instance", None)
            if client is not None:
                client.close()
            reset_requirement_retriever_cache()
            os.environ["REQUIREMENT_RAG_MILVUS_URI"] = original


def _case_map(rows: list[dict]) -> dict[str, dict[str, float]]:
    return {
        str(row["case_id"]): {name: float(row[name]) for name in METRICS}
        for row in rows
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()
    settings_from_env(profile="real")
    with isolated_milvus_uri() as eval_uri:
        rows = evaluate_retriever_cases(get_requirement_retriever(), load_cases(), k=5)
        current_cases = _case_map(rows)
        aggregate = aggregate_case_metrics(list(current_cases.values()), METRICS)
        if args.update_baseline:
            BASELINE.write_text(
                json.dumps({
                    "dataset": GOLDEN.name,
                    "case_count": len(current_cases),
                    "metrics": aggregate,
                    "cases": current_cases,
                }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if not BASELINE.exists():
            raise FileNotFoundError(
                f"committed baseline missing: run {Path(__file__).name} --update-baseline"
            )
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        regression = paired_bootstrap_gate(
            current=current_cases,
            baseline=baseline["cases"],
            metric_names=METRICS,
        )
        floor_failures = [
            name for name, threshold in QUALITY_FLOOR.items()
            if aggregate[name] < threshold
        ]
        report = {
            "passed": not floor_failures and regression["passed"],
            "dataset": GOLDEN.name,
            "milvus_eval_mode": "ISOLATED_SNAPSHOT" if not eval_uri.startswith("http") else "REMOTE_COLLECTION",
            "metrics": aggregate,
            "quality_floor": QUALITY_FLOOR,
            "floor_failures": floor_failures,
            "regression": regression,
            "cases": rows,
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
