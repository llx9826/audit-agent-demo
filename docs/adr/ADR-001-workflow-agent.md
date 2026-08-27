# ADR-001：Workflow + Agent，而不是 Full Agent

- 状态：已采纳
- 问题：复杂审核是否全部交给 Agent 自主规划？
- 决定：LangGraph 控制确定性主流程；Case Association Agent 只消解人员、角色和材料归属候选，Material Audit Agent 只消解材料类型、所属人、跨页分组和 Requirement 归属候选。制度检索只为确定性缺件绑定来源依据，不由 Agent 判断贷款规则或审批结果。
- 理由：缺件、字段相等、依赖传播和最终完整性都可编码。Full Agent 会增加上下文、协调、调试和非确定性成本。
- 何时会改：任务路径高度开放且规则无法稳定表达时。
