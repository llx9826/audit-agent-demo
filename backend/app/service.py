from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Any

from .demo.fixtures import create_case
from .domain.models import AuditResult, AuditTask, CaseState, Evidence
from .graph.builder import build_audit_graph
from .persistence.repository import InMemoryCaseRepository, SQLiteCaseRepository
from .rag.hybrid import demo_policy_trace


class AuditService:
    """Application service: persist graph transitions and expose use cases.

    Audit decisions and routing are deliberately absent from this class; the
    compiled graph is the single execution source for both run and resume.
    """

    def __init__(self, repository: InMemoryCaseRepository | None = None) -> None:
        self.repo = repository or SQLiteCaseRepository()
        self._graph: Any | None = None

    @property
    def graph(self) -> Any:
        if self._graph is None:
            self._graph = build_audit_graph()
        return self._graph

    @staticmethod
    def _audit_result(value: dict[str, Any] | AuditResult | None) -> AuditResult | None:
        if value is None or isinstance(value, AuditResult):
            return value
        return AuditResult(**value)

    @classmethod
    def _audit_task(cls, value: dict[str, Any] | AuditTask) -> AuditTask:
        if isinstance(value, AuditTask):
            return value
        return AuditTask(
            task_id=value["task_id"],
            task_type=value["task_type"],
            status=value.get("status", "PENDING"),
            depends_on=list(value.get("depends_on", [])),
            required_documents=list(value.get("required_documents", [])),
            required_entities=list(value.get("required_entities", [])),
            result=cls._audit_result(value.get("result")),
        )

    @staticmethod
    def _evidence(value: dict[str, Any] | Evidence) -> Evidence:
        return value if isinstance(value, Evidence) else Evidence(**value)

    @classmethod
    def _apply_graph_state(cls, original: CaseState, graph_state: dict[str, Any]) -> CaseState:
        updated = deepcopy(original)
        valid_fields = {item.name for item in fields(CaseState)}
        for key, value in graph_state.items():
            if key not in valid_fields:
                continue
            if key == "audit_plan":
                updated.audit_plan = [cls._audit_task(item) for item in value]
            elif key == "task_results":
                updated.task_results = {task_id: cls._audit_result(item) for task_id, item in value.items()}  # type: ignore[dict-item]
            elif key == "evidence_ledger":
                updated.evidence_ledger = [cls._evidence(item) for item in value]
            else:
                setattr(updated, key, deepcopy(value))
        return updated

    def _execute(self, state: CaseState) -> CaseState:
        graph_input = state.to_dict()
        graph_input["pending_events"] = []
        result = self.graph.invoke(graph_input)
        updated = self._apply_graph_state(state, result)
        for spec in result.get("pending_events", []):
            self.repo.append_event(updated, spec["event_type"], spec["actor"], spec["payload"])
        self.repo.save(updated)
        if updated.status == "WAITING_HUMAN":
            self.repo.checkpoint(updated, "HITL / Plan V1")
        elif updated.status == "COMPLETED":
            label = "Completed / Plan V2" if updated.plan_version > 1 else "Completed / Plan V1"
            self.repo.checkpoint(updated, label)
        return updated

    def new_demo(self, scenario: str) -> CaseState:
        state = create_case(scenario)
        self.repo.clear_case(state.case_id)
        self.repo.save(state)
        self.repo.append_event(
            state, "CASE_CREATED", "workflow",
            {
                "node": "case_created",
                "action": "CREATE_DEMO_CASE",
                "observation": {"scenario": scenario},
                "state_diff": {"status": [None, "READY"]},
            },
        )
        self.repo.checkpoint(state, "Case Created")
        return state

    def run(self, case_id: str) -> CaseState:
        state = self.repo.get(case_id)
        if state.status in {"WAITING_HUMAN", "COMPLETED"}:
            return state
        return self._execute(state)

    def supplement(self, case_id: str, event: dict[str, Any]) -> CaseState:
        state = self.repo.get(case_id)
        if state.resume_event and state.resume_event.get("event_id") == event.get("event_id"):
            return state
        if state.status != "WAITING_HUMAN":
            raise ValueError("case is not waiting for a supplement")
        certificate = event.get("marriage_certificate")
        if not event.get("event_id") or not isinstance(certificate, dict):
            raise ValueError("event_id and marriage_certificate are required")
        if not certificate.get("husband") or not certificate.get("wife"):
            raise ValueError("marriage_certificate.husband and wife are required")
        state.resume_event = deepcopy(event)
        return self._execute(state)

    def finish(self, case_id: str) -> CaseState:
        """Compatibility endpoint; resume now executes through final validator."""
        state = self.repo.get(case_id)
        if state.status == "COMPLETED":
            return state
        if state.resume_event:
            return self._execute(state)
        return self.run(case_id)

    def inspect(self, case_id: str) -> dict:
        state = self.repo.get(case_id)
        return {
            "state": state.to_dict(),
            "events": self.repo.event_dicts(case_id),
            "checkpoints": list(self.repo.checkpoints.get(case_id, {})),
        }

    def replay(self, case_id: str, checkpoint_id: str) -> CaseState:
        return self.repo.replay(case_id, checkpoint_id)

    def rag_trace(self, case_id: str) -> dict[str, Any]:
        state = self.repo.get(case_id)
        return deepcopy(state.rag_trace or demo_policy_trace())
