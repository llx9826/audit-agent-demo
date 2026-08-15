# ARGUS 宅抵贷智能审核演示改版计划

> 状态：**实施基线（Baseline）**
> 适用范围：`audit-agent-demo` 前后端、演示数据、事件流与自动化测试
> 基线日期：2026-08-15
> 约束：本文档确认信息架构和真实性边界；进入实施后，影响主叙事、单案例事实、事件契约或验收标准的变更，必须先更新本文档。

## 1. 改版目标

本次改版不再把首页做成一张需要自行阅读的完整架构图，也不再依靠多个 Tab、Quick Tour 或 Deep Dive 解释项目。整个产品改为一条可点击推进的三幕演示，让观看者在同一个宅抵贷案例中依次理解：

1. 为什么固定 Workflow 是起点；
2. 为什么复杂关系和动态任务需要 Audit Agent；
3. 为什么开放式异常必须被隔离到受控 Sub-Agent，并最终由证据、制度和 Final Validator 收口。

核心目标：

- 用“问题出现 → 架构升级 → 能力落地”的因果关系讲清 Agent 设计，而不是罗列技术名词。
- 三幕始终复用同一个 `case_id`、CaseState、任务 DAG 和 Evidence Ledger，避免场景割裂。
- 执行演示除补件外，必须同时看得见关系推理、动态计划、异常工具循环、选择性重规划、制度适用和最终控制措施。
- Workflow 保持业务控制权；Agent 只处理不确定性，并通过结构化契约把结果交回主图。
- 页面只展示最能说明设计能力的内容，删除重复、装饰性或无法由后端事实支撑的模块。
- 所有“实时执行”“Agent 调用”“RAG 命中”“Checkpoint 恢复”均必须与真实后端行为一致。

### 1.1 非目标

- 不建设完整银行信贷系统、客户管理系统或生产级 LOS。
- 不堆叠多案例、多产品和通用聊天入口。
- 不把房产价值等同于授信结论；第一还款来源、经营真实性和资金用途始终优先。
- 不虚构某家银行的内部制度。公开监管规则直接标注官方来源；为了演示而设置的业务阈值必须标为“演示策略”。
- 不允许 Agent 直接批准、拒绝贷款或绕过 Final Validator 修改全局状态。

## 2. 单案例设计

### 2.1 案例定位

“宅抵贷”在本演示中准确表示为：**以住房等不动产提供担保的个人经营贷款**。房产是第二还款来源，不替代对真实经营需求、偿债能力和资金用途的审查。

案例日期固定为 `2026-08-15`，以便演示规则生效日期和版本适用判断。

### 2.2 Case V1 初始事实

| 维度 | 演示事实 | 预期作用 |
| --- | --- | --- |
| 产品 | 个人经营性房产抵押贷款 | 限定检索和审核策略范围 |
| 金额与期限 | 申请 280 万元，期限 5 年 | 触发经营需求、偿债能力和支付方式审核 |
| 借款人 | 张三，小微企业实际经营人 | 建立借款人—企业关系 |
| 抵押人 | 李四，关系初始为 `UNKNOWN` | 为动态关系任务留出真实触发条件 |
| 经营用途 | 支付建材采购合同，单一交易对手金额 120 万元 | 超过个人经营贷 50 万元受托支付判断线 |
| 交易对手 | 供应商与抵押人存在股权关系 | 触发关联交易对手和真实贸易背景核验 |
| 抵押房产 | 持有 8 个月；原成交价与本次评估价存在明显差异 | 触发短期持有、估值合理性和抵押率审查 |
| 材料异常 | 产权证 OCR 姓名或地址与登记信息存在低置信冲突 | 触发受控异常 Sub-Agent，不直接触发补件 |
| 材料缺口 | 婚姻证明缺失；配偶抵押同意需要补强 | 触发 durable HITL、Reconcile 和 Selective Replan |
| 案例日期 | 2026-08-15 | 触发 2026-08-01 起施行规则的时效门禁 |

### 2.3 单案例中的四次“认知转折”

1. **固定校验完成，但任务仍不完整**：身份证、营业执照和产权证可以按规则核验；然而借款人、抵押人、企业、供应商之间的关系决定后续任务，不能靠一条固定清单覆盖。
2. **关系发现改变计划**：Audit Agent 发现配偶、共同财产和关联交易对手关系，主图据此增加配偶身份、抵押同意、交易背景与受托支付任务。
3. **证据冲突需要有限调查**：产权材料的低置信冲突交给 Exception Recovery Sub-Agent，在工具白名单和步数预算内求证；解决则回主图，不能解决则转人工。
4. **新证据只影响部分结论**：补件形成 Case V2。Reconcile 显式生成事实差异，依赖图只重跑受影响任务；最后通过现行制度 Grounding 和 Final Validator 形成带控制条件的结论。

### 2.4 最终演示结论

最终结论不使用简单的“模型认为通过”，而采用结构化的 `PASS_WITH_CONTROLS`：

- 抵押登记完成前不得放款；
- 120 万元采购款采用受托支付，并核验真实交易对手；
- 因抵押房产持有不足 3 年，增加贷后用途检查及核查记录；
- 签约前完成综合融资成本明示与客户确认；
- 所有任务结果均能回溯到 Evidence ID、Rule ID、原始片段和事件序号。

如任何强制证据、规则有效性或依赖条件不满足，Final Validator 必须输出 `BLOCKED`，而非由 Agent 自行降级放行。

## 3. 三幕信息架构

三幕位于一条连续页面中，使用顶部章节进度 `01 架构演进 / 02 协作执行 / 03 证据闭环`。不设置 Quick Tour、Deep Dive 或平行模式切换。用户只通过“上一步、下一步、重播当前版本”控制讲解节奏；切幕时保留同一个案例状态。

### 第一幕：架构演进——为什么要这样设计

第一幕内部包含三个可点击版本。初始只展示当前版本需要的节点，点击“下一版本”后以连线绘制和节点生成动画完成升级，右侧同步出现“遇到的问题”和“新增契约”。

#### V1：Deterministic Workflow

画面仅保留稳定主干：

`Ingest → Normalize → Hard Rules → Fixed Plan → Final Gate`

讲述重点：

- 完整性、格式、金额阈值、精确字段一致性等可编码判断必须留在 Workflow。
- 每个节点输入输出固定，可测试、可重放、可审计。
- 真实问题卡片出现：同一份材料组合下，配偶、共同产权、关联供应商、房产短期持有等关系会改变审核任务；继续扩展 `if/else` 会产生路径组合爆炸。

动画要求：主干逐节点点亮；问题卡出现时，固定 Plan 的三条分支以“未覆盖关系”状态中止。

#### V2：Workflow + Audit Agent

在主干侧面生成 Audit Agent，并画出双向 typed handoff：

`Scoped AuditContext → Audit Agent → PlanPatch / AuditResult → Workflow`

讲述重点：

- Agent 处理跨文档语义和实体关系：借款人—配偶—抵押房产—经营企业—交易对手。
- Agent 不执行最终状态写入，只返回结构化关系、证据引用、置信度和建议任务。
- Workflow 校验 `PlanPatch` 后更新任务 DAG，例如新增配偶身份、抵押同意、关联交易背景和受托支付任务。
- “智能”应通过关系图变化和 Plan V1 → Plan V1.1 的差异被看见，而非一段自然语言总结。

动画要求：关系边先生成，再由关系边触发任务节点插入 DAG；每条新增任务旁显示触发事实。

#### V3：Workflow + Audit Agent + Bounded Exception Sub-Agent

从 Audit Agent 或确定性节点的异常出口生成独立恢复子图：

`ExceptionEnvelope → Tool Loop → RESOLVED / NEED_HUMAN → Parent Graph`

讲述重点：

- OCR 冲突、低置信和证据缺口不是正常审核语义，应隔离到局部上下文。
- 明示 `tool_allowlist`、`max_steps = 3`、完成条件、Loop Guard 和两个合法出口。
- Sub-Agent 只能补充观察和证据，不能更改授信结论。
- `RESOLVED` 返回父图继续执行；`NEED_HUMAN` 持久化暂停并生成结构化补件请求。

动画要求：异常分支从主图向下展开；每次工具调用消耗一个 Step Token；达到完成条件后连线返回，预算耗尽则连线到 HITL。

第一幕结束时只保留一条总结：**确定性留在主干，不确定性受限委派，最终结论由证据契约收口。**

### 第二幕：单案执行——让技术含量可见

第二幕不是普通进度条，而是同一 CaseState 的可视化执行台。主画面固定为四区：

1. **关系与事实图**：借款人、抵押人、企业、供应商、房产及其边；新事实到达时增量更新。
2. **LangGraph 运行轨迹**：只显示当前节点、已完成节点和真实条件边，不同时铺满所有节点。
3. **任务 DAG / Plan Diff**：展示任务依赖、触发事实以及 `KEEP / ADD / RERUN / INVALIDATED / RESOLVED`。
4. **事件检查器**：展示当前事件的 actor、action、tool、observation、Evidence、State Diff 和下一跳理由。

底层事件按以下业务节拍归并，但页面只设置四个由用户手动推进的决策停顿：任务计划、语义路由、异常交接、人工暂停。

| Beat | 可见事件 | 必须看见的能力 |
| --- | --- | --- |
| 1. 接件与硬规则 | 材料归一化、身份和金额规则完成 | Workflow 的确定性边界 |
| 2. 关系审计 | Agent 建立配偶、房产、企业和供应商关系 | 关系推理，不是文本摘要 |
| 3. 动态计划 | 根据关系生成任务并插入 DAG | Agent 决定“还要查什么”，Workflow 决定“能否加入” |
| 4. 异常恢复 | OCR/VLM/登记查询按预算依次调用 | 受控工具循环、观察结果、退出条件 |
| 5. 经营与支付审核 | 经营需求匹配、关联交易对手、受托支付判断 | 不只展示补件，体现贷款业务推理 |
| 6. HITL | 缺少婚姻证据，写 Checkpoint 后暂停 | 失败不是猜测；可恢复长流程 |
| 7. 补件与重规划 | Case V2、事实 Diff、依赖传播、Plan V2 | 只重跑被新事实污染的结果 |

第二幕中的补件只是执行链后段的暂停原因，不再占据整个执行演示。操作按钮只保留与叙事有关的“上一步 / 下一决策 / 进入补件闭环”。

### 第三幕：证据落锤——Grounding、控制措施与可回放结论

第三幕回答“为什么这个结论可采用”，由三层组成：

1. **Policy Retrieval Trace**：展示 query rewrite、候选规则、BM25 / Dense / RRF 分数，以及产品、状态、生效日、失效日门禁。
2. **Evidence Chain**：从最终任务结论反查 Rule ID、官方条款、材料片段和生成该证据的事件。
3. **Final Validator**：校验任务依赖闭合、结果 Schema、Evidence 可解析、规则在案例日期有效、人工条件完成，并输出 `PASS_WITH_CONTROLS` 或 `BLOCKED`。

重点演示一个明确的 RAG 反例：语义分数更高但已失效或不适用的候选被门禁排除，现行有效规则胜出。规则卡必须展示官方标题、发布机关、发布日期/生效日期、条款号和直接链接；不能继续使用未标注来源的 `MORT-V1/MORT-V2` 作为真实制度。

第三幕结尾展示：

- 最终结论与四项控制措施；
- Case V1 → V2、Plan V1 → V2；
- 本次执行的节点数、Agent 调用数、工具步数、人工中断次数、复用任务数；
- 从任一 Checkpoint 重放时，事件序列和结论可复现。

## 4. 统一事件契约

### 4.1 原则

- 后端事件是执行演示唯一事实源；前端只消费事件并做表现层动画。
- 架构第一幕的“V1/V2/V3 讲解动画”属于静态产品讲解，不伪装成后端执行事件。
- 事件 append-only，按 Case 内单调递增 `seq` 排序；断线后通过 `after_seq` 恢复。
- 每个事件都要回答：谁、在哪个节点、因为什么输入、执行了什么、产生什么证据或状态变化、为什么走下一条边。
- 敏感工具参数只保留脱敏摘要；材料正文通过 Evidence ID 按需读取。

### 4.2 EventEnvelope V2

```json
{
  "schema_version": "2.0",
  "event_id": "EV-...",
  "case_id": "CASE-ARCH-001",
  "seq": 18,
  "timestamp": "2026-08-15T10:30:00+08:00",
  "event_type": "PLAN_PATCH_APPLIED",
  "actor": "workflow",
  "node": "dynamic_plan",
  "phase": "EXECUTION",
  "case_version": 1,
  "plan_version": 2,
  "checkpoint_id": null,
  "correlation_id": "RUN-...",
  "causation_id": "EV-...",
  "payload": {
    "summary": "配偶与关联交易关系触发 4 个新增审核任务",
    "task_id": null,
    "action": "VALIDATE_AND_APPLY_PLAN_PATCH",
    "input_refs": ["FACT-REL-01", "FACT-PAYEE-02"],
    "output_refs": ["TASK-T06", "TASK-T07", "TASK-T08", "TASK-T09"],
    "evidence_refs": ["E-REL-01", "E-CORP-03"],
    "rule_refs": [],
    "tool_call": null,
    "observation": {
      "relation": "SPOUSE",
      "related_payee": true
    },
    "decision": {
      "code": "APPLY",
      "confidence": 0.97,
      "reason": "关系事实满足任务模板触发条件",
      "requires_human": false
    },
    "control": {
      "policy": "PLAN_PATCH_SCHEMA",
      "status": "PASSED"
    },
    "state_diff": {},
    "plan_diff": [
      {"task_id": "T06", "operation": "ADD", "reason_ref": "FACT-REL-01"}
    ],
    "next_node": "deterministic_checks"
  }
}
```

### 4.3 枚举约束

- `actor`：`workflow | planner | rule | audit_agent | exception_agent | retriever | validator | human | tool`
- `phase`：`INGEST | EXECUTION | INTERRUPT | RESUME | GROUNDING | FINALIZE`
- 任务影响：`KEEP | ADD | RERUN | INVALIDATED | RESOLVED | BLOCKED`
- Agent 出口：`RESOLVED | NEED_HUMAN | REJECTED_BY_GUARDRAIL`
- 最终结论：`PASS_WITH_CONTROLS | BLOCKED`

### 4.4 必要事件类型

| 事件组 | 事件类型 |
| --- | --- |
| 接件 | `CASE_CREATED`、`CASE_INGESTED`、`FACTS_NORMALIZED` |
| 计划 | `PLAN_COMPILED`、`RELATION_DISCOVERED`、`PLAN_PATCH_PROPOSED`、`PLAN_PATCH_APPLIED` |
| 任务 | `TASK_STARTED`、`RULE_CHECK_COMPLETED`、`TASK_COMPLETED`、`TASK_INVALIDATED` |
| 异常 | `EXCEPTION_RAISED`、`TOOL_CALLED`、`TOOL_RESULT`、`EXCEPTION_RESOLVED`、`EXCEPTION_NEEDS_HUMAN` |
| 人工 | `CHECKPOINT_CREATED`、`HITL_REQUESTED`、`SUPPLEMENT_RECEIVED`、`GRAPH_RESUMED` |
| 重规划 | `STATE_RECONCILED`、`IMPACT_ANALYZED`、`PLAN_REVISED`、`TASK_REUSED`、`TASK_REEXECUTED` |
| Grounding | `RAG_QUERY_REWRITTEN`、`POLICY_CANDIDATE_RETRIEVED`、`POLICY_CANDIDATE_FILTERED`、`POLICY_SELECTED`、`EVIDENCE_GROUNDED` |
| 收口 | `FINAL_VALIDATED`、`CASE_COMPLETED`、`CASE_BLOCKED` |

### 4.5 状态恢复

- 普通事件携带最小 `state_diff` / `plan_diff`，不重复整份 CaseState。
- `CHECKPOINT_CREATED` 持久化完整、带 Schema 版本的状态快照。
- 页面重连时先读取最新 Checkpoint，再按 `seq` 应用其后的事件。
- `event_id`、补件 `command_id` 和工具调用幂等键必须唯一；重复请求不能生成重复任务、重复事件或重复 Case Version。

## 5. 页面删、留、加

### 5.1 删除

- 删除当前首页一次性铺开的八节点大图和密集节点说明抽屉。
- 删除“架构设计 / 执行演示”的割裂式 Tab 心智，改为一条三幕章节进度。
- 删除 Quick Tour、Deep Dive、模式切换和重复入口。
- 删除无助于主叙事的通用指标、装饰性状态卡和大段静态术语。
- 删除前端用定时器伪装后端实时执行的表达；表现层延时只能用于动画，不得改变事件事实和顺序。
- 删除无官方来源却以真实制度呈现的 `MORT-V1/MORT-V2` 文案、固定分数和固定命中结论。
- 删除从页面源码直接推导的审核结果、硬编码观察和与 CaseState 无关的兜底事实。

### 5.2 保留

- Workflow-first、typed handoff、bounded autonomy、evidence-first 四项架构原则。
- LangGraph conditional edges、FastAPI、SSE、Append-only Event Log。
- Checkpoint、HITL、Reconcile、Impact Analysis、Selective Replan。
- Evidence Ledger、制度适用门禁和 Final Validator。
- 当前清晰、克制的银行业务视觉基调。

### 5.3 新增

- 三幕章节进度与第一幕 V1/V2/V3 版本步进器。
- 连线绘制、节点生成、关系边触发任务的解释性动画，并支持 `prefers-reduced-motion`。
- 借款人—配偶—企业—供应商—房产关系图。
- PlanPatch 提议与 Workflow 接受/拒绝过程。
- 经营需求、短期持有房产、估值差异、关联交易对手和受托支付等业务任务。
- Exception Envelope、工具白名单、Step Budget、Loop Guard 和退出原因的运行面板。
- 官方规则语料、真实检索 Trace、时效/产品/版本适用门禁和可点击来源。
- `PASS_WITH_CONTROLS` 控制措施卡，以及故意移除证据后可见的 Validator 阻断状态。
- 事件级回放、断线续播和 Checkpoint 恢复。

## 6. 实施状态与真实性边界

本次实现已经把主叙事所需能力落到真实代码路径；仍未实施的能力会在页面和文档中准确标注，不以动画替代后端事实。

| 状态 | 已实现或当前边界 |
| --- | --- |
| 已完成 | `CASE-ZD-042` 固定为 280 万元、60 个月、企业成立 10 个月、房产持有 8 个月的单一案例。 |
| 已完成 | 初始 Plan 编译身份、关系、经营真实性、短期持有、估值比对与受托支付任务；关系变化新增 T06/T07，2026 规则适用后由 PlanPatch 新增 T12。 |
| 已完成 | Exception Recovery 是真实编译的 LangGraph 子图，执行 `prepare → select_tool → execute_tool → evaluate` 循环；Registry、Allowlist、`max_steps`、Loop Guard 和退出契约均进入控制流。 |
| 已完成 | OCR/VLM/材料查询采用本地确定性工具适配器，事件明确标记 `OFFLINE_DETERMINISTIC_TOOLS`，页面不称为实时模型推理。 |
| 已完成 | 运行时生成 `ROUTE_EVALUATED`、`HANDOFF_CREATED`、`AGENT_TOOL_STARTED/FINISHED`、`AGENT_RETURNED`、`STATE_PATCH_APPLIED` 和 `RESULT_GROUNDED` 等结构化事件。 |
| 已完成 | RAG 在本地规则小语料上根据真实查询计算 hashed dense 与 BM25 分数，执行 RRF 和产品/状态/生效日门禁；最终规则带官方 URL。 |
| 已完成 | Final Validator 输出 `PASS_WITH_CONTROLS`，并返回抵押登记、受托支付、贷后用途核查和综合融资成本明示四项结构化控制措施。 |
| 已完成 | 前端为一条手动推进的三幕路径，不再使用 Tab、模式切换或定时器伪装实时执行。 |
| 已完成 | 本地地址调用真实 Python/LangGraph 服务；无后端的托管地址使用显著标注的 `RECORDED_GRAPH_TRACE`，回放与后端同契约的固化事件，不伪装成实时调用。 |
| 当前边界 | 父图仍以同步 `invoke` 执行后持久化事件；页面准确称为“决策停顿/可审计执行”，不称实时流。SSE 端点保留，逐节点落库可作为后续增强。 |
| 当前边界 | Checkpoint 为 SQLite 应用层持久化快照，可跨进程恢复；不是 LangGraph 原生 checkpointer，页面使用 `Durable Checkpoint`，不展示 `interrupt()`。 |
| 当前边界 | Audit Agent 使用可复现的结构化关系审核节点，而非在线模型；若接入模型，仍必须遵守相同输入输出和 Plan Gate。 |
| 当前边界 | 本地 SQLite 使用 pickle 保存演示对象，属于本地演示存储，不作为生产存储方案。 |

真实性底线：即使模型或外部系统不可用，也要运行真实的状态机、工具适配器、事件持久化、RAG 排序和适用性门禁；无法真实执行的部分明确标注演示适配器，不以视觉效果替代后端事实。

## 7. 分阶段实施

### 阶段 0：冻结案例和契约

- 将第 2 节单案例写为版本化 fixture，并定义预期事实、关系、任务和最终控制措施。
- 将 EventEnvelope V2、Task、PlanPatch、Evidence、PolicyChunk 和 FinalDecision 建立 Schema。
- 为案例生成 Golden Event Sequence，作为后续前后端共同契约。

完成标志：同一输入、同一规则版本能得到稳定的任务图和事件类型；前后端无需各自维护别名。

### 阶段 1：后端业务主干与动态计划

- 将硬规则、事实归一化、关系输出和 PlanPatch 应用拆成明确节点。
- 从单案例输入实际生成实体关系图和任务 DAG。
- 实现经营需求、短期持有/估值、关联交易对手和受托支付审核。
- 加强 Final Validator 和通用幂等控制。

完成标志：修改案例关系或支付金额后，任务图会随事实变化，不需要改 Builder 常量。

### 阶段 2：受控 Agent、Grounding 与长流程恢复

- 实现 Audit Agent 结构化输出和 Workflow Schema Gate。
- 实现 Exception Agent 有限工具循环、Step Budget、Loop Guard 与两类出口。
- 建立官方规则小语料库和真实 Hybrid Retrieval Trace。
- 接入 LangGraph Checkpointer、Interrupt/Resume 和 Case/Plan 版本对账。

完成标志：异常可以在预算内解决或稳定转人工；重启服务后仍能从等待点恢复；每个制度结论都能解析到官方片段。

### 阶段 3：真实流式事件链

- 以 LangGraph `stream/astream` 为单位逐节点写入 Event Store。
- SSE 支持心跳、`after_seq`、断线重连和终止状态。
- 前端移除事件 alias 和结果推测，只根据 EventEnvelope 渲染。

完成标志：运行中的事件能逐条到达页面；刷新页面后事件数、状态和下一节点不漂移。

### 阶段 4：第一幕架构演进

- 实现 V1/V2/V3 步进器、连线动画、问题卡和契约卡。
- 关系边触发任务插入的动画使用与第二幕一致的节点/任务语义。
- 实现前后步、重播当前版本与减少动态效果模式；不增加额外演示模式。

完成标志：不阅读长文也能说清每次架构升级分别解决什么问题。

### 阶段 5：第二、三幕执行与证据收口

- 实现关系图、LangGraph 轨迹、Task DAG/Plan Diff 和事件检查器。
- 将 HITL、Reconcile、Selective Replan 置于完整执行节拍中，而非独立补件页面。
- 实现检索候选、适用性门禁、Evidence Chain、Final Validator 和控制措施卡。
- 实现 Checkpoint 回放与故障/缺证据阻断演示。

完成标志：单次完整演示同时覆盖动态任务、受控异常、人工恢复、选择性重跑和 Grounding。

### 阶段 6：验证与收尾

- 补齐后端单元、图路由、事件契约、幂等、恢复、RAG 和 Validator 测试。
- 补齐前端状态还原、SSE 重连、逐步播放、键盘操作和响应式测试。
- 在标准演示视口完成视觉验收，并检查全部文案与官方出处。

完成标志：满足第 8 节全部验收标准后，才替换当前默认首页。

## 8. 验收标准

### 8.1 叙事与信息架构

- 首页仅有一条三幕路径，无 Quick Tour、Deep Dive 或平行模式切换。
- 第一幕点击三次以内即可依次看到 Workflow、Audit Agent、Bounded Exception Agent 的生成过程。
- 每个版本同时展示“新增组件、触发原因、输入输出契约、控制边界”，不能只有架构名称。
- 三幕始终显示同一 `case_id`，案例事实不在不同页面间悄然改变。
- 首次观看者应能在 90 秒内准确复述三次架构选择的原因。

### 8.2 Agent 与 LangGraph

- LangGraph 源码中存在与页面一致的条件边，事件中的 `next_node` 与实际路由一致。
- Audit Agent 返回结构化 RelationResult / PlanPatch；不直接改写全局 CaseState。
- 改变抵押人关系、房产持有时间、交易对手关系或支付金额，至少一项审核任务会动态新增、移除或改变依赖。
- Exception Agent 最多调用 3 次白名单工具；重复动作且状态无变化时 Loop Guard 生效。
- Exception Agent 只有 `RESOLVED`、`NEED_HUMAN`、`REJECTED_BY_GUARDRAIL` 三种出口，不能输出授信批准。

### 8.3 补件、状态与重规划

- `WAITING_HUMAN` 前必有可读取 Checkpoint；服务重启后可以继续同一 Case。
- 重复提交相同 `command_id` 不增加 Case Version、Plan Version 或事件数量。
- 补件只有在规范化事实真正变化时生成 Case V2。
- Plan Diff 清楚显示未受影响任务 `KEEP`，关系任务 `RERUN`，旧制度结论 `INVALIDATED`，已由补件确定解决的任务 `RESOLVED`，以及新关系任务 `ADD`。
- 被 `KEEP` 的任务不得再次产生工具调用或重复 Evidence。

### 8.4 RAG 与 Evidence Grounding

- 语料至少包含本方案第 9 节列出的核心官方规则，并保存标题、机关、发布日期、生效/失效日期、产品范围、条款号、原文片段和 URL。
- 查询变化会实际改变 BM25/Dense 排名；页面分数来自后端 Trace，不是固定展示值。
- 无效、过期、尚未生效或产品不匹配的高分候选必须被 Applicability Gate 排除。
- 每个 Policy AuditResult 至少包含一个可解析 `rule_ref` 和 `evidence_ref`；点击后能打开对应条款和官方链接。
- 删除任一强制 Evidence、换成失效规则或破坏任务依赖时，Final Validator 必须输出 `BLOCKED`。

### 8.5 事件一致性

- 本地页面使用后端已持久化的 append-only 事件作为执行事实源，并通过四个手动决策停顿组织讲解；托管页面只能使用显著标注的同契约记录 Trace，不使用前端定时器伪装实时流。
- 同一 Case 的 `seq` 单调递增且无重复；`/events?after=` 与 SSE 游标均有契约测试。
- 从应用层 Checkpoint 重放后，Case Version、Plan Version、任务状态和最终结论保持一致。
- 页面中的 route、tool、observation、Evidence、State Diff 和停止原因均能在对应事件中找到。

### 8.6 质量与展示

- 后端路由、异常预算、幂等、恢复、RAG 门禁和 Validator 均有自动化测试。
- 前端通过 lint、类型检查、生产构建和关键路径端到端测试。
- 1440×900 演示视口不出现核心信息折叠、横向滚动或不可点击区域；小屏保留完整章节顺序。
- 支持键盘推进和 `prefers-reduced-motion`；动画不会遮挡事件内容。
- 页面不出现无法从代码和事件证明的“实时模型推理”“生产级”或类似表述。

## 9. 官方研究依据

以下公开资料用于定义业务场景和规则语料。实施时应保存具体条款，而非只保存网页摘要。

1. [国家金融监督管理总局《个人贷款管理办法》（2024年第3号）](https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1151064&itemId=861)：要求全流程管理、尽职调查、偿债能力与经营情况评价、自动审批人工复审、担保流程、受托支付、贷后用途监测；自 2024-07-01 施行。
2. [《个人贷款管理办法》可读全文（交通运输部信用交通转载，来源为金融监管总局）](https://credit.mot.gov.cn/zhengcefagui/202408/t20240807_4147084.html)：个人经营贷一般不超过 5 年、最长不超过 10 年；单次提款超过 50 万元原则上采用受托支付，并核查化整为零规避行为。
3. [银保监办发〔2021〕39号《关于防止经营用途贷款违规流入房地产领域的通知》](https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/202204/t20220414_2677047.html)：要求核查空壳企业、短期受让股权、经营需求与流水、第一还款来源、短期持有抵押房产、关联方支付、资金流向和中介机构。
4. [最高人民法院发布的《中华人民共和国民法典》全文](https://www.court.gov.cn/zixun/xiangqing/233181.html)：第 301 条涉及共有财产处分同意，第 399 条规定不得抵押财产，第 402 条规定不动产抵押权自登记时设立，第 414 条规定多重抵押受偿顺序；婚姻家庭编涉及夫妻共同财产和共同意思表示。
5. [最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释](https://www.court.gov.cn/fabu/xiangqing/282721.html)：用于补充担保主体、登记、处分权限及相关司法适用判断。
6. [国家金融监督管理总局、中国人民银行《个人贷款业务明示综合融资成本规定》（金规〔2026〕2号）](https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=1251479&generaltype=1&itemId=861)：自 2026-08-01 起施行，要求签约前明示贷款人及合作机构各项息费、收取主体、年化综合成本和违约成本。
7. [中国工商银行“个人房产抵押消费与经营组合贷款”](https://www.icbc.com.cn/page/721852475778039857.html)：公开列明经营资格、还款来源、用途和抵押权属材料，以及不得流入房地产、证券、期货、股权投资、借贷和理财等用途。
8. [中国银行“个人经营贷款”](https://www.boc.cn/pbservice/pb2/200806/t20080625_719.html)：公开列明借款人及配偶、企业经营资格、流水与税单、抵押物权属和价值、处分权人同意、购销合同等材料。
9. [中国建设银行“小企业速贷通”办理程序](https://company.ccb.com/cn/home/company/gsyw/560016.html)：展示申请、尽职调查、抵押物评估、审批、合同、抵押登记和提款流程。
10. [平安银行“中小企业标准类房产抵押融资产品”](https://bank.pingan.com/gongsi/daikuan/shouxin/rongzicp.shtml)：展示资料提交、审查审批、合同与抵押登记、用款和还款的公开流程。
11. [重庆监管局关于加强个人经营性贷款业务管理的通知](https://www.nfra.gov.cn/branch/chongqing/view/pages/common/ItemDetail.html?docId=1092023&itemId=4201)：公开列举虚构经营背景、伪造流水、空壳公司包装、过桥资金、中间账户过渡和中介协助等现实风险。

## 10. 基线决策摘要

- **一个案例**：不切换场景，以 Case/Plan 版本变化承载全部能力。
- **三幕连续演示**：架构演进、单案执行、证据落锤。
- **三次架构升级**：Workflow → Audit Agent → Bounded Exception Sub-Agent。
- **一个控制中心**：Workflow 持有状态、路由和最终边界。
- **一个事实源**：后端 Event Store；前端不推测执行事实。
- **一种最终表达**：Evidence-grounded `PASS_WITH_CONTROLS`，缺证据则 `BLOCKED`。

本文档即本轮改版的实施基线。后续开发、任务拆分、验收和演示脚本均以此为准。
