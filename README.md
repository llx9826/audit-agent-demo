# ARGUS · 宅抵贷智能审核 Agent

ARGUS 用一条连续的住宅抵押经营贷 Case，展示 LangGraph 系统为什么从确定性 Workflow 演进到 Audit Agent 与受控 Exception Sub-Agent，以及补件后如何通过选择性重规划、RAG Grounding、Evidence Ledger 和 Final Validator 收口。

> 所有姓名、材料、金额和案例事件均为合成演示数据。公开监管规则会保留发布机关、生效日期和官方来源；本地工具适配器不会被描述成在线模型服务。

实施基线与行业依据见 [docs/REVAMP_PLAN.md](docs/REVAMP_PLAN.md)。

## 三幕演示

页面只有一条路径，不提供平行模式和顶层 Tab：

1. **架构演进**：同一画布依次生成 V1 Workflow、V2 Audit Agent 和 V3 Bounded Exception Sub-Agent；每次升级都说明触发问题、设计决策、职责边界和状态结果。
2. **协作执行**：CASE-ZD-042 停在任务计划、条件路由、Typed Handoff、工具循环与 Audit Agent 返回五类关键决策，不展示刷屏式原始日志。
3. **证据闭环**：应用层 Checkpoint 等待补件；恢复后执行 Reconcile、Impact Analysis、Selective Replan、规则适用性门禁和 Final Validator。

## 单一案例

`CASE-ZD-042` 的核心事实：

- 申请金额 280 万元，期限 60 个月，用于企业流动资金；
- 企业成立 10 个月，抵押房产持有 8 个月；
- 原成交价与评估价存在明显偏离；
- 借款人张三、抵押人李四，关系初始为 `UNKNOWN`；
- 户口簿 OCR 与身份证姓名冲突；
- 婚姻关系证明缺失，主图最终进入 `WAITING_HUMAN`。

补件后 Case 从 V1 进入 V2，Plan 从 V1 进入 V2。未受影响任务复用，关系与制度任务局部重跑，配偶相关任务新增；2026-08-01 生效的规则再通过 PlanPatch 新增综合融资成本明示任务。

## 实际实现

### LangGraph 主图

```text
Ingest → Build State → Plan → Deterministic Checks → Audit Route
                                                     │
                                 OCR_CONFLICT ───────┘
                                                     ↓
                                      Exception Recovery Subgraph
                                                     ↓
                               Relation Review → WAITING_HUMAN
                                                     ↓
Supplement → Reconcile → Impact → Selective Replan → Grounding → Validator
```

### 受控 Exception Sub-Agent

Exception Recovery 是独立编译的 LangGraph 子图：

```text
prepare → select_tool → execute_tool → evaluate
             ↑                         │
             └──────── loop ───────────┘
                         ↓
               RESOLVED / NEED_HUMAN
```

控制边界由运行时代码执行：

- `ExceptionToolRegistry` 注册表；
- 每次 Handoff 独立的 Tool Allowlist；
- `max_steps = 3`；
- 重复动作且状态无变化时触发 Loop Guard；
- 只允许 `RESOLVED` 或 `NEED_HUMAN` 返回父图。

OCR Retry、VLM Extract 和 Document Search 是可复现的本地确定性适配器，事件中标记为 `OFFLINE_DETERMINISTIC_TOOLS`。

### 结构化事件

执行事件包括：

- `ROUTE_EVALUATED`
- `HANDOFF_CREATED`
- `AGENT_TOOL_STARTED / FINISHED`
- `AGENT_RETURNED`
- `STATE_PATCH_APPLIED`
- `PLAN_PATCH_APPLIED`
- `RESULT_GROUNDED`
- `FINAL_VALIDATED`

前端读取这些事件中的 route、handoff、tool observation、budget、stop reason 和 state diff，不展示模型思维过程。

### RAG Grounding

本地规则语料在运行时根据案例问题计算 hashed dense 与 BM25 分数，再执行 RRF 融合。候选随后经过产品、状态和生效日期门禁。

演示会让语义分数更高但已停用的策略被排除，并选择 2026-08-01 生效的《个人贷款业务明示综合融资成本规定》。最终结果带 Rule ID、Evidence ID、生效日和官方 URL。

### Final Validator

Validator 检查任务状态、Evidence 引用和 Rule 引用，输出结构化决定：

```text
PASS_WITH_CONTROLS
```

并返回四项控制措施：抵押登记前不放款、采购款受托支付、贷后用途增强检查、综合融资成本明示与客户确认。

## 当前边界

- 父图当前同步执行，页面使用已持久化事件做手动决策回放，不称为实时流。
- Checkpoint 是可跨进程恢复的 SQLite 应用层快照，不称为 LangGraph 原生 `interrupt()`。
- Audit Agent 使用结构化、可复现的关系审核节点；未配置在线模型。
- 本地 SQLite 存储用于演示，不是生产数据库方案。
- 本地地址调用真实 Python/LangGraph 服务；托管地址没有 Python API 时显示 `RECORDED_GRAPH_TRACE` 并回放同契约事件，不把记录数据称为实时执行。

## 启动

环境要求：Node.js 22+、Python 3.11+。

```bash
make init
make demo
```

前端默认运行在 [http://localhost:3000](http://localhost:3000)，FastAPI 文档位于 [http://localhost:8000/docs](http://localhost:8000/docs)。

## 验证

```bash
make test
```

当前测试覆盖主图路由、异常子图工具边界、Loop Guard、应用层 Checkpoint、补件幂等、选择性重规划、运行时 Hybrid Retrieval、规则适用性门禁、Final Validator 和前端生产构建。
