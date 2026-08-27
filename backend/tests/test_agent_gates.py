"""两个决策 Agent 的候选闭包与写入 Gate 回归测试。"""
from __future__ import annotations

import unittest

from app.orchestration.stages.association import apply_human as apply_association_human
from app.orchestration.stages.association import gate as association_gate
from app.orchestration.stages.review import _build_candidates, plan_gate


class DecisionAgentGateTests(unittest.TestCase):
    def test_human_confirmation_rebases_assignment_before_returning_to_gate(self):
        """回归：人工确认升级 Case 后不能把同一候选误判为 stale。"""

        candidates = [
            {
                "candidate_id": "PERSON-P1", "candidate_type": "PERSON_ENTITY",
                "person_id": "P1", "display_name": "张三", "mention_ids": ["MENTION-1"],
                "evidence_refs": ["EV-1"], "workflow_score": 0.98,
            },
            {
                "candidate_id": "ROLE-P1", "candidate_type": "PERSON_ROLE",
                "person_id": "P1", "display_name": "张三", "role": "BORROWER",
                "evidence_refs": ["EV-1"], "workflow_score": 0.98,
            },
            {
                "candidate_id": "OWNER-P1", "candidate_type": "MATERIAL_OWNER",
                "person_id": "P1", "display_name": "张三", "page_id": "PAGE-1",
                "evidence_refs": ["EV-1"], "workflow_score": 0.98,
            },
        ]
        selected_ids = [item["candidate_id"] for item in candidates]
        state = {
            "case_id": "CASE-1", "thread_id": "THREAD-1", "case_version": 1,
            "pages": [{"page_id": "PAGE-1"}], "persons": [], "human_tasks": [],
            "association_assignment": {
                "case_id": "CASE-1", "thread_id": "THREAD-1", "case_version": 1,
                "assignment_id": "ASSOC-1", "allowed_actions": ["APPLY_CANDIDATES"],
                "candidates": candidates,
            },
            "pending_human_request": {
                "task_id": "ASSOC-1", "action": "CONFIRM_ASSOCIATION",
            },
            "resume_event": {
                "event_id": "HUMAN-1", "task_id": "ASSOC-1",
                "action": "CONFIRM_ASSOCIATION", "selected_candidate_ids": selected_ids,
            },
        }

        human_patch = apply_association_human(state)
        resumed_state = {**state, **human_patch}
        gate_patch = association_gate(resumed_state)

        self.assertEqual(human_patch["case_version"], 2)
        self.assertEqual(human_patch["association_assignment"]["case_version"], 2)
        self.assertTrue(gate_patch["association_gate"]["accepted"])
        self.assertEqual(gate_patch["association_gate"]["outcome"], "CONFIRMED")
        self.assertIsNone(gate_patch["pending_human_request"])

    def test_association_gate_rejects_stale_assignment(self):
        state = {
            "case_id": "CASE-1",
            "thread_id": "THREAD-1",
            "case_version": 2,
            "pages": [],
            "persons": [],
            "human_tasks": [],
            "association_assignment": {
                "case_id": "CASE-1",
                "thread_id": "THREAD-1",
                "case_version": 1,
                "assignment_id": "ASSOC-1",
                "allowed_actions": ["APPLY_CANDIDATES"],
                "candidates": [
                    {"candidate_id": "PERSON-P1", "candidate_type": "PERSON_ENTITY", "person_id": "P1", "display_name": "张三", "evidence_refs": ["EV-1"]},
                    {"candidate_id": "ROLE-P1", "candidate_type": "PERSON_ROLE", "person_id": "P1", "display_name": "张三", "role": "BORROWER", "evidence_refs": ["EV-1"]},
                ],
            },
            "association_decision": {
                "action": "APPLY_CANDIDATES",
                "selected_candidate_ids": ["PERSON-P1", "ROLE-P1"],
                "evidence_refs": ["EV-1"],
                "rationale_summary": "证据一致",
            },
        }

        patch = association_gate(state)

        self.assertFalse(patch["association_gate"]["accepted"])
        self.assertEqual(patch["association_gate"]["outcome"], "HITL_REQUIRED")

    def test_association_gate_rejects_owner_for_unselected_person(self):
        state = {
            "case_id": "CASE-1", "thread_id": "THREAD-1", "case_version": 1,
            "pages": [{"page_id": "PAGE-1"}], "persons": [], "human_tasks": [],
            "association_assignment": {
                "case_id": "CASE-1", "thread_id": "THREAD-1", "case_version": 1,
                "assignment_id": "ASSOC-1", "allowed_actions": ["APPLY_CANDIDATES"],
                "candidates": [
                    {"candidate_id": "PERSON-P1", "candidate_type": "PERSON_ENTITY", "person_id": "P1", "display_name": "张三", "mention_ids": [], "evidence_refs": ["EV-1"], "workflow_score": 0.95},
                    {"candidate_id": "ROLE-P1", "candidate_type": "PERSON_ROLE", "person_id": "P1", "display_name": "张三", "role": "BORROWER", "evidence_refs": ["EV-1"], "workflow_score": 0.95},
                    {"candidate_id": "OWNER-P2", "candidate_type": "MATERIAL_OWNER", "person_id": "P2", "display_name": "李四", "page_id": "PAGE-1", "evidence_refs": ["EV-2"], "workflow_score": 0.80},
                ],
            },
            "association_decision": {
                "action": "APPLY_CANDIDATES",
                "selected_candidate_ids": ["PERSON-P1", "ROLE-P1", "OWNER-P2"],
                "evidence_refs": ["EV-1", "EV-2"], "rationale_summary": "选择候选",
            },
        }

        patch = association_gate(state)

        self.assertFalse(patch["association_gate"]["accepted"])

    def test_material_candidates_respect_confirmed_owner_and_stable_order(self):
        state = {
            "persons": [
                {"person_id": "P1", "confirmed": True},
                {"person_id": "P2", "confirmed": True},
            ],
            "material_owner_bindings": [
                {"page_id": "PAGE-1", "person_id": "P1", "status": "CONFIRMED"},
            ],
            "audit_plan": [
                {"task_id": "TASK-P1", "person_id": "P1", "material_type": "marriage_certificate", "requirement_id": "REQ-M"},
                {"task_id": "TASK-P2", "person_id": "P2", "material_type": "marriage_certificate", "requirement_id": "REQ-M"},
            ],
        }
        task = state["audit_plan"][0]
        pages = [{
            "page_id": "PAGE-1", "confidence": 0.8, "status": "OWNER_AMBIGUOUS",
            "evidence_refs": ["EV-1"],
            "extracted_fields": {
                "owner_candidates": ["P2", "P1"],
                "candidate_material_types": ["marriage_certificate"],
                "candidate_requirement_ids": ["REQ-M"],
            },
        }]

        first, _ = _build_candidates(state, task, pages)
        second, _ = _build_candidates(state, task, list(reversed(pages)))

        self.assertEqual([item.candidate_id for item in first], [item.candidate_id for item in second])
        self.assertEqual({item.proposed_person_id for item in first}, {"P1"})

    def test_material_plan_gate_rejects_stale_assignment(self):
        candidate = {
            "candidate_id": "CAND-1", "page_ids": ["PAGE-1"],
            "proposed_person_id": "P1", "proposed_material_type": "identity_document",
            "proposed_requirement_id": "REQ-1", "proposed_bundle_id": None,
            "evidence_refs": ["EV-1"], "workflow_score": 0.9,
        }
        state = {
            "case_id": "CASE-1", "thread_id": "THREAD-1", "case_version": 2, "plan_version": 1,
            "pages": [{"page_id": "PAGE-1", "status": "OWNER_AMBIGUOUS"}],
            "persons": [{"person_id": "P1", "confirmed": True}],
            "material_owner_bindings": [], "human_tasks": [],
            "audit_plan": [{
                "task_id": "TASK-1", "status": "AMBIGUOUS", "person_id": "P1",
                "material_type": "identity_document", "requirement_id": "REQ-1",
            }],
            "audit_assignment": {
                "case_id": "CASE-1", "thread_id": "THREAD-1", "case_version": 1, "plan_version": 1,
                "issue": {"task_id": "TASK-1", "issue_type": "OWNER_AMBIGUOUS", "candidate_page_ids": ["PAGE-1"]},
                "candidates": [candidate], "allowed_actions": ["APPLY_CANDIDATE"],
            },
            "audit_decision": {
                "action": "APPLY_CANDIDATE", "selected_candidate_id": "CAND-1",
                "evidence_refs": ["EV-1"], "rationale_summary": "选择候选",
            },
        }

        patch = plan_gate(state)

        self.assertFalse(patch["audit_gate"]["accepted"])
        self.assertEqual(patch["audit_gate"]["outcome"], "REJECTED_TO_HITL")


if __name__ == "__main__":
    unittest.main()
