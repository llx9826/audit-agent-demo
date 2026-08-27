from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Callable, Literal, TypedDict

from pydantic import TypeAdapter, ValidationError

from ..contracts import (
    AgentDecision,
    AgentObservation,
    CallToolDecision,
    EscalateDecision,
    ExceptionDecisionContext,
    ResolveDecision,
    CompletionCondition,
)
from ..completion_policy import CompletionPolicy
from ...providers.decision_adapters import AgentDecisionAdapter, ModelAdapterError, model_adapter_from_env
from ...prompting import PromptRegistry
from ...tools import (
    ToolAccessError,
    ToolCallRequest,
    ToolObservation,
    ToolRegistry,
    ToolRuntimeContext,
    ToolSpec,
    ToolVisibilityPolicy,
)
from ...tools.local import build_local_tool_registry


DEFAULT_TOOL_ALLOWLIST = (
    "ocr_retry",
    "vlm_extract",
    "document_search",
    "neighbor_page_search",
    "page_integrity_check",
    "document_reload",
)


def _emit_custom(
    event_type: str,
    *,
    node: str,
    task_id: str | None,
    payload: dict[str, Any],
) -> None:
    """Emit a LangGraph custom event, or no-op outside a streaming run."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer({
            "event_type": event_type,
            "actor": "exception_agent",
            "node": node,
            "task_id": task_id,
            "payload": payload,
        })
    except (ImportError, KeyError, LookupError, RuntimeError):
        # Unit calls and legacy ``invoke`` callers may not install a custom
        # stream writer. Event emission must never alter control flow.
        return


@dataclass(slots=True)
class ExceptionTask:
    exception_type: str
    source_task_id: str
    problem: str
    evidence_refs: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)
    completion_condition: CompletionCondition | None = None


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
    decision_trace: list[dict[str, Any]] = field(default_factory=list)


class ToolPolicyViolation(ToolAccessError):
    """A requested tool is outside the executable registry contract."""

    def __init__(
        self,
        code: str,
        tool: str,
        detail: str = "",
        *,
        status: str | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(code, tool, detail, status=status, attempts=attempts)


ToolHandler = Callable[[ExceptionTask, dict[str, Any]], ToolObservation]


class ExceptionToolRegistry:
    """Backward-compatible facade over the provider-neutral ToolRegistry."""

    def __init__(self, unified: ToolRegistry | None = None) -> None:
        self.unified = unified or ToolRegistry()

    def register(self, name: str, handler: ToolHandler) -> None:
        if not name:
            raise ValueError("tool name is required")
        spec = ToolSpec(
            name=name,
            version="legacy-1",
            description=f"Legacy local adapter for {name}",
            provider_type="LOCAL",
            provider_name="legacy-exception-adapter",
            supported_intents=["*"],
            side_effect="STATE_PROPOSAL",
        )

        def adapted(_arguments: Any, runtime: ToolRuntimeContext) -> ToolObservation:
            return ToolObservation.model_validate(handler(runtime.subject, runtime.values))

        self.unified.register(spec, adapted)

    @property
    def names(self) -> tuple[str, ...]:
        return self.unified.names

    def visible_names(self, *, task_intents: list[str]) -> list[str]:
        return ToolVisibilityPolicy().visible_names(self.unified, task_intents=task_intents)

    def specs(self) -> list[ToolSpec]:
        return self.unified.specs()

    def execute(
        self,
        name: str,
        *,
        task: ExceptionTask,
        context: dict[str, Any],
        allowed_tools: list[str],
    ) -> ToolObservation:
        call = ToolCallRequest(
            name=name,
            arguments=dict(context.get("tool_arguments", {})),
            task_id=task.source_task_id,
            task_intent=f"EXCEPTION:{task.exception_type}",
            allowed_tools=allowed_tools,
        )
        runtime = ToolRuntimeContext(
            task_id=task.source_task_id,
            task_intent=call.task_intent,
            values=dict(context),
            subject=task,
        )
        try:
            return self.unified.invoke(call, runtime)
        except ToolAccessError as exc:
            raise ToolPolicyViolation(
                exc.code,
                exc.tool,
                exc.detail,
                status=exc.status,
                attempts=exc.attempts,
            ) from exc


class ExceptionAgentState(TypedDict, total=False):
    task: ExceptionTask
    max_steps: int
    allowed_tools: list[str]
    candidate_tools: list[str]
    blocked_tools: dict[str, str]
    tool_context: dict[str, Any]
    next_tool: str | None
    next_tool_arguments: dict[str, Any]
    pending_decision: dict[str, Any]
    decision_trace: list[dict[str, Any]]
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
    return ExceptionToolRegistry(build_local_tool_registry())


def _state_hash(evidence_refs: list[str], normalized_values: dict[str, str]) -> str:
    projection = {
        "evidence_refs": sorted(set(evidence_refs)),
        "normalized_values": sorted(normalized_values.items()),
    }
    raw = json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


class ExceptionRecoveryAgent:
    """Bounded exception recovery implemented as a small LangGraph tool loop.

    Every loop iteration renders the versioned prompt from current observations
    and asks a structured model adapter for the next action. Tool registration,
    per-run allowlists, step budget, completion condition and loop guard remain
    executable control-flow boundaries outside the model.
    """

    def __init__(
        self,
        max_steps: int = 3,
        *,
        registry: ExceptionToolRegistry | ToolRegistry | None = None,
        allowed_tools: list[str] | tuple[str, ...] = DEFAULT_TOOL_ALLOWLIST,
        model_adapter: AgentDecisionAdapter | None = None,
        prompt_registry: PromptRegistry | None = None,
        completion_condition: CompletionCondition | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.max_steps = max_steps
        self.registry = (
            registry if isinstance(registry, ExceptionToolRegistry)
            else ExceptionToolRegistry(registry) if isinstance(registry, ToolRegistry)
            else _default_registry()
        )
        self.allowed_tools = list(dict.fromkeys(allowed_tools))
        self._explicit_allowlist = tuple(self.allowed_tools) != tuple(DEFAULT_TOOL_ALLOWLIST)
        self.model_adapter = model_adapter or model_adapter_from_env()
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.default_completion_policy = CompletionPolicy(
            completion_condition or CompletionCondition()
        )
        self._decision_validator = TypeAdapter(AgentDecision)
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
            "next_tool_arguments": {},
            "pending_decision": {},
            "decision_trace": [],
            "candidate_tools": [],
            "blocked_tools": {},
        }

    def _build_candidates(self, state: ExceptionAgentState) -> ExceptionAgentState:
        """按最新 Observation 重建候选 Tool，固定计划不得跨轮沿用。"""

        from .tool_policy import build_candidate_tools

        task = state["task"]
        intent = f"EXCEPTION:{task.exception_type}"
        visible_names = set(self.registry.visible_names(task_intents=[intent]))
        visible_specs = [item for item in self.registry.specs() if item.name in visible_names]
        candidates = build_candidate_tools(
            master_allowlist=state.get("allowed_tools", []),
            visible_specs=visible_specs,
            actions=state.get("actions", []),
        )
        step = int(state.get("steps_used", 0)) + 1
        _emit_custom(
            "EXCEPTION_CANDIDATES_BUILT",
            node="build_candidates",
            task_id=task.source_task_id,
            payload={
                "step": step,
                "exception_type": task.exception_type,
                "candidate_tools": list(candidates.enabled),
                "blocked_tools": dict(candidates.blocked),
                "completion_condition": (
                    task.completion_condition or self.default_completion_policy.condition
                ).model_dump(mode="json"),
                "step_budget": int(state["max_steps"]),
                "remaining_budget": int(state["max_steps"]) - int(state.get("steps_used", 0)),
            },
        )
        if not candidates.enabled:
            return {
                "candidate_tools": [],
                "blocked_tools": dict(candidates.blocked),
                "status": "NEED_HUMAN",
                "stop_reason": "NO_CANDIDATE_TOOL",
            }
        return {
            "candidate_tools": list(candidates.enabled),
            "blocked_tools": dict(candidates.blocked),
            "status": "RUNNING",
        }

    def _decision_context(
        self,
        state: ExceptionAgentState,
        registered_tools: tuple[str, ...],
        tool_specs: list[ToolSpec] | None = None,
    ) -> ExceptionDecisionContext:
        task = state["task"]
        observations = [
            AgentObservation(
                step=int(item["step"]),
                tool=str(item["tool"]),
                result=str(item["result"]) if item.get("result") is not None else None,
                normalized_value=item.get("normalized_value"),
                evidence_refs=list(item.get("evidence_refs", [])),
                confidence=item.get("confidence"),
                error_code=item.get("error_code"),
                state_changed=bool(item.get("state_changed", False)),
            )
            for item in state.get("actions", [])
        ]
        return ExceptionDecisionContext(
            exception_type=task.exception_type,
            source_task_id=task.source_task_id,
            problem=task.problem,
            context_refs=list(task.context_refs),
            evidence_refs=list(state.get("evidence_refs", task.evidence_refs)),
            allowed_tools=list(state.get("candidate_tools") or state.get("allowed_tools", [])),
            registered_tools=list(registered_tools),
            tool_specs=list(tool_specs or []),
            observations=observations,
            normalized_values=dict(state.get("normalized_values", {})),
            steps_used=int(state.get("steps_used", 0)),
            max_steps=int(state["max_steps"]),
            completion_condition=task.completion_condition or self.default_completion_policy.condition,
        )

    def _select_tool(self, state: ExceptionAgentState) -> ExceptionAgentState:
        steps_used = int(state.get("steps_used", 0))
        max_steps = int(state["max_steps"])
        if steps_used >= max_steps:
            return {
                "status": "NEED_HUMAN",
                "stop_reason": "BUDGET_EXHAUSTED",
                "next_tool": None,
            }

        allowed_names = set(state.get("candidate_tools") or state.get("allowed_tools", []))
        context = self._decision_context(
            state,
            self.registry.names,
            [spec for spec in self.registry.specs() if spec.name in allowed_names],
        )
        prompt = self.prompt_registry.render_exception_next_action(context)
        try:
            raw_decision = self.model_adapter.decide(prompt=prompt, context=context)
            decision = self._decision_validator.validate_python(raw_decision)
        except (ModelAdapterError, ValidationError, TypeError, ValueError) as exc:
            _emit_custom(
                "EXCEPTION_DECISION_MADE",
                node="select_tool",
                task_id=state["task"].source_task_id,
                payload={
                    "step": steps_used + 1,
                    "status": "INVALID",
                    "reason_code": "INVALID_STRUCTURED_OUTPUT",
                    "prompt_id": prompt.metadata.prompt_id,
                    "prompt_version": prompt.metadata.version,
                    "prompt_sha256": prompt.metadata.sha256,
                },
            )
            return {
                "status": "NEED_HUMAN",
                "stop_reason": "INVALID_STRUCTURED_OUTPUT",
                "next_tool": None,
                "decision_trace": [
                    *state.get("decision_trace", []),
                    {
                        "step": steps_used + 1,
                        "prompt_id": prompt.metadata.prompt_id,
                        "prompt_version": prompt.metadata.version,
                        "prompt_sha256": prompt.metadata.sha256,
                        "error": str(exc),
                    },
                ],
            }

        trace_item = {
            "step": steps_used + 1,
            "prompt_id": prompt.metadata.prompt_id,
            "prompt_version": prompt.metadata.version,
            "prompt_sha256": prompt.metadata.sha256,
            "decision": decision.model_dump(mode="json"),
            "observation_count": len(context.observations),
            "model_trace": getattr(self.model_adapter, "last_trace", None),
        }
        decision_trace = [*state.get("decision_trace", []), trace_item]
        _emit_custom(
            "EXCEPTION_DECISION_MADE",
            node="select_tool",
            task_id=state["task"].source_task_id,
            payload={
                **trace_item,
                "status": "VALID",
                "remaining_budget": max_steps - steps_used,
                "allowed_tools": list(context.allowed_tools),
            },
        )
        if isinstance(decision, CallToolDecision):
            return {
                "status": "RUNNING",
                "next_tool": decision.tool_call.name,
                "next_tool_arguments": dict(decision.tool_call.arguments),
                "pending_decision": decision.model_dump(mode="json"),
                "decision_trace": decision_trace,
            }
        if isinstance(decision, EscalateDecision):
            return {
                "status": "NEED_HUMAN",
                "stop_reason": decision.reason_code,
                "conclusion": decision.rationale_summary,
                "next_tool": None,
                "pending_decision": decision.model_dump(mode="json"),
                "decision_trace": decision_trace,
            }

        assert isinstance(decision, ResolveDecision)
        evidence = set(state.get("evidence_refs", []))
        condition = state["task"].completion_condition or self.default_completion_policy.condition
        evaluation = CompletionPolicy(condition).evaluate(
            state.get("actions", [])
        )
        completion_met = evaluation.met and set(decision.evidence_refs).issubset(evidence)
        return {
            "status": "RESOLVED" if completion_met else "NEED_HUMAN",
            "stop_reason": "COMPLETION_CONDITION_MET" if completion_met else "PREMATURE_RESOLVE",
            "conclusion": decision.conclusion if completion_met else "model attempted resolution before completion condition",
            "next_tool": None,
            "pending_decision": decision.model_dump(mode="json"),
            "decision_trace": decision_trace,
        }

    def _execute_tool(self, state: ExceptionAgentState) -> ExceptionAgentState:
        tool = state.get("next_tool")
        if not tool:
            return {}

        task = state["task"]
        step = int(state.get("steps_used", 0)) + 1
        max_steps = int(state["max_steps"])
        allowed_tools = list(state.get("candidate_tools") or state.get("allowed_tools", []))
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
            "tool_arguments": dict(state.get("next_tool_arguments", {})),
            "decision_reason_code": state.get("pending_decision", {}).get("reason_code"),
            "decision_summary": state.get("pending_decision", {}).get("rationale_summary"),
            "observation_metadata": {},
        }
        blocked_reason = state.get("blocked_tools", {}).get(tool)
        if blocked_reason in {"NO_STATE_CHANGE", "OBSERVATION_ALREADY_COLLECTED"}:
            action.update({
                "error_code": "DUPLICATE_ACTION",
                "result": blocked_reason,
                "state_hash_after": before_hash,
                "remaining_budget_after": max_steps - step,
                "state_changed": False,
            })
            _emit_custom(
                "EXCEPTION_TOOL_GATE_EVALUATED",
                node="execute_tool",
                task_id=task.source_task_id,
                payload={
                    "step": step,
                    "tool": tool,
                    "allowed": False,
                    "registered": tool in self.registry.names,
                    "blocked_reason": blocked_reason,
                    "remaining_budget_before": action["remaining_budget_before"],
                },
            )
            return {
                "steps_used": step,
                "actions": [*state.get("actions", []), action],
                "state_hashes": [*state.get("state_hashes", []), before_hash],
                "status": "NEED_HUMAN",
                "stop_reason": "LOOP_GUARD",
                "loop_guard_triggered": True,
            }
        _emit_custom(
            "EXCEPTION_TOOL_GATE_EVALUATED",
            node="execute_tool",
            task_id=task.source_task_id,
            payload={
                "step": step,
                "tool": tool,
                "allowed": action["allowed"],
                "registered": action["registered"],
                "remaining_budget_before": action["remaining_budget_before"],
            },
        )
        _emit_custom(
            "TOOL_STARTED",
            node="execute_tool",
            task_id=task.source_task_id,
            payload={
                "step": step,
                "step_budget": max_steps,
                "remaining_budget_before": max_steps - step + 1,
                "tool": tool,
                "arguments": action["tool_arguments"],
                "allowed": action["allowed"],
                "registered": action["registered"],
                "state_hash_before": before_hash,
                "decision_reason_code": action["decision_reason_code"],
            },
        )

        try:
            observation = self.registry.execute(
                tool,
                task=task,
                context={
                    **state.get("tool_context", {}),
                    "tool_arguments": dict(state.get("next_tool_arguments", {})),
                },
                allowed_tools=allowed_tools,
            )
        except ToolPolicyViolation as exc:
            action.update({"error_code": exc.code, "result": str(exc)})
            action["tool_status"] = exc.status or "REJECTED"
            action["tool_attempts"] = exc.attempts
            action["state_hash_after"] = before_hash
            action["remaining_budget_after"] = max_steps - step
            _emit_custom(
                "EXCEPTION_TOOL_OBSERVED",
                node="execute_tool",
                task_id=task.source_task_id,
                payload={
                    "step": step,
                    "tool": tool,
                    "status": exc.status or "REJECTED",
                    "error_code": exc.code,
                    "result": str(exc),
                    "evidence_refs": [],
                    "state_changed": False,
                    "state_hash_before": before_hash,
                    "state_hash_after": before_hash,
                    "remaining_budget_after": max_steps - step,
                    "attempts": exc.attempts,
                },
            )
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
            "provider_type": observation.provider_type,
            "provider_name": observation.provider_name,
            "tool_status": observation.status,
            "tool_attempts": observation.metadata.get("attempt", 1),
            # 结构化 Tool 元数据只返回父 Workflow，不进入模型可见 Context。
            # Gate 可据此校验并应用人员/角色/类型 Observation。
            "observation_metadata": dict(observation.metadata),
            "state_hash_after": after_hash,
            "remaining_budget_after": max_steps - step,
            "state_changed": after_hash != before_hash,
        })
        _emit_custom(
            "EXCEPTION_TOOL_OBSERVED",
            node="execute_tool",
            task_id=task.source_task_id,
            payload={
                "step": step,
                "tool": tool,
                "status": "SUCCESS",
                "result": observation.result,
                "normalized_value": observation.normalized_value,
                "evidence_refs": list(observation.evidence_refs),
                "confidence": observation.confidence,
                "provider_type": observation.provider_type,
                "provider_name": observation.provider_name,
                "attempts": observation.metadata.get("attempt", 1),
                "state_changed": after_hash != before_hash,
                "state_hash_before": before_hash,
                "state_hash_after": after_hash,
                "remaining_budget_after": max_steps - step,
            },
        )
        return {
            "steps_used": step,
            "actions": [*state.get("actions", []), action],
            "evidence_refs": evidence_after,
            "normalized_values": values_after,
            "state_hashes": [*state.get("state_hashes", []), after_hash],
        }

    def _evaluate(self, state: ExceptionAgentState) -> ExceptionAgentState:
        if state.get("status") == "NEED_HUMAN":
            _emit_custom(
                "COMPLETION_EVALUATED",
                node="evaluate",
                task_id=state["task"].source_task_id,
                payload={
                    "status": "NEED_HUMAN",
                    "completion_met": False,
                    "stop_reason": state.get("stop_reason", "EVIDENCE_UNRESOLVED"),
                    "steps_used": int(state.get("steps_used", 0)),
                    "step_budget": int(state["max_steps"]),
                },
            )
            return {}

        actions = state.get("actions", [])
        action_names = [str(item["tool"]) for item in actions]
        if self.loop_guard(action_names, state.get("state_hashes", [])):
            _emit_custom(
                "COMPLETION_EVALUATED",
                node="evaluate",
                task_id=state["task"].source_task_id,
                payload={
                    "status": "NEED_HUMAN",
                    "completion_met": False,
                    "stop_reason": "LOOP_GUARD",
                    "loop_guard_triggered": True,
                    "steps_used": int(state.get("steps_used", 0)),
                    "step_budget": int(state["max_steps"]),
                },
            )
            return {
                "status": "NEED_HUMAN",
                "stop_reason": "LOOP_GUARD",
                "loop_guard_triggered": True,
            }

        condition = state["task"].completion_condition or self.default_completion_policy.condition
        evaluation = CompletionPolicy(condition).evaluate(actions)
        if evaluation.met:
            completion = {
                "status": "RESOLVED",
                "stop_reason": "COMPLETION_CONDITION_MET",
                "conclusion": (
                    f"{evaluation.independent_sources} 个独立来源形成一致证据："
                    f"{evaluation.normalized_value}"
                ),
                "confidence": evaluation.confidence,
            }
            _emit_custom(
                "COMPLETION_EVALUATED",
                node="evaluate",
                task_id=state["task"].source_task_id,
                payload={
                    **completion,
                    "completion_met": True,
                    "steps_used": int(state.get("steps_used", 0)),
                    "step_budget": int(state["max_steps"]),
                    "evidence_refs": list(state.get("evidence_refs", [])),
                    "independent_sources": evaluation.independent_sources,
                },
            )
            return completion

        steps_used = int(state.get("steps_used", 0))
        if steps_used >= int(state["max_steps"]):
            _emit_custom(
                "COMPLETION_EVALUATED",
                node="evaluate",
                task_id=state["task"].source_task_id,
                payload={
                    "status": "NEED_HUMAN",
                    "completion_met": False,
                    "stop_reason": "BUDGET_EXHAUSTED",
                    "steps_used": steps_used,
                    "step_budget": int(state["max_steps"]),
                },
            )
            return {"status": "NEED_HUMAN", "stop_reason": "BUDGET_EXHAUSTED"}
        _emit_custom(
            "COMPLETION_EVALUATED",
            node="evaluate",
            task_id=state["task"].source_task_id,
            payload={
                "status": "RUNNING",
                "completion_met": False,
                "stop_reason": "MORE_EVIDENCE_REQUIRED",
                "steps_used": steps_used,
                "step_budget": int(state["max_steps"]),
                "remaining_budget": int(state["max_steps"]) - steps_used,
            },
        )
        return {"status": "RUNNING"}

    @staticmethod
    def _route_after_candidates(state: ExceptionAgentState) -> Literal["select", "finish"]:
        return "select" if state.get("status") == "RUNNING" and state.get("candidate_tools") else "finish"

    @staticmethod
    def _route_after_select(state: ExceptionAgentState) -> Literal["execute", "finish"]:
        return "execute" if state.get("status") == "RUNNING" and state.get("next_tool") else "finish"

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
            "LOOP_GUARD": "repeated action produced no state change",
            "TOOL_NOT_ALLOWED": "requested tool is outside the allowlist",
            "TOOL_NOT_REGISTERED": "requested tool has no registered implementation",
            "TOOL_TIMEOUT_EXHAUSTED": "tool timed out after bounded retries",
            "TOOL_FAILED_EXHAUSTED": "tool failed after bounded retries",
            "TOOL_ARGUMENTS_INVALID": "tool arguments failed schema validation",
            "TOOL_NOT_VISIBLE": "tool is outside the current task-intent visibility scope",
            "INVALID_STRUCTURED_OUTPUT": "model decision failed structured output validation",
            "PREMATURE_RESOLVE": "model attempted to resolve before the completion condition",
            "NO_CANDIDATE_TOOL": "no eligible tool can add an independent observation",
        }.get(stop_reason, "evidence remains unresolved")
        return {"status": "NEED_HUMAN", "conclusion": conclusion, "confidence": 0.0}

    def _build_graph(self) -> Any:
        from .graph import build_exception_graph

        return build_exception_graph(self)

    def resolve(
        self,
        task: ExceptionTask,
        vlm_value: str,
        *,
        trusted_document_value: str,
        tool_context: dict[str, Any] | None = None,
    ) -> ExceptionResult:
        intent_key = f"EXCEPTION:{task.exception_type}"
        visible_tools = self.registry.visible_names(task_intents=[intent_key])
        runtime_allowed_tools = (
            list(self.allowed_tools)
            if self._explicit_allowlist
            else [name for name in self.allowed_tools if name in visible_tools]
        )
        initial: ExceptionAgentState = {
            "task": task,
            "max_steps": self.max_steps,
            "allowed_tools": runtime_allowed_tools,
            "tool_context": {
                "vlm_value": vlm_value,
                "trusted_document_value": trusted_document_value,
                **dict(tool_context or {}),
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
            allowed_tools=runtime_allowed_tools,
            loop_guard_triggered=bool(state.get("loop_guard_triggered", False)),
            decision_trace=list(state.get("decision_trace", [])),
        )
