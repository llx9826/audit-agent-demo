# ADR-003：关系与制度审核按需加载 Skill

- 状态：已采纳
- 问题：领域知识放在全局 Prompt 还是模块化加载？
- 决定：按 task_type / exception_type 加载 relation_review、policy_review、exception_resolution。
- 理由：减少无关上下文；领域原则、证据规则、工具指导和 Few-shot 可独立演进。
- 何时会改：建设企业级 Skill Registry 后迁移到集中管理。
