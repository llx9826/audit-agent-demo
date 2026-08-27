import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.persistence.repository import InMemoryCaseRepository, SQLiteCaseRepository
from app.runtime.checkpoint import memory_checkpointer, sqlite_checkpointer
from app.service import AuditService
from app.agents.contracts import RequestRecoveryDecision
from app.agents.material_audit import MaterialAuditAgent
from app.agents.case_association import (
    DeterministicAssociationAdapter,
    RequestAssociationHuman,
    RequestAssociationRecovery,
    CaseAssociationAgent,
)
from app.domain.models import CaseState, PageAsset
from app.orchestration.association_evidence import AssociationPageObservation
from demo.fixtures import create_demo_case
from demo.providers import build_demo_pipeline_dependencies


def memory_service() -> AuditService:
    return AuditService(
        InMemoryCaseRepository(),
        checkpointer=memory_checkpointer(),
        pipeline_dependencies=build_demo_pipeline_dependencies(),
    )


def new_demo(service: AuditService):
    return service.create_case(
        create_demo_case("material_completeness"),
        source="DEMO_FIXTURE",
        metadata={"scenario": "material_completeness"},
    )


class ServiceIntegrationTests(unittest.TestCase):
    def test_association_human_resume_commits_candidates_in_same_thread(self):
        """人工确认后应穿过 Gate，不能因 Case 版本升级再次落回相同 interrupt。"""

        class AlwaysHumanAdapter:
            def decide_association(self, *, prompt, assignment):
                del prompt
                return RequestAssociationHuman(
                    action="REQUEST_HUMAN",
                    selected_candidate_ids=[],
                    reason_code="REVIEW_REQUIRED",
                    rationale_summary="回归测试强制进入人员关联人工确认。",
                    evidence_refs=[],
                    confidence=.5,
                    requires_human=True,
                )

        dependencies = replace(
            build_demo_pipeline_dependencies(),
            case_association_agent=CaseAssociationAgent(model_adapter=AlwaysHumanAdapter()),
        )
        service = AuditService(
            InMemoryCaseRepository(),
            checkpointer=memory_checkpointer(),
            pipeline_dependencies=dependencies,
        )
        paused = service.run(new_demo(service).case_id)
        request = paused.pending_human_request
        selected_ids = [item["candidate_id"] for item in request["candidate_options"]]

        resumed = service.supplement(paused.case_id, {
            "event_id": "H-ASSOCIATION-1",
            "action": "CONFIRM_ASSOCIATION",
            "task_id": request["task_id"],
            "selected_candidate_ids": selected_ids,
        })

        self.assertEqual(resumed.thread_id, paused.thread_id)
        self.assertEqual(resumed.association_gate["outcome"], "CONFIRMED")
        self.assertTrue(resumed.persons)
        self.assertNotEqual(
            (resumed.pending_human_request or {}).get("action"),
            "CONFIRM_ASSOCIATION",
        )
        service.close()

    def test_domain_only_input_discovers_people_and_roles_before_checklist(self):
        """真实入口只给六分类；页级取证和 Association Gate 自己建立人员角色。"""

        class DomainOnlyExtractor:
            def extract(self, *, case_id, page):
                del case_id
                return AssociationPageObservation(
                    person_id="P-FOUND",
                    person_name="脱敏客户",
                    identity_key="HASH-FOUND",
                    role_signals=["BORROWER"],
                    owner_person_id="P-FOUND",
                    confidence=.98,
                    evidence_refs=[f"EV-{page['page_id']}-VLM"],
                    provider="LOCAL:vlm-service",
                )

        dependencies = replace(
            build_demo_pipeline_dependencies(),
            association_evidence_extractor=DomainOnlyExtractor(),
        )
        service = AuditService(
            InMemoryCaseRepository(),
            checkpointer=memory_checkpointer(),
            pipeline_dependencies=dependencies,
        )
        created = service.create_case(CaseState(
            case_id="CASE-DOMAIN-ONLY",
            thread_id="THREAD-DOMAIN-ONLY",
            persons=[],
            pages=[PageAsset(
                page_id="PAGE-DOMAIN-001",
                bundle_id="B01",
                page_number=1,
                domain="身份与主体证明",
                status="PROCESSING",
            )],
            business_fields={
                "product_type": "宅抵贷",
                "channel": "DIRECT",
                "case_date": "2026-08-15",
            },
            status="READY",
        ))

        paused = service.run(created.case_id)

        self.assertEqual([(p.person_id, p.roles) for p in paused.persons], [("P-FOUND", ["BORROWER"])])
        self.assertTrue(paused.audit_plan)
        events = service.repo.event_dicts(created.case_id)
        self.assertIn("ASSOCIATION_GATE_EVALUATED", {item["event_type"] for item in events})
        service.close()

    def test_association_agent_recovery_handoff_returns_to_association_gate(self):
        class RecoverOnceAdapter:
            def __init__(self):
                self.calls = 0
                self.fallback = DeterministicAssociationAdapter()

            def decide_association(self, *, prompt, assignment):
                self.calls += 1
                if self.calls == 1:
                    return RequestAssociationRecovery(
                        action="REQUEST_RECOVERY",
                        selected_candidate_ids=[],
                        exception_type="OWNER_EVIDENCE_INSUFFICIENT",
                        missing_observations=["VLM_OWNER", "INDEPENDENT_DOCUMENT_OWNER"],
                        reason_code="OWNER_OBSERVATION_REQUIRED",
                        rationale_summary="材料所属人需要两路独立 Observation。",
                        evidence_refs=assignment.candidates[0].evidence_refs,
                        confidence=.65,
                        requires_human=False,
                    )
                return self.fallback.decide_association(prompt=prompt, assignment=assignment)

        adapter = RecoverOnceAdapter()
        dependencies = replace(
            build_demo_pipeline_dependencies(),
            case_association_agent=CaseAssociationAgent(model_adapter=adapter),
        )
        service = AuditService(
            InMemoryCaseRepository(),
            checkpointer=memory_checkpointer(),
            pipeline_dependencies=dependencies,
        )

        paused = service.run(new_demo(service).case_id)

        self.assertGreaterEqual(adapter.calls, 2)
        self.assertTrue(paused.persons)
        events = service.repo.event_dicts(paused.case_id)
        handoffs = [
            item for item in events
            if item["event_type"] == "HANDOFF_CREATED"
            and item["payload"].get("action") == "DELEGATE_ASSOCIATION_TO_EXCEPTION_AGENT"
        ]
        self.assertEqual(len(handoffs), 1)
        self.assertIn("ASSOCIATION_RECOVERY_COMPLETED", {item["event_type"] for item in events})
        service.close()

    def test_audit_recovery_request_is_routed_through_exception_then_rematched(self):
        class RecoveryRequestAdapter:
            def decide_material(self, *, assignment, **_kwargs):
                return RequestRecoveryDecision(
                    action="REQUEST_RECOVERY",
                    exception_type="OWNER_EVIDENCE_INSUFFICIENT",
                    missing_observations=["VLM_OWNER", "INDEPENDENT_DOCUMENT_OWNER"],
                    reason_code="OWNER_OBSERVATION_REQUIRED",
                    rationale_summary="候选证据同分，需要两路独立所属人 Observation。",
                    evidence_refs=assignment.issue.evidence_refs,
                    confidence=.72,
                    requires_human=False,
                )

        dependencies = replace(
            build_demo_pipeline_dependencies(),
            material_audit_agent=MaterialAuditAgent(model_adapter=RecoveryRequestAdapter()),
        )
        service = AuditService(
            InMemoryCaseRepository(),
            checkpointer=memory_checkpointer(),
            pipeline_dependencies=dependencies,
        )

        state = service.run(new_demo(service).case_id)

        self.assertEqual(state.pending_human_request["action"], "REQUEST_SUPPLEMENT")
        self.assertEqual(state.audit_gate["outcome"], "RECOVERY_REQUIRED")
        recovered_page = next(page for page in state.pages if page.page_id == "PAGE-021")
        self.assertEqual(recovered_page.owner_person_id, "P02")
        self.assertEqual(recovered_page.status, "VERIFIED")
        events = service.repo.event_dicts(state.case_id)
        self.assertEqual(sum(item["event_type"] == "HANDOFF_CREATED" for item in events), 2)
        self.assertIn(
            "OWNER_ASSIGNMENT_AMBIGUOUS",
            [
                item["payload"].get("observation", {}).get("exception_type")
                for item in events
                if item["event_type"] == "HANDOFF_CREATED"
            ],
        )
        service.close()

    def test_initial_run_uses_rule_engine_exception_and_audit_candidate_gate(self):
        service = memory_service()
        created = new_demo(service)
        self.assertEqual(len(created.pages), 216)
        paused = service.run(created.case_id)
        self.assertEqual(paused.status, "WAITING_HUMAN")
        self.assertEqual(paused.pending_human_request["action"], "CONFIRM_OWNER")
        self.assertEqual(len(paused.audit_plan), 7)
        types = [event["event_type"] for event in service.repo.event_dicts(created.case_id)]
        self.assertTrue({
            "ASSOCIATION_PAGES_SELECTED", "ASSOCIATION_PAGE_EVIDENCE_EXTRACTED",
            "ASSOCIATION_EVIDENCE_EXTRACTED", "ASSOCIATION_CANDIDATES_BUILT",
            "ASSOCIATION_DECISION_PROPOSED", "ASSOCIATION_GATE_EVALUATED",
            "REQUIREMENT_RULES_RESOLVED", "READY_TASKS_DISPATCHED",
            "TASK_WORKER_COMPLETED", "TASK_FAN_IN_COMMITTED",
            "HANDOFF_CREATED", "AGENT_TOOL_FINISHED",
            "EXCEPTION_RESOLVED", "COMPLETENESS_CHECKED", "AUDIT_CANDIDATES_BUILT",
            "PROMPT_RENDERED", "AUDIT_DECISION_PROPOSED", "AUDIT_PLAN_GATE_EVALUATED", "HITL_REQUESTED",
        }.issubset(types))
        # 首批 7 个 Task 并行执行；异常恢复后只把未闭合 Task 重新送入 Ready Batch。
        self.assertGreaterEqual(types.count("TASK_WORKER_COMPLETED"), 7)
        self.assertEqual(paused.rag_trace, None)
        self.assertIsNotNone(paused.audit_assignment)
        service.close()

    def test_three_human_commands_resume_same_thread_and_complete_materials(self):
        service = memory_service()
        state = new_demo(service)
        thread_id = state.thread_id
        state = service.run(state.case_id)
        request = state.pending_human_request
        state = service.supplement(state.case_id, {
            "event_id": "H-OWNER-1", "action": "CONFIRM_OWNER",
            "task_id": request["task_id"], "page_id": "PAGE-021", "person_id": request["person_id"],
        })
        self.assertEqual(state.pending_human_request["action"], "REQUEST_SUPPLEMENT")
        request = state.pending_human_request
        state = service.supplement(state.case_id, {
            "event_id": "H-SUP-1", "action": "REQUEST_SUPPLEMENT", "task_id": request["task_id"],
        })
        self.assertEqual(state.status, "WAITING_SUPPLEMENT")
        request = state.pending_human_request
        state = service.supplement(state.case_id, {
            "event_id": "H-ARRIVE-1", "action": "SUPPLEMENT_RECEIVED", "task_id": request["task_id"],
            "page": {"page_id": "PAGE-UPLOAD-001", "confidence": .99},
        })
        self.assertEqual(state.status, "COMPLETED")
        self.assertEqual(state.completeness_status, "COMPLETE")
        self.assertEqual(state.thread_id, thread_id)
        self.assertTrue(all(task.status == "MATCHED" for task in state.audit_plan))
        self.assertNotIn("final_decision", state.business_fields)
        event_types = [item["event_type"] for item in service.repo.event_dicts(state.case_id)]
        for required in (
            "CHECKPOINT_LOOKUP_STARTED", "CHECKPOINT_FOUND", "INTERRUPTED_STATE_LOADED",
            "RESUME_COMMAND_ACCEPTED", "CHECKPOINT_RESUMED", "STATE_RECONCILIATION_COMPLETED",
            "IMPACT_ANALYSIS_COMPLETED", "SELECTIVE_REPLAN_COMPLETED", "PLAN_VERSION_COMMITTED",
        ):
            self.assertIn(required, event_types)
        service.close()

    def test_selective_replan_keeps_unaffected_task_result_version(self):
        service = memory_service()
        state = service.run(new_demo(service).case_id)
        borrower_before = next(task for task in state.audit_plan if task.task_id == "TASK-BORROWER-ID-P01")
        request = state.pending_human_request
        state = service.supplement(state.case_id, {
            "event_id": "H-OWNER-2", "action": "CONFIRM_OWNER",
            "task_id": request["task_id"], "page_id": "PAGE-021", "person_id": request["person_id"],
        })
        borrower_after = next(task for task in state.audit_plan if task.task_id == "TASK-BORROWER-ID-P01")
        self.assertEqual(borrower_before.result.case_version, borrower_after.result.case_version)
        self.assertEqual(
            next(item for item in state.replan_decisions if item["task_id"] == borrower_after.task_id)["operation"],
            "KEEP",
        )
        replan_events = service.repo.event_dicts(state.case_id)
        reused_event = next(
            item for item in replan_events
            if item["event_type"] == "TASK_RESULT_REUSED"
            and item["payload"]["task_id"] == borrower_after.task_id
        )
        reused_observation = reused_event["payload"]["observation"]
        self.assertEqual(reused_observation["operation"], "KEEP")
        self.assertIn("matched_changed_facts", reused_observation)
        self.assertEqual(
            reused_observation["before_result_version"],
            reused_observation["after_result_version"],
        )
        invalidated_event = next(
            item for item in replan_events if item["event_type"] == "TASK_RESULT_INVALIDATED"
        )
        invalidated_observation = invalidated_event["payload"]["observation"]
        self.assertEqual(invalidated_observation["operation"], "RERUN")
        self.assertTrue(invalidated_observation["matched_changed_facts"])
        self.assertEqual(
            invalidated_observation["after_result_version"],
            invalidated_observation["before_result_version"] + 1,
        )
        service.close()

    def test_repeated_idempotency_event_is_not_applied_twice(self):
        service = memory_service()
        state = service.run(new_demo(service).case_id)
        request = state.pending_human_request
        command = {
            "event_id": "H-IDEMPOTENT", "action": "CONFIRM_OWNER",
            "task_id": request["task_id"], "page_id": "PAGE-021", "person_id": request["person_id"],
        }
        first = service.supplement(state.case_id, command)
        second = service.supplement(state.case_id, command)
        self.assertEqual(first.case_version, second.case_version)
        service.close()

    def test_requirement_evidence_rag_is_not_called_before_completeness_problem(self):
        service = memory_service()
        state = new_demo(service)
        trace = service.rag_trace(state.case_id)
        self.assertEqual(trace["trigger"], "NOT_TRIGGERED")
        self.assertEqual(trace["final_requirements"], [])
        state = service.run(state.case_id)
        self.assertEqual(service.rag_trace(state.case_id)["trigger"], "NOT_TRIGGERED")
        request = state.pending_human_request
        state = service.supplement(state.case_id, {
            "event_id": "H-RAG-OWNER", "action": "CONFIRM_OWNER",
            "task_id": request["task_id"], "page_id": "PAGE-021", "person_id": request["person_id"],
        })
        trace = service.rag_trace(state.case_id)
        self.assertEqual(trace["trigger"], "COMPLETENESS_PROBLEM")
        self.assertIn("REQ-SPOUSE-CONSENT", trace["final_requirements"])
        retired = next(item for item in trace["candidates"] if item["requirement_id"] == "REQ-OLD-SPOUSE-CONSENT")
        other_product = next(item for item in trace["candidates"] if item["requirement_id"] == "REQ-CONSUMER-LOAN-ID")
        self.assertIn("VERSION_INACTIVE", retired["filter_reasons"])
        self.assertIn("PRODUCT_MISMATCH", other_product["filter_reasons"])
        service.close()

    def test_checkpoint_interrupt_survives_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            repository_path = Path(directory) / "cases.sqlite3"
            checkpoint_path = Path(directory) / "checkpoints.sqlite3"
            first = AuditService(
                SQLiteCaseRepository(repository_path),
                checkpointer=sqlite_checkpointer(checkpoint_path),
                pipeline_dependencies=build_demo_pipeline_dependencies(),
            )
            paused = first.run(new_demo(first).case_id)
            request = paused.pending_human_request
            first.close()

            second = AuditService(
                SQLiteCaseRepository(repository_path),
                checkpointer=sqlite_checkpointer(checkpoint_path),
                pipeline_dependencies=build_demo_pipeline_dependencies(),
            )
            resumed = second.supplement(paused.case_id, {
                "event_id": "H-RESTART", "action": "CONFIRM_OWNER",
                "task_id": request["task_id"], "page_id": "PAGE-021", "person_id": request["person_id"],
            })
            self.assertEqual(resumed.thread_id, paused.thread_id)
            self.assertEqual(resumed.pending_human_request["action"], "REQUEST_SUPPLEMENT")
            second.close()


if __name__ == "__main__":
    unittest.main()
