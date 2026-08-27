from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
import pickle
import sqlite3
from threading import RLock
from uuid import uuid4

from ..domain.models import CaseState, Event


class InMemoryCaseRepository:
    """Local adapter. MySQL implements the same contract in production mode."""

    def __init__(self) -> None:
        self.cases: dict[str, CaseState] = {}
        self.events: dict[str, list[Event]] = {}
        self.checkpoints: dict[str, dict[str, CaseState]] = {}

    def save(self, state: CaseState) -> None:
        self.cases[state.case_id] = deepcopy(state)

    def clear_case(self, case_id: str) -> None:
        self.cases.pop(case_id, None)
        self.events.pop(case_id, None)
        self.checkpoints.pop(case_id, None)

    def get(self, case_id: str) -> CaseState:
        return deepcopy(self.cases[case_id])

    @staticmethod
    def state_snapshot(state: CaseState, active_node: str | None = None) -> dict:
        """Small immutable projection embedded in every event for UI playback."""
        return {
            "status": state.status,
            "active_node": active_node or state.active_node,
            "current_task_id": state.current_task_id,
            "case_version": state.case_version,
            "plan_version": state.plan_version,
            "completeness_status": state.completeness_status,
            "task_statuses": {task.task_id: task.status for task in state.audit_plan},
            "changed_facts": list(state.changed_facts),
            "dirty_tasks": list(state.dirty_tasks),
            "invalidated_tasks": list(state.invalidated_tasks),
        }

    def append_event(
        self,
        state: CaseState,
        event_type: str,
        actor: str,
        payload: dict,
        checkpoint_id: str | None = None,
        *,
        run_id: str | None = None,
        namespace: str | None = None,
    ) -> Event:
        stream = self.events.setdefault(state.case_id, [])
        normalized = deepcopy(payload)
        node = normalized.get("node") or state.active_node or "workflow"
        normalized.setdefault("node", node)
        normalized.setdefault("actor", actor)
        normalized.setdefault("task_id", state.current_task_id)
        normalized.setdefault("action", event_type)
        normalized.setdefault("tool", None)
        normalized.setdefault("observation", None)
        normalized.setdefault("state_diff", {})
        normalized.setdefault("evidence", [])
        normalized.setdefault("evidence_refs", list(normalized["evidence"]))
        normalized.setdefault("case_version", state.case_version)
        normalized.setdefault("plan_version", state.plan_version)
        normalized.setdefault("thread_id", state.thread_id)
        normalized.setdefault("run_id", run_id)
        normalized.setdefault("namespace", namespace)
        normalized.setdefault("state_snapshot", self.state_snapshot(state, node))
        event = Event(
            event_id=f"EV-{uuid4().hex[:10].upper()}", seq=len(stream) + 1,
            case_id=state.case_id, actor=actor, event_type=event_type,
            timestamp=datetime.now(UTC).isoformat(), case_version=int(normalized["case_version"]),
            plan_version=int(normalized["plan_version"]), checkpoint_id=checkpoint_id, payload=normalized,
            thread_id=state.thread_id or None, run_id=run_id, namespace=namespace,
        )
        stream.append(event)
        return event

    def checkpoint(self, state: CaseState, label: str) -> str:
        sequence = len(self.checkpoints.setdefault(state.case_id, {})) + 1
        checkpoint_id = f"CP-{sequence:04d}"
        self.checkpoints[state.case_id][checkpoint_id] = deepcopy(state)
        self.append_event(state, "CHECKPOINT_CREATED", "workflow", {"label": label}, checkpoint_id)
        return checkpoint_id

    def replay(self, case_id: str, checkpoint_id: str) -> CaseState:
        snapshot = deepcopy(self.checkpoints[case_id][checkpoint_id])
        snapshot.case_id = f"{case_id}-REPLAY-{uuid4().hex[:4].upper()}"
        snapshot.status = "READY"
        self.save(snapshot)
        self.append_event(snapshot, "REPLAY_STARTED", "human", {"source_case_id": case_id, "checkpoint_id": checkpoint_id})
        return snapshot

    def event_dicts(self, case_id: str, after: int = 0) -> list[dict]:
        return [asdict(event) for event in self.events.get(case_id, []) if event.seq > after]

    def close(self) -> None:
        """Release adapter resources; the in-memory adapter owns none."""


class SQLiteCaseRepository(InMemoryCaseRepository):
    """Durable local adapter for restart-safe WAITING_HUMAN cases.

    Pickle is intentionally restricted to this trusted, local demo database.
    Production adapters should use typed relational rows and MySQL transactions.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__()
        db_path = Path(path) if path is not None else Path(__file__).resolve().parents[2] / ".data" / "material_completeness_v1.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_cases(case_id TEXT PRIMARY KEY, state BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS case_events(case_id TEXT NOT NULL, seq INTEGER NOT NULL, event BLOB NOT NULL, PRIMARY KEY(case_id, seq));
            CREATE TABLE IF NOT EXISTS checkpoint_metadata(case_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, state BLOB NOT NULL, PRIMARY KEY(case_id, checkpoint_id));
            """
        )
        self._load()

    def _load(self) -> None:
        for case_id, blob in self._db.execute("SELECT case_id, state FROM audit_cases"):
            self.cases[case_id] = pickle.loads(blob)
        for case_id, _seq, blob in self._db.execute("SELECT case_id, seq, event FROM case_events ORDER BY case_id, seq"):
            self.events.setdefault(case_id, []).append(pickle.loads(blob))
        for case_id, checkpoint_id, blob in self._db.execute("SELECT case_id, checkpoint_id, state FROM checkpoint_metadata"):
            self.checkpoints.setdefault(case_id, {})[checkpoint_id] = pickle.loads(blob)

    def save(self, state: CaseState) -> None:
        with self._lock:
            super().save(state)
            self._db.execute(
                "INSERT INTO audit_cases(case_id,state) VALUES(?,?) ON CONFLICT(case_id) DO UPDATE SET state=excluded.state",
                (state.case_id, pickle.dumps(state)),
            )
            self._db.commit()

    def clear_case(self, case_id: str) -> None:
        with self._lock:
            super().clear_case(case_id)
            self._db.execute("DELETE FROM case_events WHERE case_id=?", (case_id,))
            self._db.execute("DELETE FROM checkpoint_metadata WHERE case_id=?", (case_id,))
            self._db.execute("DELETE FROM audit_cases WHERE case_id=?", (case_id,))
            self._db.commit()

    def append_event(
        self,
        state: CaseState,
        event_type: str,
        actor: str,
        payload: dict,
        checkpoint_id: str | None = None,
        *,
        run_id: str | None = None,
        namespace: str | None = None,
    ) -> Event:
        with self._lock:
            event = super().append_event(
                state,
                event_type,
                actor,
                payload,
                checkpoint_id,
                run_id=run_id,
                namespace=namespace,
            )
            self._db.execute(
                "INSERT OR REPLACE INTO case_events(case_id,seq,event) VALUES(?,?,?)",
                (state.case_id, event.seq, pickle.dumps(event)),
            )
            self._db.commit()
            return event

    def checkpoint(self, state: CaseState, label: str) -> str:
        with self._lock:
            checkpoint_id = super().checkpoint(state, label)
            snapshot = self.checkpoints[state.case_id][checkpoint_id]
            self._db.execute(
                "INSERT OR REPLACE INTO checkpoint_metadata(case_id,checkpoint_id,state) VALUES(?,?,?)",
                (state.case_id, checkpoint_id, pickle.dumps(snapshot)),
            )
            self._db.commit()
            return checkpoint_id

    def close(self) -> None:
        with self._lock:
            self._db.close()
