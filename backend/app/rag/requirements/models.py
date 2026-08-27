from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AtomicRequirementRecord:
    requirement_id: str
    title: str
    product: str
    channel: str
    checklist_version: int
    effective_from: date
    person_role: str
    material_type: str
    source_document: str
    source_section: str
    atomic_requirement: str
    condition_expression: str = "always"
    required_pages: int = 1
    effective_to: date | None = None
    status: str = "ACTIVE"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        return " ".join([
            str(self.metadata.get("generated_context", "")),
            str(self.metadata.get("parent_title", "")),
            self.title,
            self.product,
            self.channel,
            str(self.metadata.get("region", "")),
            str(self.metadata.get("branch", "")),
            self.person_role,
            self.material_type,
            self.atomic_requirement,
            self.source_document,
            self.source_section,
        ])

    @property
    def evidence_id(self) -> str:
        return f"REQ-EV-{self.requirement_id}"

    @property
    def child_chunk_id(self) -> str:
        return str(self.metadata.get("child_chunk_id") or f"CHILD-{self.requirement_id}")


def requirement_from_mapping(payload: Mapping[str, Any]) -> AtomicRequirementRecord:
    return AtomicRequirementRecord(
        requirement_id=str(payload["requirement_id"]),
        title=str(payload["title"]),
        product=str(payload["product"]),
        channel=str(payload["channel"]),
        checklist_version=int(payload["checklist_version"]),
        effective_from=date.fromisoformat(str(payload["effective_from"])),
        effective_to=(date.fromisoformat(str(payload["effective_to"])) if payload.get("effective_to") else None),
        person_role=str(payload["person_role"]),
        material_type=str(payload["material_type"]),
        source_document=str(payload["source_document"]),
        source_section=str(payload["source_section"]),
        atomic_requirement=str(payload["atomic_requirement"]),
        condition_expression=str(payload.get("condition_expression", "always")),
        required_pages=int(payload.get("required_pages", 1)),
        status=str(payload.get("status", "ACTIVE")),
        metadata=dict(payload.get("metadata", {})),
    )
