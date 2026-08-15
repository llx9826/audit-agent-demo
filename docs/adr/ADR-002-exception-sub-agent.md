# ADR-002：异常恢复使用独立 Sub-Agent

- 状态：已采纳
- 问题：异常恢复是否放进主 Audit Agent？
- 决定：建立独立 Exception Recovery Agent。
- 理由：异常路径需要独立 Prompt、上下文、工具白名单、Step Budget 与完成条件，隔离后更容易观测和防循环。
- 何时会改：异常类型足够稳定，可全部转为确定性规则时。
