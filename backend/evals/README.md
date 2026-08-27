# RAG 回归门禁

`requirement_retrieval.jsonl` 是 50 条版本化 Golden Set，覆盖内部宅抵贷清单、公开个人经营抵押贷款材料，以及南京、北京、广州、深圳、武汉、重庆、苏州、天津、陕西等公积金贷款公开材料。每条记录保留 Query、适用范围、Metadata Filter 和相关 Requirement ID。

运行：

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/evaluate_requirement_rag.py
```

合并门禁同时使用两层约束：绝对阈值为 HitRate@5 ≥ 0.95、Recall@5 ≥ 0.95、MRR ≥ 0.60、NDCG@5 ≥ 0.75；并与已提交的逐 Case 基线做 2,000 次成对 Bootstrap，置信区间确认显著回归时阻断。评测会把只读 Milvus Lite 索引复制到临时快照，避免与运行中的 API 争用数据库 `LOCK`。

`case_association_golden.jsonl`、`material_audit_golden.jsonl` 与 `exception_candidate_golden.jsonl` 对 Agent 的结构化动作、封闭候选、Evidence 绑定和 Tool Policy 做轨迹门禁；`knowledge_answer_golden.jsonl` 对引用、拒答和 Faithfulness 做真实模型评测。

Agent Harness 默认对每个 Case 执行 3 个 Trial。确定性跑道还会完整跑通一次材料审核，把服务和 Checkpointer 关闭后重新打开 SQLite，再从持久化 `CaseState + case_events` 投影 `Final DB Outcome`，而不是相信 Agent 的完成文案。报告同时给出：

- `passed`：所有确定性 Outcome/轨迹断言的平均通过率；
- `strict_case_pass_rate`：同一个 Case 的所有 Trial 是否全部成功；
- `outcome_stability`：同一个 Case 多 Trial 最终 Outcome 的众数比例；
- `shadow_replay`：按 `case_id + trial_index` 与已提交 Baseline 配对，且声明只使用冻结输入/已记录 Observation；
- `regression`：以 Case 为采样单位执行 2,000 次成对 Bootstrap，仅在置信区间确认回归时阻断。

运行：

```bash
make agent-eval       # 确定性回归，适合本地/CI
make agent-live-eval  # 当前模型真实结构化决策
make rag-answer-eval  # 真实检索、Grounding 与 Judge
```

只有确认新版本确实更好时才更新 Baseline：

```bash
make agent-eval-baseline
```

## 人工反馈数据闭环

`AUDIT_CANDIDATES_BUILT → AUDIT_DECISION_PROPOSED → HUMAN_DECISION_APPLIED` 事件可以重建：

- Candidate Impression：候选全集、排序位置、原始分数和特征；
- Human Feedback：选择的候选、原因码、审核员和动作；
- Hard Case：满足 `id/input/expected/meta` 的 JSONL 评测样本。

API `GET /api/cases/{case_id}/feedback` 用于演示单 Case 的反馈投影；批量导出运行：

```bash
make feedback-export
```

默认写入 `.data/feedback/material_hard_cases.jsonl`。人工数据不会直接在线更新模型；必须先经过离线训练/调参、Shadow Replay 和 Bootstrap Gate。
