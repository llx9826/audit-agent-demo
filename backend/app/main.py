from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .service import AuditService

app = FastAPI(title="Complex Credit Audit Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])
service = AuditService()


def _state(case_id: str):
    try:
        return service.repo.get(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "mode": "DEMO", "llm": "mock"}


@app.post("/api/cases/demo/{scenario}")
def create_demo(scenario: str) -> dict:
    return service.new_demo(scenario).to_dict()


@app.post("/api/cases/{case_id}/run")
def run_case(case_id: str) -> dict:
    _state(case_id)
    return service.run(case_id).to_dict()


@app.get("/api/cases/{case_id}")
@app.get("/api/cases/{case_id}/state")
def get_case(case_id: str) -> dict:
    return _state(case_id).to_dict()


@app.get("/api/cases/{case_id}/plan")
def get_plan(case_id: str) -> list[dict]:
    return [asdict(task) for task in _state(case_id).audit_plan]


@app.get("/api/cases/{case_id}/events")
def get_events(case_id: str, after: int = 0) -> list[dict]:
    _state(case_id)
    return service.repo.event_dicts(case_id, after=max(0, after))


@app.get("/api/cases/{case_id}/checkpoints")
def get_checkpoints(case_id: str) -> list[str]:
    _state(case_id)
    return list(service.repo.checkpoints.get(case_id, {}))


@app.post("/api/cases/{case_id}/supplement")
@app.post("/api/cases/{case_id}/resume")
def supplement(case_id: str, event: dict) -> dict:
    _state(case_id)
    try:
        return service.supplement(case_id, event).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/cases/{case_id}/finalize")
def finalize(case_id: str) -> dict:
    _state(case_id)
    return service.finish(case_id).to_dict()


@app.post("/api/cases/{case_id}/replay/{checkpoint_id}")
def replay(case_id: str, checkpoint_id: str) -> dict:
    _state(case_id)
    try:
        return service.replay(case_id, checkpoint_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="checkpoint not found") from exc


@app.get("/api/cases/{case_id}/evidence")
def evidence(case_id: str) -> list[dict]:
    return [asdict(item) for item in _state(case_id).evidence_ledger]


@app.get("/api/cases/{case_id}/rag-trace")
def rag_trace(case_id: str) -> dict:
    _state(case_id)
    return service.rag_trace(case_id)


@app.get("/api/architecture")
def architecture() -> dict:
    return {
        "principle": "Workflow first; bounded agent delegation only for uncertainty",
        "graph": {
            "deterministic_path": ["ingest", "build_state", "dynamic_plan", "deterministic_checks", "audit_route"],
            "exception_path": ["exception_recovery_subgraph", "exception_exit_route", "relation_review"],
            "human_interrupt": ["provisional_policy_review", "wait_human"],
            "resume_path": ["supplement_ingest", "reconcile", "impact_analysis", "selective_replan", "rerun_impacted", "policy_grounding", "final_validator"],
        },
        "agent_boundaries": {
            "exception_recovery": {
                "trigger": "OCR conflict or low-confidence extraction",
                "runtime": "compiled LangGraph subgraph",
                "tool_allowlist": ["ocr_retry", "vlm_extract", "document_search"],
                "tool_registry_enforced": True,
                "step_budget": 3,
                "loop_guard": "same tool + unchanged scoped state twice",
                "execution_mode": "OFFLINE_DETERMINISTIC_TOOLS",
                "completion_condition": "two-source identity agreement",
                "exit_contract": ["RESOLVED", "NEED_HUMAN"],
            },
            "policy_grounding": {
                "retrieval": "runtime hashed-dense + BM25 scores over local corpus, then RRF",
                "applicability_gate": ["product", "version status", "effective date"],
                "output_contract": ["evidence_id", "rule_id", "effective_date", "clause", "source_url"],
            },
        },
        "checkpoint": "application-level durable SQLite snapshot",
        "runtime_events": [
            "ROUTE_EVALUATED", "HANDOFF_CREATED", "AGENT_TOOL_STARTED",
            "AGENT_TOOL_FINISHED", "AGENT_RETURNED", "STATE_PATCH_APPLIED",
            "RESULT_GROUNDED",
        ],
    }


@app.get("/api/cases/{case_id}/stream")
def stream(case_id: str, after: int = 0) -> StreamingResponse:
    _state(case_id)

    async def emit():
        cursor = max(0, after)
        while True:
            events = service.repo.event_dicts(case_id, after=cursor)
            for event in events:
                yield f"id: {event['seq']}\nevent: audit_event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                cursor = event["seq"]
            if service.repo.get(case_id).status in {"WAITING_HUMAN", "COMPLETED"}:
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        emit(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
