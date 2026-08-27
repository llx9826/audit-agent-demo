# ARGUS 项目知识库

> 供 `interview-prep` 面试官使用。以当前仓库代码为事实来源；数字可能变化时先运行对应命令，不要背历史值。

## 目录

1. 业务边界与输入
2. 架构总览与代码入口
3. 人员关联 Agent
4. 动态任务与并行 Worker
5. 材料语义仲裁 Agent
6. Exception Recovery Sub-Agent
7. HITL、Checkpoint 与 Selective Replan
8. Requirement RuleEngine 与两类 RAG
9. 离线 RAG
10. 在线 RAG 与知识库意图
11. 模型、Prompt、Tool 与 MCP
12. 持久化、SSE 与前端演示
13. 评估与测试
14. 高频露馅点与代码导航

## 1. 业务边界与输入

ARGUS 面向宅抵贷等多人员、多角色、多影像进件，回答：**已确认角色应提供的材料是否到齐、可读且归属明确**。

它不做以下判断：

- 贷款是否通过、额度、利率、估值和放款；
- 信用风险、反欺诈、还款能力和准入；
- 材料内容是否满足贷款政策中的实体审批规则。

上游已经完成影像接收、初步分类和 OCR/VLM 字段抽取。真实入口可以只提供分类页面；`persons` 是兼容 Seed，不直接视为可信人员。人员实体、角色、材料所属人仍需经过 Evidence → Case Association Agent → Association Gate。

Demo 固定一笔 216 页、36 个 Bundle、6 类影像的宅抵贷 Case。固定数据只在根目录 `demo/`；Demo 与 Real 共用 Graph、Prompt、Agent、RAG、Checkpoint、SSE 和前端合同。

## 2. 架构总览与代码入口

一句话架构：

> 一个确定性 LangGraph 控制平面，两个一次性结构化决策 Agent，一个独立有界 Tool Loop 的共享异常恢复 Sub-Agent。

控制流入口：`backend/app/orchestration/audit_pipeline.py::build_audit_pipeline`。

依赖合同：`backend/app/orchestration/dependencies.py::AuditPipelineDependencies`。

组合根：`backend/app/bootstrap/container.py`。只有这里组装 Profile、ModelGateway、Tool Registry、RAG、Agent、Graph、RunManager 和 Orchestrator。

应用门面：`backend/app/orchestration/orchestrator.py::AuditOrchestrator`，统一 `start/resume/inspect/events`，自身不复制业务路由。

主链路：

```text
Case/Page 入件
→ Send 页级 Association Evidence Worker
→ Evidence Fan-in Gate
→ Case Association Agent
→ Association Gate
→ SQLite Requirement RuleEngine
→ 编译 Person × Requirement Task
→ Dependency Resolver
→ Send Material Match Worker
→ Fan-in Plan Gate
→ Outcome Router
   ├─ 明确缺件/不可读 → Evidence RAG → HITL
   ├─ 语义歧义 → Material Audit Agent → Plan Gate
   ├─ 机器异常/缺 Observation → Shared Exception Recovery → Result Gate
   └─ 齐套 → Final Validator
→ interrupt / Command(resume)
→ Reconciliation → Impact Analysis → Selective Replan
```

为什么不是三个平级自治 Agent：Case Association 与 Material Audit 都是“封闭候选 + 一次结构化提议”；只有 Exception Recovery 有多步 Tool Loop。它们不自由对话，通过主图的 Typed Assignment/Handoff/Result 和 Gate 交互。

## 3. 人员关联 Agent

代码：

- `backend/app/agents/case_association/contracts.py`
- `backend/app/agents/case_association/agent.py`
- `backend/app/orchestration/stages/association.py`
- Prompt：`backend/app/prompts/case_association/v3/`

职责：从页级 `IdentityMention`、`RoleSignal`、`MaterialOwnerSignal` 构造封闭候选，处理人员实体归并、业务角色绑定和材料所属人。

允许动作：

- `APPLY_CANDIDATES`
- `REQUEST_HUMAN`
- `REQUEST_RECOVERY`

Agent 不能新增人员、角色、页面或证据，也不能直接调用 Tool。输出先经过 Pydantic 判别联合校验，再由 Association Gate 校验 `case_id/thread_id/case_version`、候选成员关系、人员/角色一致性、页码范围和 Evidence，最后才写入 Projection。

为什么需要 Agent：姓名别名、跨页身份提及、借款人/抵押人/配偶重叠和材料归属存在开放语义判断，规则难以穷举；但候选范围和写权限仍可确定性控制。

## 4. 动态任务与并行 Worker

代码：

- `backend/app/planning/planner.py`
- `backend/app/orchestration/stages/planning.py`
- `backend/app/orchestration/stages/matching.py`

RuleEngine 先按产品、渠道、角色、版本和生效日期选出适用 Atomic Requirement。Planner 对每个 `Person × Requirement` 生成 `RequiredMaterialTask`，包含：

- `fact_dependencies`
- `task_dependencies`
- `conflict_keys`
- `requirement_refs`
- `result_version`
- `executor/execution_group`

`resolve_ready_tasks` 选择 `PENDING/DIRTY/INVALIDATED` 且依赖已满足的任务。`dispatch_ready_tasks` 使用 LangGraph `Send` 分发 Task-scoped 只读上下文。Worker 不接触整笔 Case、Agent 或持久化能力，只计算候选 Result。

`match_materials` 是 Fan-in Gate：拒绝陈旧 `case_version/plan_version/result_version/dispatch_id`，按 Conflict Key 串行提交。当前 Demo 的七个材料任务没有互相依赖，可进入同一 Ready Batch；架构支持有依赖任务，不应谎称当前 Demo 已展示复杂 DAG。

## 5. 材料语义仲裁 Agent

代码：

- `backend/app/agents/material_audit/agent.py`
- 共享合同：`backend/app/agents/contracts.py`
- `backend/app/orchestration/stages/review.py`
- Prompt：`backend/app/prompts/material_audit/v4/`

职责只覆盖四类歧义：

- `OWNER_AMBIGUOUS`
- `TYPE_AMBIGUOUS`
- `BUNDLE_AMBIGUOUS`
- `REQUIREMENT_MATCH_AMBIGUOUS`

Workflow 构造最多 8 个稳定排序候选。已确认 Material Owner Binding 是权威约束；候选必须属于当前 Case、当前 Plan、当前 Requirement/Person/Material 组合。

允许动作：`APPLY_CANDIDATE / REQUEST_HUMAN / REQUEST_RECOVERY`。Agent 无 Tool、无 Case State 写权。Plan Gate 再校验版本、候选 ID、页面范围、已确认人员、Owner Binding 和 Evidence。

为什么不是普通节点：普通节点适合明确规则和可枚举逻辑；这里要理解跨页组合、材料语义和开放证据，但仍必须在候选集内做判断。若所有输入可规则化，应优先留在 Workflow，不为“看起来像 Agent”而调用模型。

## 6. Exception Recovery Sub-Agent

代码：

- `backend/app/agents/exception_recovery/graph.py`
- `backend/app/agents/exception_recovery/agent.py`
- `backend/app/agents/exception_recovery/tool_policy.py`
- `backend/app/agents/completion_policy.py`
- 主图适配：`backend/app/orchestration/stages/recovery.py`
- Prompt：`backend/app/prompts/exception_next_action/v1/`

三个来源 `CASE_ASSOCIATION / MATERIAL_MATCHER / MATERIAL_AUDIT` 先构造统一、版本化 `ExceptionHandoff`，汇入唯一 `exception_recovery_agent`，再经 `exception_result_gate` 返回 Association、Matcher 或 HITL。

私有子图：

```text
prepare → build_candidates → select_tool → execute_tool → evaluate
             ↑                                      │
             └──────────── loop ─────────────────────┘
                                      → finish
```

每轮根据最新 Observation 重建 2–4 个 Task-scoped Candidate Tool，不执行固定计划。当前本地能力包括：

- `ocr_retry`
- `vlm_extract`
- `document_search`
- `neighbor_page_search`
- `page_integrity_check`
- `document_reload`

Guardrail：Tool Allowlist、Task Intent 可见性、MaxStep、MaxRetry、Duplicate Action、State No-Change、结构化输出校验、Completion Condition 和 Result Gate。

默认完成条件不是“某个 Tool 调过就成功”，而是 `NORMALIZED_VALUE_CONSENSUS`：至少两个独立来源、置信度达到阈值、Evidence 闭合。模型提前 `RESOLVE` 会得到 `PREMATURE_RESOLVE`，预算耗尽或无安全动作则升级 HITL。

## 7. HITL、Checkpoint 与 Selective Replan

代码：

- `backend/app/orchestration/stages/hitl.py`
- `backend/app/orchestration/stages/reconciliation.py`
- `backend/app/service.py`
- `backend/app/runtime/checkpoint.py`

HITL 在 `prepare_human` 先建立可重放状态，再由 `await_human` 调 `interrupt(payload)`。恢复必须使用相同 `thread_id` 的 `Command(resume=structured_command)`，并校验 action、task_id 和当前请求一致。

支持人工确认归属、复核影像、发起补件和补件到件。补件到件后：

1. 从同一 Checkpoint 承接旧状态；
2. 应用补件并递增 `case_version`；
3. State Reconciliation 合并新旧状态；
4. Changed Fact Detection 产生 `page/person/material/...` 变化；
5. Impact Analysis 根据 Fact/Task Dependency 找受影响任务；
6. 保留未受影响结果，清空受影响结果并标记 `DIRTY/INVALIDATED`；
7. 递增 `plan_version`，Selective Replan 只派发 Dirty Task。

不是“回退到依赖之前”，也不是整笔重跑。Checkpoint 是恢复载体，Reconciliation 是对账，Invalidation 是一致性保护，Selective Replan 是最小执行范围。

## 8. Requirement RuleEngine 与两类 RAG

业务规则 SQLite：`backend/app/rag/requirements/store.py`。当前种子为 77 条 Atomic Requirement。

RuleEngine：`backend/app/rag/requirements/rule_engine.py`。它按 `product + channel + person_role + effective_date + ACTIVE version` 枚举全量应交清单，不依赖 Top-K。

两类在线使用：

1. **Workflow Evidence RAG**：`RequirementEvidenceRAG`。入口已有确定的 `requirement_id`，不做意图识别；只在缺件或不可读问题产生后绑定 `Requirement ID + Child Chunk ID + Evidence ID`。
2. **材料知识库**：自然语言入口。先做意图识别、实体抽取、澄清/拒答，再做 Metadata scoped Retrieval 和 grounded answer；不会修改 Case。

## 9. 离线 RAG

代码：`backend/app/rag/offline/` 与 `backend/scripts/`。

真实流程：Source Registry → robots-aware 抓取 → 原始快照与 Checksum → 正文抽取/格式归一/内容范围 → 章节 Parent → 条款、清单项或完整句群 Semantic Unit → 超长单元 Token Window 兜底 → LLM Contextual Retrieval → Catalog Link → BGE-M3 Embedding / Milvus Index。

切分原则：先结构、再语义，不按固定 Token 无脑切。短条款保留原文；只有单个语义单元超过 384 Token 时，才使用 BGE-M3 Tokenizer 做 48 Token overlap。LLM 生成的上下文与别名只用于检索增强，Citation 仍引用不可变原始 Child。

当前快照：12 个官方来源、19 个 Parent、187 个 Child；数据以 `build_manifest.json` 为准。

离线 Prompt：`backend/app/prompts/offline_contextualization/v1/`。记录 Prompt ID、Version、SHA256 和 Chunk 输入哈希；成功结果缓存，批任务失败可重跑而不重复调用已完成 Chunk。

## 10. 在线 RAG 与知识库意图

代码：

- `backend/app/rag/online/pipeline.py`
- `backend/app/rag/requirements/hybrid.py`
- `backend/app/rag/requirements/milvus_index.py`
- `backend/app/knowledge/service.py`
- `backend/app/knowledge/contracts.py`
- `backend/app/knowledge/taxonomy.py`

在线链路：Query Rewrite → Metadata Pre-filter → BGE-M3 Dense + Milvus BM25 → RRF → Cross-Encoder Rerank → Parent Context Expansion → LLM Grounding → Citation Validator / Refuse。

Metadata 在 Dense 与 BM25 查询内部前置过滤，不是 Top-K 后过滤。范围包含产品、渠道、人员角色、版本/日期、地区、分行和材料领域。

知识库顶层意图只有两个：

- `MATERIAL_REQUIREMENT`
- `SOURCE_TRACE`

检索模式可组合：`LOOKUP / APPLICABILITY / WAIVER_OR_SUBSTITUTE / REGION_COMPARISON / SUPPLEMENT`。同一个问题既问要求又问依据时，通过多个 `answer_modes` 回答，不拆成多个 Agent。

路由为 `ACCEPT / CLARIFY / REFUSE`。缺产品、地区、人物状态或材料范围时澄清；贷款审批、额度、风险等越界问题和伪造/绕过审核请求必须拒答。

缓存：`backend/app/rag/cache.py`。Local 使用 Memory TTL，生产可切 Redis，未配置可 Null。Key 包含规范化问题、Index Version、Prompt Family 和 Model Signature；地区/产品等通过问题与已校验结果作用域隔离。Cache-aside 使用 single-flight；写入 Envelope 带过期时间和 Payload Checksum，可配置写后反读验证。Redis 配置失败显式失败，不伪装成 Memory。

## 11. 模型、Prompt、Tool 与 MCP

模型适配：`backend/app/providers/gateway.py` 与 `backend/app/providers/decision_adapters.py`。

业务代码只依赖 Provider 合同。`.env` 定义主 Endpoint、任意 Fallback 和按角色路由：`association/audit/exception/knowledge_intent/knowledge_grounding/query_rewrite/offline_contextualization`。瞬时错误做少量有界重试；401/400 等永久错误直接切换或失败。模型切换不改变 Prompt、Schema、Agent 或 RAG 代码。

Prompt 全部版本化存放在 `backend/app/prompts/`，业务代码不内联长 Prompt。结构化结果即使由 Provider JSON Mode 返回，仍需 Pydantic 校验。

Tool Schema：`backend/app/tools/`。本项目领域能力为 Local Tool；跨业务通用能力可以通过 `backend/app/tools/mcp/adapter.py` 的惰性 MCP Adapter 接入。不要声称当前所有 Tool 都走 MCP。

## 12. 持久化、SSE 与前端演示

- LangGraph Checkpoint：`thread_id`、interrupt/resume、跨进程恢复。
- Case/Event SQLite：Case Projection、审计事件、SSE 序号和断线续读。
- RunManager：后台执行、事件唤醒、`Last-Event-ID` 之后继续读取，不是执行完成后伪回放。
- RAG Catalog SQLite：Source、Document Version、Parent、Child、Build Run 和 Index Version。
- Milvus：Dense/BM25 与 Metadata Filter。

前端四个页签：架构演进、材料审核、人机闭环、材料知识库。架构演进依次展示：V1 确定性 Workflow；V2 两个决策 Agent；V3 共享 Exception Recovery Sub-Agent。运行时检查器显示 Task Ledger、Agent 决策、Tool Observation、Gate、Checkpoint Spine 和 Selective Replan，不显示模型思维链。

## 13. 评估与测试

Golden 资产：

- `requirement_retrieval.jsonl`：50 条 Retrieval Case；
- `case_association_golden.jsonl`：4 条；
- `material_audit_golden.jsonl`：4 条；
- `exception_candidate_golden.jsonl`：5 条；
- `knowledge_answer_golden.jsonl`：5 条。

RAG 绝对门禁：HitRate@5 ≥ 0.95、Recall@5 ≥ 0.95、MRR ≥ 0.60、NDCG@5 ≥ 0.75；再与同 Case 基线做 2,000 次 paired bootstrap，统计显著回归才阻断。

Agent 评估检查结构化动作、候选封闭性、Evidence、Tool Policy、完成条件和轨迹。知识答案评估检查引用、拒答和 Faithfulness。确定性单测应 mock 模型；真实模型只进入 live eval/smoke，避免普通单测不稳定。

命令：

```text
make test
make verify
make describe
make rag-eval
make rag-answer-eval
make agent-eval
make agent-live-eval
make e2e
```

测试数量会变化；面试官若追问当前数字，先执行测试，不引用历史知识库。

## 14. 高频露馅点与代码导航

| 错误表述 | 正确判断 | 首选代码 |
|---|---|---|
| “三个 Agent 互相对话” | 主 Workflow 用 Typed Handoff 协调；Agent 不自由对话 | `audit_pipeline.py`、`recovery.py` |
| “Audit Agent 是 Plan-and-Solve” | Planner 确定性生成 Task；Material Audit 只做封闭候选消歧 | `planner.py`、`review.py` |
| “Exception 按 OCR→VLM 固定执行” | 每轮按 Observation 重建 Candidate Tool 并由模型选下一步 | `exception_recovery/agent.py` |
| “RAG 生成材料清单” | RuleEngine 枚举清单；RAG 只绑定证据或回答知识问题 | `rule_engine.py`、`evidence.py` |
| “所有文本按 384/48 切” | 章节/条款/清单项优先；384/48 只处理超长语义单元 | `offline/document_processing.py` |
| “补件后回到旧节点继续” | 同 checkpoint 恢复后对账、失效影响任务、选择性重规划 | `hitl.py`、`reconciliation.py` |
| “Worker 并行写 Case” | Worker 只读；Fan-in Gate 唯一提交 | `matching.py` |
| “模型输出就是业务结果” | Structured Output → Pydantic → Gate → State | Agent contracts 与 Stage Gate |
| “ModelGateway 是远程服务” | 它是进程内模型适配/路由层 | `providers/gateway.py` |
| “Demo 业务代码写死” | 固定 Case/Observation 只在 `demo/`，生产 Graph 共用 | `demo/`、`bootstrap/container.py` |
