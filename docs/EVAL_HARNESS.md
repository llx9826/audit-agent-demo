# ARGUS Eval Harness 与人工反馈闭环

## 目标

评测对象不是一段最终文案，而是“模型 + Prompt + Tool Policy + Workflow + Gate + 持久化”的完整 Agent Harness。发布前必须回答三个问题：

1. 同一 Case 重跑是否稳定；
2. 最终数据库状态是否真的正确；
3. Challenger 相比已批准 Baseline 是否出现统计显著回归。

人工反馈不会直接在线训练模型。它先沉淀成可审计数据，再进入离线调参、Shadow Replay 和发布门禁。

## 执行链

```text
Golden Cases
  → 3 Trials / Case
  → Trial Artifact（Outcome + Trajectory + Scores）
  → Final DB Outcome Grader
  → Paired Shadow Replay
  → Paired Bootstrap 95% CI
  → Pass / Block
```

当前确定性门禁覆盖 19 个 Case，每个 Case 3 个 Trial，共 57 个 Trial。指标包括：

- `passed`：逐 Case 确定性断言的平均通过率；
- `strict_case_pass_rate`：一个 Case 的所有 Trial 是否全部通过；
- `outcome_stability`：同一 Case 多 Trial 最终 Outcome 的众数比例；
- `changed_outcome_count`：Shadow 与 Baseline 的最终 Outcome 差异数；
- Bootstrap `mean_delta / ci_lower / ci_upper`：以 Case 为单位的配对差值置信区间。

当前提交 Baseline：`passed=1.0`、`strict_case_pass_rate=1.0`、`outcome_stability=1.0`。

## Final DB Outcome

端到端 Trial 使用临时 SQLite Case Store 和 LangGraph Checkpointer 跑完三次人工命令。随后关闭 `AuditService` 和数据库连接，重新打开 SQLite，再读取：

- `status / completeness_status`；
- 所有材料 Task 的最终状态；
- `COMPLETENESS_VALIDATED` 业务结果；
- `RUN_COMPLETED` 持久终态；
- `credit_decision=OUT_OF_SCOPE` 安全边界。

因此“Agent 输出完成了”不会被当作完成；只有数据库 Outcome 满足约束才得分。

## Shadow Replay

Baseline 和 Challenger 按 `(case_id, trial_index)` 配对。评测输入、封闭候选和 Demo Tool Observation 都是冻结数据，Shadow 不访问真实银行后端，也不写生产命名空间。

配对后记录：

- Outcome 是否改变；
- 每项指标的逐 Trial 差值；
- 所有配对 Trial 的平均差值。

## Bootstrap Gate

Bootstrap 的重采样单位是 Case，而不是把同一 Case 的多个 Trial 当作独立样本。门禁执行 2,000 次成对重采样，并计算 95% 置信区间；只有差值置信区间上界仍低于允许 Margin 时，才判定为统计显著回归。

同时保留 1.0 的确定性质量下限，用于立即拦截结构化合同、权限边界、Final Outcome 等不可妥协的失败。

## 人工反馈闭环

事件链：

```text
AUDIT_CANDIDATES_BUILT
  → AUDIT_DECISION_PROPOSED
  → HUMAN_DECISION_APPLIED
```

人工命令记录：

- `selected_candidate_id`；
- `reason_code`；
- `operator_id`；
- Case/Plan 版本及完整候选集。

事件投影生成三类数据：

1. Candidate Impression：候选曝光、排序位置、分数与特征；
2. Human Feedback：人工动作、选择结果、原因和审核员；
3. Hard Case：`id/input/expected/meta` JSONL，可加入下一版 Golden Set。

这套数据未来可以支持 Learning-to-Rank 和置信度校准，但当前实现只负责高质量采集与评测沉淀，不伪装成已经上线的自动学习系统。

## 演示命令

```bash
# 57 Trial + Final DB Outcome + Shadow + Bootstrap Gate
make agent-eval

# 仅在人工确认 Challenger 更好后更新已批准 Baseline
make agent-eval-baseline

# 从本地持久事件导出 Hard Case
make feedback-export
```

完整报告写入 `.data/eval-reports/agent_deterministic_harness.json`，已批准基线位于 `backend/evals/agent_deterministic_baseline.json`。

单 Case 数据闭环演示：

```text
GET /api/cases/{case_id}/feedback
```

## 面试表达

> 我们没有只检查 Agent 最终回复，而是把每个 Case 做多 Trial，记录轨迹并关闭、重开 SQLite 后校验最终数据库 Outcome。新版本在冻结 Observation 上以 Shadow 模式运行，与已批准 Baseline 按 Case/Trial 配对；最后用成对 Bootstrap 置信区间阻断统计显著回归。人工确认同时沉淀候选曝光、选择结果和原因码，可自动形成下一版 Hard Case，但不会绕过离线评测直接更新生产模型。

