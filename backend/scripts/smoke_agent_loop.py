"""真实模型 Agent Smoke：固定 Demo Case/Tool Observation，共用生产 Graph/Prompt/Loop。"""
from __future__ import annotations

import json

from app.bootstrap.container import ApplicationContainer
from demo.fixtures import create_demo_case


def main() -> int:
    container = ApplicationContainer.build(profile="demo")
    try:
        state = create_demo_case("material_completeness")
        container.audit_service.create_case(
            state,
            source="AGENT_LIVE_SMOKE",
            metadata={"scripted_boundary": "CASE_AND_TOOL_OBSERVATIONS_ONLY"},
        )
        # 使用与 SSE 后台 Run 相同的 stream 路径，确保私有子图 Custom Event
        # 在执行当下持久化，而不是只验证最终聚合结果。
        result = container.audit_service.execute_stream(
            state.case_id,
            run_id="RUN-AGENT-LIVE-SMOKE",
        )
        events = container.audit_service.repo.event_dicts(state.case_id)
        exception_events = [item for item in events if item["event_type"].startswith("EXCEPTION_")]
        report = {
            "status": result.status,
            "thread_id": result.thread_id,
            "active_node": result.active_node,
            "pending_human_action": (result.pending_human_request or {}).get("action"),
            "model_gateway_shared": bool(container.model_gateway),
            "exception_event_count": len(exception_events),
            "exception_candidate_rounds": sum(
                item["event_type"] == "EXCEPTION_CANDIDATES_BUILT" for item in exception_events
            ),
            "event_types": list(dict.fromkeys(item["event_type"] for item in events)),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if result.status in {"WAITING_HUMAN", "WAITING_SUPPLEMENT", "COMPLETED"} else 1
    finally:
        container.close()


if __name__ == "__main__":
    raise SystemExit(main())
