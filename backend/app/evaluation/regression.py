"""基于逐 Case 指标的成对 Bootstrap 回归门禁。"""
from __future__ import annotations

from random import Random
from statistics import mean
from typing import Mapping, Sequence


def aggregate_case_metrics(
    rows: Sequence[Mapping[str, float]],
    metric_names: Sequence[str],
) -> dict[str, float]:
    """聚合逐 Case 指标；Case 是 Bootstrap 的最小重采样单位。"""

    if not rows:
        return {name: 0.0 for name in metric_names}
    return {name: mean(float(row[name]) for row in rows) for name in metric_names}


def paired_bootstrap_gate(
    *,
    current: Mapping[str, Mapping[str, float]],
    baseline: Mapping[str, Mapping[str, float]],
    metric_names: Sequence[str],
    samples: int = 2000,
    confidence: float = .95,
    margin: float = 0.0,
    seed: int = 20260817,
) -> dict:
    """判断当前结果相对同一 Case 基线是否出现统计显著回归。

    只比较两侧共有 Case，避免数据集增删被误判为模型质量变化。若差值置信区间
    上界仍低于 ``-margin``，才阻断发布。
    """

    case_ids = sorted(set(current).intersection(baseline))
    if not case_ids:
        raise ValueError("paired bootstrap requires overlapping case ids")
    rng = Random(seed)
    alpha = (1.0 - confidence) / 2.0
    report: dict[str, dict] = {}
    regressed: list[str] = []
    for metric in metric_names:
        deltas = [float(current[key][metric]) - float(baseline[key][metric]) for key in case_ids]
        sampled = sorted(
            mean(deltas[rng.randrange(len(deltas))] for _ in deltas)
            for _ in range(samples)
        )
        lower = sampled[max(0, int(alpha * samples))]
        upper = sampled[min(samples - 1, int((1.0 - alpha) * samples) - 1)]
        is_regression = upper < -margin
        if is_regression:
            regressed.append(metric)
        report[metric] = {
            "mean_delta": mean(deltas),
            "ci_lower": lower,
            "ci_upper": upper,
            "regressed": is_regression,
        }
    return {
        "paired_case_count": len(case_ids),
        "confidence": confidence,
        "samples": samples,
        "margin": margin,
        "metrics": report,
        "regressed": regressed,
        "passed": not regressed,
    }
