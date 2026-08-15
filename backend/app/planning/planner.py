from __future__ import annotations

from dataclasses import replace

from ..domain.models import AuditTask


def build_plan(relation: str = "UNKNOWN") -> list[AuditTask]:
    tasks = [
        AuditTask("T01", "borrower_identity", depends_on=["borrower"], required_documents=["borrower_id"]),
        AuditTask("T02", "mortgagor_identity", depends_on=["mortgagor"], required_documents=["mortgagor_id"]),
        AuditTask("T03", "relation_check", depends_on=["borrower", "mortgagor", "marriage_documents"]),
        AuditTask("T04", "marriage_document", depends_on=["marital_status", "marriage_documents"]),
        AuditTask("T05", "policy_review", depends_on=["product_type", "relation", "loan_purpose"]),
        AuditTask("T08", "business_authenticity", depends_on=["company_age_months", "business_registration"]),
        AuditTask("T09", "property_holding_period", depends_on=["property_holding_months", "property_certificate"]),
        AuditTask("T10", "valuation_cross_check", depends_on=["purchase_price", "appraised_value"]),
        AuditTask("T11", "purpose_payment_control", depends_on=["loan_purpose", "purchase_contract_amount"]),
    ]
    if relation == "SPOUSE":
        tasks.extend([
            AuditTask("T06", "spouse_identity", depends_on=["relation", "spouse"]),
            AuditTask("T07", "spouse_consent", depends_on=["relation", "spouse_consent"]),
        ])
    return tasks


def impacted_task_ids(tasks: list[AuditTask], changed_facts: list[str]) -> list[str]:
    changed_roots = {fact.split(".", 1)[0] for fact in changed_facts} | set(changed_facts)
    return [task.task_id for task in tasks if changed_roots.intersection(task.depends_on)]


def selective_replan(
    tasks: list[AuditTask],
    changed_facts: list[str],
    relation: str,
    resolved_task_ids: set[str] | None = None,
) -> list[AuditTask]:
    """Revise only tasks affected by changed business facts.

    ``resolved_task_ids`` models deterministic supplement ingestion.  For
    example, receiving and validating a marriage certificate resolves T04
    directly; it must not be scheduled for an unnecessary second execution.
    """
    impacted = set(impacted_task_ids(tasks, changed_facts))
    resolved = resolved_task_ids or set()
    revised: list[AuditTask] = []
    for task in tasks:
        if task.task_id in resolved:
            revised.append(replace(task, status="SUCCESS"))
        elif task.task_id in impacted:
            status = "INVALIDATED" if task.result else "DIRTY"
            revised.append(replace(task, status=status, result=None))
        else:
            revised.append(task)
    existing = {task.task_id for task in revised}
    for task in build_plan(relation):
        if task.task_id not in existing:
            revised.append(task)
    return revised
