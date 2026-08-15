"""Executable LangGraph workflow for the local audit demonstration.

All business transitions live here.  ``AuditService`` persists the graph's
append-only event specs and state, but does not recreate audit decisions.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any, Literal

from ..agents.exception_agent import ExceptionRecoveryAgent, ExceptionTask
from ..domain.models import AuditResult, AuditTask
from ..planning.planner import impacted_task_ids, selective_replan
from ..planning.reconciliation import reconcile
from ..rag.hybrid import demo_policy_trace
from .state import AuditState


def _snapshot(state: AuditState, patch: dict[str, Any], node: str) -> dict[str, Any]:
    tasks = patch.get("audit_plan", state.get("audit_plan", []))
    fields = patch.get("business_fields", state.get("business_fields", {}))
    return {
        "status": patch.get("status", state.get("status", "READY")),
        "active_node": node,
        "current_task_id": patch.get("current_task_id", state.get("current_task_id")),
        "case_version": patch.get("case_version", state.get("case_version", 1)),
        "plan_version": patch.get("plan_version", state.get("plan_version", 1)),
        "relation": fields.get("relation", "UNKNOWN"),
        "task_statuses": {task["task_id"]: task["status"] for task in tasks},
        "changed_facts": patch.get("changed_facts", state.get("changed_facts", [])),
        "dirty_tasks": patch.get("dirty_tasks", state.get("dirty_tasks", [])),
        "invalidated_tasks": patch.get("invalidated_tasks", state.get("invalidated_tasks", [])),
    }


def _event(
    state: AuditState,
    patch: dict[str, Any],
    *,
    event_type: str,
    node: str,
    actor: str,
    task_id: str | None = None,
    action: str | None = None,
    tool: str | None = None,
    observation: Any = None,
    state_diff: Any = None,
    evidence: list[Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = _snapshot(state, patch, node)
    payload = {
        "node": node,
        "actor": actor,
        "task_id": task_id,
        "action": action,
        "tool": tool,
        "observation": observation,
        "state_diff": state_diff if state_diff is not None else {},
        "evidence": evidence or [],
        "evidence_refs": evidence or [],
        "case_version": snapshot["case_version"],
        "plan_version": snapshot["plan_version"],
        "state_snapshot": snapshot,
    }
    if details:
        payload.update(details)
    return {"event_type": event_type, "actor": actor, "payload": payload}


def _evidence(
    evidence_id: str,
    source_type: str,
    source_id: str,
    value: str,
    *,
    document_id: str | None = None,
    field: str | None = None,
    rule_id: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "source_id": source_id,
        "value": value,
        "document_id": document_id,
        "page": None,
        "field": field,
        "rule_id": rule_id,
        "confidence": confidence,
    }


def _append_evidence(ledger: list[dict[str, Any]], *items: dict[str, Any]) -> list[dict[str, Any]]:
    result = deepcopy(ledger)
    existing = {item["evidence_id"] for item in result}
    for item in items:
        if item["evidence_id"] not in existing:
            result.append(item)
            existing.add(item["evidence_id"])
    return result


def _result(
    state: AuditState,
    task_id: str,
    conclusion: str,
    evidence_refs: list[str],
    rule_refs: list[str] | None = None,
    confidence: float = .99,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "PASS",
        "conclusion": conclusion,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "rule_refs": rule_refs or [],
        "case_version": state.get("case_version", 1),
        "plan_version": state.get("plan_version", 1),
    }


def _complete_task(
    tasks: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    next_tasks = deepcopy(tasks)
    next_results = deepcopy(results)
    task_id = result["task_id"]
    next_results[task_id] = result
    for task in next_tasks:
        if task["task_id"] == task_id:
            task["status"] = "SUCCESS"
            task["result"] = result
            break
    return next_tasks, next_results


def _set_task_status(tasks: list[dict[str, Any]], task_ids: set[str], status: str) -> list[dict[str, Any]]:
    next_tasks = deepcopy(tasks)
    for task in next_tasks:
        if task["task_id"] in task_ids:
            task["status"] = status
    return next_tasks


def _as_audit_task(value: dict[str, Any]) -> AuditTask:
    result = value.get("result")
    return AuditTask(
        task_id=value["task_id"],
        task_type=value["task_type"],
        status=value.get("status", "PENDING"),
        depends_on=list(value.get("depends_on", [])),
        required_documents=list(value.get("required_documents", [])),
        required_entities=list(value.get("required_entities", [])),
        result=AuditResult(**result) if result else None,
    )


def _ingest(state: AuditState) -> dict[str, Any]:
    patch = {"status": "RUNNING", "active_node": "ingest", "current_task_id": None}
    event = _event(
        state, patch, event_type="CASE_INGESTED", node="ingest", actor="workflow",
        action="NORMALIZE_DOCUMENT_MANIFEST", observation=f"{len(state.get('documents', []))} documents registered",
    )
    return {**patch, "pending_events": [event]}


def _build_state(state: AuditState) -> dict[str, Any]:
    patch = {"active_node": "build_state"}
    event = _event(
        state, patch, event_type="STATE_BUILT", node="build_state", actor="workflow",
        action="BUILD_CANONICAL_CASE_STATE",
        observation={"entities": list(state.get("entities", {})), "relation": state.get("business_fields", {}).get("relation")},
    )
    return {**patch, "pending_events": [event]}


def _dynamic_plan(state: AuditState) -> dict[str, Any]:
    patch = {"active_node": "dynamic_plan"}
    event = _event(
        state, patch, event_type="PLAN_CREATED", node="dynamic_plan", actor="planner",
        action="COMPILE_DEPENDENCY_AWARE_PLAN",
        observation={"task_count": len(state.get("audit_plan", [])), "plan_version": state.get("plan_version", 1)},
    )
    return {**patch, "pending_events": [event]}


def _deterministic_checks(state: AuditState) -> dict[str, Any]:
    tasks = state.get("audit_plan", [])
    results = state.get("task_results", {})
    ledger = state.get("evidence_ledger", [])

    borrower_evidence = _evidence(
        "E-DOC-01", "DOCUMENT_FIELD", "DOC-01.name", "张三",
        document_id="DOC-01", field="name", confidence=.99,
    )
    borrower_result = _result(state, "T01", "借款人身份与进件信息一致", ["E-DOC-01"])
    tasks_after_t1, results_after_t1 = _complete_task(tasks, results, borrower_result)
    ledger_after_t1 = _append_evidence(ledger, borrower_evidence)
    t1_patch = {
        "audit_plan": tasks_after_t1, "task_results": results_after_t1,
        "evidence_ledger": ledger_after_t1, "current_task_id": "T01", "active_node": "deterministic_checks",
    }
    t1_event = _event(
        state, t1_patch, event_type="TASK_COMPLETED", node="deterministic_checks", actor="rule",
        task_id="T01", action="EXACT_IDENTITY_MATCH", tool="deterministic_rule",
        observation="borrower_id.name == application.borrower.name", evidence=["E-DOC-01"],
    )

    mortgagor_evidence = _evidence(
        "E-DOC-02", "DOCUMENT_FIELD", "DOC-02.name", "李四",
        document_id="DOC-02", field="name", confidence=.99,
    )
    mortgagor_result = _result(state, "T02", "抵押人身份与产权人一致", ["E-DOC-02"])
    final_tasks, final_results = _complete_task(tasks_after_t1, results_after_t1, mortgagor_result)
    final_ledger = _append_evidence(ledger_after_t1, mortgagor_evidence)
    patch = {
        "audit_plan": final_tasks, "task_results": final_results, "evidence_ledger": final_ledger,
        "current_task_id": "T02", "active_node": "deterministic_checks",
    }
    t2_event = _event(
        state, patch, event_type="TASK_COMPLETED", node="deterministic_checks", actor="rule",
        task_id="T02", action="EXACT_IDENTITY_MATCH", tool="deterministic_rule",
        observation="mortgagor_id.name == property_certificate.owner", evidence=["E-DOC-02"],
    )
    fields = state.get("business_fields", {})
    company_age = int(fields.get("company_age_months", 0))
    holding_months = int(fields.get("property_holding_months", 0))
    purchase_price = int(fields.get("purchase_price", 0))
    appraised_value = int(fields.get("appraised_value", 0))
    contract_amount = int(fields.get("purchase_contract_amount", 0))
    deviation = float(fields.get("valuation_deviation", 0))
    business_specs = [
        (
            "T08", "E-FACT-COMPANY-AGE", "company_age_months", str(company_age),
            "TRIGGER_ENHANCED_BUSINESS_REVIEW",
            f"企业成立 {company_age} 个月，已触发经营真实性增强核验",
            {"company_age_months": company_age, "enhanced_review": company_age < 12},
        ),
        (
            "T09", "E-FACT-PROPERTY-AGE", "property_holding_months", str(holding_months),
            "FLAG_SHORT_HOLDING_PERIOD",
            f"抵押房产持有 {holding_months} 个月，已进入短期持有复核",
            {"property_holding_months": holding_months, "enhanced_review": holding_months < 12},
        ),
        (
            "T10", "E-FACT-VALUATION", "valuation_comparison",
            f"purchase={purchase_price}; appraisal={appraised_value}; deviation={deviation:.1%}",
            "COMPARE_PURCHASE_AND_APPRAISAL",
            f"评估价较原成交价偏离 {deviation:.1%}，采用审慎估值并保留人工复核条件",
            {"purchase_price": purchase_price, "appraised_value": appraised_value, "deviation": deviation},
        ),
        (
            "T11", "E-FACT-PURPOSE", "purchase_contract_amount", str(contract_amount),
            "APPLY_ENTRUSTED_PAYMENT_CONTROL",
            f"采购合同金额 {contract_amount // 10_000} 万元，适用受托支付并核验交易对手",
            {"contract_amount": contract_amount, "payment_control": "ENTRUSTED_PAYMENT"},
        ),
    ]
    events = [t1_event, t2_event]
    current_tasks, current_results, current_ledger = final_tasks, final_results, final_ledger
    for task_id, evidence_id, source_id, value, action, conclusion, observation in business_specs:
        evidence = _evidence(evidence_id, "CASE_FACT", source_id, value, confidence=.99)
        result = _result(state, task_id, conclusion, [evidence_id], confidence=.99)
        current_tasks, current_results = _complete_task(current_tasks, current_results, result)
        current_ledger = _append_evidence(current_ledger, evidence)
        patch = {
            "audit_plan": current_tasks,
            "task_results": current_results,
            "evidence_ledger": current_ledger,
            "current_task_id": task_id,
            "active_node": "deterministic_checks",
        }
        events.append(_event(
            state, patch, event_type="RULE_CHECK_COMPLETED", node="deterministic_checks", actor="rule",
            task_id=task_id, action=action, tool="deterministic_rule",
            observation=observation, evidence=[evidence_id],
            state_diff={f"task_status.{task_id}": ["PENDING", "SUCCESS"]},
        ))
    return {**patch, "pending_events": events}


def _needs_exception(state: AuditState) -> Literal["exception", "relation"]:
    return "exception" if state.get("business_fields", {}).get("ocr_conflict") else "relation"


def _audit_route_decision(state: AuditState) -> dict[str, Any]:
    has_conflict = bool(state.get("business_fields", {}).get("ocr_conflict"))
    selected = "exception_recovery" if has_conflict else "relation_review"
    rejected = ["relation_review"] if has_conflict else ["exception_recovery"]
    patch = {"active_node": "audit_route", "current_task_id": "T03"}
    event = _event(
        state, patch, event_type="ROUTE_EVALUATED", node="audit_route", actor="router",
        task_id="T03", action="EVALUATE_EXCEPTION_HANDOFF",
        observation={"ocr_conflict": has_conflict, "selected_edge": selected},
        details={
            "route": {
                "source_node": "deterministic_checks",
                "predicate": "business_fields.ocr_conflict == true",
                "actual_value": has_conflict,
                "selected_edge": selected,
                "rejected_edges": rejected,
                "reason_code": "OCR_CONFLICT_REQUIRES_RECOVERY" if has_conflict else "NO_EXTRACTION_CONFLICT",
            },
        },
    )
    return {**patch, "pending_events": [event]}


def _exception_recovery(state: AuditState) -> dict[str, Any]:
    fields = state.get("business_fields", {})
    allowed_tools = fields.get("exception_allowed_tools") or ["ocr_retry", "vlm_extract", "document_search"]
    tool_plan = fields.get("exception_tool_plan") or ["ocr_retry", "vlm_extract", "document_search"]
    agent = ExceptionRecoveryAgent(max_steps=int(fields.get("exception_max_steps", 3)), allowed_tools=allowed_tools)
    exception_task = ExceptionTask(
        "OCR_CONFLICT", "T03", "户口簿 OCR 张叁 != 身份证 张三", ["E-DOC-01", "E-DOC-04"],
        ["DOC-01.name", "DOC-04.name"],
    )
    result = agent.resolve_ocr_conflict(exception_task, tool_plan=tool_plan)
    tasks = _set_task_status(state.get("audit_plan", []), {"T03"}, "RUNNING")
    context = {
        "exception_type": "OCR_CONFLICT",
        "source_task_id": "T03",
        "skill": "exception_resolution",
        "tool_allowlist": result.allowed_tools,
        "step_budget": agent.max_steps,
        "steps_used": result.steps_used,
        "completion_condition": "identity value confirmed by two independent sources",
        "execution_mode": "OFFLINE_DETERMINISTIC_TOOLS",
        "status": result.status,
        "stop_reason": result.stop_reason,
        "loop_guard_triggered": result.loop_guard_triggered,
        "tool_trace": deepcopy(result.actions),
    }
    running_patch = {
        "audit_plan": tasks, "current_task_id": "T03", "active_node": "exception_recovery",
        "exception_context": context,
    }
    events = [_event(
        state, running_patch, event_type="EXCEPTION_RAISED", node="exception_recovery", actor="workflow",
        task_id="T03", action="DELEGATE_BOUNDED_EXCEPTION",
        observation=exception_task.problem, evidence=exception_task.evidence_refs,
        details={"exception": context},
    )]
    events.append(_event(
        state, running_patch, event_type="HANDOFF_CREATED", node="exception_recovery", actor="workflow",
        task_id="T03", action="CREATE_TYPED_HANDOFF", observation="ExceptionEnvelope validated",
        evidence=exception_task.evidence_refs,
        details={
            "handoff": {
                "handoff_type": "EXCEPTION_ENVELOPE",
                "from": "audit_route",
                "to": "exception_recovery_subgraph",
                "exception_type": exception_task.exception_type,
                "source_task_id": exception_task.source_task_id,
                "problem": exception_task.problem,
                "context_refs": exception_task.context_refs,
                "evidence_refs": exception_task.evidence_refs,
                "allowed_tools": result.allowed_tools,
                "step_budget": result.step_budget,
                "completion_condition": context["completion_condition"],
                "exit_contract": ["RESOLVED", "NEED_HUMAN"],
                "execution_mode": context["execution_mode"],
            },
        },
    ))
    events.append(_event(
        state, running_patch, event_type="SKILL_LOADED", node="exception_recovery", actor="exception_agent",
        task_id="T03", action="LOAD_EXCEPTION_RESOLUTION_CONTRACT",
        observation={
            "skill": "exception_resolution",
            "step_budget": agent.max_steps,
            "registered_tools": list(agent.registry.names),
            "allowed_tools": result.allowed_tools,
        },
    ))
    for item in result.actions:
        step = int(item["step"])
        events.append(_event(
            state, running_patch, event_type="AGENT_TOOL_STARTED", node="exception_recovery", actor="exception_agent",
            task_id="T03", action="EXECUTE_ALLOWLISTED_TOOL", tool=str(item["tool"]),
            observation={"status": "STARTED", "state_hash": item["state_hash_before"]},
            details={
                "agent_step": {
                    "step": step,
                    "step_budget": result.step_budget,
                    "remaining_budget": item["remaining_budget_before"],
                    "allowed": item["allowed"],
                    "registered": item["registered"],
                },
            },
        ))
        events.append(_event(
            state, running_patch, event_type="TOOL_CALLED", node="exception_recovery", actor="exception_agent",
            task_id="T03", action="RECOVERY_TOOL_STEP", tool=item["tool"], observation=item["result"],
            evidence=list(item.get("evidence_refs", [])),
            details={"step": step, "step_budget": agent.max_steps, "remaining_budget": item["remaining_budget_after"]},
        ))
        events.append(_event(
            state, running_patch, event_type="AGENT_TOOL_FINISHED", node="exception_recovery", actor="exception_agent",
            task_id="T03", action="RECORD_TOOL_OBSERVATION", tool=str(item["tool"]),
            observation={
                "result": item["result"],
                "normalized_value": item.get("normalized_value"),
                "error_code": item.get("error_code"),
            },
            state_diff={
                "agent_state_hash": [item["state_hash_before"], item["state_hash_after"]],
                "evidence_added": item.get("evidence_refs", []),
            },
            evidence=list(item.get("evidence_refs", [])),
            details={
                "agent_step": {
                    "step": step,
                    "step_budget": result.step_budget,
                    "remaining_budget": item["remaining_budget_after"],
                    "state_changed": item.get("state_changed", False),
                    "executed": item["executed"],
                    "allowed": item["allowed"],
                    "registered": item["registered"],
                },
            },
        ))

    events.append(_event(
        state, running_patch, event_type="AGENT_RETURNED", node="exception_recovery", actor="exception_agent",
        task_id="T03", action="RETURN_TYPED_RESOLUTION", observation=result.conclusion,
        evidence=result.evidence_refs,
        details={
            "agent_result": {
                "status": result.status,
                "confidence": result.confidence,
                "stop_reason": result.stop_reason,
                "steps_used": result.steps_used,
                "step_budget": result.step_budget,
                "loop_guard_triggered": result.loop_guard_triggered,
                "evidence_refs": result.evidence_refs,
                "execution_mode": context["execution_mode"],
            },
        },
    ))

    next_ledger = state.get("evidence_ledger", [])
    evidence_added: list[str] = []
    if "E-VLM-01" in result.evidence_refs:
        vlm_action = next((item for item in result.actions if item["tool"] == "vlm_extract" and item["executed"]), None)
        recovery_evidence = _evidence(
            "E-VLM-01", "VLM_EXTRACTION", "DOC-04.name", str(vlm_action.get("normalized_value", "张三")) if vlm_action else "张三",
            document_id="DOC-04", field="name", confidence=float(vlm_action.get("confidence") or .0) if vlm_action else None,
        )
        next_ledger = _append_evidence(next_ledger, recovery_evidence)
        evidence_added.append("E-VLM-01")
    next_task_status = "PENDING" if result.status == "RESOLVED" else "WAITING_HUMAN"
    tasks = _set_task_status(tasks, {"T03"}, next_task_status)
    patch = {
        **running_patch,
        "audit_plan": tasks,
        "evidence_ledger": next_ledger,
    }
    events.append(_event(
        state, patch, event_type="STATE_PATCH_APPLIED", node="exception_recovery", actor="workflow",
        task_id="T03", action="APPLY_AGENT_RESOLUTION", observation="parent state updated from typed AgentResult",
        state_diff={
            "task_status.T03": ["RUNNING", next_task_status],
            "evidence_added": evidence_added,
            "exception_status": result.status,
        },
        evidence=evidence_added,
        details={"patch_paths": ["audit_plan.T03.status", "evidence_ledger", "exception_context"]},
    ))
    events.append(_event(
        state, patch,
        event_type="EXCEPTION_RESOLVED" if result.status == "RESOLVED" else "EXCEPTION_NEEDS_HUMAN",
        node="exception_recovery", actor="exception_agent",
        task_id="T03", action="RETURN_TO_PARENT_WORKFLOW", observation=result.conclusion,
        evidence=result.evidence_refs,
        details={
            "resolution_status": result.status,
            "confidence": result.confidence,
            "steps_used": result.steps_used,
            "stop_reason": result.stop_reason,
            "loop_guard_triggered": result.loop_guard_triggered,
        },
    ))
    return {**patch, "pending_events": events}


def _exception_route(state: AuditState) -> Literal["relation", "human"]:
    context = state.get("exception_context") or {}
    return "relation" if context.get("status") == "RESOLVED" else "human"


def _exception_route_decision(state: AuditState) -> dict[str, Any]:
    context = state.get("exception_context") or {}
    resolved = context.get("status") == "RESOLVED"
    selected = "relation_review" if resolved else "wait_human"
    patch = {"active_node": "exception_exit_route", "current_task_id": "T03"}
    event = _event(
        state, patch, event_type="ROUTE_EVALUATED", node="exception_exit_route", actor="router",
        task_id="T03", action="EVALUATE_AGENT_EXIT_CONTRACT",
        observation={
            "agent_status": context.get("status"),
            "stop_reason": context.get("stop_reason"),
            "selected_edge": selected,
        },
        details={
            "route": {
                "source_node": "exception_recovery",
                "predicate": "agent_result.status == RESOLVED",
                "actual_value": resolved,
                "selected_edge": selected,
                "rejected_edges": ["wait_human" if resolved else "relation_review"],
                "reason_code": "AGENT_RESOLUTION_ACCEPTED" if resolved else context.get("stop_reason", "AGENT_NEEDS_HUMAN"),
            },
        },
    )
    return {**patch, "pending_events": [event]}


def _relation_review(state: AuditState) -> dict[str, Any]:
    marriage = next((doc for doc in state.get("documents", []) if doc.get("type") == "marriage_certificate"), None)
    missing = marriage is None or marriage.get("status") != "VERIFIED"
    evidence_refs = ["E-VLM-01"] if state.get("exception_context") else []
    audit_decision = {
        "relation": "UNKNOWN" if missing else "SPOUSE",
        "relation_hypothesis": "SPOUSE",
        "reason_code": "RELATION_EVIDENCE_GAP" if missing else "RELATION_EVIDENCE_CONFIRMED",
        "evidence_refs": evidence_refs,
        "task_intents": ["VERIFY_MARRIAGE_DOCUMENT"] if missing else ["VERIFY_SPOUSE_IDENTITY", "VERIFY_SPOUSE_CONSENT"],
        "requires_human": missing,
        "write_authority": "WORKFLOW_PLAN_GATE",
    }
    patch = {"active_node": "relation_review", "current_task_id": "T03"}
    event = _event(
        state, patch, event_type="RELATION_REVIEWED", node="relation_review", actor="audit_agent",
        task_id="T03", action="CHECK_RELATION_EVIDENCE", tool="document_registry",
        observation="marriage_certificate missing" if missing else "marriage_certificate available",
        evidence=evidence_refs,
        details={"requires_human": missing, "audit_decision": audit_decision},
    )
    return {**patch, "pending_events": [event]}


def _relation_route(state: AuditState) -> Literal["human", "continue"]:
    marriage = next((doc for doc in state.get("documents", []) if doc.get("type") == "marriage_certificate"), None)
    return "human" if marriage is None or marriage.get("status") != "VERIFIED" else "continue"


def _relation_route_decision(state: AuditState) -> dict[str, Any]:
    marriage = next((doc for doc in state.get("documents", []) if doc.get("type") == "marriage_certificate"), None)
    available = marriage is not None and marriage.get("status") == "VERIFIED"
    selected = "document_check" if available else "provisional_policy_review"
    patch = {"active_node": "relation_route", "current_task_id": "T03"}
    event = _event(
        state, patch, event_type="ROUTE_EVALUATED", node="relation_route", actor="router",
        task_id="T03", action="EVALUATE_RELATION_EVIDENCE_COMPLETENESS",
        observation={"marriage_certificate_verified": available, "selected_edge": selected},
        details={
            "route": {
                "source_node": "relation_review",
                "predicate": "marriage_certificate.status == VERIFIED",
                "actual_value": available,
                "selected_edge": selected,
                "rejected_edges": ["provisional_policy_review" if available else "document_check"],
                "reason_code": "RELATION_EVIDENCE_COMPLETE" if available else "RELATION_EVIDENCE_GAP",
            },
        },
    )
    return {**patch, "pending_events": [event]}


def _provisional_policy_review(state: AuditState) -> dict[str, Any]:
    evidence = _evidence(
        "E-RULE-PRECHECK", "POLICY_RULE", "NFRA-2024-PERSONAL-LOAN", "基于 UNKNOWN 关系事实的个人贷款初审结论",
        rule_id="NFRA-2024-PERSONAL-LOAN", confidence=.90,
    )
    result = _result(
        state, "T05", "当前 UNKNOWN 关系事实下初审通过；关系补证后需重新评估",
        ["E-RULE-PRECHECK"], ["NFRA-2024-PERSONAL-LOAN"], confidence=.90,
    )
    tasks, results = _complete_task(state.get("audit_plan", []), state.get("task_results", {}), result)
    patch = {
        "audit_plan": tasks, "task_results": results,
        "evidence_ledger": _append_evidence(state.get("evidence_ledger", []), evidence),
        "active_node": "provisional_policy_review", "current_task_id": "T05",
    }
    event = _event(
        state, patch, event_type="TASK_COMPLETED", node="provisional_policy_review", actor="audit_agent",
        task_id="T05", action="PROVISIONAL_POLICY_REVIEW", observation=result["conclusion"],
        evidence=["E-RULE-PRECHECK"], details={"provisional": True, "invalidation_dependency": "relation"},
    )
    return {**patch, "pending_events": [event]}


def _wait_human(state: AuditState) -> dict[str, Any]:
    exception_context = state.get("exception_context") or {}
    unresolved_exception = exception_context.get("status") == "NEED_HUMAN"
    if unresolved_exception:
        request = {
            "type": "MANUAL_IDENTITY_REVIEW_REQUIRED",
            "documents": ["clear_household_register"],
            "reason": "受控异常恢复未在预算内形成双源一致证据",
            "reason_code": exception_context.get("stop_reason", "EVIDENCE_UNRESOLVED"),
            "resume_contract": {"required_fields": ["event_id", "manual_identity_review"]},
        }
        waiting_tasks = {"T03"}
    else:
        request = {
            "type": "SUPPLEMENT_REQUIRED",
            "documents": ["marriage_certificate"],
            "reason": "姓名冲突已解决，但婚姻关系仍缺少有效材料",
            "reason_code": "RELATION_EVIDENCE_GAP",
            "resume_contract": {"required_fields": ["event_id", "marriage_certificate.husband", "marriage_certificate.wife"]},
        }
        waiting_tasks = {"T03", "T04"}
    tasks = _set_task_status(state.get("audit_plan", []), waiting_tasks, "WAITING_HUMAN")
    patch = {
        "audit_plan": tasks, "pending_human_request": request, "status": "WAITING_HUMAN",
        "active_node": "wait_human", "current_task_id": None,
    }
    event = _event(
        state, patch, event_type="HITL_REQUESTED", node="wait_human", actor="workflow",
        action="DURABLE_INTERRUPT", observation=request["reason"], details={"request": request},
    )
    return {**patch, "pending_events": [event]}


def _document_check(state: AuditState) -> dict[str, Any]:
    fields = deepcopy(state.get("business_fields", {}))
    fields["relation"] = "SPOUSE"
    relation_evidence = _evidence(
        "E-REL-01", "DOCUMENT_CROSS_CHECK", "DOC-05", "张三-李四:配偶",
        document_id="DOC-05", confidence=.99,
    )
    document_evidence = _evidence(
        "E-DOC-05", "DOCUMENT", "DOC-05", "marriage_certificate verified",
        document_id="DOC-05", confidence=.99,
    )
    tasks, results = _complete_task(
        state.get("audit_plan", []), state.get("task_results", {}),
        _result(state, "T03", "婚姻关系证据充分", ["E-REL-01"]),
    )
    tasks, results = _complete_task(tasks, results, _result(state, "T04", "婚姻材料齐备", ["E-DOC-05"]))
    patch = {
        "business_fields": fields, "audit_plan": tasks, "task_results": results,
        "evidence_ledger": _append_evidence(state.get("evidence_ledger", []), relation_evidence, document_evidence),
        "active_node": "document_check", "current_task_id": "T04",
    }
    events = [
        _event(
            state, patch, event_type="TASK_COMPLETED", node="document_check", actor="audit_agent",
            task_id="T03", action="CROSS_CHECK_RELATION", observation="relation=SPOUSE", evidence=["E-REL-01"],
        ),
        _event(
            state, patch, event_type="TASK_COMPLETED", node="document_check", actor="rule",
            task_id="T04", action="VERIFY_REQUIRED_DOCUMENT", observation="marriage_certificate verified", evidence=["E-DOC-05"],
        ),
    ]
    return {**patch, "pending_events": events}


def _supplement_ingest(state: AuditState) -> dict[str, Any]:
    event = state.get("resume_event") or {}
    certificate = event.get("marriage_certificate")
    if not certificate:
        raise ValueError("resume requires marriage_certificate")
    documents = deepcopy(state.get("documents", []))
    for document in documents:
        if document.get("type") == "marriage_certificate":
            document.update({"status": "VERIFIED", "fields": deepcopy(certificate)})
            break
    next_version = state.get("case_version", 1) + 1
    patch = {
        "documents": documents, "case_version": next_version, "status": "RUNNING",
        "pending_human_request": None, "active_node": "supplement_ingest", "current_task_id": "T04",
    }
    audit_event = _event(
        state, patch, event_type="SUPPLEMENT_RECEIVED", node="supplement_ingest", actor="human",
        task_id="T04", action="VALIDATE_AND_INGEST_SUPPLEMENT", tool="document_ingest",
        observation={"event_id": event.get("event_id"), "document": "marriage_certificate"},
        state_diff={"case_version": [state.get("case_version", 1), next_version], "marriage_certificate": ["MISSING", "VERIFIED"]},
        evidence=["DOC-05"],
    )
    return {**patch, "pending_events": [audit_event]}


def _reconcile_state(state: AuditState) -> dict[str, Any]:
    fields = state.get("business_fields", {})
    old_facts = {
        "borrower": state.get("entities", {}).get("borrower", {}).get("name"),
        "mortgagor": state.get("entities", {}).get("mortgagor", {}).get("name"),
        "relation": fields.get("relation", "UNKNOWN"),
    }
    merged, changed = reconcile(old_facts, state.get("resume_event") or {})
    next_fields = deepcopy(fields)
    next_fields.update({key: value for key, value in merged.items() if key != "documents"})
    evidence = _evidence(
        "E-DOC-05-V2", "DOCUMENT", "DOC-05", "marriage_certificate verified by supplement",
        document_id="DOC-05", confidence=.99,
    )
    provisional_state = dict(state)
    t04_result = _result(provisional_state, "T04", "补件验真后确定性解决", ["E-DOC-05-V2"])
    tasks, results = _complete_task(state.get("audit_plan", []), state.get("task_results", {}), t04_result)
    patch = {
        "business_fields": next_fields, "changed_facts": changed,
        "audit_plan": tasks, "task_results": results,
        "evidence_ledger": _append_evidence(state.get("evidence_ledger", []), evidence),
        "active_node": "reconcile", "current_task_id": "T04",
    }
    event = _event(
        state, patch, event_type="STATE_RECONCILED", node="reconcile", actor="reconciler",
        task_id="T04", action="MERGE_AND_DIFF_CANONICAL_FACTS",
        observation={"changed_facts": changed},
        state_diff={"relation": [old_facts["relation"], next_fields.get("relation")], "resolved_task": "T04"},
        evidence=["E-DOC-05-V2"],
    )
    patch_event = _event(
        state, patch, event_type="STATE_PATCH_APPLIED", node="reconcile", actor="reconciler",
        task_id="T04", action="APPLY_RECONCILED_FACTS",
        observation="canonical facts and deterministic task resolution committed",
        state_diff={
            "business_fields.relation": [old_facts["relation"], next_fields.get("relation")],
            "changed_facts": changed,
            "task_status.T04": ["WAITING_HUMAN", "SUCCESS"],
            "evidence_added": ["E-DOC-05-V2"],
        },
        evidence=["E-DOC-05-V2"],
        details={"patch_paths": ["business_fields.relation", "changed_facts", "audit_plan.T04", "evidence_ledger"]},
    )
    return {**patch, "pending_events": [event, patch_event]}


def _impact_analysis(state: AuditState) -> dict[str, Any]:
    tasks = [_as_audit_task(task) for task in state.get("audit_plan", [])]
    impacted = impacted_task_ids(tasks, state.get("changed_facts", []))
    decisions = [
        {"task_id": "T01", "decision": "KEEP", "detail": "REUSE"},
        {"task_id": "T02", "decision": "KEEP", "detail": "REUSE"},
        {"task_id": "T03", "decision": "RERUN", "detail": "relation changed"},
        {"task_id": "T04", "decision": "RESOLVED", "detail": "supplement deterministically verified"},
        {"task_id": "T05", "decision": "INVALIDATED_RERUN", "detail": "policy input relation changed"},
        {"task_id": "T06", "decision": "ADD", "detail": "spouse relation activated"},
        {"task_id": "T07", "decision": "ADD", "detail": "spouse relation activated"},
    ]
    patch = {"replan_decisions": decisions, "active_node": "impact_analysis", "current_task_id": None}
    event = _event(
        state, patch, event_type="IMPACT_ANALYZED", node="impact_analysis", actor="planner",
        action="TRAVERSE_FACT_TASK_DEPENDENCIES", observation={"impacted_tasks": impacted, "decisions": decisions},
        state_diff={"changed_facts": state.get("changed_facts", [])},
    )
    return {**patch, "pending_events": [event]}


def _selective_replan_node(state: AuditState) -> dict[str, Any]:
    revised = selective_replan(
        [_as_audit_task(task) for task in state.get("audit_plan", [])],
        state.get("changed_facts", []),
        state.get("business_fields", {}).get("relation", "UNKNOWN"),
        resolved_task_ids={"T04"},
    )
    tasks = [asdict(task) for task in revised]
    next_plan_version = state.get("plan_version", 1) + 1
    results = deepcopy(state.get("task_results", {}))
    results.pop("T05", None)
    if "T04" in results:
        results["T04"]["plan_version"] = next_plan_version
        for task in tasks:
            if task["task_id"] == "T04":
                task["result"] = results["T04"]
    patch = {
        "audit_plan": tasks, "task_results": results, "plan_version": next_plan_version,
        "dirty_tasks": ["T03"], "invalidated_tasks": ["T05"],
        "active_node": "selective_replan", "current_task_id": None,
    }
    event = _event(
        state, patch, event_type="PLAN_REVISED", node="selective_replan", actor="planner",
        action="SELECTIVE_REPLAN", observation={"plan_version": next_plan_version},
        state_diff={"plan_version": [state.get("plan_version", 1), next_plan_version], "plan_diff": state.get("replan_decisions", [])},
    )
    patch_event = _event(
        state, patch, event_type="STATE_PATCH_APPLIED", node="selective_replan", actor="planner",
        action="COMMIT_REVISED_PLAN", observation="only impacted task results were invalidated",
        state_diff={
            "plan_version": [state.get("plan_version", 1), next_plan_version],
            "dirty_tasks": ["T03"],
            "invalidated_tasks": ["T05"],
            "added_tasks": ["T06", "T07"],
            "reused_tasks": ["T01", "T02"],
            "resolved_tasks": ["T04"],
        },
        details={"patch_paths": ["plan_version", "audit_plan", "task_results.T05", "dirty_tasks", "invalidated_tasks"]},
    )
    return {**patch, "pending_events": [event, patch_event]}


def _rerun_impacted(state: AuditState) -> dict[str, Any]:
    tasks = state.get("audit_plan", [])
    results = state.get("task_results", {})
    ledger = state.get("evidence_ledger", [])
    specs = [
        ("T03", "补件与主体信息交叉验证，关系确认为 SPOUSE", "E-REL-V2", "DOC-05 relation cross-check"),
        ("T06", "配偶身份验真通过", "E-SPOUSE-ID", "DOC-02 spouse identity"),
        ("T07", "配偶同意材料验真通过", "E-SPOUSE-CONSENT", "DOC-06 spouse consent"),
    ]
    events: list[dict[str, Any]] = []
    for task_id, conclusion, evidence_id, value in specs:
        evidence = _evidence(evidence_id, "DOCUMENT_CROSS_CHECK", value.split()[0], value, confidence=.98)
        ledger = _append_evidence(ledger, evidence)
        intermediate = dict(state)
        intermediate["case_version"] = state.get("case_version", 1)
        intermediate["plan_version"] = state.get("plan_version", 1)
        result = _result(intermediate, task_id, conclusion, [evidence_id], confidence=.98)
        tasks, results = _complete_task(tasks, results, result)
        task_patch = {
            "audit_plan": tasks, "task_results": results, "evidence_ledger": ledger,
            "active_node": "rerun_impacted", "current_task_id": task_id,
        }
        events.append(_event(
            state, task_patch, event_type="TASK_REEXECUTED" if task_id == "T03" else "TASK_ADDED_COMPLETED",
            node="rerun_impacted", actor="audit_agent" if task_id == "T03" else "rule",
            task_id=task_id, action="EXECUTE_REVISED_PLAN_TASK", observation=conclusion, evidence=[evidence_id],
        ))
    patch = {
        "audit_plan": tasks, "task_results": results, "evidence_ledger": ledger,
        "active_node": "rerun_impacted", "current_task_id": "T07",
    }
    return {**patch, "pending_events": events}


def _policy_grounding(state: AuditState) -> dict[str, Any]:
    trace = demo_policy_trace()
    filtered_rule = next(item for item in trace["candidates"] if item["rule_id"] == "DEMO-COST-2025")
    personal_loan_rule = next(item for item in trace["candidates"] if item["rule_id"] == "NFRA-2024-PERSONAL-LOAN")
    selected_rule = next(item for item in trace["candidates"] if item["rule_id"] == "NFRA-2026-COST-01")
    policy_evidence = _evidence(
        "E-RULE-LOAN-2024", "POLICY_RULE", "NFRA-2024-PERSONAL-LOAN",
        "个人贷款调查覆盖经营情况、用途、第一还款来源与抵押物权属价值",
        rule_id="NFRA-2024-PERSONAL-LOAN", confidence=.99,
    )
    cost_evidence = _evidence(
        "E-RULE-COST-2026", "POLICY_RULE", "NFRA-2026-COST-01", trace["grounding"]["clause"],
        rule_id="NFRA-2026-COST-01", confidence=.99,
    )
    policy_result = _result(
        state, "T05", "现行个人贷款规则已完成经营、用途、还款来源与抵押物审查",
        ["E-RULE-LOAN-2024"], ["NFRA-2024-PERSONAL-LOAN"], confidence=.99,
    )
    cost_result = _result(
        state, "T12", "案例日期为 2026-08-15，签约前须完成综合融资成本明示与客户确认",
        ["E-RULE-COST-2026"], ["NFRA-2026-COST-01"], confidence=.99,
    )
    grounding_tasks = deepcopy(state.get("audit_plan", []))
    if not any(task["task_id"] == "T12" for task in grounding_tasks):
        grounding_tasks.append(asdict(AuditTask(
            "T12", "financing_cost_disclosure",
            depends_on=["case_date", "product_type"],
        )))
    tasks, results = _complete_task(grounding_tasks, state.get("task_results", {}), policy_result)
    tasks, results = _complete_task(tasks, results, cost_result)
    patch = {
        "audit_plan": tasks, "task_results": results,
        "evidence_ledger": _append_evidence(state.get("evidence_ledger", []), policy_evidence, cost_evidence),
        "rag_trace": trace, "active_node": "policy_grounding", "current_task_id": "T12",
    }
    events = [
        _event(
            state, patch, event_type="RAG_STARTED", node="policy_grounding", actor="audit_agent",
            task_id="T05", action="REWRITE_QUERY_WITH_CASE_FACTS", tool="policy_search",
            observation=trace["rewritten_query"],
        ),
        _event(
            state, patch, event_type="POLICY_FILTERED", node="policy_grounding", actor="retriever",
            task_id="T05", action="APPLICABILITY_GATE", tool="metadata_filter",
            observation={"rule_id": filtered_rule["rule_id"], "dense_score": filtered_rule["dense_score"], "filter_reason": filtered_rule["filter_reason"]},
            details={"candidate": filtered_rule},
        ),
        _event(
            state, patch, event_type="EVIDENCE_GROUNDED", node="policy_grounding", actor="retriever",
            task_id="T12", action="GROUND_CONCLUSION", tool="hybrid_rrf",
            observation={"rule_id": selected_rule["rule_id"], "dense_score": selected_rule["dense_score"], "selection_reason": "PRODUCT_STATUS_EFFECTIVE_DATE_MATCH"},
            evidence=["E-RULE-COST-2026"], details={"candidate": selected_rule, "grounding": trace["grounding"]},
        ),
        _event(
            state, patch, event_type="PLAN_PATCH_APPLIED", node="policy_grounding", actor="workflow",
            task_id="T12", action="ADD_TASK_FROM_APPLICABLE_RULE", observation="2026-08-01 生效规则触发融资成本明示任务",
            state_diff={"audit_plan.T12": [None, "PENDING"]}, evidence=["E-RULE-COST-2026"],
            details={"plan_diff": [{"task_id": "T12", "operation": "ADD", "reason_ref": "NFRA-2026-COST-01"}]},
        ),
        _event(
            state, patch, event_type="RESULT_GROUNDED", node="policy_grounding", actor="audit_agent",
            task_id="T05", action="BIND_RESULT_TO_EVIDENCE_AND_RULE",
            observation=policy_result["conclusion"], evidence=["E-RULE-LOAN-2024"],
            state_diff={
                "task_status.T05": ["INVALIDATED", "SUCCESS"],
                "result_version": {"case_version": state.get("case_version", 1), "plan_version": state.get("plan_version", 1)},
            },
            details={
                "grounded_result": {
                    "task_id": "T05",
                    "conclusion": policy_result["conclusion"],
                    "evidence_refs": ["E-RULE-LOAN-2024"],
                    "rule_refs": ["NFRA-2024-PERSONAL-LOAN"],
                    "rule_version": 1,
                    "applicability_checks": {
                        "product": "PASS",
                        "effective_date": "PASS",
                        "version_status": "PASS",
                    },
                },
            },
        ),
        _event(
            state, patch, event_type="RESULT_GROUNDED", node="policy_grounding", actor="audit_agent",
            task_id="T12", action="BIND_RESULT_TO_EVIDENCE_AND_RULE",
            observation=cost_result["conclusion"], evidence=["E-RULE-COST-2026"],
            state_diff={"task_status.T12": ["PENDING", "SUCCESS"], "rule_effective_date": "2026-08-01"},
            details={
                "grounded_result": {
                    "task_id": "T12",
                    "conclusion": cost_result["conclusion"],
                    "evidence_refs": ["E-RULE-COST-2026"],
                    "rule_refs": ["NFRA-2026-COST-01"],
                    "rule_version": 1,
                    "source_url": selected_rule["source_url"],
                    "applicability_checks": {"product": "PASS", "effective_date": "PASS", "version_status": "PASS"},
                },
            },
        ),
        _event(
            state, patch, event_type="TASK_REEXECUTED", node="policy_grounding", actor="audit_agent",
            task_id="T05", action="POLICY_REVIEW_WITH_GROUNDING", observation=policy_result["conclusion"],
            evidence=["E-RULE-LOAN-2024"], details={"rule_refs": ["NFRA-2024-PERSONAL-LOAN"], "candidate": personal_loan_rule},
        ),
        _event(
            state, patch, event_type="TASK_COMPLETED", node="policy_grounding", actor="audit_agent",
            task_id="T12", action="ADD_EFFECTIVE_RULE_TASK", observation=cost_result["conclusion"],
            evidence=["E-RULE-COST-2026"], details={"rule_refs": ["NFRA-2026-COST-01"]},
        ),
    ]
    return {**patch, "pending_events": events}


def _final_validator(state: AuditState) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    results = state.get("task_results", {})
    for task in state.get("audit_plan", []):
        result = results.get(task["task_id"])
        if task.get("status") != "SUCCESS" or not result:
            missing.append({"task_id": task["task_id"], "reason": "NOT_SUCCESS"})
        elif not result.get("evidence_refs"):
            missing.append({"task_id": task["task_id"], "reason": "MISSING_EVIDENCE"})
        elif task.get("task_type") == "policy_review" and not result.get("rule_refs"):
            missing.append({"task_id": task["task_id"], "reason": "MISSING_RULE_REF"})
    passed = not missing
    fields = deepcopy(state.get("business_fields", {}))
    controls = [
        {
            "control_id": "CTRL-REGISTRATION",
            "code": "NO_DISBURSEMENT_BEFORE_MORTGAGE_REGISTRATION",
            "title": "抵押登记完成前不得放款",
            "status": "REQUIRED",
        },
        {
            "control_id": "CTRL-ENTRUSTED-PAYMENT",
            "code": "ENTRUSTED_PAYMENT",
            "title": f"{int(fields.get('purchase_contract_amount', 1_200_000) / 10_000)} 万元采购款采用受托支付并核验真实交易对手",
            "status": "REQUIRED",
        },
        {
            "control_id": "CTRL-POST-LOAN-PURPOSE",
            "code": "ENHANCED_POST_LOAN_PURPOSE_CHECK",
            "title": f"抵押房产持有 {fields.get('property_holding_months', 8)} 个月，增加贷后用途检查及核查记录",
            "status": "REQUIRED",
        },
        {
            "control_id": "CTRL-COST-DISCLOSURE",
            "code": "TOTAL_FINANCING_COST_DISCLOSURE",
            "title": "签约前完成综合融资成本明示与客户确认",
            "status": "REQUIRED",
        },
    ] if passed else []
    final_decision = "PASS_WITH_CONTROLS" if passed else "BLOCKED"
    fields["final_decision"] = final_decision
    fields["controls"] = controls
    patch = {
        "status": "COMPLETED" if passed else "FAILED",
        "business_fields": fields,
        "active_node": "final_validator", "current_task_id": None,
    }
    event = _event(
        state, patch, event_type="FINAL_VALIDATED" if passed else "VALIDATION_FAILED",
        node="final_validator", actor="validator", action="VERIFY_RESULT_EVIDENCE_CONTRACT",
        observation={"validator": "PASS" if passed else "FAIL", "violations": missing, "final_decision": final_decision},
        evidence=[ref for result in results.values() for ref in result.get("evidence_refs", [])],
        details={
            "final_decision": final_decision,
            "controls": controls,
            "decision": {
                "code": final_decision,
                "reason": "all task, evidence and rule-reference contracts passed" if passed else "validator contract violations remain",
                "requires_human": not passed,
            },
        },
    )
    completed = _event(
        state, patch, event_type="CASE_COMPLETED" if passed else "CASE_FAILED",
        node="final_validator", actor="workflow", action="CLOSE_CASE",
        observation={"status": patch["status"], "final_decision": final_decision},
        details={"final_decision": final_decision, "controls": controls},
    )
    return {**patch, "pending_events": [event, completed]}


def _entry_route(state: AuditState) -> Literal["new", "resume"]:
    return "resume" if state.get("resume_event") else "new"


def build_audit_graph(checkpointer: Any | None = None) -> Any:
    """Compile the real conditional workflow used by ``AuditService``."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise RuntimeError("Install backend/requirements.txt to run LangGraph") from exc

    graph = StateGraph(AuditState)
    graph.add_node("ingest", _ingest)
    graph.add_node("build_state", _build_state)
    graph.add_node("dynamic_plan", _dynamic_plan)
    graph.add_node("deterministic_checks", _deterministic_checks)
    graph.add_node("audit_route", _audit_route_decision)
    graph.add_node("exception_recovery", _exception_recovery)
    graph.add_node("exception_exit_route", _exception_route_decision)
    graph.add_node("relation_review", _relation_review)
    graph.add_node("relation_route", _relation_route_decision)
    graph.add_node("provisional_policy_review", _provisional_policy_review)
    graph.add_node("wait_human", _wait_human)
    graph.add_node("document_check", _document_check)
    graph.add_node("supplement_ingest", _supplement_ingest)
    graph.add_node("reconcile", _reconcile_state)
    graph.add_node("impact_analysis", _impact_analysis)
    graph.add_node("selective_replan", _selective_replan_node)
    graph.add_node("rerun_impacted", _rerun_impacted)
    graph.add_node("policy_grounding", _policy_grounding)
    graph.add_node("final_validator", _final_validator)

    graph.add_conditional_edges(START, _entry_route, {"new": "ingest", "resume": "supplement_ingest"})
    graph.add_edge("ingest", "build_state")
    graph.add_edge("build_state", "dynamic_plan")
    graph.add_edge("dynamic_plan", "deterministic_checks")
    graph.add_edge("deterministic_checks", "audit_route")
    graph.add_conditional_edges(
        "audit_route", _needs_exception,
        {"exception": "exception_recovery", "relation": "relation_review"},
    )
    graph.add_edge("exception_recovery", "exception_exit_route")
    graph.add_conditional_edges(
        "exception_exit_route", _exception_route,
        {"relation": "relation_review", "human": "wait_human"},
    )
    graph.add_edge("relation_review", "relation_route")
    graph.add_conditional_edges(
        "relation_route", _relation_route,
        {"human": "provisional_policy_review", "continue": "document_check"},
    )
    graph.add_edge("provisional_policy_review", "wait_human")
    graph.add_edge("wait_human", END)
    graph.add_edge("document_check", "policy_grounding")
    graph.add_edge("supplement_ingest", "reconcile")
    graph.add_edge("reconcile", "impact_analysis")
    graph.add_edge("impact_analysis", "selective_replan")
    graph.add_edge("selective_replan", "rerun_impacted")
    graph.add_edge("rerun_impacted", "policy_grounding")
    graph.add_edge("policy_grounding", "final_validator")
    graph.add_edge("final_validator", END)
    return graph.compile(checkpointer=checkpointer)
