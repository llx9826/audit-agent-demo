# ARGUS 后端代码导览

本项目只判断“已确认角色应提供的材料是否到齐、可读且归属明确”，不判断贷款准入、额度、估值、风险或审批结果。

## 从哪里开始读

1. `backend/app/orchestration/audit_pipeline.py`：整笔进件唯一主拓扑，只定义 Node、Edge、分支和回环。
2. `backend/app/orchestration/dependencies.py`：主图所需 RuleEngine、Evidence RAG、两个决策 Agent 和一个共享恢复 Sub-Agent 的能力合同。
3. `backend/app/bootstrap/container.py`：唯一 Composition Root，组装 Profile、ModelGateway、Tool、RAG、Graph、RunManager 与 Orchestrator。
4. `backend/app/orchestration/stages/`：每个阶段持有本阶段算法；主拓扑只声明控制流。`matching.py` 可直接读到 Ready Batch、`Send` Worker 与 Fan-in Gate。
5. `backend/app/service.py`：持久化 Graph 转移、Checkpoint 和事件，不保存业务路由规则。

运行 `make describe` 会从真实编译 Graph 反射 Node、Edge 和 Mermaid，不维护第二份手工流程图。

## 主 Pipeline

```text
进件登记
  → 依据分类/材料类型选择身份、角色、所属人承载页
  → LangGraph Send 并行执行 Page-scoped Evidence Worker
      ├─ Demo/已结构化上游：读取 Page extracted_fields
      └─ Real：经统一 Tool Registry 调 VLM 服务
  → Evidence Fan-in Gate 汇聚 IdentityMention / RoleSignal / OwnerSignal
  → Case Association Agent 在封闭候选集中归并人员、绑定角色与材料归属
  → Association Gate 写入已确认 Person/Role Projection，证据不足时进入 HITL
  → 规则引擎解析适用 Atomic Requirement
  → 编译 Person × Requirement Task
  → Dependency Resolver 选择 Ready Batch
  → LangGraph Send 并行执行 Task-scoped Material Match Worker（无写权限）
  → Fan-in Plan Gate 校验 Case/Plan/Result Version 与 Conflict Key 后提交
  → Task Outcome Router（确定性）
      ├─ 机器 Observation 异常 → Exception Recovery 私有 Tool Loop → 重新匹配
      ├─ 语义候选歧义 → Material Audit Agent → Plan Gate
      │                         ├─ 候选可确认 → 写回并重匹配
      │                         ├─ 缺 Observation → Exception Tool Loop → 重匹配
      │                         └─ 仍不唯一 → 人工
      ├─ 确定性缺件 → Requirement Evidence RAG → 补件 HITL
      └─ 已齐套 → Final Validator
  → interrupt / Command(resume)
  → State Reconciliation → Impact Analysis → Selective Replan
  → 只把 Dirty Task 重新送入 Ready Resolver
```

## Orchestrator / Worker 写入边界

- `compile_checklist` 输出事实依赖、Task 依赖、Conflict Key、Executor 和 Result Version；当前七个材料齐套 Task 没有互相依赖，因此可进入同一 Ready Batch。
- `resolve_ready_tasks` 只选择 `PENDING/DIRTY/INVALIDATED` 或当前恢复 Task，生成稳定 `dispatch_id`；不会因一次异常把所有非通过 Task 重跑。
- `dispatch_ready_tasks` 通过 LangGraph `Send` 只发送当前 Task、对应 Requirement、只读 Page Projection 和版本号，不向 Worker 暴露整笔业务状态、Agent 或持久化能力。
- `match_task_worker` 只计算候选 Task Result；`match_materials` 是 Fan-in Gate，串行拒绝陈旧版本、记录冲突组并写回 `audit_plan`。
- 并发上限由 `TASK_WORKER_MAX_CONCURRENCY` 配置；主图 superstep 预算由 `AUDIT_GRAPH_RECURSION_LIMIT` 配置。Exception Sub-Agent 仍保留更严格的独立 MaxStep/Loop Guard。

Case Association 前置 Evidence Worker 使用相同的 `Send + reducer + Fan-in` 结构，但并行单元是已筛选页面。Real Profile 通过 `ToolAssociationEvidenceExtractor` 调统一注册的 `vlm_extract`；模型服务只接收 page_id、bundle、分类、材料类型、预览地址和请求字段，不接收整笔 Case。

## 三个 Agent 为什么同属 `agents/` 却不直接对话

- `agents/case_association/`：只消费页级身份/角色 Evidence 和封闭候选集，提议人员归并、角色绑定和材料归属；最终写入由 Association Gate 持有。
- `agents/material_audit/`：只在 Workflow 给出的封闭候选集中解决所属人、材料类型、跨页分组或 Requirement 归属；无 Tool、无 CaseState 写入权。
- `agents/exception_recovery/`：持有独立 Context 和私有 LangGraph；每轮重新构建 2–4 个 Task-scoped Candidate Tool，由模型基于最新 Observation 选择，再经 Tool Gate、执行、State Diff 与 Completion Policy 决定回环或退出。
- 三个组件通过主图的 Typed Handoff/Result 协作。两个决策 Agent 都不能直接调用 Exception Agent；它们只能返回 `REQUEST_RECOVERY`。Workflow 决定是否委托共享恢复 Sub-Agent，并由 Result Gate 校验结果后再写主状态。

这种结构避免 Agent 之间形成不可追踪的自由对话，也让 Checkpoint、预算、审计事件和最终写入权保持在确定性控制平面。

## 数据层职责

| 数据层 | 位置 | 职责 |
|---|---|---|
| 业务规则 SQLite | `requirements/store.py` | 通过产品、渠道、角色、版本、生效日期确定应交清单；不依赖 Top-K |
| RAG 资产 SQLite | `offline/catalog_store.py` | 保存 Source、Document Version、Parent、Child、Build Run、Index Version |
| Milvus | `requirements/milvus_index.py` | BGE-M3 Dense、Milvus BM25 与 Metadata in-path filter |
| LangGraph Checkpoint | `runtime/checkpoint.py` | `thread_id`、interrupt/resume 与跨进程恢复 |
| Case/Event SQLite | `persistence/repository.py` | Case State、审计事件、SSE 断线续读和展示投影 |
| RAG Cache Port | `rag/cache.py` | Memory/Redis/Null 可切换、作用域版本 Key、TTL、single-flight 与写后反读 |

知识库查询通过 `POST /api/knowledge/runs` 建立 Run，再由标准 SSE 增量发送真实 Intent、Rewrite、Metadata Filter、Dense/BM25、RRF、Rerank、Parent Context、Grounding、Citation 和 Cache 事件；页面不生成定时假进度。

## 模型路由与降级

- Agent、Knowledge 和离线上下文化只依赖 `LLMProvider` 合同，Endpoint Registry 由 Composition Root 统一组装。
- 当前本地路由由 `.env` 的 Endpoint Registry 定义，例如 `primary → mimo-fallback`；厂商、Base URL、Model ID、Thinking 和 JSON Mode 都不进入业务代码。
- 主 Endpoint 使用短超时和一次有界重试；429/5xx/超时/不完整 HTTP 读取视为瞬时错误，401/400 等永久错误不重试。
- 每个 Endpoint 可独立声明 Thinking 与结构化 Token 策略；Fallback 切换不改变 Prompt、Schema、Agent 或 RAG 代码。
- 成功或失败 Trace 只记录 Endpoint 名、错误类型、延迟和最终选择，不记录 Prompt、响应原文或 Key；前端 Execution Inspector 投影该路由。

## Demo 与真实模式

根目录 `demo/` 只固定人物、216 页影像分布和 Tool Observation。Demo 与 Real 共用生产 Graph、Prompt、Agent、ModelGateway、RAG、Checkpoint、SSE 和前端 Projection。真实模式隐藏 `/api/demo/*`，并把 OCR/VLM/材料检索 Tool 指向配置的后端服务。
