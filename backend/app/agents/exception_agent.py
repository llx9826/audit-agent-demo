from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Callable, Literal, TypedDict


DEFAULT_TOOL_ALLOWLIST = ("ocr_retry", "vlm_extract", "document_search")
DEFAULT_TOOL_PLAN = DEFAULT_TOOL_ALLOWLIST


@dataclass(slots=True)
class ExceptionTask:
    exception_type: str
    source_task_id: str
    problem: str
    evidence_refs: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolObservation:
    result: str
    normalized_value: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class ExceptionResult:
    status: Literal["RESOLVED", "NEED_HUMAN"]
    conclusion: str
    confidence: float
    evidence_refs: list[str]
    actions: list[dict[str, Any]]
    stop_reason: str
    steps_used: int
    step_budget: int
    allowed_tools: list[str]
    loop_guard_triggered: bool = False


class ToolPolicyViolation(RuntimeError):
    """A requested tool is outside the executable registry contract."""

    def __init__(self, code: str, tool: str) -> None:
        super().__init__(f"{code}: {tool}")
        self.code = code
        self.tool = tool


ToolHandler = Callable[[ExceptionTask, dict[str, Any]], ToolObservation]


class ExceptionToolRegistry:
    """In-process registry that enforces registration and per-run allowlists."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        if not name or name in self._handlers:
            raise ValueError(f"tool already registered or invalid: {name}")
        self._handlers[name] = handler

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def execute(
        self,
        name: str,
        *,
        task: ExceptionTask,
        context: dict[str, Any],
        allowed_tools: list[str],
    ) -> ToolObservation:
        if name not in allowed_tools:
            raise ToolPolicyViolation("TOOL_NOT_ALLOWED", name)
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolPolicyViolation("TOOL_NOT_REGISTERED", name)
        return handler(task, context)


class ExceptionAgentState(TypedDict, total=False):
    task: ExceptionTask
    max_steps: int
    allowed_tools: list[str]
    tool_plan: list[str]
    tool_context: dict[str, Any]
    next_tool: str | None
    steps_used: int
    actions: list[dict[str, Any]]
    evidence_refs: list[str]
    normalized_values: dict[str, str]
    state_hashes: list[str]
    status: str
    stop_reason: str
    conclusion: str
    confidence: float
    loop_guard_triggered: bool


def _default_registry() -> ExceptionToolRegistry:
    registry = ExceptionToolRegistry()

    def ocr_retry(_task: ExceptionTask, _context: dict[str, Any]) -> ToolObservation:
        return ToolObservation(result="same_value_low_confidence")

    def vlm_extract(_task: ExceptionTask, context: dict[str, Any]) -> ToolObservation:
        value = str(context.get("vlm_value", "张三"))
        return ToolObservation(
            result=value,
            normalized_value=value,
            evidence_refs=["E-VLM-01"],
            confidence=.96,
        )

    def document_search(_task: ExceptionTask, context: dict[str, Any]) -> ToolObservation:
        value = str(context.get("trusted_document_value", "张三"))
        return ToolObservation(
            result=f"borrower_id.name={value}",
            normalized_value=value,
            evidence_refs=["E-DOC-01"],
            confidence=.99,
        )

    registry.register("ocr_retry", ocr_retry)
    registry.register("vlm_extract", vlm_extract)
    registry.register("document_search", document_search)
    return registry


def _state_hash(evidence_refs: list[str], normalized_values: dict[str, str]) -> str:
    projection = {
        "evidence_refs": sorted(set(evidence_refs)),
        "normalized_values": sorted(normalized_values.items()),
    }
    raw = json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


class ExceptionRecoveryAgent:
    """Bounded exception recovery implemented as a small LangGraph tool loop.

    Tools remain deterministic local adapters in the demo, but their registry,
    per-run allowlist, step budget, completion condition and loop guard are all
    enforced by the executable control flow rather than display metadata.
    """

    def __init__(
        self,
        max_steps: int = 3,
        *,
        registry: ExceptionToolRegistry | None = None,
        allowed_tools: list[str] | tuple[str, ...] = DEFAULT_TOOL_ALLOWLIST,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.max_steps = max_steps
        self.registry = registry or _default_registry()
        self.allowed_tools = list(dict.fromkeys(allowed_tools))
        self._graph = self._build_graph()

    @staticmethod
    def loop_guard(actions: list[str], state_hashes: list[str]) -> bool:
        return (
            len(actions) >= 2
            and actions[-1] == actions[-2]
            and len(state_hashes) >= 2
            and state_hashes[-1] == state_hashes[-2]
        )

    def _prepare(self, state: ExceptionAgentState) -> ExceptionAgentState:
        task = state["task"]
        evidence = list(dict.fromkeys(task.evidence_refs))
        return {
            "steps_used": 0,
            "actions": [],
            "evidence_refs": evidence,
            "normalized_values": {},
            "state_hashes": [],
            "status": "RUNNING",
            "stop_reason": "",
            "conclusion": "",
            "confidence": 0.0,
            "loop_guard_triggered": False,
            "next_tool": None,
        }

    @staticmethod
    def _select_tool(state: ExceptionAgentState) -> ExceptionAgentState:
        steps_used = int(state.get("steps_used", 0))
        max_steps = int(state["max_steps"])
        tool_plan = state.get("tool_plan", [])
        if steps_used >= max_steps or steps_used >= len(tool_plan):
            return {
                "status": "NEED_HUMAN",
                "stop_reason": "BUDGET_EXHAUSTED" if steps_used >= max_steps else "TOOL_PLAN_EXHAUSTED",
                "next_tool": None,
            }
        return {"next_tool": tool_plan[steps_used]}

    def _execute_tool(self, state: ExceptionAgentState) -> ExceptionAgentState:
        tool = state.get("next_tool")
        if not tool:
            return {}

        task = state["task"]
        step = int(state.get("steps_used", 0)) + 1
        max_steps = int(state["max_steps"])
        allowed_tools = list(state.get("allowed_tools", []))
        evidence_before = list(state.get("evidence_refs", []))
        values_before = dict(state.get("normalized_values", {}))
        before_hash = _state_hash(evidence_before, values_before)

        action: dict[str, Any] = {
            "step": step,
            "tool": tool,
            "allowed": tool in allowed_tools,
            "registered": tool in self.registry.names,
            "executed": False,
            "remaining_budget_before": max_steps - step + 1,
            "state_hash_before": before_hash,
            "result": None,
            "normalized_value": None,
            "evidence_refs": [],
            "confidence": None,
            "error_code": None,
        }

        try:
            observation = self.registry.execute(
                tool,
                task=task,
                context=state.get("tool_context", {}),
                allowed_tools=allowed_tools,
            )
        except ToolPolicyViolation as exc:
            action.update({"error_code": exc.code, "result": str(exc)})
            action["state_hash_after"] = before_hash
            action["remaining_budget_after"] = max_steps - step
            return {
                "steps_used": step,
                "actions": [*state.get("actions", []), action],
                "state_hashes": [*state.get("state_hashes", []), before_hash],
                "status": "NEED_HUMAN",
                "stop_reason": exc.code,
            }

        evidence_after = list(dict.fromkeys([*evidence_before, *observation.evidence_refs]))
        values_after = dict(values_before)
        if observation.normalized_value is not None:
            values_after[tool] = observation.normalized_value
        after_hash = _state_hash(evidence_after, values_after)
        action.update({
            "executed": True,
            "result": observation.result,
            "normalized_value": observation.normalized_value,
            "evidence_refs": list(observation.evidence_refs),
            "confidence": observation.confidence,
            "state_hash_after": after_hash,
            "remaining_budget_after": max_steps - step,
            "state_changed": after_hash != before_hash,
        })
        return {
            "steps_used": step,
            "actions": [*state.get("actions", []), action],
            "evidence_refs": evidence_after,
            "normalized_values": values_after,
            "state_hashes": [*state.get("state_hashes", []), after_hash],
        }

    def _evaluate(self, state: ExceptionAgentState) -> ExceptionAgentState:
        if state.get("status") == "NEED_HUMAN":
            return {}

        actions = state.get("actions", [])
        action_names = [str(item["tool"]) for item in actions]
        if self.loop_guard(action_names, state.get("state_hashes", [])):
            return {
                "status": "NEED_HUMAN",
                "stop_reason": "LOOP_GUARD",
                "loop_guard_triggered": True,
            }

        values = state.get("normalized_values", {})
        vlm_value = values.get("vlm_extract")
        trusted_value = values.get("document_search")
        if vlm_value and trusted_value and vlm_value == trusted_value:
            confidences = [
                float(item["confidence"])
                for item in actions
                if item.get("confidence") is not None
            ]
            return {
                "status": "RESOLVED",
                "stop_reason": "COMPLETION_CONDITION_MET",
                "conclusion": f"VLM 与身份证交叉验证为{vlm_value}",
                "confidence": min(confidences) if confidences else .0,
            }

        steps_used = int(state.get("steps_used", 0))
        if steps_used >= int(state["max_steps"]):
            return {"status": "NEED_HUMAN", "stop_reason": "BUDGET_EXHAUSTED"}
        if steps_used >= len(state.get("tool_plan", [])):
            return {"status": "NEED_HUMAN", "stop_reason": "TOOL_PLAN_EXHAUSTED"}
        return {"status": "RUNNING"}

    @staticmethod
    def _route_after_select(state: ExceptionAgentState) -> Literal["execute", "finish"]:
        return "finish" if state.get("status") == "NEED_HUMAN" else "execute"

    @staticmethod
    def _route_after_evaluate(state: ExceptionAgentState) -> Literal["loop", "finish"]:
        return "loop" if state.get("status") == "RUNNING" else "finish"

    @staticmethod
    def _finish(state: ExceptionAgentState) -> ExceptionAgentState:
        if state.get("status") == "RESOLVED":
            return {}
        stop_reason = state.get("stop_reason") or "EVIDENCE_UNRESOLVED"
        conclusion = {
            "BUDGET_EXHAUSTED": "step budget exhausted before evidence agreement",
            "TOOL_PLAN_EXHAUSTED": "tool plan exhausted before evidence agreement",
            "LOOP_GUARD": "repeated action produced no state change",
            "TOOL_NOT_ALLOWED": "requested tool is outside the allowlist",
            "TOOL_NOT_REGISTERED": "requested tool has no registered implementation",
        }.get(stop_reason, "evidence remains unresolved")
        return {"status": "NEED_HUMAN", "conclusion": conclusion, "confidence": 0.0}

    def _build_graph(self) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("Install backend/requirements.txt to run LangGraph") from exc

        graph = StateGraph(ExceptionAgentState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("select_tool", self._select_tool)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("evaluate", self._evaluate)
        graph.add_node("finish", self._finish)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "select_tool")
        graph.add_conditional_edges("select_tool", self._route_after_select, {"execute": "execute_tool", "finish": "finish"})
        graph.add_edge("execute_tool", "evaluate")
        graph.add_conditional_edges("evaluate", self._route_after_evaluate, {"loop": "select_tool", "finish": "finish"})
        graph.add_edge("finish", END)
        return graph.compile()

    def resolve_ocr_conflict(
        self,
        task: ExceptionTask,
        vlm_value: str = "张三",
        *,
        trusted_document_value: str = "张三",
        tool_plan: list[str] | tuple[str, ...] | None = None,
    ) -> ExceptionResult:
        initial: ExceptionAgentState = {
            "task": task,
            "max_steps": self.max_steps,
            "allowed_tools": list(self.allowed_tools),
            "tool_plan": list(tool_plan or DEFAULT_TOOL_PLAN),
            "tool_context": {
                "vlm_value": vlm_value,
                "trusted_document_value": trusted_document_value,
            },
        }
        state = self._graph.invoke(initial)
        return ExceptionResult(
            status=state["status"],  # type: ignore[arg-type]
            conclusion=state.get("conclusion", ""),
            confidence=float(state.get("confidence", 0.0)),
            evidence_refs=list(state.get("evidence_refs", task.evidence_refs)),
            actions=list(state.get("actions", [])),
            stop_reason=state.get("stop_reason", ""),
            steps_used=int(state.get("steps_used", 0)),
            step_budget=self.max_steps,
            allowed_tools=list(self.allowed_tools),
            loop_guard_triggered=bool(state.get("loop_guard_triggered", False)),
        )
