import unittest

from app.evaluation import (
    TrialArtifact,
    paired_bootstrap_gate,
    project_feedback,
    shadow_replay_report,
    summarize_trial_artifacts,
)


class EvaluationGateTests(unittest.TestCase):
    def test_multi_trial_reports_strict_reliability_and_outcome_stability(self):
        artifacts = [
            TrialArtifact(
                case_id="C-1",
                trial_index=index,
                variant="challenger",
                final_db_outcome={"status": status},
                trajectory=[],
                scores={"passed": score},
            )
            for index, (status, score) in enumerate(
                [("COMPLETE", 1.0), ("COMPLETE", 1.0), ("FAILED", 0.0)],
                start=1,
            )
        ]

        report = summarize_trial_artifacts(artifacts, variant="challenger")

        self.assertAlmostEqual(report["metrics"]["passed"], 2 / 3)
        self.assertAlmostEqual(report["metrics"]["outcome_stability"], 2 / 3)
        self.assertEqual(report["metrics"]["strict_case_pass_rate"], 0.0)

    def test_shadow_replay_pairs_the_same_case_and_trial(self):
        baseline = [{
            "case_id": "C-1", "trial_index": 1,
            "final_db_outcome": {"status": "COMPLETE"}, "scores": {"passed": 1.0},
        }]
        challenger = [{
            "case_id": "C-1", "trial_index": 1,
            "final_db_outcome": {"status": "FAILED"}, "scores": {"passed": 0.0},
        }]

        report = shadow_replay_report(
            baseline_trials=baseline,
            challenger_trials=challenger,
            metric_names=["passed"],
        )

        self.assertEqual(report["paired_trial_count"], 1)
        self.assertEqual(report["changed_outcome_count"], 1)
        self.assertEqual(report["mean_metric_deltas"]["passed"], -1.0)

    def test_human_confirmation_projects_training_feedback_and_hard_case(self):
        events = [
            {
                "seq": 1, "event_id": "EV-1", "case_id": "CASE-1",
                "event_type": "AUDIT_CANDIDATES_BUILT",
                "payload": {"task_id": "TASK-1", "observation": {"candidates": [
                    {
                        "candidate_id": "C-1", "page_ids": ["PAGE-1"],
                        "proposed_person_id": "P-1", "proposed_material_type": "id_card",
                        "proposed_requirement_id": "REQ-1", "workflow_score": .8,
                        "evidence_refs": ["E-1"],
                    },
                ]}},
            },
            {
                "seq": 2, "event_id": "EV-2", "case_id": "CASE-1",
                "event_type": "AUDIT_DECISION_PROPOSED",
                "payload": {"task_id": "TASK-1", "observation": {
                    "action": "REQUEST_HUMAN", "selected_candidate_id": "C-1",
                }},
            },
            {
                "seq": 3, "event_id": "EV-3", "case_id": "CASE-1",
                "event_type": "HUMAN_DECISION_APPLIED",
                "payload": {"task_id": "TASK-1", "action": "CONFIRM_OWNER", "observation": {
                    "action": "CONFIRM_OWNER", "selected_candidate_id": "C-1",
                    "reason_code": "HUMAN_CONFIRMED_OWNER", "operator_id": "reviewer-1",
                }},
            },
        ]

        projection = project_feedback(events)

        self.assertEqual(len(projection["candidate_impressions"]), 1)
        self.assertEqual(projection["human_feedback"][0]["selected_candidate_id"], "C-1")
        self.assertEqual(projection["hard_cases"][0]["expected"]["selected_candidate_id"], "C-1")

    def test_paired_bootstrap_blocks_consistent_case_level_regression(self):
        baseline = {f"C-{index}": {"mrr": .9} for index in range(20)}
        current = {f"C-{index}": {"mrr": .6} for index in range(20)}

        report = paired_bootstrap_gate(
            current=current,
            baseline=baseline,
            metric_names=["mrr"],
            samples=500,
        )

        self.assertFalse(report["passed"])
        self.assertEqual(report["regressed"], ["mrr"])

    def test_paired_bootstrap_ignores_non_overlapping_new_cases(self):
        baseline = {"C-1": {"mrr": .8}}
        current = {"C-1": {"mrr": .8}, "C-NEW": {"mrr": 0.0}}

        report = paired_bootstrap_gate(
            current=current,
            baseline=baseline,
            metric_names=["mrr"],
            samples=100,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["paired_case_count"], 1)


if __name__ == "__main__":
    unittest.main()
