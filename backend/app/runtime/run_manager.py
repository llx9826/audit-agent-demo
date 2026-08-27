from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Condition
from typing import Any, Literal
from uuid import uuid4

from ..service import AuditService


RunStatus = Literal["QUEUED", "RUNNING", "PAUSED", "COMPLETED", "FAILED"]


@dataclass(slots=True)
class RunRecord:
    run_id: str
    case_id: str
    thread_id: str
    status: RunStatus
    after_seq: int
    created_at: str
    finished_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunManager:
    """Own background graph runs; clients can disconnect without cancelling them."""

    def __init__(self, service: AuditService) -> None:
        self.service = service
        self.runs: dict[str, RunRecord] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="audit-run")
        self._tasks: dict[str, Future[None]] = {}
        self._condition = Condition()

    def get(self, run_id: str) -> RunRecord:
        return self.runs[run_id]

    async def start(
        self,
        case_id: str,
        *,
        resume_event: dict[str, Any] | None = None,
    ) -> RunRecord:
        state = self.service.repo.get(case_id)
        active = next((item for item in self.runs.values() if item.case_id == case_id and item.status in {"QUEUED", "RUNNING"}), None)
        if active:
            return active
        current_events = self.service.repo.event_dicts(case_id)
        run = RunRecord(
            run_id=f"RUN-{uuid4().hex[:12].upper()}",
            case_id=case_id,
            thread_id=state.thread_id,
            status="QUEUED",
            after_seq=current_events[-1]["seq"] if current_events else 0,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.runs[run.run_id] = run
        self._tasks[run.run_id] = self._executor.submit(self._execute, run, resume_event)
        return run

    def _execute(self, run: RunRecord, resume_event: dict[str, Any] | None) -> None:
        run.status = "RUNNING"
        try:
            result = self.service.execute_stream(
                run.case_id,
                run_id=run.run_id,
                resume_event=resume_event,
                on_event=lambda _event: self._notify(),
            )
            run.status = "PAUSED" if result.status in {"WAITING_HUMAN", "WAITING_SUPPLEMENT"} else "COMPLETED"
        except Exception as exc:  # pragma: no cover - exercised through API contract
            run.status = "FAILED"
            run.error = f"{type(exc).__name__}: {exc}"
            state = self.service.repo.get(run.case_id)
            failure_details: dict[str, Any] = {
                "node": state.active_node or "runtime",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            model_trace = getattr(exc, "trace", None)
            if isinstance(model_trace, dict):
                failure_details["model_trace"] = model_trace
            self.service.repo.append_event(
                state,
                "RUN_FAILED",
                "runtime",
                failure_details,
                run_id=run.run_id,
            )
        finally:
            run.finished_at = datetime.now(UTC).isoformat()
            self._notify()

    def _notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def wait_for_events(
        self,
        run_id: str,
        *,
        after: int,
        timeout: float = 15.0,
    ) -> tuple[list[dict[str, Any]], RunStatus]:
        """Block a worker thread until a real event or terminal run state."""

        with self._condition:
            events = self.events(run_id, after=after)
            status = self.get(run_id).status
            if not events and status not in {"PAUSED", "COMPLETED", "FAILED"}:
                self._condition.wait(timeout=timeout)
                events = self.events(run_id, after=after)
                status = self.get(run_id).status
            return events, status

    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        run = self.get(run_id)
        return [
            event
            for event in self.service.repo.event_dicts(run.case_id, after=after)
            if event.get("run_id") == run_id
        ]

    def close(self) -> None:
        """Drain graph workers before application persistence is closed."""
        self._executor.shutdown(wait=True, cancel_futures=False)
