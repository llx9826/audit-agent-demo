from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from ...knowledge import KnowledgeRunManager, KnowledgeService


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=500)


def knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


KnowledgeServiceDep = Annotated[KnowledgeService, Depends(knowledge_service)]


def knowledge_run_manager(request: Request) -> KnowledgeRunManager:
    return request.app.state.knowledge_run_manager


KnowledgeRunManagerDep = Annotated[KnowledgeRunManager, Depends(knowledge_run_manager)]


@router.post("/queries")
def query_knowledge(command: KnowledgeQuery, service: KnowledgeServiceDep) -> dict:
    try:
        return service.query(command.question)
    except RuntimeError as exc:
        # Provider/向量库瞬时不可用属于可重试服务错误；对前端返回稳定结构，
        # 不泄漏内部 Endpoint、Key、文件路径或第三方异常对象。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KNOWLEDGE_RUNTIME_UNAVAILABLE",
                "message": "知识检索服务暂时不可用，请稍后重试。",
                "retryable": True,
            },
        ) from exc


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def start_knowledge_run(
    command: KnowledgeQuery,
    response: Response,
    runs: KnowledgeRunManagerDep,
) -> dict:
    run = runs.start(command.question)
    response.headers["Location"] = f"/api/knowledge/runs/{run.run_id}"
    return {
        **run.to_dict(),
        "result": None,
        "stream_url": f"/api/knowledge/runs/{run.run_id}/events",
    }


@router.get("/runs/{run_id}")
def get_knowledge_run(run_id: str, runs: KnowledgeRunManagerDep) -> dict:
    try:
        return runs.get(run_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge run not found") from exc


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def stream_knowledge_run(
    run_id: str,
    runs: KnowledgeRunManagerDep,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> AsyncIterator[ServerSentEvent]:
    try:
        runs.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge run not found") from exc
    header_cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    cursor = max(after, header_cursor)
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
                event="knowledge_event",
                id=str(cursor),
                retry=1000,
            )
        if current_status in {"COMPLETED", "FAILED"} and not events:
            break
        if not events:
            yield ServerSentEvent(comment="heartbeat")


@router.get("/build")
def knowledge_build(service: KnowledgeServiceDep) -> dict:
    return service.build_report()
