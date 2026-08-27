"""应用级编排门面：把启动、恢复、观察统一成一条可发现的调用路径。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AuditOrchestrator:
    """协调主图执行与后台 Run；不承载审核决策，也不复制 Graph 拓扑。"""

    service: Any
    run_manager: Any

    async def start(self, case_id: str) -> dict[str, Any]:
        """从已持久化 Case 启动后台 Graph，并返回可订阅的 Run 合同。"""

        run = await self.run_manager.start(case_id)
        return {**run.to_dict(), "stream_url": f"/api/runs/{run.run_id}/events"}

    async def resume(self, case_id: str, command: dict[str, Any]) -> dict[str, Any]:
        """用结构化人工命令恢复同一 thread_id，而不是创建一条新业务流程。"""

        run = await self.run_manager.start(case_id, resume_event=command)
        return {**run.to_dict(), "stream_url": f"/api/runs/{run.run_id}/events"}

    def inspect(self, case_id: str) -> dict[str, Any]:
        """返回状态、事件和 Checkpoint，供 API、CLI 与运行检查器共用。"""

        return self.service.inspect(case_id)

    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        """读取已落库事件；SSE 断线后可用序号继续。"""

        return self.run_manager.events(run_id, after=after)

