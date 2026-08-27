# 业务问题分类

## A. 动态清单与人员角色

公开资料反复体现：申请材料不是一张静态表，而是由产品、用途、地区/经办机构、申请人身份和人员角色共同决定。

- 借款人、配偶、共同购房人、房屋共有人、卖方产权人、受托人会触发不同材料任务。
- 已婚、离婚、未婚、丧偶对应的婚姻材料不同。
- 消费用途、经营用途、组合贷款、新建房、二手房和异地缴存会增加或替换部分材料。
- 银行公开页面明确提示具体标准以当地分行为准，因此 `branch/region` 不是展示字段，而是 Requirement 适用性字段。

对应系统能力：RuleEngine 先根据结构化 Case Fact 生成 `Person × AtomicRequirement`，检索排序不得决定清单成员。

## B. 大体量影像与页级齐套

线上进件不是“有这个文件名就算齐”。公开办事指南会要求证件正反面、合同关键页、登记机关盖章页、房产权属信息页、附记和图纸页等特定页面，并要求内容完整、无遮挡、清晰可见。

因此一个材料任务至少要区分：

- 文件是否存在；
- 必需页是否齐全；
- 页面是否清晰可读；
- 页面属于哪一种材料；
- 页面属于哪一个人员角色；
- 多页合同/证明是否组成同一材料包。

对应系统能力：200+ Page Asset、六大材料域、页级分类、Owner Proposal、Required Page Count 和 Evidence Ledger。

## C. 电子证照与材料来源替代

北京和广州公开指南均体现：能够通过电子证照或数据共享核验时，部分纸质材料可以免提交；查询异常时又可能恢复为纸质补充。

同一 Requirement 因此不能只保存“是否必需”，还要保存：

- `source_mode = UPLOAD / ELECTRONIC_CERTIFICATE / DATA_SHARE`；
- 电子核验是否成功；
- 失败后的替代材料；
- 适用地区、产品、日期和版本。

对应系统能力：Requirement Applicability、Metadata Filter、替代来源状态和失败后的受控恢复。

## D. 语义归属歧义

当一个进件同时包含借款人、配偶、抵押人、共有人或受托人的相似证件时，单纯依靠文件名或 OCR 字段无法稳定确定材料归属。公开材料清单说明了“哪些人都要提供”，但实际扫描包往往不会替系统做好人员归档。

典型问题：

- 两张身份证或两本婚姻证明类型相同，但 Owner 不确定；
- 一份合同同时出现多名人员，不能只按首个姓名归属；
- 人工改了结构化角色后，旧匹配结果需要失效；
- 同一页面可能是 Requirement 的候选证据，但不能由 Agent 直接写入最终状态。

对应系统能力：Audit Agent 接收最小任务包，输出结构化 Proposal；Workflow Plan Gate 验证后才更新 Material Match。

## E. 低置信与工具异常

影像要求“清晰可见”意味着不可读、旋转、遮挡、缺页和 OCR 低置信是正常业务异常，而不是普通业务分支。处理这些异常需要根据新 Observation 动态选择 OCR Retry、VLM 重识别或可信材料检索；如果重复动作没有产生新状态，应停止并升级人工。

对应系统能力：独立 Exception Context、动态 Tool Selection、Allowlist、MaxStep、Timeout/Retry、Duplicate Action、State No-Change、Completion Condition 和 `RESOLVED / NEED_HUMAN`。

## F. 缺件、一次性告知与跨时恢复

官方业务规范和答问材料显示，材料不齐时通常需要一次性告知补正内容；有的场景允许先受理，待申请人补齐后再提交后续处理。

这不是一次对话，而是一个跨时间的状态机：

```text
发现缺件
  → 绑定 Requirement Evidence
  → 人工确认补件动作
  → 持久化 WAITING_SUPPLEMENT
  → 客户经理/客户补件或修正结构化事实
  → State Reconciliation
  → Changed Fact Detection
  → Impact Analysis / Invalidation
  → Selective Replan
  → 只重跑 Dirty Task
```

对应系统能力：LangGraph Checkpoint、`thread_id`、`interrupt()`、`Command(resume=...)`、补件事件和选择性重规划。

## G. 明确排除项

演示和知识库都必须拒答或转交以下问题：

- 是否批准贷款；
- 客户风险等级、信用评分或欺诈结论；
- 可贷额度、利率、期限或定价；
- 房产估值和抵押率；
- 还款能力、用途合规或授信建议；
- 根据材料内容推断未确认的人际/婚姻事实。

本项目只回答：对已确认的人与业务事实，要求的材料是否到齐、是否可读、归属是否明确，以及缺件依据是什么。

