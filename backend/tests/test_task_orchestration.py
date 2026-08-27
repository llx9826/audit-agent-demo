import unittest

from app.orchestration.audit_pipeline import describe_audit_pipeline
from app.orchestration.stages.matching import (
    dispatch_ready_tasks,
    match_materials,
    match_task_worker,
    resolve_ready_tasks,
)


def base_state():
    return {
        "case_id": "CASE-1",
        "thread_id": "THREAD-1",
        "case_version": 2,
        "plan_version": 3,
        "pages": [{
            "page_id": "PAGE-1",
            "material_type": "identity_document",
            "owner_person_id": "P01",
            "status": "VERIFIED",
            "confidence": 0.98,
            "evidence_refs": ["EV-1"],
        }],
        "requirements": [{
            "requirement_id": "REQ-ID",
            "required_pages": 1,
        }],
        "audit_plan": [{
            "task_id": "TASK-ID-P01",
            "task_type": "required_material",
            "status": "PENDING",
            "depends_on": ["person:P01", "material:identity_document"],
            "fact_dependencies": ["person:P01", "material:identity_document"],
            "task_dependencies": [],
            "conflict_keys": ["material_slot:P01:identity_document"],
            "requirement_refs": ["REQ-ID"],
            "executor": "MATERIAL_MATCH_WORKER",
            "execution_group": "MATERIAL_MATCH",
            "result_version": 0,
            "requirement_id": "REQ-ID",
            "person_id": "P01",
            "person_role": "BORROWER",
            "material_type": "identity_document",
            "matched_page_ids": [],
            "evidence_refs": [],
            "result": None,
        }],
        "material_matches": [],
        "pending_events": [],
    }


class TaskOrchestrationTests(unittest.TestCase):
    def test_compiled_graph_keeps_association_recovery_and_worker_handoffs(self):
        """主图快照直接由编译 Graph 反射，防止文档与真实 Edge 漂移。"""

        snapshot = describe_audit_pipeline()
        stages = set(snapshot["stages"])
        edges = {(item["source"], item["target"]) for item in snapshot["edges"]}
        self.assertTrue({
            "extract_association_page",
            "case_association_agent",
            "prepare_association_recovery",
            "exception_recovery_agent",
            "exception_result_gate",
            "resolve_requirements",
            "resolve_ready_tasks",
            "match_task_worker",
            "match_materials",
        }.issubset(stages))
        self.assertIn(("case_association_agent", "association_gate"), edges)
        self.assertIn(("prepare_association_recovery", "exception_recovery_agent"), edges)
        self.assertIn(("prepare_matcher_recovery", "exception_recovery_agent"), edges)
        self.assertIn(("prepare_material_recovery", "exception_recovery_agent"), edges)
        self.assertIn(("exception_recovery_agent", "exception_result_gate"), edges)
        self.assertIn(("exception_result_gate", "select_association_pages"), edges)
        self.assertIn(("exception_result_gate", "resolve_ready_tasks"), edges)
        self.assertIn(("exception_result_gate", "prepare_human"), edges)
        self.assertIn(("match_task_worker", "match_materials"), edges)

    def test_ready_batch_sends_minimal_task_context_then_fan_in_commits(self):
        state = base_state()
        state.update(resolve_ready_tasks(state))
        sends = dispatch_ready_tasks(state)

        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0].node, "match_task_worker")
        self.assertNotIn("business_fields", sends[0].arg)
        self.assertNotIn("audit_plan", sends[0].arg)

        worker_patch = match_task_worker(sends[0].arg)
        state["task_worker_results"] = worker_patch["task_worker_results"]
        committed = match_materials(state)
        task = committed["audit_plan"][0]
        self.assertEqual(task["status"], "MATCHED")
        self.assertEqual(task["result_version"], 1)
        self.assertEqual(task["result"]["result_version"], 1)
        self.assertEqual(committed["material_matches"][0]["page_ids"], ["PAGE-1"])

    def test_fan_in_rejects_result_from_stale_case_version(self):
        state = base_state()
        state.update(resolve_ready_tasks(state))
        send = dispatch_ready_tasks(state)[0]
        worker = match_task_worker(send.arg)["task_worker_results"][0]
        worker["expected_case_version"] = 1
        state["task_worker_results"] = [worker]

        committed = match_materials(state)

        self.assertEqual(committed["audit_plan"][0]["status"], "INVALIDATED")
        event_types = [event["event_type"] for event in committed["pending_events"]]
        self.assertIn("STALE_TASK_RESULT_REJECTED", event_types)
        self.assertIn("TASK_FAN_IN_COMMITTED", event_types)


if __name__ == "__main__":
    unittest.main()
