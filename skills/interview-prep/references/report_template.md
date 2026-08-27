# ARGUS 模拟面试报告模板

> 仅在面试结束后读取。报告事实必须来自当前代码和 `project_knowledge.md`，不得复制旧项目答案。

## 1. 输出结构

```markdown
# 模拟面试报告

**项目**：ARGUS · 复杂信贷进件材料齐套审核 Agent
**时间**：{datetime}
**风格**：{style}
**骰子**：{dice}
**综合评分**：{score}/10

## 一、面试记录

| 题号 | 方向 | 问题（原文） | 回答（原文，不摘要） | 判断 |
|---|---|---|---|---|
| Q1 | ... | ... | ... | ✅/⚠️/❌ |

## 二、逐题点评与更强答案

### Q1
- 答对：...
- 缺失/错误：...
- 面试官反应：...
- 代码证据：`绝对路径:行号`
- 60 秒参考答案：...

## 三、简历真实性

### 能自圆其说
- 简历原文 → 回答证据 → 代码证据

### 风险/露馅
- 简历原文 → 具体差距 → 严重性（高/中/低）

## 四、五维评分

| 维度 | 分数 | 具体扣分依据 |
|---|---:|---|
| 业务边界与总体架构 | x | ... |
| Agent / LangGraph 设计 | x | ... |
| RAG 与评估 | x | ... |
| 工程实现与稳定性 | x | ... |
| 表达与取舍 | x | ... |
| **综合** | **x** | ... |

## 五、下一轮训练
1. ...
2. ...
3. ...
```

必须逐字复制 `[QA_LOG]`，包括口头填充词。每题至少指出一个答对点和一个可改进点；完全答对时，改进点可以是更紧凑的表达或量化证据。

## 2. 评分标准

| 分档 | 标准 |
|---|---|
| 9–10 | 业务边界、控制流、Agent 必要性、RAG、HITL 与代码位置均准确；能给指标、失败案例和取舍。 |
| 7–8 | 主干正确，1–2 个实现细节不稳；没有把未实现能力说成已实现。 |
| 5–6 | 能讲名词和业务例子，但 Gate、版本、恢复、评估等至少三处说不清，或出现一处严重架构误解。 |
| 3–4 | 只能描述前端演示或概念；无法解释真实代码、失败分支和数据边界。 |
| 1–2 | 把项目说成贷款审批、把旧 RAG MCP 项目当当前实现，或核心简历内容无法解释。 |

五维权重建议：业务与架构 20%，Agent/LangGraph 25%，RAG 20%，工程稳定性 20%，表达取舍 15%。如果本场没有考到某维度，不得假装有证据；标注“未充分考察”，按已观察内容保守评分。

## 3. 核心参考答案

按实际题目选择、组合并改写，不要把全部答案无差别复制到报告。

### A. 业务边界与架构

> 项目面向宅抵贷等复杂进件，只判断已确认角色需要的材料是否到齐、可读且归属明确，不做准入、额度、风险或放款。上游先完成影像分类和 OCR/VLM 抽取，主 LangGraph 通过页级 Evidence 关联人员与角色，再由 SQLite RuleEngine 按产品、渠道、角色和生效日期生成清单，编译 Person × Requirement Task，并行匹配材料。确定性缺件走 Evidence RAG 绑定依据；语义歧义交给受控决策 Agent；机器 Observation 不足进入共享 Exception Tool Loop；必要时 interrupt 到人工，补件后从 checkpoint 做 Selective Replan。

### B. 为什么是两个决策 Agent 加一个恢复 Sub-Agent

> Case Association Agent 处在事实入口，处理跨页人员归并、角色和材料所属人；Material Audit Agent 处在 Matcher 之后，只处理 Owner、Type、Bundle、Requirement 候选歧义。两者面对的输入合同、失败代价和写入 Gate 不同，不应合并。它们只在封闭候选集内做一次结构化提议，无 Tool、无 State 写权。只有 Exception Recovery 需要依据新 Observation 多步选 Tool，因此使用独立 Context 的私有 LangGraph Loop。三个能力不自由对话，全部由父 Workflow 用 Typed Handoff 和 Result Gate 协调。

### C. 为什么决策 Agent 不是普通 Workflow 节点

> 可枚举的材料清单、依赖、版本和缺件判断留在规则节点。人员别名、跨页组合、材料语义与证据充分性难以穷举，适合模型判断。Agent 与普通 LLM 节点的区别不在“叫 Agent”，而在明确 Objective、Typed Assignment、允许动作、受控候选、独立 Prompt/评估和失败路由；但它仍不拥有业务写权限。若某场景能完全规则化，就应回收到 Workflow。

### D. Orchestrator / Worker 与 Send

> Planner 为 Task 建立 Fact Dependency、Task Dependency、Conflict Key 和 Result Version。Ready Resolver 只派发依赖已满足的 PENDING/DIRTY/INVALIDATED Task。LangGraph Send 给每个 Worker 最小只读上下文，Worker 只返回候选 Result；Fan-in Gate 统一检查 dispatch、Case/Plan/Result Version 和 Conflict Key 后提交。这允许无依赖 Task 并行，又避免并发 Worker 直接修改共享 Case。

### E. Exception Tool Loop

> 三个异常来源先生成统一 ExceptionHandoff，进入唯一共享恢复节点。私有子图每轮都按最新 Observation 重建 2–4 个 Candidate Tool，由模型在允许集合内输出 CALL_TOOL/RESOLVE/ESCALATE。Tool Gate、MaxStep、MaxRetry、Duplicate Action、State No-Change、Pydantic 和 Completion Policy 都在模型外执行。完成要求至少两个独立来源对同一 normalized value 达成高置信共识；模型提前 RESOLVE 会被判 PREMATURE_RESOLVE，预算耗尽或无工具时转 HITL。

### F. Checkpoint 与 Selective Replan

> 人工任务先持久化，再由 interrupt 暂停。客户端以同一 thread_id 和结构化 Command(resume) 恢复，服务校验 task_id/action 与当前请求一致。补件应用后 case_version 增加，Reconciliation 对比新旧状态，Changed Fact Detection 标记变化；Impact Analysis 用 Task Dependency 找受影响任务。未受影响结果复用，受影响结果清空并标记 DIRTY/INVALIDATED，plan_version 增加，只重新派发 Dirty Task。不是从头重跑，也不是机械回到旧 Plan 下一节点。

### G. RuleEngine 与 Evidence RAG

> RuleEngine 必须确定性枚举全量清单，因为漏召回一条必需材料不能由 Top-K 容忍。它查询 SQLite Atomic Requirement Catalog，按产品、渠道、角色、版本和日期过滤。Matcher 确认缺件后，Evidence RAG 才用已知 requirement_id 检索原子条款，为补件说明绑定 Requirement ID、Child Chunk ID 和 Evidence ID。这个 Workflow 入口不需要意图识别；自然语言知识库才需要 Intent、Clarify 和 Refuse。

### H. 离线 RAG 与 Chunk

> 离线从登记的官方来源抓取并保留 Checksum，先做正文抽取和结构归一，再按章/节建立 Parent，按条款、清单项和完整句群建立 Semantic Child。短条款不拆；只有一个语义单元超过 384 Token 时才使用 BGE-M3 Tokenizer 做 48 Token overlap。LLM Contextual Retrieval 生成的产品、地区、机构和章节上下文只进入 Dense/BM25 检索文本，原始 Child 保持不可变并作为 Citation。然后做 Catalog Link、Embedding 和 Milvus Index。

### I. 在线 Hybrid Retrieval

> 在线只做受约束 Query Rewrite、Metadata Pre-filter、BGE-M3 Dense 与 Milvus BM25 双路召回、RRF、Cross-Encoder、Parent Expansion、Grounding 和 Citation Validation。RRF 使用 `1/(k+rank_dense)+1/(k+rank_sparse)`，未命中的通道贡献 0，不能把 BM25 原始分 0 误认为 rank 0。Metadata 必须在两路检索内部前置过滤，以免其他地区或版本占满 Top-K。

### J. 知识库意图与拒答

> 顶层 Intent 只有 MATERIAL_REQUIREMENT 和 SOURCE_TRACE，避免把一个复合问题拆成多个 Agent。Query Mode 可以组合 LOOKUP、APPLICABILITY、WAIVER_OR_SUBSTITUTE、REGION_COMPARISON、SUPPLEMENT。缺产品、地区、人物状态或材料范围时 CLARIFY；审批、额度、风险等越界问题，以及伪造材料、绕过审核请求 REFUSE。接受后把模型实体链接到稳定产品、地区、分行和领域枚举，再进入检索。

### K. RAG Cache

> 使用精确作用域 cache-aside，不做语义近似答案缓存。Key 包含规范化问题、Index Version、Prompt Family 和 Model Signature；结果 Envelope 带 TTL、Key Digest 和 Payload Checksum。Memory 用于本地演示，Redis 用于多实例；同 Key 使用 single-flight 防击穿，写后可反读验证。Redis 配置失败显式失败，不暗中退化成进程内 Memory，否则面试时无法证明多实例命中。

### L. 模型与 Prompt

> 所有 Agent、知识库和离线上下文化只依赖 ModelGateway 合同。主 Endpoint、多个 Fallback、Thinking/JSON 策略和按角色路由都在环境配置与 Composition Root；业务代码没有模型厂商和 ID。瞬时错误只做少量重试，永久错误立即切换或失败；模型返回后还要做 Pydantic Schema 校验，业务 Gate 再验证版本和候选语义。Prompt 以目录版本化，记录 prompt_id/version/sha256，不内联到业务逻辑。

### M. 评估体系

> 评估分结果、轨迹和稳定性。Retrieval Golden 当前 50 条，检查 HitRate@5、Recall@5、MRR、NDCG@5，并做同 Case paired bootstrap 回归门禁。Association/Audit/Exception Golden 检查结构化动作、封闭候选、Evidence、Tool Policy 和完成条件；Knowledge Answer 检查引用、拒答与 Faithfulness。单元测试 mock 模型以保证可复现；真实模型只进入 live eval/smoke。API/E2E 再验证 SSE、人工 Dialog、Checkpoint Resume 和 Selective Replan。

### N. Demo / Real 隔离

> Demo 只在根目录 demo/ 固定 Case、216 页分布和合成 Tool Observation。Real 隐藏 Demo API，并把 OCR/VLM/材料检索 Tool 指向真实服务。两种 Profile 共用主 Graph、Prompt、Agent、ModelGateway、RAG、Checkpoint、SSE 和前端合同，因此演示数据固定不等于业务代码写死。

## 4. 真实性判定规则

以下情况至少标记中风险：

- 把 Material Audit Agent 说成负责生成 Plan 或审批贷款；
- 把两个决策 Agent 说成自由选择 Tool；
- 说 Exception Agent 按固定 OCR→VLM 顺序执行；
- 说 RAG 决定全量应交清单；
- 说所有 Chunk 固定按 384/48 切；
- 说补件后直接沿旧 Plan 继续或整笔重跑；
- 声称所有 Tool 已通过 MCP，或 ModelGateway 是独立远程服务；
- 引用旧 Chroma、旧 MCP Server、1198 tests 等非当前事实。

以下情况标记高风险：

- 无法指出主 Graph 入口和两个 Gate 的作用；
- 无法解释 Checkpoint、Reconciliation、Invalidation、Replan 的差异；
- 无法说明 Golden Set 的输入、期望结果/轨迹和判定指标；
- 把 Demo 固定响应当成真实 Agent 决策代码。
