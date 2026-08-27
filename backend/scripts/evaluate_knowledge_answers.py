"""真实 Knowledge RAG 答案评测：意图、拒答、引用与 LLM Faithfulness Judge。"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.bootstrap.container import ApplicationContainer
from app.bootstrap.settings import settings_from_env
from app.providers.contracts import Message
from evaluate_requirement_rag import isolated_milvus_uri


GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "knowledge_answer_golden.jsonl"
REPORT = Path(__file__).resolve().parents[2] / ".data" / "eval-reports" / "knowledge_answers_live.json"


class FaithfulnessDecision(BaseModel):
    """Judge 只判断可引用支持性，不评价文风。"""

    model_config = ConfigDict(extra="forbid")
    faithful: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=300)


def _judge(container: ApplicationContainer, result: dict) -> FaithfulnessDecision:
    if container.model_gateway is None:
        raise ValueError("faithfulness eval requires configured judge model")
    citations = [{
        "requirement_id": item["requirement_id"],
        "child_chunk_id": item["child_chunk_id"],
        "atomic_requirement": item["atomic_requirement"],
        "source_document": item["source_document"],
    } for item in result["citations"]]
    judged = container.model_gateway.structured_sync(
        role="judge",
        schema=TypeAdapter(FaithfulnessDecision),
        schema_name="knowledge_faithfulness",
        messages=[
            Message(
                role="system",
                content=(
                    "你是材料知识库 Faithfulness Judge。逐项检查答案中的材料要求结论是否由 citations 明确支持；"
                    "不得使用外部常识。先在 rationale 中简述核验，再输出 faithful。格式修饰不算事实错误。"
                ),
            ),
            Message(role="user", content=json.dumps({
                "question": result["question"],
                "answer": result["answer"],
                "citations": citations,
            }, ensure_ascii=False, sort_keys=True)),
        ],
    )
    return judged.value


def main() -> int:
    settings_from_env(profile="real")
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows: list[dict] = []
    with isolated_milvus_uri():
        container = ApplicationContainer.build(profile="real")
        try:
            for case in cases:
                result = container.knowledge_service.query(case["question"])
                actual_status = result["status"]
                expected_status = case["expected_status"]
                status_ok = actual_status == expected_status
                actual_requirements = {item["requirement_id"] for item in result.get("citations", [])}
                required = set(case.get("required_requirement_ids", []))
                citations_ok = required.issubset(actual_requirements)
                inline_ok = all(
                    f"[{item['child_chunk_id']}]" in result.get("answer", "")
                    for item in result.get("citations", [])
                )
                faithful = True
                judge_rationale = "NOT_APPLICABLE"
                if actual_status == "ANSWERED":
                    judged = _judge(container, result)
                    faithful = judged.faithful
                    judge_rationale = judged.rationale
                passed = status_ok and citations_ok and inline_ok and faithful
                rows.append({
                    "case_id": case["id"],
                    "failure_mode": case["failure_mode"],
                    "expected_status": expected_status,
                    "actual_status": actual_status,
                    "status_ok": status_ok,
                    "citation_requirement_ids_ok": citations_ok,
                    "inline_citations_ok": inline_ok,
                    "faithful": faithful,
                    "judge_rationale": judge_rationale,
                    "passed": passed,
                })
        finally:
            container.close()
    metrics = {
        "case_count": len(rows),
        "pass_rate": sum(row["passed"] for row in rows) / len(rows),
        "refusal_accuracy": sum(
            row["status_ok"] for row in rows if row["expected_status"] != "ANSWERED"
        ) / max(1, sum(row["expected_status"] != "ANSWERED" for row in rows)),
        "citation_accuracy": sum(row["citation_requirement_ids_ok"] for row in rows) / len(rows),
        "faithfulness": sum(row["faithful"] for row in rows) / len(rows),
    }
    report = {"passed": all(row["passed"] for row in rows), "metrics": metrics, "cases": rows}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
