import unittest

from app.domain.models import AuditResult, RequiredMaterialTask
from app.planning.planner import build_plan, impacted_task_ids, selective_replan


REQUIREMENTS = [
    {"requirement_id": "REQ-BORROWER-ID", "person_role": "BORROWER", "material_type": "identity_document"},
    {"requirement_id": "REQ-SPOUSE-CONSENT", "person_role": "SPOUSE", "material_type": "spouse_consent"},
]
PERSONS = [
    {"person_id": "P01", "roles": ["BORROWER"]},
    {"person_id": "P02", "roles": ["SPOUSE", "MORTGAGOR"]},
]


class PlanningTests(unittest.TestCase):
    def test_requirement_person_pairs_compile_deterministically(self):
        tasks = build_plan(REQUIREMENTS, PERSONS)
        self.assertEqual(
            [task.task_id for task in tasks],
            ["TASK-BORROWER-ID-P01", "TASK-SPOUSE-CONSENT-P02"],
        )
        self.assertEqual(tasks[1].depends_on[-1], "material:spouse_consent")
        self.assertEqual(tasks[1].task_dependencies, [])
        self.assertEqual(
            tasks[1].conflict_keys,
            ["material_slot:P02:spouse_consent"],
        )

    def test_changed_material_only_impacts_dependent_task(self):
        tasks = build_plan(REQUIREMENTS, PERSONS)
        self.assertEqual(
            impacted_task_ids(tasks, ["material:spouse_consent"]),
            ["TASK-SPOUSE-CONSENT-P02"],
        )

    def test_page_change_impacts_task_that_previously_matched_page(self):
        tasks = build_plan(REQUIREMENTS, PERSONS)
        tasks[0].matched_page_ids = ["PAGE-001"]
        self.assertEqual(impacted_task_ids(tasks, ["page:PAGE-001"]), ["TASK-BORROWER-ID-P01"])

    def test_selective_replan_reuses_unaffected_result(self):
        tasks = build_plan(REQUIREMENTS, PERSONS)
        tasks[0].status = "MATCHED"
        tasks[0].result = AuditResult(
            tasks[0].task_id, "PASS", "matched", .99, ["EV-1"], ["REQ-BORROWER-ID"], 1, 1,
        )
        tasks[1].status = "MISSING"
        revised = selective_replan(tasks, ["material:spouse_consent"])
        by_id: dict[str, RequiredMaterialTask] = {task.task_id: task for task in revised}
        self.assertEqual(by_id["TASK-BORROWER-ID-P01"].status, "MATCHED")
        self.assertEqual(by_id["TASK-BORROWER-ID-P01"].result.conclusion, "matched")
        self.assertEqual(by_id["TASK-SPOUSE-CONSENT-P02"].status, "DIRTY")


if __name__ == "__main__":
    unittest.main()
