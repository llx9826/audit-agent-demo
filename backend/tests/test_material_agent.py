import json
from tempfile import TemporaryDirectory
import unittest

from app.agents.contracts import MaterialAuditAssignment, MaterialCandidate, MaterialIssue
from app.agents.material_audit import MaterialAuditAgent
from app.providers.decision_adapters import (
    ModelAdapterError,
    QwenVllmAdapter,
)
from app.prompting import PromptRegistry
from demo.providers import DemoMaterialAuditAdapter


def assignment() -> MaterialAuditAssignment:
    return MaterialAuditAssignment(
        assignment_id="MA-TEST-C1-P1",
        case_id="CASE-TEST",
        thread_id="THREAD-TEST",
        case_version=1,
        plan_version=1,
        objective="为无法确定的材料齐套问题选择受控人工动作",
        issue=MaterialIssue(
            task_id="TASK-SPOUSE-MARRIAGE-P02",
            issue_type="OWNER_AMBIGUOUS",
            person_id="P02",
            material_type="marriage_certificate",
            candidate_page_ids=["PAGE-021"],
            evidence_refs=["E-PAGE-021"],
            confidence=.61,
        ),
        candidates=[
            MaterialCandidate(
                candidate_id="CAND-P01", page_ids=["PAGE-021"],
                proposed_person_id="P01", proposed_material_type="marriage_certificate",
                proposed_requirement_id="REQ-SPOUSE-MARRIAGE", evidence_refs=["E-PAGE-021"],
                workflow_score=.61,
            ),
            MaterialCandidate(
                candidate_id="CAND-P02", page_ids=["PAGE-021"],
                proposed_person_id="P02", proposed_material_type="marriage_certificate",
                proposed_requirement_id="REQ-SPOUSE-MARRIAGE", evidence_refs=["E-PAGE-021"],
                workflow_score=.61,
            ),
        ],
        allowed_actions=["APPLY_CANDIDATE", "REQUEST_HUMAN", "REQUEST_RECOVERY"],
    )


class MaterialAuditAgentTests(unittest.TestCase):
    def test_prompt_registry_fails_at_composition_when_current_assets_are_missing(self):
        with TemporaryDirectory() as empty_root:
            with self.assertRaises(FileNotFoundError):
                PromptRegistry(empty_root)

    def test_deterministic_adapter_prioritizes_owner_confirmation(self):
        adapter = DemoMaterialAuditAdapter()
        run = MaterialAuditAgent(model_adapter=adapter).decide(assignment())

        self.assertEqual(run.prompt.prompt_id, "material-audit-candidate-resolution")
        self.assertEqual(run.prompt.version, "4.0.0")
        self.assertEqual(run.decision.action, "REQUEST_HUMAN")
        self.assertEqual(run.decision.evidence_refs, ["E-PAGE-021"])
        self.assertTrue(run.decision.requires_human)
        self.assertEqual(len(adapter.invocations), 1)

    def test_invalid_model_output_falls_back_to_safe_human_action(self):
        class BrokenAdapter:
            def decide_material(self, **_kwargs):
                raise ModelAdapterError("invalid response")

        run = MaterialAuditAgent(model_adapter=BrokenAdapter()).decide(assignment())

        self.assertEqual(run.decision.action, "REQUEST_HUMAN")
        self.assertEqual(run.decision.reason_code, "INVALID_STRUCTURED_OUTPUT")

    def test_qwen_vllm_uses_material_decision_json_schema(self):
        captured = {}

        def transport(http_request, timeout):
            captured["body"] = json.loads(http_request.data)
            captured["timeout"] = timeout
            return json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "action": "REQUEST_HUMAN",
                            "selected_candidate_id": "CAND-P02",
                            "reason_code": "OWNER_EVIDENCE_AMBIGUOUS",
                            "rationale_summary": "候选影像存在，但材料所属人需要人工确认。",
                            "evidence_refs": ["E-PAGE-021"],
                            "confidence": .78,
                            "requires_human": True,
                        }, ensure_ascii=False),
                    }
                }]
            }, ensure_ascii=False).encode()

        adapter = QwenVllmAdapter(
            base_url="http://qwen.local/v1",
            model="Qwen/Qwen3-8B",
            timeout=5,
            transport=transport,
        )
        agent = MaterialAuditAgent(model_adapter=adapter)
        run = agent.decide(assignment())

        schema = captured["body"]["response_format"]["json_schema"]
        self.assertEqual(schema["name"], "material_audit_decision")
        self.assertTrue(schema["strict"])
        self.assertEqual(captured["timeout"], 5)
        self.assertEqual(run.decision.action, "REQUEST_HUMAN")


if __name__ == "__main__":
    unittest.main()
