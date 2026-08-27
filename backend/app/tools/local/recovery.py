"""HTTP adapters for real OCR, VLM and case-material services."""
from __future__ import annotations

import json
import os
from urllib import request

from pydantic import BaseModel

from ..contracts import ToolObservation, ToolRuntimeContext, ToolSpec
from ..registry import ToolRegistry


_INTENTS_BY_TOOL = {
    "ocr_retry": ["EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE", "EXCEPTION:OCR_FIELD_CONFLICT"],
    "vlm_extract": [
        "ASSOCIATION:IDENTITY_ROLE_EXTRACTION",
        "EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE",
        "EXCEPTION:MATERIAL_TYPE_AMBIGUOUS",
        "EXCEPTION:OWNER_ASSIGNMENT_AMBIGUOUS",
        "EXCEPTION:CROSS_PAGE_CONFLICT",
    ],
    "document_search": [
        "EXCEPTION:MATERIAL_IMAGE_LOW_CONFIDENCE",
        "EXCEPTION:OWNER_ASSIGNMENT_AMBIGUOUS",
        "EXCEPTION:MATERIAL_TYPE_AMBIGUOUS",
        "EXCEPTION:CROSS_PAGE_CONFLICT",
    ],
    "neighbor_page_search": ["EXCEPTION:CROSS_PAGE_CONFLICT", "EXCEPTION:MATERIAL_TYPE_AMBIGUOUS"],
    "page_integrity_check": ["EXCEPTION:PAGE_MISSING_OR_DUPLICATE"],
    "document_reload": ["EXCEPTION:TOOL_FAILURE", "EXCEPTION:PAGE_MISSING_OR_DUPLICATE"],
}


def _spec(name: str, description: str, provider_name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1.0.0",
        description=description,
        provider_type="LOCAL",
        provider_name=provider_name,
        supported_intents=_INTENTS_BY_TOOL[name],
        side_effect="STATE_PROPOSAL",
        timeout_ms=int(os.getenv("MATERIAL_TOOL_TIMEOUT_MS", "10000")),
        max_retries=int(os.getenv("MATERIAL_TOOL_MAX_RETRIES", "1")),
    )


def _call_service(env_key: str, runtime: ToolRuntimeContext) -> ToolObservation:
    endpoint = os.getenv(env_key, "").strip()
    if not endpoint:
        raise RuntimeError(f"{env_key} is required in the real profile")
    payload = {
        "case_id": runtime.case_id,
        "task_id": runtime.task_id,
        "task_intent": runtime.task_intent,
        "context": runtime.values,
    }
    http_request = request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=int(os.getenv("MATERIAL_TOOL_TIMEOUT_MS", "10000")) / 1000) as response:  # noqa: S310 - deployment-owned endpoint
        return ToolObservation.model_validate_json(response.read())


def build_local_tool_registry() -> ToolRegistry:
    """Build real service-backed Local Tools without any scripted observations."""

    registry = ToolRegistry()

    def ocr_retry(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        return _call_service("OCR_RETRY_SERVICE_URL", runtime)

    def vlm_extract(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        return _call_service("VLM_EXTRACT_SERVICE_URL", runtime)

    def document_search(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        return _call_service("CASE_MATERIAL_SEARCH_URL", runtime)

    def neighbor_page_search(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        return _call_service("CASE_MATERIAL_SEARCH_URL", runtime)

    def page_integrity_check(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        return _call_service("PAGE_INTEGRITY_SERVICE_URL", runtime)

    def document_reload(_arguments: BaseModel, runtime: ToolRuntimeContext) -> ToolObservation:
        return _call_service("DOCUMENT_RELOAD_SERVICE_URL", runtime)

    registry.register(
        _spec("ocr_retry", "Retry OCR for one scoped low-confidence page.", "ocr-service"),
        ocr_retry,
    )
    registry.register(
        _spec("vlm_extract", "Re-extract material type and owner from one scoped page.", "vlm-service"),
        vlm_extract,
    )
    registry.register(
        _spec("document_search", "Search case material for independent owner evidence.", "case-material-service"),
        document_search,
    )
    registry.register(
        _spec("neighbor_page_search", "Retrieve adjacent pages to restore bundle context.", "case-material-service"),
        neighbor_page_search,
    )
    registry.register(
        _spec("page_integrity_check", "Detect duplicated pages and missing page ranges.", "page-integrity-service"),
        page_integrity_check,
    )
    registry.register(
        _spec("document_reload", "Reload the scoped source asset after a provider failure.", "document-service"),
        document_reload,
    )
    return registry
