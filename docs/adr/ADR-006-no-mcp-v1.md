# ADR-006：V1 使用 Typed Tool Contract，不引入 MCP

- 状态：已采纳
- 问题：OCR、VLM、检索和规则工具是否通过 MCP 暴露？
- 决定：V1 采用进程内 Tool Registry 与类型化 Contract。
- 理由：工具目前仅服务单个项目；先验证输入输出、超时、幂等和权限边界更重要。
- 何时会改：工具需要被多个 Agent 平台跨进程发现和复用时。
