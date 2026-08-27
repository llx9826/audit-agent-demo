from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(slots=True)
class CheckpointerHandle:
    """Own a LangGraph checkpointer and any connection backing it."""

    saver: Any
    connection: sqlite3.Connection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


def memory_checkpointer() -> CheckpointerHandle:
    from langgraph.checkpoint.memory import InMemorySaver

    return CheckpointerHandle(InMemorySaver())


def sqlite_checkpointer(path: str | Path | None = None) -> CheckpointerHandle:
    """Create the durable local checkpoint adapter used by the live demo."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    database = Path(path) if path is not None else Path(__file__).resolve().parents[2] / ".data" / "material_completeness_graph_v1.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return CheckpointerHandle(saver=saver, connection=connection)
