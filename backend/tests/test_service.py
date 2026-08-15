import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.service import AuditService
from app.persistence.repository import InMemoryCaseRepository, SQLiteCaseRepository


class ServiceIntegrationTests(unittest.TestCase):
    def test_normal_case_completes(self):
        service = AuditService(InMemoryCaseRepository())
        state = service.new_demo("normal")
        completed = service.run(state.case_id)
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(len(completed.task_results), 10)
        self.assertIn("T12", completed.task_results)

    def test_persisted_pause_resume_replans_selectively_and_is_idempotent(self):
        service = AuditService(InMemoryCaseRepository())
        state = service.new_demo("supplement_replan")
        paused = service.run(state.case_id)
        self.assertEqual(paused.status, "WAITING_HUMAN")
        event = {"event_id": "SUP-001", "marriage_certificate": {"husband": "张三", "wife": "李四"}}
        replanned = service.supplement(state.case_id, event)
        self.assertEqual((replanned.case_version, replanned.plan_version), (2, 2))
        self.assertEqual(replanned.business_fields["relation"], "SPOUSE")
        self.assertIn("T05", replanned.invalidated_tasks)
        self.assertEqual(service.supplement(state.case_id, event).case_version, 2)
        final = service.finish(state.case_id)
        self.assertEqual(final.status, "COMPLETED")
        self.assertEqual(final.task_results["T01"].case_version, 1)
        self.assertEqual(final.task_results["T06"].case_version, 2)

    def test_checkpoint_replay_branches(self):
        service = AuditService(InMemoryCaseRepository())
        state = service.new_demo("normal")
        checkpoint = next(iter(service.repo.checkpoints[state.case_id]))
        replay = service.replay(state.case_id, checkpoint)
        self.assertNotEqual(replay.case_id, state.case_id)
        self.assertIn("REPLAY", replay.case_id)

    def test_waiting_human_case_survives_repository_restart(self):
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / "audit.sqlite3")
            first = AuditService(SQLiteCaseRepository(path))
            state = first.new_demo("supplement_replan")
            first.run(state.case_id)
            second = AuditService(SQLiteCaseRepository(path))
            restored = second.repo.get(state.case_id)
            self.assertEqual(restored.status, "WAITING_HUMAN")
            self.assertTrue(second.repo.checkpoints[state.case_id])

    def test_architecture_demo_runs_exception_hitl_replan_and_grounding(self):
        service = AuditService(InMemoryCaseRepository())
        created = service.new_demo("architecture_demo")
        self.assertEqual(created.case_id, "CASE-ZD-042")
        self.assertEqual(created.business_fields["application_amount"], 2_800_000)
        self.assertEqual(created.business_fields["loan_term_months"], 60)
        self.assertEqual(created.business_fields["company_age_months"], 10)
        self.assertEqual(created.business_fields["property_holding_months"], 8)
        self.assertTrue({"T08", "T09", "T10", "T11"}.issubset({task.task_id for task in created.audit_plan}))
        paused = service.run(created.case_id)
        self.assertEqual(paused.status, "WAITING_HUMAN")
        first_events = service.repo.event_dicts(created.case_id)
        first_types = [event["event_type"] for event in first_events]
        self.assertLess(first_types.index("ROUTE_EVALUATED"), first_types.index("HANDOFF_CREATED"))
        self.assertLess(first_types.index("HANDOFF_CREATED"), first_types.index("AGENT_TOOL_STARTED"))
        self.assertEqual(first_types.count("AGENT_TOOL_STARTED"), 3)
        self.assertEqual(first_types.count("AGENT_TOOL_FINISHED"), 3)
        self.assertIn("AGENT_RETURNED", first_types)
        self.assertIn("STATE_PATCH_APPLIED", first_types)
        self.assertEqual(first_types.count("RULE_CHECK_COMPLETED"), 4)
        self.assertLess(first_types.index("EXCEPTION_RAISED"), first_types.index("EXCEPTION_RESOLVED"))
        self.assertLess(first_types.index("EXCEPTION_RESOLVED"), first_types.index("HITL_REQUESTED"))

        handoff = next(event for event in first_events if event["event_type"] == "HANDOFF_CREATED")
        self.assertEqual(handoff["payload"]["handoff"]["allowed_tools"], [
            "ocr_retry", "vlm_extract", "document_search",
        ])
        returned = next(event for event in first_events if event["event_type"] == "AGENT_RETURNED")
        self.assertEqual(returned["payload"]["agent_result"]["stop_reason"], "COMPLETION_CONDITION_MET")
        finished = [event for event in first_events if event["event_type"] == "AGENT_TOOL_FINISHED"]
        self.assertEqual([event["payload"]["agent_step"]["remaining_budget"] for event in finished], [2, 1, 0])

        completed = service.supplement(created.case_id, {
            "event_id": "SUP-ARCH-001",
            "marriage_certificate": {"husband": "张三", "wife": "李四"},
        })
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual((completed.case_version, completed.plan_version), (2, 2))
        decisions = {item["task_id"]: item["decision"] for item in completed.replan_decisions}
        self.assertEqual(decisions, {
            "T01": "KEEP", "T02": "KEEP", "T03": "RERUN", "T04": "RESOLVED",
            "T05": "INVALIDATED_RERUN", "T06": "ADD", "T07": "ADD",
        })
        self.assertEqual(completed.task_results["T04"].conclusion, "补件验真后确定性解决")
        self.assertEqual(completed.task_results["T01"].case_version, 1)
        self.assertEqual(completed.task_results["T05"].case_version, 2)
        self.assertEqual(completed.task_results["T12"].rule_refs, ["NFRA-2026-COST-01"])
        self.assertEqual(len(completed.audit_plan), 12)
        self.assertEqual(completed.business_fields["final_decision"], "PASS_WITH_CONTROLS")
        self.assertEqual(len(completed.business_fields["controls"]), 4)

        events = service.repo.event_dicts(created.case_id)
        types = [event["event_type"] for event in events]
        for required in (
            "STATE_RECONCILED", "IMPACT_ANALYZED", "PLAN_REVISED",
            "POLICY_FILTERED", "EVIDENCE_GROUNDED", "PLAN_PATCH_APPLIED",
            "RESULT_GROUNDED", "FINAL_VALIDATED",
        ):
            self.assertIn(required, types)
        validated = next(event for event in events if event["event_type"] == "FINAL_VALIDATED")
        self.assertEqual(validated["payload"]["final_decision"], "PASS_WITH_CONTROLS")
        self.assertEqual(len(validated["payload"]["controls"]), 4)
        for event in events:
            payload = event["payload"]
            self.assertTrue({
                "node", "actor", "task_id", "action", "tool", "observation",
                "state_diff", "evidence", "case_version", "plan_version", "state_snapshot",
            }.issubset(payload))
            self.assertTrue({
                "status", "active_node", "current_task_id", "case_version", "plan_version",
                "relation", "task_statuses", "changed_facts", "dirty_tasks", "invalidated_tasks",
            }.issubset(payload["state_snapshot"]))

    def test_loop_guard_scenario_exits_to_human_without_running_third_tool(self):
        service = AuditService(InMemoryCaseRepository())
        created = service.new_demo("loop_guard")
        paused = service.run(created.case_id)

        self.assertEqual(paused.status, "WAITING_HUMAN")
        events = service.repo.event_dicts(created.case_id)
        types = [event["event_type"] for event in events]
        self.assertIn("EXCEPTION_NEEDS_HUMAN", types)
        self.assertNotIn("RELATION_REVIEWED", types)
        returned = next(event for event in events if event["event_type"] == "AGENT_RETURNED")
        self.assertEqual(returned["payload"]["agent_result"]["stop_reason"], "LOOP_GUARD")
        self.assertTrue(returned["payload"]["agent_result"]["loop_guard_triggered"])
        request = paused.pending_human_request
        self.assertEqual(request["type"], "MANUAL_IDENTITY_REVIEW_REQUIRED")
        self.assertEqual(request["reason_code"], "LOOP_GUARD")


if __name__ == "__main__":
    unittest.main()
