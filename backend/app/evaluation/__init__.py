"""可复现的离线质量门禁和事件驱动反馈投影。"""

from .feedback import project_feedback
from .harness import TrialArtifact, run_multi_trial, shadow_replay_report, summarize_trial_artifacts
from .outcome import project_final_db_outcome, score_material_outcome
from .regression import aggregate_case_metrics, paired_bootstrap_gate

__all__ = [
    "TrialArtifact",
    "aggregate_case_metrics",
    "paired_bootstrap_gate",
    "project_feedback",
    "project_final_db_outcome",
    "run_multi_trial",
    "score_material_outcome",
    "shadow_replay_report",
    "summarize_trial_artifacts",
]
