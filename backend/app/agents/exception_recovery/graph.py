"""Exception Recovery Agent 私有 LangGraph Tool Loop。

图只声明循环拓扑；候选裁剪、模型决策、Tool Gate、执行和完成判断分别由
Facade 的 Node 方法负责。主编排层不得导入本模块。
"""
from __future__ import annotations

from typing import Any


def build_exception_graph(agent: Any) -> Any:
    """构建一个有预算、有确定性退出边的私有恢复子图。"""

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise RuntimeError("Install backend/requirements.txt to run LangGraph") from exc

    from .agent import ExceptionAgentState

    graph = StateGraph(ExceptionAgentState)
    graph.add_node("prepare", agent._prepare)
    graph.add_node("build_candidates", agent._build_candidates)
    graph.add_node("select_tool", agent._select_tool)
    graph.add_node("execute_tool", agent._execute_tool)
    graph.add_node("evaluate", agent._evaluate)
    graph.add_node("finish", agent._finish)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "build_candidates")
    graph.add_conditional_edges(
        "build_candidates",
        agent._route_after_candidates,
        {"select": "select_tool", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "select_tool",
        agent._route_after_select,
        {"execute": "execute_tool", "finish": "finish"},
    )
    graph.add_edge("execute_tool", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        agent._route_after_evaluate,
        {"loop": "build_candidates", "finish": "finish"},
    )
    graph.add_edge("finish", END)
    # 子图没有 interrupt，也不需要跨调用记忆；每次恢复任务独立运行。
    return graph.compile(checkpointer=False)
