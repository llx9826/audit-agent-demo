# 架构演进证据映射

## V1：确定性 Workflow

### 先解决的问题

- 根据产品、渠道、地区、角色、婚姻状态和日期动态生成材料清单；
- 对明确类型、明确 Owner、页数完整的影像稳定匹配；
- 对缺件问题检索 Requirement Evidence，解释“为什么要补这份材料”；
- 状态、任务依赖、结果和 Evidence 都由 Workflow 管理。

### 为什么 V1 不够

公开材料清单要求多个角色提交相似证件，线上又以混合影像包上传。确定性规则可以识别“应当有谁的什么材料”，但无法仅凭固定字段稳定解决跨页语义和 Owner 歧义。若把全部歧义直接抛给人工，系统只能做清单工具，无法体现受控的开放判断。

### 下一版的明确触发

出现 `OWNER_AMBIGUOUS` 或 `MATERIAL_CLASS_AMBIGUOUS`：系统已经有候选页和证据，但规则无法确定哪个 Proposal 更合适，需要一个只做语义判断、没有直接写入权的 Agent。

## V2：加入 Audit Agent

### 新增的能力

- Workflow 只下发当前 Task 的最小上下文合同；
- Agent 在候选页、已有 Evidence 和受控动作内生成结构化 Proposal；
- Proposal 必须包含 `task_id`、候选页、拟议 Owner/材料类型、Evidence 和下一动作；
- Plan Gate 校验合法性、证据和版本后才允许更新状态。

### 为什么 V2 仍不够

Agent 的推理不能弥补缺失或失真的 Observation。OCR 低置信、图像旋转、页面模糊、Tool 超时等情况需要多步取得新证据。若把所有恢复工具和长历史都塞进主 Agent，会扩大 Context、暴露无关工具，并让终止条件变得不清晰。

### 下一版的明确触发

出现 `MATERIAL_IMAGE_LOW_CONFIDENCE`、`TOOL_TIMEOUT`、`STATE_NO_CHANGE` 等技术性异常：需要隔离 Context 的短生命周期子图，并用明确预算和完成条件约束循环。

## V3：加入 Exception Recovery Sub-Agent

### 真实目标拓扑

```text
prepare
  → select_tool
  → execute_tool
  → evaluate
       ├─ RUNNING + budget → select_tool
       ├─ RESOLVED → finish → 返回父 Workflow
       └─ NEED_HUMAN → finish → 返回父 Workflow / HITL
```

循环不是固定执行 OCR → VLM → Search。每轮必须把最新 `observations` 和 `normalized_values` 重新放入版本化 Prompt，由模型在本 Task 的可见工具中决定下一步。Workflow 仍负责 Allowlist、参数 Schema、Timeout/Retry、Step Budget、State Hash、Completion Condition 和最终写入。

### V3 的完成定义

- `RESOLVED`：满足可执行的完成条件，结论绑定当前运行产生的 Evidence；
- `NEED_HUMAN`：预算耗尽、重复动作无状态变化、工具失败、输出无效或证据仍不足；
- 子 Agent 只返回 typed result，不改写父 Workflow 的 Plan 和最终齐套状态。

## 前端讲解卡的统一结构

每个版本不再只放一句“触发问题”，而是固定展示四行：

1. `业务现场`：面试官看得懂的材料问题；
2. `本版解决`：当前架构解决了什么；
3. `暴露缺口`：为什么不能停在这里；
4. `架构变化`：下一版新增的节点、边界或循环。

V3 不再显示“生成下一版”，而显示完成条件和两条退出路径。

