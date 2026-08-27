"""厂商无关的模型请求、响应与可观测合同。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)


class CompletionRequest(StrictModel):
    """Gateway 内部统一请求；model 由 Endpoint 配置注入。"""

    messages: list[Message] = Field(min_length=1)
    response_schema: dict[str, Any] | None = None
    schema_name: str | None = None
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1)


class CompletionResponse(StrictModel):
    text: str = ""
    finish_reason: str = "stop"
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str


@runtime_checkable
class LLMProvider(Protocol):
    """单一 Endpoint 的异步能力；重试与 Fallback 由 Gateway 统一处理。"""

    name: str
    model: str

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


@dataclass(frozen=True, slots=True)
class GatewayAttempt:
    endpoint: str
    provider: str
    model: str
    transport_attempt: int
    schema_attempt: int
    status: Literal["SUCCESS", "TRANSIENT_ERROR", "PERMANENT_ERROR", "SCHEMA_ERROR"]
    latency_ms: float
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayTrace:
    route: str
    selected_endpoint: str | None
    attempts: tuple[GatewayAttempt, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        """仅暴露运行元数据，禁止携带 Prompt、响应原文或 API Key。"""

        return {
            "route": self.route,
            "selected_endpoint": self.selected_endpoint,
            "attempts": [
                {
                    "endpoint": item.endpoint,
                    "provider": item.provider,
                    "model": item.model,
                    "transport_attempt": item.transport_attempt,
                    "schema_attempt": item.schema_attempt,
                    "status": item.status,
                    "latency_ms": round(item.latency_ms, 2),
                    "error_code": item.error_code,
                }
                for item in self.attempts
            ],
        }


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredResult(Generic[T]):
    value: T
    trace: GatewayTrace
    usage: Usage = field(default_factory=Usage)


class ProviderCallError(RuntimeError):
    """Adapter 归一化错误；是否可重试由 transient 显式表达。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        transient: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.status_code = status_code


class GatewayExhaustedError(RuntimeError):
    """全部配置 Endpoint 均失败；错误消息不包含 Secret 或响应正文。"""

    def __init__(self, route: str, trace: GatewayTrace) -> None:
        super().__init__(f"model route {route!r} exhausted {len(trace.attempts)} attempt(s)")
        self.route = route
        self.trace = trace
