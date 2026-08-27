from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Any, Callable

from .agents.exception_recovery import ExceptionRecoveryAgent
from .agents.case_association import CaseAssociationAgent
from .agents.material_audit import MaterialAuditAgent
from .domain.models import (
    AtomicRequirement,
    AuditResult,
    AuditTask,
    CaseState,
    Evidence,
    HumanTask,
    IdentityMention,
    MaterialMatch,
    MaterialOwnerBinding,
    PageAsset,
    PersonEntity,
    PersonRole,
    RoleBinding,
    RoleSignal,
    SupplementRequest,
)
from .orchestration import AuditPipelineDependencies, build_audit_pipeline
from .orchestration.association_evidence import PageFieldAssociationEvidenceExtractor
from .persistence.repository import InMemoryCaseRepository, SQLiteCaseRepository
from .rag.requirements.evidence import RequirementEvidenceRAG
from .rag.requirements.rule_engine import RequirementRuleEngine
from .runtime.checkpoint import CheckpointerHandle, memory_checkpointer, sqlite_checkpointer


EventCallback = Callable[[dict[str, Any]], None]


class AuditService:
    """Application service: persist graph transitions and expose use cases.

    Audit decisions and routing are deliberately absent from this class; the
    compiled graph is the single execution source for both run and resume.
    """

    def __init__(
        self,
        repository: InMemoryCaseRepository | None = None,
        *,
        checkpointer: CheckpointerHandle | None = None,
        exception_agent: ExceptionRecoveryAgent | None = None,
        association_agent: CaseAssociationAgent | None = None,
        material_agent: MaterialAuditAgent | None = None,
        pipeline_dependencies: AuditPipelineDependencies | None = None,
        max_task_concurrency: int = 4,
        graph_recursion_limit: int = 96,
    ) -> None:
        owns_default_repository = repository is None
        self.repo = repository or SQLiteCaseRepository()
        self._checkpointer = checkpointer or (
            sqlite_checkpointer() if owns_default_repository else memory_checkpointer()
        )
        self._graph: Any | None = None
        self._pipeline_dependencies = pipeline_dependencies or AuditPipelineDependencies(
            requirement_resolver=RequirementRuleEngine(),
            requirement_evidence_rag=RequirementEvidenceRAG(),
            association_evidence_extractor=PageFieldAssociationEvidenceExtractor(),
            case_association_agent=association_agent or CaseAssociationAgent(),
            exception_agent=exception_agent or ExceptionRecoveryAgent(max_steps=3),
            material_audit_agent=material_agent or MaterialAuditAgent(),
        )
        self._closed = False
        if max_task_concurrency < 1:
            raise ValueError("max_task_concurrency must be at least 1")
        if graph_recursion_limit < 32:
            raise ValueError("graph_recursion_limit must be at least 32")
        self._max_task_concurrency = max_task_concurrency
        self._graph_recursion_limit = graph_recursion_limit

    @property
    def graph(self) -> Any:
        if self._graph is None:
            self._graph = build_audit_pipeline(
                self._pipeline_dependencies,
                checkpointer=self._checkpointer.saver,
            )
        return self._graph

    def _graph_config(self, state: CaseState) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": state.thread_id or state.case_id},
            "max_concurrency": self._max_task_concurrency,
            "recursion_limit": self._graph_recursion_limit,
        }

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
            task_type=value.get("task_type", "required_material"),
            status=value.get("status", "PENDING"),
            depends_on=list(value.get("depends_on", [])),
            fact_dependencies=list(value.get("fact_dependencies", value.get("depends_on", []))),
            task_dependencies=list(value.get("task_dependencies", [])),
            conflict_keys=list(value.get("conflict_keys", [])),
            requirement_refs=list(value.get("requirement_refs", [])),
            executor=value.get("executor", "MATERIAL_MATCH_WORKER"),
            execution_group=value.get("execution_group", "MATERIAL_MATCH"),
            result_version=int(value.get("result_version", 0)),
            requirement_id=value.get("requirement_id"),
            person_id=value.get("person_id"),
            person_role=value.get("person_role"),
            material_type=value.get("material_type"),
            matched_page_ids=list(value.get("matched_page_ids", [])),
            evidence_refs=list(value.get("evidence_refs", [])),
            result=cls._audit_result(value.get("result")),
        )

    @staticmethod
    def _evidence(value: dict[str, Any] | Evidence) -> Evidence:
        return value if isinstance(value, Evidence) else Evidence(**value)

    @staticmethod
    def _typed(value: Any, model: type) -> Any:
        return value if isinstance(value, model) else model(**value)

    @classmethod
    def _apply_graph_state(cls, original: CaseState, graph_state: dict[str, Any]) -> CaseState:
        updated = deepcopy(original)
        valid_fields = {item.name for item in fields(CaseState)}
        for key, value in graph_state.items():
            if key not in valid_fields:
                continue
            if key == "audit_plan":
                updated.audit_plan = [cls._audit_task(item) for item in value]
            elif key == "evidence_ledger":
                updated.evidence_ledger = [cls._evidence(item) for item in value]
            elif key == "persons":
                updated.persons = [cls._typed(item, PersonRole) for item in value]
            elif key == "person_entities":
                updated.person_entities = [cls._typed(item, PersonEntity) for item in value]
            elif key == "identity_mentions":
                updated.identity_mentions = [cls._typed(item, IdentityMention) for item in value]
            elif key == "role_signals":
                updated.role_signals = [cls._typed(item, RoleSignal) for item in value]
            elif key == "role_bindings":
                updated.role_bindings = [cls._typed(item, RoleBinding) for item in value]
            elif key == "material_owner_bindings":
                updated.material_owner_bindings = [cls._typed(item, MaterialOwnerBinding) for item in value]
            elif key == "pages":
                updated.pages = [cls._typed(item, PageAsset) for item in value]
            elif key == "requirements":
                updated.requirements = [cls._typed(item, AtomicRequirement) for item in value]
            elif key == "material_matches":
                updated.material_matches = [cls._typed(item, MaterialMatch) for item in value]
            elif key == "human_tasks":
                updated.human_tasks = [cls._typed(item, HumanTask) for item in value]
            elif key == "supplement_requests":
                updated.supplement_requests = [cls._typed(item, SupplementRequest) for item in value]
            else:
                setattr(updated, key, deepcopy(value))
        return updated

    def _persist_event_specs(
        self,
        state: CaseState,
        specs: list[dict[str, Any]],
        *,
        run_id: str | None = None,
        namespace: str | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        for spec in specs:
            event = self.repo.append_event(
                state,
                spec["event_type"],
                spec["actor"],
                spec["payload"],
                checkpoint_id=spec.get("checkpoint_id"),
                run_id=run_id,
                namespace=namespace,
            )
            if on_event:
                from dataclasses import asdict

                on_event(asdict(event))

    def _prepare_resume(
        self,
        state: CaseState,
        resume_event: dict[str, Any],
        *,
        run_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> dict[str, Any]:
        """核验并记录同一 thread 的暂停 Checkpoint，不暴露完整状态 Blob。"""

        config = self._graph_config(state)
        graph_snapshot = self.graph.get_state(config)
        checkpoints = self.repo.checkpoints.get(state.case_id, {})
        latest_checkpoint_id = next(reversed(checkpoints), None)
        lookup = [{
            "event_type": "CHECKPOINT_LOOKUP_STARTED",
            "actor": "checkpoint_store",
            "payload": {
                "node": "resume_control",
                "action": "LOOKUP_INTERRUPTED_THREAD",
                "observation": {"thread_id": state.thread_id},
            },
        }]
        self._persist_event_specs(state, lookup, run_id=run_id, on_event=on_event)
        if latest_checkpoint_id is None:
            raise ValueError("no persisted checkpoint found for this case")
        paused_state = checkpoints[latest_checkpoint_id]
        if paused_state.thread_id != state.thread_id:
            raise ValueError("checkpoint thread_id does not match the active case")
        if paused_state.status not in {"WAITING_HUMAN", "WAITING_SUPPLEMENT"}:
            raise ValueError("latest checkpoint is not an interrupted state")
        graph_checkpoint_id = (
            (graph_snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
            if graph_snapshot else None
        )
        summary = {
            "checkpoint_id": latest_checkpoint_id,
            "langgraph_checkpoint_id": graph_checkpoint_id,
            "thread_id": state.thread_id,
            "case_version": paused_state.case_version,
            "plan_version": paused_state.plan_version,
            "status": paused_state.status,
            "task_counts": {
                status: sum(1 for task in paused_state.audit_plan if task.status == status)
                for status in sorted({task.status for task in paused_state.audit_plan})
            },
        }
        accepted_specs = [
            {
                "event_type": "CHECKPOINT_FOUND",
                "actor": "checkpoint_store",
                "checkpoint_id": latest_checkpoint_id,
                "payload": {
                    "node": "resume_control",
                    "action": "RESOLVE_CHECKPOINT_LINEAGE",
                    "observation": summary,
                },
            },
            {
                "event_type": "INTERRUPTED_STATE_LOADED",
                "actor": "checkpoint_store",
                "checkpoint_id": latest_checkpoint_id,
                "payload": {
                    "node": "resume_control",
                    "action": "LOAD_REDACTED_STATE_SUMMARY",
                    "observation": summary,
                },
            },
            {
                "event_type": "RESUME_COMMAND_ACCEPTED",
                "actor": "workflow",
                "checkpoint_id": latest_checkpoint_id,
                "payload": {
                    "node": "resume_control",
                    "task_id": resume_event.get("task_id"),
                    "action": str(resume_event.get("action")),
                    "observation": {
                        "event_id": resume_event.get("event_id"),
                        "thread_id": state.thread_id,
                        "checkpoint_id": latest_checkpoint_id,
                    },
                },
            },
        ]
        if resume_event.get("action") == "SUPPLEMENT_RECEIVED":
            accepted_specs.insert(0, {
                "event_type": "SUPPLEMENT_EVENT_ACCEPTED",
                "actor": "workflow",
                "checkpoint_id": latest_checkpoint_id,
                "payload": {
                    "node": "resume_control",
                    "task_id": resume_event.get("task_id"),
                    "action": "VALIDATE_SUPPLEMENT_EVENT",
                    "observation": {
                        "event_id": resume_event.get("event_id"),
                        "page_id": (resume_event.get("page") or {}).get("page_id"),
                    },
                },
            })
        self._persist_event_specs(state, accepted_specs, run_id=run_id, on_event=on_event)
        enriched = deepcopy(resume_event)
        enriched["_resume_context"] = summary
        return enriched

    def _finalize_execution(
        self,
        original: CaseState,
        graph_state: dict[str, Any],
        *,
        run_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> CaseState:
        updated = self._apply_graph_state(original, graph_state)
        self.repo.save(updated)
        if updated.status in {"WAITING_HUMAN", "WAITING_SUPPLEMENT"}:
            checkpoint_id = self.repo.checkpoint(updated, f"{updated.status} / Plan V{updated.plan_version}")
            terminal = self.repo.append_event(
                updated,
                "RUN_PAUSED",
                "workflow",
                {
                    "node": "await_human",
                    "checkpoint_id": checkpoint_id,
                    "reason": updated.status,
                    "pending_human_request": deepcopy(updated.pending_human_request),
                },
                run_id=run_id,
            )
        else:
            label = "Completed / Plan V2" if updated.plan_version > 1 else "Completed / Plan V1"
            checkpoint_id = self.repo.checkpoint(updated, label)
            terminal = self.repo.append_event(
                updated,
                "RUN_COMPLETED",
                "workflow",
                {"node": "final_validator", "checkpoint_id": checkpoint_id},
                run_id=run_id,
            )
        if on_event:
            from dataclasses import asdict

            on_event(asdict(terminal))
        return updated

    def _execute(self, state: CaseState, *, resume_event: dict[str, Any] | None = None) -> CaseState:
        from langgraph.types import Command

        config = self._graph_config(state)
        snapshot = self.graph.get_state(config)
        existing_specs = len(snapshot.values.get("pending_events", [])) if snapshot.values else 0
        if resume_event is None:
            graph_input: dict[str, Any] | Command = state.to_dict()
            graph_input["pending_events"] = []
        else:
            graph_input = Command(resume=self._prepare_resume(state, resume_event))
        result = self.graph.invoke(graph_input, config=config)
        updated = self._apply_graph_state(state, result)
        self._persist_event_specs(updated, list(result.get("pending_events", []))[existing_specs:])
        return self._finalize_execution(state, result)

    @staticmethod
    def _stream_parts(chunk: Any) -> tuple[tuple[str, ...], str, Any]:
        if isinstance(chunk, tuple) and len(chunk) == 3 and isinstance(chunk[0], tuple):
            namespace, mode, data = chunk
            return namespace, str(mode), data
        if isinstance(chunk, tuple) and len(chunk) == 2:
            mode, data = chunk
            return (), str(mode), data
        return (), "values", chunk

    def execute_stream(
        self,
        case_id: str,
        *,
        run_id: str,
        resume_event: dict[str, Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> CaseState:
        """Execute a graph thread while persisting each node/custom event immediately."""
        from langgraph.types import Command

        state = self.repo.get(case_id)
        config = self._graph_config(state)
        snapshot = self.graph.get_state(config)
        persisted_spec_count = len(snapshot.values.get("pending_events", [])) if snapshot.values else 0
        if resume_event is None:
            graph_input: dict[str, Any] | Command = state.to_dict()
            graph_input["pending_events"] = []
        else:
            graph_input = Command(resume=self._prepare_resume(
                state,
                resume_event,
                run_id=run_id,
                on_event=on_event,
            ))

        latest_graph_state: dict[str, Any] = snapshot.values or state.to_dict()
        for raw_chunk in self.graph.stream(
            graph_input,
            config=config,
            stream_mode=["custom", "values"],
            subgraphs=True,
        ):
            namespace_path, mode, data = self._stream_parts(raw_chunk)
            namespace = "/".join(namespace_path) or None
            if mode == "custom" and isinstance(data, dict) and data.get("event_type"):
                projected = self._apply_graph_state(state, latest_graph_state)
                spec = {
                    "event_type": str(data["event_type"]),
                    "actor": str(data.get("actor", "agent")),
                    "payload": {key: deepcopy(value) for key, value in data.items() if key not in {"event_type", "actor"}},
                }
                self._persist_event_specs(
                    projected,
                    [spec],
                    run_id=run_id,
                    namespace=namespace,
                    on_event=on_event,
                )
                continue
            if mode != "values" or not isinstance(data, dict):
                continue
            latest_graph_state = data
            projected = self._apply_graph_state(state, data)
            self.repo.save(projected)
            all_specs = list(data.get("pending_events", []))
            fresh_specs = all_specs[persisted_spec_count:]
            if fresh_specs:
                self._persist_event_specs(
                    projected,
                    fresh_specs,
                    run_id=run_id,
                    namespace=namespace,
                    on_event=on_event,
                )
                persisted_spec_count = len(all_specs)

        final_snapshot = self.graph.get_state(config)
        final_values = dict(final_snapshot.values or latest_graph_state)
        return self._finalize_execution(
            state,
            final_values,
            run_id=run_id,
            on_event=on_event,
        )

    def create_case(
        self,
        state: CaseState,
        *,
        source: str = "CASE_API",
        metadata: dict[str, Any] | None = None,
    ) -> CaseState:
        """Persist a typed case supplied by any composition-root provider."""

        self.repo.clear_case(state.case_id)
        self.repo.save(state)
        self.repo.append_event(
            state, "CASE_CREATED", "workflow",
            {
                "node": "case_created",
                "action": "CREATE_CASE",
                "observation": {"source": source, **(metadata or {})},
                "state_diff": {"status": [None, "READY"]},
            },
        )
        self.repo.checkpoint(state, "Case Created")
        return state

    def run(self, case_id: str) -> CaseState:
        state = self.repo.get(case_id)
        if state.status in {"WAITING_HUMAN", "WAITING_SUPPLEMENT", "COMPLETED"}:
            return state
        return self._execute(state)

    def supplement(self, case_id: str, event: dict[str, Any]) -> CaseState:
        state = self.repo.get(case_id)
        if state.resume_event and state.resume_event.get("event_id") == event.get("event_id"):
            return state
        if state.status not in {"WAITING_HUMAN", "WAITING_SUPPLEMENT"}:
            raise ValueError("case is not waiting for a human command")
        if not event.get("event_id") or not event.get("action") or not event.get("task_id"):
            raise ValueError("event_id, action and task_id are required")
        return self._execute(state, resume_event=event)

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
        if state.rag_trace:
            return deepcopy(state.rag_trace)
        return {
            "trace_type": "REQUIREMENT_EVIDENCE_RAG",
            "trigger": "NOT_TRIGGERED",
            "reason": "仅当齐套校验发现缺件、影像不可读或归属不确定时执行",
            "original_query": None,
            "rewritten_query": None,
            "retrieval": {
                "strategy": "METADATA_FILTER_DENSE_BM25_RRF_CROSS_ENCODER",
                "channel_backend": None,
                "reranker": None,
                "candidate_count": 0,
                "eligible_count": 0,
            },
            "pipeline": [{"stage": "WAITING_FOR_COMPLETENESS_PROBLEM"}],
            "candidates": [],
            "selected": [],
            "final_requirements": [],
            "problem_task_ids": [],
            "groundings": [],
        }

    def close(self) -> None:
        """Close the LangGraph checkpoint and application persistence adapters."""
        if self._closed:
            return
        self._closed = True
        self._checkpointer.close()
        self.repo.close()
