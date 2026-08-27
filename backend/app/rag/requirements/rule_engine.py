"""Deterministic requirement resolution for the material checklist."""
from __future__ import annotations

from datetime import date
from typing import Any

from .models import AtomicRequirementRecord
from .store import SQLiteRequirementStore, get_requirement_store


class RequirementRuleEngine:
    def __init__(self, store: SQLiteRequirementStore | None = None) -> None:
        self.store = store or get_requirement_store()

    def resolve(
        self,
        *,
        product: str,
        channel: str,
        case_date: date,
        person_roles: list[str],
    ) -> dict[str, Any]:
        records = self.store.resolve_applicable(
            product=product,
            channel=channel,
            case_date=case_date,
            person_roles=person_roles,
        )
        return {
            "engine": "SQLITE_REQUIREMENT_RULE_ENGINE",
            "input": {
                "product": product,
                "channel": channel,
                "case_date": case_date.isoformat(),
                "person_roles": list(person_roles),
            },
            "rule": "product + channel + role + effective date + ACTIVE version",
            "requirements": records,
            "requirement_ids": [item.requirement_id for item in records],
            "count": len(records),
        }


def requirement_to_domain(record: AtomicRequirementRecord) -> dict[str, Any]:
    return {
        "requirement_id": record.requirement_id,
        "title": record.title,
        "product": record.product,
        "channel": record.channel,
        "checklist_version": record.checklist_version,
        "effective_from": record.effective_from.isoformat(),
        "effective_to": record.effective_to.isoformat() if record.effective_to else None,
        "person_role": record.person_role,
        "material_type": record.material_type,
        "source_document": record.source_document,
        "source_section": record.source_section,
        "atomic_requirement": record.atomic_requirement,
        "condition_expression": record.condition_expression,
        "required_pages": record.required_pages,
        "evidence_id": record.evidence_id,
    }
