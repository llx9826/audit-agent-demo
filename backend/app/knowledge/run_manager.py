"""知识库实时 Run 管理器：后台执行与可重连 SSE 事件缓冲。"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import logging
from threading import Condition
from typing import Any, Literal
from uuid import uuid4

from .service import KnowledgeService
from .adapters import KnowledgeModelRouteError


KnowledgeRunStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KnowledgeRunRecord:
    run_id: str
    question: str
    status: KnowledgeRunStatus
    created_at: str
    finished_at: str | None = None
    error: str | None = None
    error_code: str | None = None
    failed_stage: str | None = None
    model_trace: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("events", None)
        return payload


class KnowledgeRunManager:
    """一次 Query 一个 Run；断线后用 seq 从内存缓冲继续消费。"""

    def __init__(self, service: KnowledgeService) -> None:
        self.service = service
        self.runs: dict[str, KnowledgeRunRecord] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="knowledge-run")
        self._tasks: dict[str, Future[None]] = {}
        self._condition = Condition()

    def start(self, question: str) -> KnowledgeRunRecord:
        normalized_question = " ".join(question.split())
        with self._condition:
            # 同一个问题的活跃 Run 允许多个 SSE 消费者复用，避免双击或组件
            # 重挂载把相同的 LLM/Retrieval 工作重复压入无界线程队列。
            for existing in reversed(tuple(self.runs.values())):
                if (
                    existing.status in {"QUEUED", "RUNNING"}
                    and " ".join(existing.question.split()) == normalized_question
                ):
                    return existing
        run = KnowledgeRunRecord(
            run_id=f"KRUN-{uuid4().hex[:12].upper()}",
            question=question,
            status="QUEUED",
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._condition:
            self.runs[run.run_id] = run
        self._tasks[run.run_id] = self._executor.submit(self._execute, run)
        return run

    def get(self, run_id: str) -> KnowledgeRunRecord:
        with self._condition:
            return self.runs[run_id]

    def _append_event(self, run: KnowledgeRunRecord, event_type: str, payload: dict[str, Any]) -> None:
        with self._condition:
            run.events.append({
                "seq": len(run.events) + 1,
                "event_type": event_type,
                "run_id": run.run_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": payload,
            })
            self._condition.notify_all()

    def _execute(self, run: KnowledgeRunRecord) -> None:
        with self._condition:
            run.status = "RUNNING"
        self._append_event(run, "KNOWLEDGE_RUN_STARTED", {"stage": "RUN_STARTED"})
        try:
            run.result = self.service.query(
                run.question,
                stage_callback=lambda stage, payload: self._append_event(
                    run,
                    "KNOWLEDGE_STAGE_COMPLETED",
                    {"stage": stage, **payload},
                ),
            )
            with self._condition:
                run.status = "COMPLETED"
            self._append_event(run, "KNOWLEDGE_RUN_COMPLETED", {
                "stage": "RUN_COMPLETED",
                "answer_status": run.result.get("status"),
                "citation_count": len(run.result.get("citations", [])),
            })
        except Exception as exc:  # pragma: no cover - API contract covers safe failure
            model_failure = exc if isinstance(exc, KnowledgeModelRouteError) else None
            public_trace = model_failure.trace if model_failure else None
            attempt_codes = {
                str(item.get("error_code"))
                for item in (public_trace or {}).get("attempts", [])
                if item.get("error_code")
            }
            auth_codes = {"HTTP_401", "HTTP_403"}
            has_auth_failure = bool(attempt_codes & auth_codes)
            has_non_auth_failure = bool(attempt_codes - auth_codes)
            if model_failure and "STRUCTURED_OUTPUT_INVALID" in attempt_codes:
                safe_message = "知识意图或回答未通过结构化校验，请重试；若持续失败，请检查备用模型配置。"
            elif model_failure and has_auth_failure and not has_non_auth_failure:
                safe_message = "知识模型鉴权失败，请检查当前模型配置后重试。"
            elif model_failure and has_auth_failure:
                safe_message = "知识模型暂时不可达，且备用端点鉴权未通过；请稍后重试或检查备用模型配置。"
            else:
                safe_message = "知识检索服务暂时不可用，请稍后重试。"
            with self._condition:
                run.status = "FAILED"
                run.error = safe_message
                run.error_code = "KNOWLEDGE_MODEL_ROUTE_EXHAUSTED" if model_failure else "KNOWLEDGE_RUNTIME_UNAVAILABLE"
                run.failed_stage = model_failure.role if model_failure else self._last_stage(run)
                run.model_trace = public_trace
            logger.exception(
                "knowledge run failed: run_id=%s error_code=%s failed_stage=%s",
                run.run_id,
                run.error_code,
                run.failed_stage,
            )
            self._append_event(run, "KNOWLEDGE_RUN_FAILED", {
                "stage": "RUN_FAILED",
                "error_type": type(exc).__name__,
                "error_code": run.error_code,
                "failed_stage": run.failed_stage,
                "model_trace": public_trace,
                "retryable": isinstance(exc, RuntimeError),
            })
        finally:
            with self._condition:
                run.finished_at = datetime.now(UTC).isoformat()
                self._condition.notify_all()

    @staticmethod
    def _last_stage(run: KnowledgeRunRecord) -> str:
        """从已持久化事件恢复失败前控制点，避免只留下泛化错误。"""

        for event in reversed(run.events):
            payload = event.get("payload") or {}
            if event.get("event_type") == "KNOWLEDGE_STAGE_COMPLETED" and payload.get("stage"):
                return str(payload["stage"])
        return "RUN_STARTED"

    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        with self._condition:
            return [item.copy() for item in self.runs[run_id].events if int(item["seq"]) > after]

    def wait_for_events(
        self,
        run_id: str,
        *,
        after: int,
        timeout: float = 15.0,
    ) -> tuple[list[dict[str, Any]], KnowledgeRunStatus]:
        with self._condition:
            run = self.runs[run_id]
            events = [item.copy() for item in run.events if int(item["seq"]) > after]
            if not events and run.status not in {"COMPLETED", "FAILED"}:
                self._condition.wait(timeout=timeout)
                events = [item.copy() for item in run.events if int(item["seq"]) > after]
            return events, run.status

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
