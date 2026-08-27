from __future__ import annotations

import os
from pathlib import Path
import json

from fastapi import APIRouter, Request

from ...orchestration import describe_audit_pipeline
from ...prompting.registry import CURRENT_PROMPT_VERSIONS


router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "runtime": "LANGGRAPH",
        "scope": "MATERIAL_COMPLETENESS_ONLY",
        "capabilities": [
            "requirement_rule_engine",
            "requirement_evidence_rag",
            "knowledge_rag",
            "typed_agent_handoff",
            "bounded_exception_tool_loop",
            "checkpoint_interrupt_resume",
            "selective_replan",
            "live_sse",
        ],
    }


@router.get("/ready")
def ready(request: Request) -> dict:
    profile = request.app.state.profile
    demo = profile == "demo"
    model_settings = getattr(request.app.state, "model_settings", None)
    model_ready = bool(
        model_settings
        and model_settings.endpoints
        and all(item.base_url and item.model for item in model_settings.endpoints)
    )
    tools_ready = demo or all(
        os.getenv(key, "").strip()
        for key in ("OCR_RETRY_SERVICE_URL", "VLM_EXTRACT_SERVICE_URL", "CASE_MATERIAL_SEARCH_URL")
    )
    rag_backend = os.getenv("REQUIREMENT_RAG_BACKEND", "milvus").strip().lower()
    milvus_uri = os.getenv("REQUIREMENT_RAG_MILVUS_URI", "").strip()
    corpus_path = os.getenv("REQUIREMENT_CORPUS_PATH", "").strip()
    index_manifest_path = Path(
        os.getenv("REQUIREMENT_RAG_INDEX_MANIFEST", ".data/rag_index_manifest.json")
    )
    try:
        index_manifest = json.loads(index_manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        index_manifest = None
    local_milvus_ready = bool(
        milvus_uri
        and not milvus_uri.startswith(("http://", "https://"))
        and Path(milvus_uri).exists()
    )
    remote_milvus_configured = milvus_uri.startswith(("http://", "https://"))
    rag_ready = bool(
        rag_backend == "milvus"
        and corpus_path
        and Path(corpus_path).exists()
        and (local_milvus_ready or remote_milvus_configured)
        and index_manifest is not None
        and int(index_manifest.get("record_count", 0)) > 0
    )
    return {
        "ready": bool(model_ready and tools_ready and rag_ready),
        "profile": profile,
        "graph": True,
        "prompt_registry": True,
        "model_provider": {
            "ready": model_ready,
            "adapter": "provider_gateway",
            "routes": [] if model_settings is None else sorted(model_settings.routes),
            "fallback_endpoint_count": 0 if model_settings is None else max(0, len(model_settings.endpoints) - 1),
        },
        "agent_tool_loop": tools_ready,
        "requirement_rule_engine": True,
        "requirement_evidence_rag": rag_ready,
        "knowledge_rag": bool(model_ready and rag_ready),
        "rag_backend": rag_backend,
        "checkpoint": True,
        "live_sse": True,
    }


@router.get("/api/architecture")
def architecture(request: Request) -> dict:
    profile = request.app.state.profile
    rag_backend = os.getenv("REQUIREMENT_RAG_BACKEND", "milvus").strip().lower()
    dense = "BGE-M3"
    reranker = "Cross-Encoder"
    return {
        "profile": profile,
        "scope": {
            "does": ["人员角色建模", "材料清单生成", "缺件与可读性检查", "材料归属确认"],
            "does_not": ["贷款准入", "授信审批", "风险定价", "房产估值"],
        },
        "principle": "Deterministic Workflow first; bounded Agent delegation for ambiguity",
        "graph": {
            "runtime": describe_audit_pipeline(),
            "workflow": [
                "ingest_case", "resolve_requirements", "compile_checklist", "match_materials",
                "validate_completeness", "issue_router", "ground_requirement_evidence",
            ],
            "agentic": [
                "case_association_agent",
                "association_gate",
                "material_agent_review",
                "audit_plan_gate",
                "exception_recovery_agent",
                "exception_result_gate",
            ],
            "hitl": ["prepare_human", "await_human", "apply_human_command"],
            "resume": ["reconcile_state", "selective_replan", "match_materials", "final_validator"],
        },
        "requirement_evidence_rag": {
            "trigger": "workflow-confirmed missing or unreadable task",
            "pipeline": [
                "confirmed task query", "metadata filter", f"{dense} dense", "Milvus BM25",
                "RRF", f"{reranker} rerank", "stable chunk citation",
            ],
            "metadata": ["product", "channel", "person_role", "version", "effective_date"],
        },
        "requirement_rule_engine": "SQLite applicability query; retrieval rank never decides checklist membership",
        "knowledge_rag": "LLM intent + LLM query rewrite + metadata filter + Milvus hybrid retrieval + Cross-Encoder + LLM grounded answer",
        "agent_boundaries": {
            "case_association_agent": {
                "input": "CaseAssociationAssignment",
                "output": "CaseAssociationDecision",
                "purpose": "在封闭证据候选中归并人员，并提议角色与材料所属人关联",
                "tools": [],
                "write_authority": "ASSOCIATION_GATE",
                "prompt_version": CURRENT_PROMPT_VERSIONS["case_association"],
            },
            "material_audit_agent": {
                "input": "MaterialAuditAssignment",
                "output": "MaterialAuditDecision",
                "purpose": "只仲裁所属人、类型、跨页分组与 Requirement 归属候选",
                "tools": [],
                "write_authority": "WORKFLOW_PLAN_GATE",
                "prompt_version": CURRENT_PROMPT_VERSIONS["material_audit"],
            },
            "exception_recovery": {
                "kind": "SHARED_SUB_AGENT",
                "context": "isolated",
                "callers": ["ASSOCIATION_GATE", "MATERIAL_MATCHER", "PLAN_GATE"],
                "return_authority": "WORKFLOW_RESULT_GATE",
                "tools": [
                    "ocr_retry", "vlm_extract", "document_search", "neighbor_page_search",
                    "page_integrity_check", "document_reload",
                ],
                "guards": ["max_step", "task_scoped_allowlist", "tool_gate", "duplicate_action", "state_no_change", "completion_condition"],
                "prompt_version": CURRENT_PROMPT_VERSIONS["exception_next_action"],
            },
        },
        "persistence": "LangGraph checkpoint + thread_id + interrupt/Command(resume)",
        "streaming": "background run + persisted custom events + resumable SSE",
    }
