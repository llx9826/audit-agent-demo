# 复杂信贷进件材料齐套审核 Agent：简历—后端—演示对齐基线

> 状态：长期约束文档  
> 基线日期：2026-08-16  
> 业务边界：只负责动态材料清单、材料分类归属、缺件/缺页识别、人工确认与补件恢复；不参与授信审批。

## 1. 项目主线

项目解决的问题是：一笔宅抵贷包含多个相关人员和约 200 页影像，不同产品、婚姻状态、人员角色及材料状态会改变“谁应该交什么材料”。传统静态清单容易重复要求、漏要求，也难以处理混合扫描、材料归属不明、低置信识别和跨天补件。

系统完成：

```text
相关人员与角色输入
→ 按人生成动态材料清单
→ 200+ 页材料分类与人员归属
→ Requirement ↔ Material 匹配
→ 缺件/缺页/不可识别项汇总
→ HITL 确认或发起补件
→ 补件到件后增量解析和局部重跑
→ 输出材料齐套状态
```

系统不完成：

```text
贷款风险判断
授信政策审核
偿债/额度/估值/用途审核
贷款批准或拒绝
```

## 2. 项目名称与一句话口径

推荐项目名：

> **复杂信贷进件材料齐套审核 Agent**

如面试中沿用“Audit Agent”，第一次出现时必须补充：

> 这里的 Audit 指材料齐套审核，只判断相关人员分别应交、已交和缺失的材料，不参与授信审批。

30 秒项目定义：

> 这是一个面向宅抵贷复杂进件的材料齐套审核 Agent。单笔业务约 200 页影像，借款人、抵押人、配偶和共有人等角色会动态改变每个人的材料清单。我用 LangGraph 将确定性清单和匹配流程放在 Workflow，把材料类型、人员归属等语义歧义交给 Audit Agent，把 OCR/VLM 低置信和工具失败隔离给 Exception Recovery Sub-Agent；遇到人工修正或跨天补件时，从 Checkpoint 恢复并只重跑受影响的清单任务。

## 3. 推荐简历版本

### 复杂信贷进件材料齐套审核 Agent

**项目背景：** 面向宅抵贷等复杂信贷进件场景，单笔业务包含约 200 页身份、婚姻、房产、经营主体及合同影像；应交材料会随产品、婚姻状态及借款人/抵押人/配偶/共有人角色动态变化。负责 Agent 核心技术方案设计与核心链路实现，完成按人动态清单生成、材料分类归属、缺件缺页识别、人工确认及跨天补件恢复。

**技术栈：** Python / LangGraph / LangChain / Qwen / Qwen-VL / vLLM / Milvus / BGE-M3 / BM25 / RRF / Cross-Encoder / MCP / TDSQL

- **Agent Workflow：** 基于 LangGraph 构建“确定性 Workflow + Agentic Execution”的轻量 Multi-Agent 架构。产品、角色与已确认结构化字段驱动的清单生成、精确材料匹配和集合差运算由 Rule/Workflow 执行；材料类型、所属人员和拆页归并等语义歧义交由 Audit Agent 生成结构化候选，低置信识别及工具失败委托独立 Context 的 Exception Recovery Sub-Agent 通过受控 Tool Loop 恢复。

- **动态清单与状态建模：** 将人工进件清单抽象为 `RequiredMaterialTask`，建立 Person/Role、Dependency、MaterialMatch、Result 与 Evidence；根据产品、婚姻状态、人员角色及材料状态编译按人清单，支持同一自然人多角色去重、清单版本管理、结果复用、失效判断和 Task 级增量重执行。

- **Tool / MCP 能力接入：** 对 OCR、Qwen-VL 页面分类、人员标识提取、材料搜索、重复页检测和清单检索等能力统一 Tool Schema；跨业务通用能力通过 MCP Client 接入，项目内领域能力封装为 Local Tool，并按当前 `MaterialTask` 动态暴露必要工具，减少无关 Tool 对模型决策的干扰。

- **材料清单 RAG：** 将产品操作手册和进件清单按 `Atomic Requirement` 切分，保存产品、渠道、版本、生效时间、人员角色和材料类型等 Metadata；基于 Milvus 构建 BGE-M3 Dense + BM25 Hybrid Retrieval，经 Metadata Filter、RRF 与 Cross-Encoder Rerank 检索当前人员应交材料，输出绑定 Requirement ID、版本和来源的清单项，并通过 HitRate@K、MRR、NDCG 进行离线评测。

- **Long-Horizon / HITL：** 基于 LangGraph Checkpoint + `thread_id` 持久化材料审核状态，通过 `interrupt` 支持人物角色纠正、材料类型/归属确认、缺件确认及跨天补件；人工修改或材料到件后进行增量解析、State Reconciliation 与 Changed Fact Detection，根据 Task Dependency 做 Impact Analysis 和 Selective Replan，仅重跑受影响的 Dirty Task。

- **异常恢复与 Guardrail：** 针对 OCR/VLM 低置信、材料类型不明、所属人员不明、重复/孤页、缺页、补件错配及 Tool 调用失败设计 OCR Retry、VLM 重分类、人员标识重提取和材料重检索；结合 Structured Output、Pydantic、MaxRetry、MaxStep、Duplicate Action、State No-Change、Completion Condition 与 Completeness Validator 防止循环和错误匹配，无法解决时生成结构化 HumanTask。

## 4. 原简历中需要替换的口径

| 原口径 | 问题 | 新口径 |
| --- | --- | --- |
| “制度合规及异常风险审核” | 容易被理解为参与授信规则和风险审批 | “材料清单 Grounding、材料异常恢复与补件闭环” |
| “复杂关系开放判断” | 容易被追问是否由模型裁定婚姻或法律关系 | “根据已确认角色生成清单；对材料与人员的归属歧义生成候选并触发 HITL” |
| “制度适用性进入 Audit Agent” | 越过材料齐套边界 | “材料要求由 Requirement RAG 检索，版本不确定时转人工确认配置” |
| “异常风险审核” | 容易被理解为反欺诈或信用风险 | “低置信、错分类、错归属、重复/孤页和 Tool 失败恢复” |
| “Final Validator” | 容易被理解为贷款终审 | “Completeness Validator，只验证每个 Required Slot 是否已匹配或已确认缺失” |
| `PASS_WITH_CONTROLS` | 属于贷款结论 | `MATERIALS_COMPLETE / MATERIALS_MISSING / WAITING_*` |

## 5. 简历能力如何在后端和投屏中被证明

| 简历能力 | 后端可运行证据 | 投屏可见证据 | 不能用什么代替 |
| --- | --- | --- | --- |
| Agent Workflow | LangGraph、Typed Handoff、Plan Gate、写权限边界 | Workflow 把材料歧义交给 Agent，结构化结果经 Gate 才更新匹配 | 静态节点动画 |
| 动态清单 | Person/Role 驱动 Planner、Task Dependency、版本 | 人工修正角色后，某人的清单新增/失效，其他人保持复用 | Fixture 预填清单 |
| Tool / MCP | 统一 ToolSpec、按 Task Allowlist、真实 MCP 注册 | 每轮只显示必要 Tool、Observation 和 Stop Reason | 固定 Tool 顺序 |
| Requirement RAG | Atomic Requirement、Dense/BM25、Filter、RRF、Rerank | 展示“为什么这个人需要这份材料”及 Requirement ID | 写死最终清单 |
| Long-Horizon / HITL | Checkpoint、interrupt/Command、持久化 HumanTask | 人工确认、发起补件、跨天到件后同一 thread 恢复 | 前端切换步骤 |
| 异常恢复 | 独立子图 Context、多步 Tool Loop、Guardrail | OCR/VLM Retry、重检索、人工出口 | 固定 retry 脚本 |

## 6. 后端职责边界

### 6.1 Workflow

- 读取已确认的 Case、Person 和 Role；
- 编译 `RequiredMaterialTask DAG`；
- 做确定性材料匹配、去重和缺件集合差；
- 校验 Agent Proposal；
- 提交 Case/Plan Version、Checkpoint 与最终齐套状态。

### 6.2 Audit Agent

只可以输出：

```text
MaterialClassificationCandidate
MaterialOwnerCandidate
PageGroupingCandidate
MaterialAssignmentIntent
RequestHumanMappingIntent
RequestSupplementIntent
RetryParseIntent
```

不可以输出：

```text
LoanApproval
RiskDecision
LimitAdjustment
ValuationDecision
PolicyComplianceConclusion
```

人物角色和婚姻/共有关系以客户经理或权威系统确认的结构化输入为准。Agent 可以发现冲突和给出候选，但不能自行写入法律关系事实。

### 6.3 Exception Recovery Sub-Agent

只处理 OCR/VLM、页面分类、人员标识、材料搜索、重复页和工具调用异常；返回受限 `MaterialPatchProposal` 或 `NEED_HUMAN`。

### 6.4 RAG

只检索材料要求：

```text
输入：product / channel / checklist_version / person_role / relation_condition
输出：requirement_id / material_type / required_party / source / version
```

不得根据材料内容输出授信合规或贷款结论。

## 7. 三条面试代码路径

### 路径 A：角色变化如何改变清单

```text
CorrectPersonFactCommand
→ State Reconciliation
→ Changed Fact Detection
→ Requirement Retrieval
→ Checklist Diff
→ Impact Analysis
→ Selective Replan
```

展示点：只失效与该角色有关的清单任务，无关人员结果继续复用。

### 路径 B：材料归属 Agent

```text
MaterialAssignment
→ Prompt Registry
→ Audit Agent Structured Output
→ Evidence/Confidence
→ Plan Gate
→ MaterialMatch or HumanTask
```

展示点：Agent 提供候选和依据，没有直接写 Case 的权限。

### 路径 C：补件恢复

```text
MISSING_CONFIRMED
→ SupplementRequest
→ Checkpoint / WAITING_SUPPLEMENT
→ MaterialReceived
→ Incremental OCR/VLM
→ Match Reconciliation
→ Dirty Task only
→ Completeness Validator
```

展示点：发起补件和材料到件是两个事件，同一 `thread_id` 恢复。

## 8. 可以展示的 HITL

只保留：

1. 客户经理纠正结构化 Person/Role；
2. 人工确认材料类型；
3. 人工确认材料所属人员；
4. 人工合并拆页或去重；
5. 人工从现有影像中找回漏分类页；
6. 确认影像不可读并要求清晰件；
7. 确认缺件并发起补件；
8. 补件到件后确认对应哪个缺件项；
9. 清单版本/渠道信息不完整时由运营人员确认配置。

人工动作不包含风险接受、贷款批准、贷款拒绝、额度调整、估值选择或法务结论。

## 9. 可量化指标

不编造数字。只有从测试集或运行事件中测量后才能写入简历。

| 指标 | 定义 |
| --- | --- |
| 清单生成准确率 | 正确 Required Slot / 标注 Slot |
| 材料分类准确率 | 正确材料类型页 / 评测页 |
| 人员归属准确率 | 正确 Person Match / 可自动归属材料 |
| 缺件识别 Precision/Recall | 系统缺件项与人工标注对比 |
| 异常自动恢复率 | `RESOLVED` 材料异常 / 全部材料异常 |
| Case 人工介入率 | 至少一个 HumanTask 的 Case / 全部 Case |
| 补件匹配成功率 | 自动/人工正确关闭的补件项 / 到件项 |
| Task 复用率 | 人工改值或补件后 `KEEP` Task / 原已完成 Task |
| Requirement HitRate@K/MRR/NDCG | 材料清单 RAG 离线指标 |
| SSE 首事件与恢复时延 | Run/Command 到首个可见事件的耗时 |

可以在指标确认后增加类似“补件后复用 `[X%]` 已完成清单任务”，但方括号不得带入投递版本。

## 10. Demo/Real 防漂移规则

- 仓库根 `demo/` 是唯一允许固定 Case、人物、合成影像、Scripted Tool Observation 和预期分支的目录；
- Core 与前端不出现固定人物、Case ID、预期答案或 `if demo`；
- Demo 与 Real 使用同一 Graph、Prompt、Tool Loop、Requirement RAG、Checkpoint、SSE 和 Completeness Validator；
- Core 不导入 Demo；Real 缺 Provider 时启动失败，不静默降级；
- 前端状态全部来自 API/Event Projection，不用本地 `decisionStep` 冒充执行。

## 11. 每次修改的检查模板

```text
Resume capability: CV-AGENT / CV-CHECKLIST / CV-TOOL / CV-RAG / CV-HITL / CV-GUARDRAIL
Material-completeness problem solved:
Backend state change:
Events emitted:
Evidence / Requirement / Checkpoint trace:
Demo provider:
Real provider:
Tests:
Metric affected:
Removed out-of-scope behavior:
```

合并前检查：

- [ ] 功能只解决人物—材料清单—匹配—缺件—补件问题；
- [ ] 没有授信、风险、额度、估值、用途或批贷逻辑；
- [ ] Agent 不自行裁定人物法律关系；
- [ ] RAG 只输出 Requirement，不输出授信结论；
- [ ] 页面变化来自后端 Event；
- [ ] 人工动作带版本、Evidence 和幂等键；
- [ ] 发起补件与材料到件分离；
- [ ] Core 没有 Demo 固定值；
- [ ] 最终状态没有 `APPROVED / REJECTED / PASS_WITH_CONTROLS / HIGH_RISK`；
- [ ] 简历没有未经测量的数字。

## 12. 面试时最重要的表达

不要说“系统判断这个人能不能贷款”，而要说：

> 系统先根据客户经理已确认的人员与角色生成按人材料清单，再把 200 多页影像分类并匹配到具体清单项。Agent 的价值不在批贷，而在处理材料类型、人员归属、混合拆页和低置信识别等非确定性；如果仍无法确认，就通过 LangGraph interrupt 让人选择。补件到达后不是全量重跑，而是根据 Person—Requirement—Material Dependency 只重跑受影响任务。

这个边界更小，但更可信，也更容易把 LangGraph、Agent Tool Loop、RAG Grounding、HITL 和增量重规划讲深。
