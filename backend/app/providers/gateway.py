"""统一模型路由、结构化输出校验、重试与 Fallback。"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from ..bootstrap.settings import ModelEndpointSettings, ModelSettings
from .contracts import (
    CompletionRequest,
    CompletionResponse,
    GatewayAttempt,
    GatewayExhaustedError,
    GatewayTrace,
    LLMProvider,
    Message,
    ProviderCallError,
    StructuredResult,
)
from .openai_compatible import OpenAICompatibleProvider


ProviderFactory = Callable[[ModelEndpointSettings], LLMProvider]
T = TypeVar("T", bound=BaseModel)


def _default_factory(settings: ModelEndpointSettings) -> LLMProvider:
    if settings.adapter != "openai_compatible":
        raise ValueError(f"unsupported model adapter: {settings.adapter}")
    return OpenAICompatibleProvider(
        name=settings.name,
        provider=settings.provider,
        base_url=settings.base_url,
        model=settings.model,
        api_key=settings.api_key.get_secret_value(),
        timeout_seconds=settings.timeout_seconds,
        structured_mode=settings.structured_mode,
        thinking_mode=settings.thinking_mode,
        omit_max_tokens_for_structured=settings.omit_max_tokens_for_structured,
    )


class ModelGateway:
    """按任务角色执行可观测的主模型重试与跨 Endpoint 故障切换。"""

    def __init__(
        self,
        settings: ModelSettings,
        *,
        provider_factory: ProviderFactory = _default_factory,
    ) -> None:
        self.settings = settings
        self.endpoint_settings = settings.endpoint_map()
        self.providers = {
            name: provider_factory(endpoint)
            for name, endpoint in self.endpoint_settings.items()
        }

    def route_chain(self, role: str) -> tuple[str, ...]:
        return self.settings.routes.get(role) or self.settings.routes["default"]

    async def _complete_endpoint(
        self,
        endpoint_name: str,
        request: CompletionRequest,
        *,
        schema_attempt: int,
        attempts: list[GatewayAttempt],
    ) -> CompletionResponse | None:
        endpoint = self.endpoint_settings[endpoint_name]
        provider = self.providers[endpoint_name]
        for transport_attempt in range(1, endpoint.max_retries + 2):
            started = time.perf_counter()
            try:
                response = await provider.complete(request)
                attempts.append(GatewayAttempt(
                    endpoint=endpoint.name,
                    provider=endpoint.provider,
                    model=endpoint.model,
                    transport_attempt=transport_attempt,
                    schema_attempt=schema_attempt,
                    status="SUCCESS",
                    latency_ms=(time.perf_counter() - started) * 1000,
                ))
                return response
            except ProviderCallError as exc:
                attempts.append(GatewayAttempt(
                    endpoint=endpoint.name,
                    provider=endpoint.provider,
                    model=endpoint.model,
                    transport_attempt=transport_attempt,
                    schema_attempt=schema_attempt,
                    status="TRANSIENT_ERROR" if exc.transient else "PERMANENT_ERROR",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error_code=exc.code,
                ))
                if not exc.transient or transport_attempt > endpoint.max_retries:
                    return None
                delay = endpoint.retry_base_seconds * (2 ** (transport_attempt - 1))
                await asyncio.sleep(delay + random.uniform(0, endpoint.retry_base_seconds))
        return None

    async def complete(self, role: str, request: CompletionRequest) -> tuple[CompletionResponse, GatewayTrace]:
        attempts: list[GatewayAttempt] = []
        for endpoint_name in self.route_chain(role):
            response = await self._complete_endpoint(
                endpoint_name,
                request,
                schema_attempt=0,
                attempts=attempts,
            )
            if response is not None:
                return response, GatewayTrace(
                    route=role,
                    selected_endpoint=endpoint_name,
                    attempts=tuple(attempts),
                )
        trace = GatewayTrace(route=role, selected_endpoint=None, attempts=tuple(attempts))
        raise GatewayExhaustedError(role, trace)

    def complete_sync(
        self,
        *,
        role: str,
        messages: list[Message],
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> tuple[CompletionResponse, GatewayTrace]:
        """同步文本生成边界，供离线 Job 等非 async 调用方复用同一重试/降级策略。"""

        request = CompletionRequest(
            messages=messages,
            temperature=temperature,
            max_tokens=self.settings.max_tokens_for(role, max_tokens),
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.complete(role, request))
        raise RuntimeError("complete_sync cannot run inside an active event loop; use await complete()")

    async def structured(
        self,
        *,
        role: str,
        messages: list[Message],
        schema: type[T] | TypeAdapter[T],
        schema_name: str,
        max_tokens: int | None = None,
    ) -> StructuredResult[T]:
        attempts: list[GatewayAttempt] = []
        schema_json = schema.json_schema() if isinstance(schema, TypeAdapter) else schema.model_json_schema()
        for endpoint_name in self.route_chain(role):
            correction_messages = list(messages)
            for schema_attempt in range(self.settings.schema_retries + 1):
                request = CompletionRequest(
                    messages=correction_messages,
                    response_schema=schema_json,
                    schema_name=schema_name,
                    temperature=0.0,
                    max_tokens=self.settings.max_tokens_for(role, max_tokens),
                )
                response = await self._complete_endpoint(
                    endpoint_name,
                    request,
                    schema_attempt=schema_attempt,
                    attempts=attempts,
                )
                if response is None:
                    break
                try:
                    value = (
                        schema.validate_json(response.text)
                        if isinstance(schema, TypeAdapter)
                        else schema.model_validate_json(response.text)
                    )
                except (ValidationError, ValueError) as exc:
                    endpoint = self.endpoint_settings[endpoint_name]
                    attempts.append(GatewayAttempt(
                        endpoint=endpoint.name,
                        provider=endpoint.provider,
                        model=endpoint.model,
                        transport_attempt=0,
                        schema_attempt=schema_attempt,
                        status="SCHEMA_ERROR",
                        latency_ms=0.0,
                        error_code="STRUCTURED_OUTPUT_INVALID",
                    ))
                    if schema_attempt >= self.settings.schema_retries:
                        break
                    correction_messages = [
                        *messages,
                        Message(
                            role="user",
                            content=(
                                "上一次输出未通过结构化校验。请只重新输出合法 JSON。"
                                f"校验错误：{str(exc)[:600]}"
                            ),
                        ),
                    ]
                    continue
                return StructuredResult(
                    value=value,
                    usage=response.usage,
                    trace=GatewayTrace(
                        route=role,
                        selected_endpoint=endpoint_name,
                        attempts=tuple(attempts),
                    ),
                )
        trace = GatewayTrace(route=role, selected_endpoint=None, attempts=tuple(attempts))
        raise GatewayExhaustedError(role, trace)

    def structured_sync(
        self,
        *,
        role: str,
        messages: list[Message],
        schema: type[T] | TypeAdapter[T],
        schema_name: str,
        max_tokens: int | None = None,
    ) -> StructuredResult[T]:
        """同步 LangGraph 边界；模型能力本身保持异步并可独立迁移到 astream。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.structured(
                role=role,
                messages=messages,
                schema=schema,
                schema_name=schema_name,
                max_tokens=max_tokens,
            ))
        raise RuntimeError("structured_sync cannot run inside an active event loop; use await structured()")


def gateway_from_settings(settings: ModelSettings) -> ModelGateway:
    return ModelGateway(settings)
