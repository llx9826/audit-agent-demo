from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ...runtime.run_manager import RunManager
from ...service import AuditService
from ..contracts import HumanResumeCommand
from ..dependencies import audit_service, case_or_404, run_manager


router = APIRouter(tags=["runs"])


@router.post("/api/cases/{case_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    case_id: str,
    response: Response,
    service: AuditService = Depends(audit_service),
    runs: RunManager = Depends(run_manager),
) -> dict:
    state = case_or_404(service, case_id)
    if state.status in {"WAITING_HUMAN", "WAITING_SUPPLEMENT"}:
        raise HTTPException(status_code=409, detail="case is waiting for a resume command")
    run = await runs.start(case_id)
    response.headers["Location"] = f"/api/runs/{run.run_id}"
    return {**run.to_dict(), "stream_url": f"/api/runs/{run.run_id}/events"}


@router.post("/api/cases/{case_id}/resume-runs", status_code=status.HTTP_202_ACCEPTED)
async def start_resume_run(
    case_id: str,
    command: HumanResumeCommand,
    response: Response,
    service: AuditService = Depends(audit_service),
    runs: RunManager = Depends(run_manager),
) -> dict:
    state = case_or_404(service, case_id)
    if state.status not in {"WAITING_HUMAN", "WAITING_SUPPLEMENT"}:
        raise HTTPException(status_code=409, detail="case is not waiting for a human command")
    run = await runs.start(case_id, resume_event=command.model_dump(exclude_none=True))
    response.headers["Location"] = f"/api/runs/{run.run_id}"
    return {**run.to_dict(), "stream_url": f"/api/runs/{run.run_id}/events"}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, runs: RunManager = Depends(run_manager)) -> dict:
    try:
        return runs.get(run_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/api/runs/{run_id}/events", response_class=EventSourceResponse)
async def stream_run(
    run_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    runs: RunManager = Depends(run_manager),
) -> AsyncIterator[ServerSentEvent]:
    try:
        run = runs.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc

    header_cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    cursor = max(after, header_cursor, run.after_seq)
    while True:
        events, current_status = await asyncio.to_thread(
            runs.wait_for_events,
            run_id,
            after=cursor,
            timeout=15.0,
        )
        for event in events:
            cursor = int(event["seq"])
            yield ServerSentEvent(
                data=event,
                event="audit_event",
                id=str(cursor),
                retry=1000,
            )
        if current_status in {"PAUSED", "COMPLETED", "FAILED"} and not events:
            break
        if not events:
            yield ServerSentEvent(comment="heartbeat")
