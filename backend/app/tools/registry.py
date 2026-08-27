"""Unified registry and execution boundary for Local and MCP tools."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError

from .contracts import (
    EmptyToolInput,
    ToolCallRequest,
    ToolObservation,
    ToolRuntimeContext,
    ToolSpec,
)


class ToolAccessError(RuntimeError):
    def __init__(
        self,
        code: str,
        tool: str,
        detail: str = "",
        *,
        status: str | None = None,
        attempts: int = 0,
    ) -> None:
        message = f"{code}: {tool}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.code = code
        self.tool = tool
        self.detail = detail
        self.status = status
        self.attempts = attempts


ToolHandler = Callable[[BaseModel, ToolRuntimeContext], ToolObservation]


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    spec: ToolSpec
    input_model: type[BaseModel]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, ToolRegistration] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        input_model: type[BaseModel] = EmptyToolInput,
    ) -> None:
        if spec.name in self._registrations:
            raise ValueError(f"tool already registered: {spec.name}")
        hydrated_spec = spec.model_copy(update={
            "input_schema": input_model.model_json_schema(),
            "output_schema": ToolObservation.model_json_schema(),
        })
        self._registrations[spec.name] = ToolRegistration(hydrated_spec, input_model, handler)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def specs(self) -> list[ToolSpec]:
        return [registration.spec.model_copy(deep=True) for registration in self._registrations.values()]

    def spec(self, name: str) -> ToolSpec:
        registration = self._registrations.get(name)
        if registration is None:
            raise ToolAccessError("TOOL_NOT_REGISTERED", name)
        return registration.spec.model_copy(deep=True)

    def invoke(self, call: ToolCallRequest, runtime: ToolRuntimeContext) -> ToolObservation:
        if call.name not in call.allowed_tools:
            raise ToolAccessError("TOOL_NOT_ALLOWED", call.name)
        registration = self._registrations.get(call.name)
        if registration is None:
            raise ToolAccessError("TOOL_NOT_REGISTERED", call.name)
        supported = set(registration.spec.supported_intents)
        if "*" not in supported and call.task_intent not in supported:
            raise ToolAccessError("TOOL_NOT_VISIBLE", call.name, call.task_intent)
        try:
            arguments = registration.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            raise ToolAccessError("TOOL_ARGUMENTS_INVALID", call.name, str(exc)) from exc
        max_attempts = registration.spec.max_retries + 1
        last_status = "FAILED"
        last_detail = "tool execution failed"
        for attempt in range(1, max_attempts + 1):
            try:
                raw = self._invoke_with_timeout(
                    registration.handler,
                    arguments,
                    runtime,
                    timeout_ms=registration.spec.timeout_ms,
                )
                observation = ToolObservation.model_validate(raw).model_copy(update={
                    "provider_type": registration.spec.provider_type,
                    "provider_name": registration.spec.provider_name,
                })
                if observation.status == "SUCCESS":
                    return observation.model_copy(update={
                        "metadata": {
                            **observation.metadata,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                        },
                    })
                last_status = observation.status
                last_detail = observation.error_code or observation.result
            except FutureTimeoutError:
                last_status = "TIMEOUT"
                last_detail = f"exceeded {registration.spec.timeout_ms}ms"
            except Exception as exc:  # normalize provider/handler failures
                if isinstance(exc, ToolAccessError):
                    raise
                last_status = "FAILED"
                last_detail = f"{type(exc).__name__}: {exc}"

        code = "TOOL_TIMEOUT_EXHAUSTED" if last_status == "TIMEOUT" else "TOOL_FAILED_EXHAUSTED"
        raise ToolAccessError(
            code,
            call.name,
            last_detail,
            status=last_status,
            attempts=max_attempts,
        )

    @staticmethod
    def _invoke_with_timeout(
        handler: ToolHandler,
        arguments: BaseModel,
        runtime: ToolRuntimeContext,
        *,
        timeout_ms: int,
    ) -> ToolObservation:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-tool")
        future = executor.submit(handler, arguments, runtime)
        try:
            return future.result(timeout=timeout_ms / 1000)
        finally:
            # A timed-out provider may not be cooperatively cancellable. Do not
            # block the graph while its worker unwinds; results are discarded.
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
