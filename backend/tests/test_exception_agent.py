import unittest

from app.agents.exception_agent import ExceptionRecoveryAgent, ExceptionTask


def task() -> ExceptionTask:
    return ExceptionTask(
        exception_type="OCR_CONFLICT",
        source_task_id="T03",
        problem="OCR value conflicts with trusted identity source",
        evidence_refs=["E-DOC-01", "E-DOC-04"],
        context_refs=["DOC-01.name", "DOC-04.name"],
    )


class ExceptionRecoveryAgentTests(unittest.TestCase):
    def test_registered_allowlisted_tool_loop_resolves_on_completion_condition(self):
        result = ExceptionRecoveryAgent(max_steps=3).resolve_ocr_conflict(task())

        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.stop_reason, "COMPLETION_CONDITION_MET")
        self.assertEqual(result.steps_used, 3)
        self.assertEqual([item["tool"] for item in result.actions], [
            "ocr_retry", "vlm_extract", "document_search",
        ])
        self.assertTrue(all(item["allowed"] and item["registered"] and item["executed"] for item in result.actions))
        self.assertIn("E-VLM-01", result.evidence_refs)
        self.assertFalse(result.loop_guard_triggered)

    def test_step_budget_is_enforced_by_control_flow(self):
        result = ExceptionRecoveryAgent(max_steps=2).resolve_ocr_conflict(task())

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "BUDGET_EXHAUSTED")
        self.assertEqual(result.steps_used, 2)
        self.assertEqual(len(result.actions), 2)

    def test_repeated_action_without_state_change_triggers_loop_guard(self):
        result = ExceptionRecoveryAgent(max_steps=3).resolve_ocr_conflict(
            task(), tool_plan=["ocr_retry", "ocr_retry", "document_search"],
        )

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "LOOP_GUARD")
        self.assertTrue(result.loop_guard_triggered)
        self.assertEqual(result.steps_used, 2)
        self.assertEqual(len(result.actions), 2)

    def test_disallowed_tool_is_blocked_before_execution(self):
        result = ExceptionRecoveryAgent(
            max_steps=3,
            allowed_tools=["ocr_retry"],
        ).resolve_ocr_conflict(task(), tool_plan=["vlm_extract"])

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "TOOL_NOT_ALLOWED")
        self.assertFalse(result.actions[0]["executed"])
        self.assertFalse(result.actions[0]["allowed"])
        self.assertTrue(result.actions[0]["registered"])

    def test_allowlisted_but_unregistered_tool_is_blocked(self):
        result = ExceptionRecoveryAgent(
            max_steps=3,
            allowed_tools=["external_lookup"],
        ).resolve_ocr_conflict(task(), tool_plan=["external_lookup"])

        self.assertEqual(result.status, "NEED_HUMAN")
        self.assertEqual(result.stop_reason, "TOOL_NOT_REGISTERED")
        self.assertFalse(result.actions[0]["executed"])
        self.assertTrue(result.actions[0]["allowed"])
        self.assertFalse(result.actions[0]["registered"])


if __name__ == "__main__":
    unittest.main()
