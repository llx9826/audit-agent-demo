---
name: policy_review
task_types: [policy_review]
allowed_tools: [policy_search, rule_eval]
---

# Policy review

先按产品、生效日期和版本做适用性过滤，再比较检索分数。语义最相似的条款若版本失效，不得作为最终依据。每个结论必须返回 Rule ID、制度版本与 Evidence ID。

## Completion

- final_rule 必须通过 metadata filter。
- 结论未关联 rule_id 时返回 `NEED_HUMAN`。
