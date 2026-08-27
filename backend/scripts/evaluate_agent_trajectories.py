"""Agent Eval Harness：多 Trial、最终 DB Outcome、Shadow Replay 与 Bootstrap Gate。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.agents.contracts import MaterialAuditAssignment, MaterialCandidate, MaterialIssue
from app.agents.case_association import (
    AssociationCandidate,
    CaseAssociationAgent,
    CaseAssociationAssignment,
)
from app.agents.exception_recovery import ExceptionTask
from app.agents.exception_recovery.tool_policy import build_candidate_tools
from app.evaluation import (
    TrialArtifact,
    paired_bootstrap_gate,
    project_feedback,
    project_final_db_outcome,
    score_material_outcome,
    shadow_replay_report,
    summarize_trial_artifacts,
)
from app.persistence.repository import SQLiteCaseRepository
from app.runtime.checkpoint import sqlite_checkpointer
from app.service import AuditService
from demo.fixtures import create_demo_case
from demo.providers import build_demo_agents, build_demo_pipeline_dependencies


EVAL_ROOT = Path(__file__).resolve().parents[1] / "evals"
REPORT_ROOT = Path(__file__).resolve().parents[2] / ".data" / "eval-reports"


def _build_agents(mode: str):
    if mode == "deterministic":
        exception_agent, audit_agent = build_demo_agents()
        return exception_agent, audit_agent, CaseAssociationAgent()
    from app.agents.exception_recovery import ExceptionRecoveryAgent
    from app.agents.material_audit import MaterialAuditAgent
    from app.bootstrap.settings import settings_from_env
    from app.providers import gateway_from_settings
    from app.providers.decision_adapters import GatewayDecisionAdapter
    from demo.providers import build_demo_tool_registry

    settings = settings_from_env(profile="real")
    if settings.model is None:
        raise ValueError("live Agent eval requires configured model endpoints")
    adapter = GatewayDecisionAdapter(gateway_from_settings(settings.model))
    return (
        ExceptionRecoveryAgent(
            max_steps=4,
            registry=build_demo_tool_registry(),
            model_adapter=adapter,
        ),
        MaterialAuditAgent(model_adapter=adapter),
        CaseAssociationAgent(model_adapter=adapter),
    )


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (EVAL_ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _artifact(
    *,
    case_id: str,
    trial_index: int,
    variant: str,
    passed: bool,
    outcome: dict[str, Any],
    trajectory: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TrialArtifact:
    return TrialArtifact(
        case_id=case_id,
        trial_index=trial_index,
        variant=variant,
        final_db_outcome=outcome,
        trajectory=trajectory or [],
        scores={"passed": float(passed)},
        metadata=metadata or {},
    )


def _agent_trial(mode: str, trial_index: int) -> list[TrialArtifact]:
    """一次加载模型适配器，跑完全部结构化决策和轨迹 Case。"""

    variant = f"{mode}/current"
    exception_agent, audit_agent, association_agent = _build_agents(mode)
    artifacts: list[TrialArtifact] = []

    exception = exception_agent.resolve(
        ExceptionTask(
            exception_type="MATERIAL_IMAGE_LOW_CONFIDENCE",
            source_task_id="TASK-EVAL",
            problem="低置信度影像需要恢复 Observation",
            context_refs=["PAGE-EVAL"],
        ),
        vlm_value="P01",
        trusted_document_value="P01",
    )
    exception_trajectory = [
        {
            "step": index,
            "tool": action["tool"],
            "allowed": action["allowed"],
            "registered": action["registered"],
        }
        for index, action in enumerate(exception.actions, start=1)
    ]
    exception_checks = {
        "resolved": exception.status == "RESOLVED",
        "tool-sequence": [item["tool"] for item in exception.actions]
        == ["ocr_retry", "vlm_extract", "document_search"],
        "tools-gated": all(item["allowed"] and item["registered"] for item in exception.actions),
        "completion-condition": exception.stop_reason == "COMPLETION_CONDITION_MET",
    }
    for name, passed in exception_checks.items():
        artifacts.append(_artifact(
            case_id=f"smoke/exception-{name}",
            trial_index=trial_index,
            variant=variant,
            passed=passed,
            outcome={"status": exception.status, "stop_reason": exception.stop_reason},
            trajectory=exception_trajectory,
        ))

    audit = audit_agent.decide(MaterialAuditAssignment(
        assignment_id="ASSIGN-EVAL",
        case_id="CASE-EVAL",
        thread_id="THREAD-EVAL",
        case_version=1,
        plan_version=1,
        objective="在封闭候选集中消解所属人歧义",
        issue=MaterialIssue(
            task_id="TASK-EVAL",
            issue_type="OWNER_AMBIGUOUS",
            person_id="P02",
            material_type="marriage_certificate",
            candidate_page_ids=["PAGE-EVAL"],
            evidence_refs=["EV-EVAL"],
            confidence=.7,
        ),
        candidates=[
            MaterialCandidate(
                candidate_id=f"C-{person}",
                page_ids=["PAGE-EVAL"],
                proposed_person_id=person,
                proposed_material_type="marriage_certificate",
                proposed_requirement_id="REQ-EVAL",
                evidence_refs=["EV-EVAL"],
                workflow_score=.7,
            )
            for person in ("P01", "P02")
        ],
        allowed_actions=["APPLY_CANDIDATE", "REQUEST_HUMAN", "REQUEST_RECOVERY"],
    ))
    selected = getattr(audit.decision, "selected_candidate_id", None)
    audit_passed = bool(
        audit.decision.action in {"REQUEST_HUMAN", "REQUEST_RECOVERY"}
        and (selected is None or selected in {"C-P01", "C-P02"})
        and set(audit.decision.evidence_refs).issubset({"EV-EVAL"})
    )
    artifacts.append(_artifact(
        case_id="smoke/material-closed-candidate-set",
        trial_index=trial_index,
        variant=variant,
        passed=audit_passed,
        outcome=audit.decision.model_dump(mode="json"),
        trajectory=[{"action": audit.decision.action, "selected_candidate_id": selected}],
    ))

    for case in _jsonl("material_audit_golden.jsonl"):
        candidates = [
            MaterialCandidate(
                candidate_id=f"{case['id']}-C{index}",
                page_ids=[f"PAGE-{index}"],
                proposed_person_id=person_id,
                proposed_material_type=material_type,
                proposed_requirement_id=f"REQ-{index}",
                evidence_refs=[f"EV-{index}"],
                workflow_score=.9 if index == 1 else .7,
            )
            for index, (person_id, material_type) in enumerate(
                zip(case["person_ids"], case["material_types"], strict=True), start=1,
            )
        ]
        run = audit_agent.decide(MaterialAuditAssignment(
            assignment_id=case["id"],
            case_id="CASE-GOLDEN",
            thread_id="THREAD-GOLDEN",
            case_version=1,
            plan_version=1,
            objective=case["objective"],
            issue=MaterialIssue(
                task_id=f"TASK-{case['id']}",
                issue_type=case["issue_type"],
                person_id=case["person_ids"][0],
                material_type=case["material_types"][0],
                candidate_page_ids=["PAGE-1", "PAGE-2"],
                evidence_refs=["EV-1", "EV-2"],
                confidence=.72,
            ),
            candidates=candidates,
            allowed_actions=["APPLY_CANDIDATE", "REQUEST_HUMAN", "REQUEST_RECOVERY"],
        ))
        selected = getattr(run.decision, "selected_candidate_id", None)
        expected_actions = set(case.get("expected_actions") or [case["expected_action"]])
        passed = bool(
            run.decision.action in expected_actions
            and (selected is None or selected in {item.candidate_id for item in candidates})
            and set(run.decision.evidence_refs).issubset({"EV-1", "EV-2"})
        )
        artifacts.append(_artifact(
            case_id=f"material/{case['id']}",
            trial_index=trial_index,
            variant=variant,
            passed=passed,
            outcome=run.decision.model_dump(mode="json"),
            trajectory=[{
                "action": run.decision.action,
                "selected_candidate_id": selected,
                "closed_candidate_set": selected is None
                or selected in {item.candidate_id for item in candidates},
            }],
            metadata={"failure_mode": case.get("failure_mode", case["issue_type"])},
        ))

    specs = exception_agent.registry.specs()
    for case in _jsonl("exception_candidate_golden.jsonl"):
        intent = f"EXCEPTION:{case['exception_type']}"
        visible = set(exception_agent.registry.visible_names(task_intents=[intent]))
        candidates = build_candidate_tools(
            master_allowlist=exception_agent.allowed_tools,
            visible_specs=[spec for spec in specs if spec.name in visible],
            actions=[],
        )
        enabled = sorted(candidates.enabled)
        expected = sorted(case["expected_tools"])
        artifacts.append(_artifact(
            case_id=f"tool-policy/{case['id']}",
            trial_index=trial_index,
            variant=variant,
            passed=enabled == expected,
            outcome={"enabled_tools": enabled},
            trajectory=[{"intent": intent, "enabled_tools": enabled}],
        ))

    for case in _jsonl("case_association_golden.jsonl"):
        candidates = [AssociationCandidate.model_validate(item) for item in case["candidates"]]
        run = association_agent.decide(CaseAssociationAssignment(
            assignment_id=case["id"],
            case_id="CASE-ASSOCIATION-GOLDEN",
            thread_id="THREAD-ASSOCIATION-GOLDEN",
            case_version=1,
            objective=case["objective"],
            candidates=candidates,
            allowed_actions=["APPLY_CANDIDATES", "REQUEST_RECOVERY", "REQUEST_HUMAN"],
        ))
        selected = set(getattr(run.decision, "selected_candidate_ids", []))
        candidate_ids = {item.candidate_id for item in candidates}
        evidence_refs = {ref for item in candidates for ref in item.evidence_refs}
        passed = bool(
            run.decision.action in set(case["expected_actions"])
            and selected.issubset(candidate_ids)
            and set(run.decision.evidence_refs).issubset(evidence_refs)
        )
        artifacts.append(_artifact(
            case_id=f"association/{case['id']}",
            trial_index=trial_index,
            variant=variant,
            passed=passed,
            outcome=run.decision.model_dump(mode="json"),
            trajectory=[{
                "action": run.decision.action,
                "closed_candidate_set": selected.issubset(candidate_ids),
                "closed_evidence_set": set(run.decision.evidence_refs).issubset(evidence_refs),
            }],
        ))
    return artifacts


def _workflow_db_outcome_trial(trial_index: int) -> TrialArtifact:
    """完整跑通流程，关闭连接后重新打开 SQLite，再对最终状态评分。"""

    with TemporaryDirectory(prefix="argus-outcome-eval-") as directory:
        root = Path(directory)
        case_db = root / "cases.sqlite3"
        checkpoint_db = root / "checkpoints.sqlite3"
        service = AuditService(
            SQLiteCaseRepository(case_db),
            checkpointer=sqlite_checkpointer(checkpoint_db),
            pipeline_dependencies=build_demo_pipeline_dependencies(),
        )
        state = service.create_case(
            create_demo_case("material_completeness"),
            source="EVAL_FIXTURE",
            metadata={"trial_index": trial_index, "namespace": "eval/shadow/current"},
        )
        state = service.run(state.case_id)
        request = state.pending_human_request or {}
        chosen = next(
            (
                item for item in request.get("candidate_options", [])
                if "PAGE-021" in item.get("page_ids", [])
                and item.get("proposed_person_id") == request.get("person_id")
            ),
            None,
        )
        state = service.supplement(state.case_id, {
            "event_id": f"EVAL-{trial_index}-OWNER",
            "action": "CONFIRM_OWNER",
            "task_id": request["task_id"],
            "page_id": "PAGE-021",
            "person_id": request["person_id"],
            "material_type": request["material_type"],
            "selected_candidate_id": (chosen or {}).get("candidate_id"),
            "reason_code": "EVAL_HUMAN_CONFIRMED_OWNER",
            "operator_id": "eval-reviewer",
        })
        request = state.pending_human_request or {}
        state = service.supplement(state.case_id, {
            "event_id": f"EVAL-{trial_index}-SUPPLEMENT",
            "action": "REQUEST_SUPPLEMENT",
            "task_id": request["task_id"],
            "reason_code": "EVAL_SUPPLEMENT_REQUIRED",
            "operator_id": "eval-reviewer",
        })
        request = state.pending_human_request or {}
        case_id = state.case_id
        service.supplement(state.case_id, {
            "event_id": f"EVAL-{trial_index}-ARRIVED",
            "action": "SUPPLEMENT_RECEIVED",
            "task_id": request["task_id"],
            "page": {"page_id": f"PAGE-EVAL-UPLOAD-{trial_index}", "confidence": .99},
            "reason_code": "EVAL_SUPPLEMENT_RECEIVED",
            "operator_id": "eval-reviewer",
        })
        service.close()

        reopened = SQLiteCaseRepository(case_db)
        try:
            outcome = project_final_db_outcome(reopened, case_id)
            events = reopened.event_dicts(case_id)
            feedback = project_feedback(events)
        finally:
            reopened.close()
        detailed_scores = score_material_outcome(outcome)
        scored_event_types = {
            "AGENT_TOOL_FINISHED",
            "AUDIT_CANDIDATES_BUILT",
            "AUDIT_DECISION_PROPOSED",
            "AUDIT_PLAN_GATE_EVALUATED",
            "HUMAN_DECISION_APPLIED",
            "CHECKPOINT_RESUMED",
            "STATE_RECONCILIATION_COMPLETED",
            "SELECTIVE_REPLAN_COMPLETED",
            "COMPLETENESS_VALIDATED",
            "RUN_COMPLETED",
        }
        trajectory = [
            {
                "seq": event["seq"],
                "event_type": event["event_type"],
                "actor": event["actor"],
                "node": event["payload"].get("node"),
                "action": event["payload"].get("action"),
                "task_id": event["payload"].get("task_id"),
            }
            for event in events
            if event["event_type"] in scored_event_types
        ]
        return _artifact(
            case_id="workflow/final-db-outcome",
            trial_index=trial_index,
            variant="deterministic/current",
            passed=detailed_scores["passed"] == 1.0,
            outcome=outcome,
            trajectory=trajectory,
            metadata={
                "outcome_scores": detailed_scores,
                "persistence_check": "SQLITE_REOPENED_AFTER_SERVICE_CLOSE",
                "candidate_impression_count": len(feedback["candidate_impressions"]),
                "human_feedback_count": len(feedback["human_feedback"]),
                "hard_case_count": len(feedback["hard_cases"]),
            },
        )


def _baseline_path(mode: str) -> Path:
    requested = EVAL_ROOT / f"agent_{mode}_baseline.json"
    if mode == "live" and not requested.exists():
        return EVAL_ROOT / "agent_deterministic_baseline.json"
    return requested


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()
    if args.trials < 1:
        raise ValueError("--trials must be at least 1")

    artifacts: list[TrialArtifact] = []
    for trial_index in range(1, args.trials + 1):
        artifacts.extend(_agent_trial(args.mode, trial_index))
        if args.mode == "deterministic":
            artifacts.append(_workflow_db_outcome_trial(trial_index))
    report = summarize_trial_artifacts(artifacts, variant=f"{args.mode}/current")
    report["dataset"] = [
        "material_audit_golden.jsonl",
        "case_association_golden.jsonl",
        "exception_candidate_golden.jsonl",
        "workflow/final-db-outcome",
    ]
    report["quality_floor"] = {
        "passed": 1.0,
        "strict_case_pass_rate": 1.0,
        "outcome_stability": 1.0,
    }
    report["floor_failures"] = [
        metric for metric, threshold in report["quality_floor"].items()
        if float(report["metrics"].get(metric, 0.0)) < threshold
    ]

    baseline_path = _baseline_path(args.mode)
    if args.update_baseline:
        report["regression"] = None
        report["shadow_replay"] = None
        report["passed"] = not report["floor_failures"]
        baseline_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        if not baseline_path.exists():
            raise FileNotFoundError(
                f"committed baseline missing: rerun with --update-baseline: {baseline_path}"
            )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        report["regression"] = paired_bootstrap_gate(
            current=report["case_metrics"],
            baseline=baseline["case_metrics"],
            metric_names=["passed"],
        )
        report["shadow_replay"] = shadow_replay_report(
            baseline_trials=baseline["trials"],
            challenger_trials=report["trials"],
            metric_names=["passed"],
        )
        report["passed"] = not report["floor_failures"] and report["regression"]["passed"]

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_ROOT / f"agent_{args.mode}_harness.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "passed": report["passed"],
        "mode": args.mode,
        "trials_per_case": report["trials_per_case"],
        "case_count": report["case_count"],
        "trial_count": report["trial_count"],
        "metrics": report["metrics"],
        "floor_failures": report["floor_failures"],
        "regression": report.get("regression"),
        "shadow_replay": {
            key: value for key, value in (report.get("shadow_replay") or {}).items()
            if key != "pairs"
        } or None,
        "report": str(report_path),
        "baseline": str(baseline_path),
    }, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
