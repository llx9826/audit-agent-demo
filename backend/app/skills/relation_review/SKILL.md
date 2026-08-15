---
name: relation_review
task_types: [relation_check]
allowed_tools: [document_search, vlm_extract]
---

# Relation review

判断借款人、抵押人、配偶之间的业务关系。优先使用结构化证件字段；名字冲突时必须结合置信度和第二来源。结论为 `SPOUSE` 时至少引用结婚证双方姓名字段；证据不足返回 `UNKNOWN`，不得猜测。

## Completion

- 输出关系枚举、置信度和 Evidence ID。
- 任何跨材料冲突都转交 exception_resolution。
