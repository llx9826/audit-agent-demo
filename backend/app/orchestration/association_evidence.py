"""Case Association 页级证据读取能力。

Demo/已结构化上游可直接读取 Page 字段；Real Profile 通过统一 Tool Registry
调用 VLM 服务。两种实现返回同一个 Observation 合同，主 Pipeline 不感知厂商。
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..tools import ToolCallRequest, ToolRegistry, ToolRuntimeContext


class AssociationPageObservation(BaseModel):
    """单页身份、角色与所属人 Observation；它不是最终业务绑定。"""

    model_config = ConfigDict(extra="forbid")

    person_id: str | None = None
    person_name: str | None = None
    identity_key: str | None = None
    role_signals: list[str] = Field(default_factory=list)
    owner_person_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    provider: str = "PAGE_FIELDS"
    status: str = "SUCCESS"
    error_code: str | None = None


class AssociationEvidenceExtractor(Protocol):
    def extract(self, *, case_id: str, page: dict[str, Any]) -> AssociationPageObservation: ...


class PageFieldAssociationEvidenceExtractor:
    """读取上游已结构化字段；不把 Seed.roles 自动当作业务角色。"""

    def extract(self, *, case_id: str, page: dict[str, Any]) -> AssociationPageObservation:
        del case_id
        fields = page.get("extracted_fields") or {}
        person_id = str(fields.get("person_id") or page.get("owner_person_id") or "").strip() or None
        person_name = str(fields.get("person_name") or "").strip() or None
        return AssociationPageObservation(
            person_id=person_id,
            person_name=person_name,
            identity_key=fields.get("identity_key") or fields.get("identity_hash"),
            role_signals=[str(item) for item in fields.get("role_signals", [])],
            owner_person_id=str(fields.get("owner_person_id") or page.get("owner_person_id") or "").strip() or None,
            confidence=float(page.get("confidence") or 0),
            evidence_refs=[str(item) for item in page.get("evidence_refs", [])] or [f"EV-{page['page_id']}"],
            provider="PAGE_FIELDS",
        )


class ToolAssociationEvidenceExtractor:
    """Real Profile 的确定性 VLM 调用；Tool 只看到单页最小上下文。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def extract(self, *, case_id: str, page: dict[str, Any]) -> AssociationPageObservation:
        intent = "ASSOCIATION:IDENTITY_ROLE_EXTRACTION"
        observation = self._registry.invoke(
            ToolCallRequest(
                name="vlm_extract",
                arguments={},
                task_id=f"ASSOC-PAGE-{page['page_id']}",
                task_intent=intent,
                allowed_tools=["vlm_extract"],
            ),
            ToolRuntimeContext(
                case_id=case_id,
                task_id=f"ASSOC-PAGE-{page['page_id']}",
                task_intent=intent,
                values={
                    "page": {
                        "page_id": page["page_id"],
                        "bundle_id": page.get("bundle_id"),
                        "domain": page.get("domain"),
                        "material_type": page.get("material_type"),
                        "preview_url": page.get("preview_url"),
                    },
                    "requested_fields": [
                        "person_id", "person_name", "identity_key",
                        "role_signals", "owner_person_id",
                    ],
                },
            ),
        )
        fields = observation.metadata.get("fields")
        if not isinstance(fields, dict):
            try:
                fields = json.loads(observation.result)
            except (TypeError, json.JSONDecodeError):
                fields = {}
        if not isinstance(fields, dict):
            fields = {}
        payload = {
            "person_id": fields.get("person_id"),
            "person_name": fields.get("person_name"),
            "identity_key": fields.get("identity_key"),
            "role_signals": fields.get("role_signals", []),
            "owner_person_id": fields.get("owner_person_id"),
            "confidence": observation.confidence or fields.get("confidence") or 0,
            "evidence_refs": observation.evidence_refs or [f"EV-{page['page_id']}-VLM"],
            "provider": f"{observation.provider_type}:{observation.provider_name}",
            "status": observation.status,
            "error_code": observation.error_code,
        }
        return AssociationPageObservation.model_validate(payload)


__all__ = [
    "AssociationEvidenceExtractor",
    "AssociationPageObservation",
    "PageFieldAssociationEvidenceExtractor",
    "ToolAssociationEvidenceExtractor",
]
