"""整笔进件唯一、可执行、可生成图的 LangGraph Pipeline。

该文件只声明 Stage、分支与循环，不实现业务算法、不读环境变量、不创建
Provider/RAG/Agent。面试代码导览从这里开始即可看清所有控制流。
"""
from __future__ import annotations

from functools import lru_cache, partial
from typing import Any

from ..graph.state import AuditState
from .dependencies import AuditPipelineDependencies
from .stages import (
    association, evidence, finalization, hitl, intake, matching, planning, reconciliation,
    recovery, review,
)


@lru_cache(maxsize=1)
def describe_audit_pipeline() -> dict[str, Any]:
    """从已编译 Graph 反射拓扑，杜绝文档再维护第二份 Node/Edge 定义。"""

    from langgraph.checkpoint.memory import InMemorySaver

    unresolved = object()
    compiled = build_audit_pipeline(
        AuditPipelineDependencies(
            requirement_resolver=unresolved,
            requirement_evidence_rag=unresolved,
            association_evidence_extractor=unresolved,
            case_association_agent=unresolved,
            exception_agent=unresolved,
            material_audit_agent=unresolved,
        ),
        checkpointer=InMemorySaver(),
    )
    runtime_graph = compiled.get_graph()
    nodes = [name for name in runtime_graph.nodes if name not in {"__start__", "__end__"}]
    edges = [{
        "source": edge.source,
        "target": edge.target,
        "label": edge.data,
        "conditional": edge.conditional,
    } for edge in runtime_graph.edges]

    return {
        "entrypoint": "app.orchestration.audit_pipeline:build_audit_pipeline",
        "stages": nodes,
        "edges": edges,
        "mermaid": runtime_graph.draw_mermaid(),
        "handoffs": {
            "case_association": "AssociationAssignment -> AssociationDecision -> AssociationGate",
            "exception_recovery": "ExceptionHandoff -> shared Exception Agent -> ResultGate",
            "material_agent_review": "MaterialAuditAssignment -> MaterialAuditDecision -> PlanGate",
            "human": "interrupt(payload) -> Command(resume=HumanResumeCommand)",
        },
    }


def build_audit_pipeline(
    dependencies: AuditPipelineDependencies,
    *,
    checkpointer: Any,
) -> Any:
    """构建唯一主图；缺少任一 Capability 时在启动期失败。"""

    if checkpointer is None:
        raise ValueError("audit pipeline requires a checkpointer for HITL")
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise RuntimeError("Install backend/requirements.txt to run LangGraph") from exc

    graph = StateGraph(AuditState)

    # 01 进件与关联：页级证据 -> 受控 Agent 提议 -> Gate 写入。
    graph.add_node("ingest_case", intake.run)
    graph.add_node("select_association_pages", association.select_pages)
    graph.add_node(
        "extract_association_page",
        partial(
            association.extract_page,
            evidence_extractor=dependencies.association_evidence_extractor,
        ),
    )
    graph.add_node("extract_association_evidence", association.extract_evidence)
    graph.add_node("build_association_candidates", association.build_candidates)
    graph.add_node(
        "case_association_agent",
        partial(association.review, association_agent=dependencies.case_association_agent),
    )
    graph.add_node("association_gate", association.gate)
    graph.add_node("prepare_association_human", association.prepare_human)
    graph.add_node("await_association_human", association.await_human)
    graph.add_node("apply_association_human", association.apply_human)

    # 02 确定性 Workflow：动态清单、材料匹配。
    graph.add_node(
        "resolve_requirements",
        partial(planning.resolve_requirements, resolver=dependencies.requirement_resolver),
    )
    graph.add_node("compile_checklist", planning.compile_checklist)
    graph.add_node("resolve_ready_tasks", matching.resolve_ready_tasks)
    graph.add_node("match_task_worker", matching.match_task_worker)
    graph.add_node("match_materials", matching.match_materials)
    graph.add_node("validate_completeness", matching.validate_completeness)

    # 03 受控能力：异常恢复、缺件依据、材料语义消歧。
    graph.add_node(
        "prepare_association_recovery",
        partial(recovery.prepare_handoff, origin="CASE_ASSOCIATION"),
    )
    graph.add_node(
        "prepare_matcher_recovery",
        partial(recovery.prepare_handoff, origin="MATERIAL_MATCHER"),
    )
    graph.add_node(
        "prepare_material_recovery",
        partial(recovery.prepare_handoff, origin="MATERIAL_AUDIT"),
    )
    graph.add_node(
        "exception_recovery_agent",
        partial(recovery.run, exception_agent=dependencies.exception_agent),
    )
    graph.add_node("exception_result_gate", recovery.result_gate)
    graph.add_node(
        "ground_requirement_evidence",
        partial(
            evidence.run,
            requirement_evidence_rag=dependencies.requirement_evidence_rag,
        ),
    )
    graph.add_node(
        "material_agent_review",
        partial(review.run, material_agent=dependencies.material_audit_agent),
    )
    graph.add_node("audit_plan_gate", review.plan_gate)
    graph.add_node("prepare_problem_human", hitl.prepare_problem)

    # 04 HITL 与 Long-Horizon 恢复。
    graph.add_node("prepare_human", hitl.prepare)
    graph.add_node("await_human", hitl.await_resume)
    graph.add_node("apply_human_command", hitl.apply_command)
    graph.add_node("reconcile_state", reconciliation.reconcile)
    graph.add_node("selective_replan", reconciliation.selective_replan)
    graph.add_node("final_validator", finalization.run)

    graph.add_edge(START, "ingest_case")
    graph.add_edge("ingest_case", "select_association_pages")
    graph.add_conditional_edges(
        "select_association_pages",
        association.dispatch_pages,
        {"extract_association_page": "extract_association_page"},
    )
    graph.add_edge("extract_association_page", "extract_association_evidence")
    graph.add_conditional_edges(
        "extract_association_evidence",
        association.route_after_evidence,
        {"recover": "prepare_association_recovery", "candidates": "build_association_candidates"},
    )
    graph.add_edge("build_association_candidates", "case_association_agent")
    graph.add_edge("case_association_agent", "association_gate")
    graph.add_conditional_edges(
        "association_gate",
        association.route_after_gate,
        {
            "human": "prepare_association_human",
            "recover": "prepare_association_recovery",
            "requirements": "resolve_requirements",
        },
    )
    graph.add_edge("prepare_association_human", "await_association_human")
    graph.add_edge("await_association_human", "apply_association_human")
    graph.add_conditional_edges(
        "apply_association_human",
        association.route_after_human,
        {"gate": "association_gate", "retry": "select_association_pages"},
    )
    graph.add_edge("resolve_requirements", "compile_checklist")
    graph.add_edge("compile_checklist", "resolve_ready_tasks")
    graph.add_conditional_edges(
        "resolve_ready_tasks",
        matching.dispatch_ready_tasks,
        {"match_task_worker": "match_task_worker", "match_materials": "match_materials"},
    )
    graph.add_edge("match_task_worker", "match_materials")
    graph.add_conditional_edges(
        "match_materials",
        matching.recovery_route,
        {"recover": "prepare_matcher_recovery", "validate": "validate_completeness"},
    )
    graph.add_conditional_edges(
        "validate_completeness",
        matching.issue_route,
        {
            "audit": "material_agent_review",
            "ground": "ground_requirement_evidence",
            "complete": "final_validator",
        },
    )
    graph.add_edge("ground_requirement_evidence", "prepare_problem_human")
    graph.add_edge("prepare_problem_human", "prepare_human")
    graph.add_edge("material_agent_review", "audit_plan_gate")
    graph.add_conditional_edges(
        "audit_plan_gate",
        review.route_after_gate,
        {"human": "prepare_human", "recover": "prepare_material_recovery", "rematch": "resolve_ready_tasks"},
    )
    # 三个来源汇聚到唯一共享恢复节点；Result Gate 再按 Handoff.return_target 回程。
    graph.add_edge("prepare_association_recovery", "exception_recovery_agent")
    graph.add_edge("prepare_matcher_recovery", "exception_recovery_agent")
    graph.add_edge("prepare_material_recovery", "exception_recovery_agent")
    graph.add_edge("exception_recovery_agent", "exception_result_gate")
    graph.add_conditional_edges(
        "exception_result_gate",
        recovery.route_after_result,
        {
            "association_retry": "select_association_pages",
            "association_human": "prepare_association_human",
            "matcher_retry": "resolve_ready_tasks",
            "material_human": "prepare_human",
        },
    )
    graph.add_edge("prepare_human", "await_human")
    graph.add_edge("await_human", "apply_human_command")
    graph.add_edge("apply_human_command", "reconcile_state")
    graph.add_edge("reconcile_state", "selective_replan")
    graph.add_edge("selective_replan", "resolve_ready_tasks")
    graph.add_edge("final_validator", END)
    return graph.compile(checkpointer=checkpointer)
