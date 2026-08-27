"""进件登记阶段：把影像页和结构化字段登记到本次审核状态。"""
from __future__ import annotations

from typing import Any

from ...graph.common import _event
from ...graph.state import AuditState


def run(state: AuditState) -> dict[str, Any]:
    """建立运行态投影；这里不推断人员关系，也不做贷款审批。"""

    manifest = state.get("business_fields", {}).get("material_manifest", {})
    patch = {
        "status": "RUNNING",
        "completeness_status": "CHECKING",
        "active_node": "ingest_case",
        "current_task_id": None,
    }
    event = _event(
        state, patch, event_type="CASE_INGESTED", node="ingest_case", actor="workflow",
        action="REGISTER_PAGE_ASSETS",
        observation={
            "image_count": len(state.get("pages", [])),
            "domain_count": manifest.get("domain_count"),
            "person_count": len(state.get("persons", [])),
        },
    )
    return {**patch, "pending_events": [event]}


__all__ = ["run"]
