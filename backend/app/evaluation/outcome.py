"""从持久化 Case 投影读取 Outcome；不相信 Agent 的完成文案。"""
from __future__ import annotations

from typing import Any

from ..persistence.repository import InMemoryCaseRepository


def project_final_db_outcome(
    repository: InMemoryCaseRepository,
    case_id: str,
) -> dict[str, Any]:
    """返回用于评分的最小业务 Outcome 和提交证据。"""

    state = repository.get(case_id)
    events = repository.event_dicts(case_id)
    validation = next(
        (
            event for event in reversed(events)
            if event["event_type"] in {
                "COMPLETENESS_VALIDATED", "COMPLETENESS_VALIDATION_FAILED",
            }
        ),
        None,
    )
    terminal = next(
        (
            event for event in reversed(events)
            if event["event_type"] in {"RUN_COMPLETED", "RUN_PAUSED", "RUN_FAILED"}
        ),
        None,
    )
    validation_observation = (validation or {}).get("payload", {}).get("observation", {})
    return {
        "case_id": state.case_id,
        "status": state.status,
        "completeness_status": state.completeness_status,
        "case_version": state.case_version,
        "plan_version": state.plan_version,
        "task_statuses": {
            task.task_id: task.status
            for task in sorted(state.audit_plan, key=lambda item: item.task_id)
        },
        "terminal_event": (terminal or {}).get("event_type"),
        "terminal_seq": (terminal or {}).get("seq"),
        "validation_result": validation_observation.get("result"),
        "credit_decision": validation_observation.get("credit_decision"),
        "human_task_count": len(state.human_tasks),
        "supplement_count": len(state.supplement_requests),
    }


def score_material_outcome(outcome: dict[str, Any]) -> dict[str, float]:
    """确定性 Outcome grader；全部从 DB 投影而非生成文本读取。"""

    task_statuses = outcome.get("task_statuses", {})
    completed = bool(task_statuses) and all(status == "MATCHED" for status in task_statuses.values())
    scores = {
        "db_outcome_correct": float(
            outcome.get("status") == "COMPLETED"
            and outcome.get("completeness_status") == "COMPLETE"
            and outcome.get("terminal_event") == "RUN_COMPLETED"
            and outcome.get("validation_result") == "MATERIALS_COMPLETE"
        ),
        "task_completion": float(completed),
        "credit_scope_safe": float(outcome.get("credit_decision") == "OUT_OF_SCOPE"),
    }
    scores["passed"] = float(all(value == 1.0 for value in scores.values()))
    return scores

