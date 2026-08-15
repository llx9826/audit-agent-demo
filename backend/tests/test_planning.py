import unittest

from app.domain.models import AuditResult
from app.planning.planner import build_plan, impacted_task_ids, selective_replan


class PlanningTests(unittest.TestCase):
    def test_dynamic_plan_adds_spouse_tasks(self):
        self.assertEqual([task.task_id for task in build_plan("SPOUSE")][-2:], ["T06", "T07"])

    def test_dependency_impact_is_selective(self):
        tasks = build_plan()
        self.assertEqual(impacted_task_ids(tasks, ["relation"]), ["T05"])

    def test_result_is_invalidated_but_unaffected_result_kept(self):
        tasks = build_plan()
        tasks[0].result = AuditResult("T01", "PASS", "ok", 1.0, [], [], 1, 1)
        tasks[0].status = "SUCCESS"
        tasks[4].result = AuditResult("T05", "PASS", "old", 1.0, [], [], 1, 1)
        tasks[4].status = "SUCCESS"
        revised = selective_replan(tasks, ["relation"], "SPOUSE")
        by_id = {task.task_id: task for task in revised}
        self.assertEqual(by_id["T01"].status, "SUCCESS")
        self.assertEqual(by_id["T05"].status, "INVALIDATED")
        self.assertIn("T06", by_id)

    def test_supplement_resolves_document_task_without_reexecuting_it(self):
        tasks = build_plan()
        tasks[3].result = AuditResult("T04", "PASS", "resolved by supplement", 1.0, ["E-DOC"], [], 2, 1)
        tasks[3].status = "SUCCESS"
        revised = selective_replan(
            tasks, ["marriage_documents", "relation"], "SPOUSE", resolved_task_ids={"T04"},
        )
        by_id = {task.task_id: task for task in revised}
        self.assertEqual(by_id["T03"].status, "DIRTY")
        self.assertEqual(by_id["T04"].status, "SUCCESS")
        self.assertEqual(by_id["T04"].result.conclusion, "resolved by supplement")


if __name__ == "__main__":
    unittest.main()
