"""可复现的 Agent Eval Harness：多 Trial、Outcome 稳定性和 Shadow 对比。"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
from statistics import mean
from typing import Any, Callable, Iterable, Mapping, Sequence


ScoreMap = dict[str, float]


@dataclass(slots=True)
class TrialArtifact:
    """一次随机试验的完整、可序列化产物。"""

    case_id: str
    trial_index: int
    variant: str
    final_db_outcome: dict[str, Any]
    trajectory: list[dict[str, Any]]
    scores: ScoreMap
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TrialExecutor = Callable[[str, int, str], TrialArtifact]


def _outcome_key(outcome: Mapping[str, Any]) -> str:
    """对 Outcome 做稳定序列化，用于衡量同 Case 多 Trial 的一致性。"""

    return json.dumps(outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_multi_trial(
    *,
    case_ids: Sequence[str],
    trials: int,
    variant: str,
    execute: TrialExecutor,
) -> dict[str, Any]:
    """对每个 Case 重复执行，并同时报告平均质量与最差 Trial 可靠性。"""

    if trials < 1:
        raise ValueError("trials must be at least 1")
    if not case_ids:
        raise ValueError("multi-trial eval requires at least one case")

    artifacts = [
        execute(case_id, trial_index, variant)
        for case_id in case_ids
        for trial_index in range(1, trials + 1)
    ]
    return summarize_trial_artifacts(artifacts, variant=variant)


def summarize_trial_artifacts(
    artifacts: Sequence[TrialArtifact],
    *,
    variant: str,
) -> dict[str, Any]:
    """汇总已经批量执行的 Trial，便于一次复用同一模型/工具实例。"""

    if not artifacts:
        raise ValueError("at least one trial artifact is required")
    case_ids = sorted({artifact.case_id for artifact in artifacts})
    trial_counts = Counter(artifact.case_id for artifact in artifacts)
    if len(set(trial_counts.values())) != 1:
        raise ValueError("every case must have the same number of trials")
    trials = next(iter(trial_counts.values()))
    metric_names = sorted({name for artifact in artifacts for name in artifact.scores})
    per_case: dict[str, dict[str, float]] = {}
    outcome_stability: dict[str, float] = {}
    strict_pass: dict[str, float] = {}
    for case_id in case_ids:
        case_trials = [artifact for artifact in artifacts if artifact.case_id == case_id]
        per_case[case_id] = {
            name: mean(float(item.scores.get(name, 0.0)) for item in case_trials)
            for name in metric_names
        }
        counts = Counter(_outcome_key(item.final_db_outcome) for item in case_trials)
        outcome_stability[case_id] = max(counts.values()) / len(case_trials)
        strict_pass[case_id] = float(all(item.scores.get("passed", 0.0) == 1.0 for item in case_trials))

    aggregate = {
        name: mean(row[name] for row in per_case.values())
        for name in metric_names
    }
    aggregate["outcome_stability"] = mean(outcome_stability.values())
    aggregate["strict_case_pass_rate"] = mean(strict_pass.values())
    return {
        "variant": variant,
        "trials_per_case": trials,
        "case_count": len(case_ids),
        "trial_count": len(artifacts),
        "metrics": aggregate,
        "case_metrics": per_case,
        "outcome_stability": outcome_stability,
        "strict_case_pass": strict_pass,
        "trials": [artifact.to_dict() for artifact in artifacts],
    }


def shadow_replay_report(
    *,
    baseline_trials: Iterable[Mapping[str, Any]],
    challenger_trials: Iterable[Mapping[str, Any]],
    metric_names: Sequence[str],
) -> dict[str, Any]:
    """按 Case/Trial 配对 Shadow 与 Baseline，避免独立样本造成伪提升。"""

    def indexed(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int], Mapping[str, Any]]:
        return {
            (str(row["case_id"]), int(row["trial_index"])): row
            for row in rows
        }

    baseline = indexed(baseline_trials)
    challenger = indexed(challenger_trials)
    keys = sorted(set(baseline).intersection(challenger))
    if not keys:
        raise ValueError("shadow replay requires paired case/trial artifacts")

    pairs: list[dict[str, Any]] = []
    for key in keys:
        before = baseline[key]
        after = challenger[key]
        before_scores = before.get("scores", {})
        after_scores = after.get("scores", {})
        pairs.append({
            "case_id": key[0],
            "trial_index": key[1],
            "outcome_changed": _outcome_key(before.get("final_db_outcome", {}))
            != _outcome_key(after.get("final_db_outcome", {})),
            "metric_deltas": {
                name: float(after_scores.get(name, 0.0)) - float(before_scores.get(name, 0.0))
                for name in metric_names
            },
        })
    return {
        "mode": "SHADOW_REPLAY",
        "side_effect_policy": "RECORDED_OBSERVATIONS_ONLY",
        "paired_trial_count": len(pairs),
        "changed_outcome_count": sum(int(pair["outcome_changed"]) for pair in pairs),
        "mean_metric_deltas": {
            name: mean(pair["metric_deltas"][name] for pair in pairs)
            for name in metric_names
        },
        "pairs": pairs,
    }
