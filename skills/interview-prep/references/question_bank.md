# ARGUS 面试题库

> 题目只覆盖当前“复杂信贷进件材料齐套审核 Agent”。首题按 `SKILL.md` 的骰子规则选择；后续追问必须基于候选人原话。

## 方向 1：业务边界与总体架构

### 开场题池（12 题）

1. 用两分钟介绍这个项目解决的业务问题、明确不解决什么，以及你负责的核心部分。
2. 一笔只有分类影像、尚未确认人员和角色的进件，如何走到最终材料齐套结论？
3. 为什么采用“确定性 Workflow + Agentic Execution”，而不是一个大 Agent 从头做到尾？
4. 当前到底有几个 Agent？哪几个有 Tool Loop？它们如何交换信息？
5. Case Association Agent 与 Material Audit Agent 分别解决什么，为什么不能合并？
6. 如果把两个决策 Agent 都改成普通 Workflow 节点，哪些场景会首先失效？
7. RuleEngine、Material Matcher、Audit Agent 与 Evidence RAG 的执行顺序是什么，为什么？
8. 216 页影像进入后，系统如何避免把整笔 Case 都塞给模型？
9. 项目测试如何从结果、轨迹和稳定性三个方面证明核心链路可靠？
10. Demo 与 Real 如何隔离？哪些内容允许写死，哪些必须共用真实实现？
11. 这个项目最难保证的数据一致性是什么？Gate、版本和 Checkpoint 分别解决哪一层？
12. 如果面试官只给你五分钟演示，你会选择哪条业务链路，为什么它最能体现 Agent 设计？

### 即兴追问方向

- 让候选人画出 Node / Edge / Handoff，而非只背模块名。
- 要求给一个人员角色变化导致 Task 清单变化的业务例子。
- 追问 Agent 的“不确定输入”和 Workflow 的“确定输出边界”。
- 追问失败场景、人工兜底、延迟和成本。
- 追问候选人本人做了哪些决策、哪些是团队已有服务。

## 方向 2：简历贡献与方案取舍

### P1：数字与指标（6 题）

1. 你写每笔约 200 张影像，这个规模下模型输入如何裁剪？一次实际发送多少页、多少 Candidate？
2. 你们 Golden Set 有多少条？Agent 与 RAG 分别怎么标注，哪些指标会阻断合并？
3. RAG 的 HitRate@5、Recall@5、MRR、NDCG@5 各自衡量什么，为什么不能只看 HitRate？
4. Exception Loop 的 MaxStep、候选 Tool 数量和置信度阈值怎么定？有没有失败样本支撑？
5. RAG 延迟主要花在哪里？Cache Hit、Dense/BM25、Rerank、Grounding 分别如何观测？
6. `Send` 并发度为什么默认是 4？增加到 20 会先遇到什么瓶颈和一致性风险？

### P2：强动词与设计责任（8 题）

1. 你说负责 Agent 核心方案设计。当时比较过哪些架构，为什么选两个决策 Agent加一个恢复 Sub-Agent？
2. 你说基于 LangGraph 实现主链路。请从 `build_audit_pipeline` 讲清一个完整分支和一次回环。
3. 你说设计动态任务模型。Task 的 Fact Dependency、Task Dependency、Conflict Key 和 Result Version 分别有什么用？
4. 你说实现 Tool/MCP 统一接入。Local Tool 与 MCP Tool 的边界是什么，当前哪些能力真实走了 MCP？
5. 你说实现制度 RAG。为什么全量清单不通过 RAG 找？RuleEngine 和 Evidence RAG 如何使用同一 Requirement ID？
6. 你说实现 Long-Horizon/HITL。跨进程恢复如何证明不是前端重新发起了一次流程？
7. 你说实现异常 Guardrail。哪个 Guardrail 在代码里最关键？它阻止过什么错误轨迹？
8. 如果重新做一次，你会删掉哪个设计、保留哪个设计？给出明确成本和收益。

### P3：技术关键词（10 题）

1. LangGraph `Send` 与普通线程池的差异是什么？为什么 Worker 结果还要 Fan-in Gate？
2. `interrupt()` 和 `Command(resume=...)` 的状态语义是什么？为什么必须使用同一个 `thread_id`？
3. RRF 公式是什么？Dense 与 BM25 分数为什么不能直接相加？
4. Cross-Encoder 为什么只用于小候选集？Rerank 挂了是否应该继续回答？
5. BGE-M3 在这个项目里负责什么？为什么离线切分还要使用它的 Tokenizer？
6. Metadata Pre-filter 为什么必须在向量库内执行，而不是取 Top-K 后再过滤？
7. Parent–Child Chunk 如何生成稳定 ID？LLM Contextual Retrieval 文本能不能作为最终证据？
8. Structured Output、Pydantic 和业务 Gate 三者是不是重复校验？分别防什么？
9. Query Rewrite 与 HyDE 有什么差别？当前为什么采用受约束 Rewrite 而没有默认 HyDE？
10. ModelGateway 是什么？Endpoint Retry、Fallback 与 Schema Retry 应如何分层？

## 方向 3：源码机制与失败处理

### A：LangGraph 主编排

A1. `audit_pipeline.py` 为什么只定义拓扑，不创建 Provider 或写业务算法？
A2. 请指出三个 Exception 来源如何汇聚到唯一 `exception_recovery_agent`，结果如何返回。
A3. `AuditState` 中哪些字段是 Worker reducer 临时状态，哪些是业务 Projection？
A4. 为什么 Association Evidence 与 Material Task 都使用 `Send + reducer + Fan-in`？
A5. 当前 Task 没有相互依赖，为什么仍保留 Dependency Resolver？这算过度设计吗？
A6. Graph recursion limit 与 Exception max_steps 为什么需要两套预算？

### B：两个决策 Agent 与 Gate

B1. Case Association Assignment 如何保证候选是封闭的？模型偷偷返回新 person_id 会在哪里被拒绝？
B2. Association Gate 要校验哪些版本、人员、角色、页面和 Evidence 条件？
B3. Material Audit Candidate 为什么最多 8 个？候选生成如何避免遍历顺序影响结果？
B4. 已确认 Material Owner Binding 与模型新提议冲突时谁优先，为什么？
B5. 模型输出 JSON 合法但业务语义陈旧，Pydantic 能拦住吗？最终由谁拦？
B6. 哪种材料问题应进入 Audit Agent，哪种应直接进入 Exception Agent？

### C：Exception Tool Loop

C1. 私有 Exception Graph 有哪些节点？每一轮为什么回到 `build_candidates`？
C2. Tool Visibility、Master Allowlist、Candidate Limit 和 Tool Gate 分别控制什么？
C3. Duplicate Action 与 State No-Change 如何组合判断死循环？
C4. Completion Condition 为什么按独立证据来源判断，而不是判断 OCR 和 VLM 各执行一次？
C5. 模型在证据不足时输出 `RESOLVE`，代码如何阻止提前完成？
C6. Tool Failure、无候选 Tool、预算耗尽和结构化输出失败分别如何退出？

### D：HITL 与持久化

D1. 为什么 `prepare_human` 和 `await_human` 要拆成两个节点？interrupt 前能做什么、不能做什么？
D2. Resume Command 如何防止提交到错误 Task 或错误人工动作？
D3. `thread_id`、业务 Checkpoint ID 和 LangGraph Checkpoint ID 有什么区别？
D4. SSE 断线后如何续读？为什么这不是“执行完再回放”？
D5. 人工修改材料归属后，`case_version` 和 `changed_facts` 如何变化？
D6. 补件到件后为何只重跑 Dirty Task？给出一个结果复用和一个结果失效的例子。

### E：Requirement RAG

E1. 离线文档如何从官方来源变成 Parent/Child Chunk？说出每一步产物。
E2. 为什么 384/48 不是主要 Chunk 策略？什么条件下才触发 Token Window？
E3. Contextual Retrieval 生成的文本如何进入 Dense/BM25，为什么 Citation 不能引用它？
E4. Milvus 中 Dense 与 BM25 如何共享 Metadata Scope Filter？
E5. RRF 中某个文档只被 Dense 命中时如何计分？BM25 原始分为 0 是否等于排名为 0？
E6. RuleEngine 已给 requirement_id 后，Evidence RAG 还需要 Query Rewrite 或意图识别吗？

### F：知识库、缓存与评估

F1. 知识库为什么只有两个顶层 Intent，却有五个 Query Mode？复合问题如何回答？
F2. 什么问题应 `CLARIFY`，什么问题应 `REFUSE`？给出业务例子。
F3. 地区、分行简称和材料领域如何转成稳定 Metadata，而不是直接相信 LLM 自由文本？
F4. RAG Cache Key 包含什么？为什么模型或 Prompt 版本变化必须失效缓存？
F5. Memory 和 Redis 缓存如何保证“写进去”？Checksum、TTL、写后反读和 single-flight 分别解决什么？
F6. 50 条 Retrieval Golden 如何同时做绝对阈值和 paired bootstrap？为什么按 Case 成对重采样？

## 压力面试通用挑战

- “你这个 Agent 不就是 `if/else + LLM` 吗？为什么叫 Agent？”
- “两个决策 Agent 都不调用 Tool，为什么不能直接用一个 LLM 节点？”
- “Exception Agent 只有三步，真的算 Agentic Execution 吗？”
- “你说证据不足拒答，但业务人员需要答案时怎么办？”
- “RAG 数据只有几十条 Requirement，为什么需要 Milvus、BM25 和 Cross-Encoder？”
- “你怎么证明 Selective Replan 没有复用已经失效的结果？”
- “模型换供应商后 Structured Output 失效，系统会不会把错误写进 Case？”
- “Demo 有固定 Observation，怎么证明业务代码不是写死的？”
