"""Agent 决策适配层。

生产链路通过 Provider-neutral ModelGateway 调用任意已配置模型；QwenVllmAdapter
仅作为旧测试和单 Endpoint 兼容入口保留，业务代码不得直接依赖它。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Protocol
from urllib import request

from pydantic import TypeAdapter, ValidationError

from ..agents.contracts import (
    AgentDecision,
    ExceptionDecisionContext,
    MaterialAuditAssignment,
    MaterialAuditDecision,
    RenderedPrompt,
)
from ..agents.case_association.contracts import (
    CaseAssociationAssignment,
    CaseAssociationDecision,
)
from ..bootstrap.settings import model_settings_from_env
from .contracts import GatewayExhaustedError, Message
from .gateway import ModelGateway, gateway_from_settings


_DECISION_ADAPTER = TypeAdapter(AgentDecision)
_MATERIAL_AUDIT_DECISION_ADAPTER = TypeAdapter(MaterialAuditDecision)
_CASE_ASSOCIATION_DECISION_ADAPTER = TypeAdapter(CaseAssociationDecision)


class ModelAdapterError(RuntimeError):
    """模型路由错误；只携带脱敏 Trace，便于 SSE/UI 解释 Fallback 过程。"""

    def __init__(self, message: str, *, trace: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.trace = trace


class AgentDecisionAdapter(Protocol):
    def decide(
        self,
        *,
        prompt: RenderedPrompt,
        context: ExceptionDecisionContext,
    ) -> AgentDecision: ...


class MaterialAuditDecisionAdapter(Protocol):
    def decide_material(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: MaterialAuditAssignment,
    ) -> MaterialAuditDecision: ...


class CaseAssociationDecisionAdapter(Protocol):
    def decide_association(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: CaseAssociationAssignment,
    ) -> CaseAssociationDecision: ...


class GatewayDecisionAdapter:
    """把关联、材料消歧与异常 Agent 统一映射到 ModelGateway。"""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self.last_trace: dict[str, Any] | None = None

    def _invoke(
        self,
        *,
        role: str,
        prompt: RenderedPrompt,
        decision_adapter: TypeAdapter,
        schema_name: str,
    ) -> Any:
        try:
            result = self.gateway.structured_sync(
                role=role,
                messages=[
                    Message(role="system", content=prompt.system),
                    Message(role="user", content=prompt.user),
                ],
                schema=decision_adapter,
                schema_name=schema_name,
            )
        except GatewayExhaustedError as exc:
            self.last_trace = exc.trace.to_public_dict()
            raise ModelAdapterError(
                f"model route {role} exhausted",
                trace=self.last_trace,
            ) from exc
        self.last_trace = result.trace.to_public_dict()
        return result.value

    def decide(
        self,
        *,
        prompt: RenderedPrompt,
        context: ExceptionDecisionContext,
    ) -> AgentDecision:
        del context
        return self._invoke(
            role="exception",
            prompt=prompt,
            decision_adapter=_DECISION_ADAPTER,
            schema_name="exception_agent_decision",
        )

    def decide_material(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: MaterialAuditAssignment,
    ) -> MaterialAuditDecision:
        del assignment
        return self._invoke(
            role="audit",
            prompt=prompt,
            decision_adapter=_MATERIAL_AUDIT_DECISION_ADAPTER,
            schema_name="material_audit_decision",
        )

    def decide_association(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: CaseAssociationAssignment,
    ) -> CaseAssociationDecision:
        del assignment
        return self._invoke(
            role="association",
            prompt=prompt,
            decision_adapter=_CASE_ASSOCIATION_DECISION_ADAPTER,
            schema_name="case_association_decision",
        )


Transport = Callable[[request.Request, float], bytes]


class QwenVllmAdapter:
    """OpenAI-compatible structured-output adapter for Qwen served by vLLM."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport or self._urlopen

    @staticmethod
    def _urlopen(http_request: request.Request, timeout: float) -> bytes:
        with request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310 - configured service URL
            return response.read()

    def decide(
        self,
        *,
        prompt: RenderedPrompt,
        context: ExceptionDecisionContext,
    ) -> AgentDecision:
        del context  # Context is already rendered into the versioned user prompt.
        return self.invoke_structured(
            prompt=prompt,
            decision_adapter=_DECISION_ADAPTER,
            schema_name="exception_agent_decision",
        )

    def decide_material(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: MaterialAuditAssignment,
    ) -> MaterialAuditDecision:
        del assignment
        return self.invoke_structured(
            prompt=prompt,
            decision_adapter=_MATERIAL_AUDIT_DECISION_ADAPTER,
            schema_name="material_audit_decision",
        )

    def decide_association(
        self,
        *,
        prompt: RenderedPrompt,
        assignment: CaseAssociationAssignment,
    ) -> CaseAssociationDecision:
        del assignment
        return self.invoke_structured(
            prompt=prompt,
            decision_adapter=_CASE_ASSOCIATION_DECISION_ADAPTER,
            schema_name="case_association_decision",
        )

    def invoke_structured(
        self,
        *,
        prompt: RenderedPrompt,
        decision_adapter: TypeAdapter,
        schema_name: str,
    ) -> Any:
        schema = decision_adapter.json_schema()
        body = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        endpoint = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        http_request = request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            payload = json.loads(self._transport(http_request, self.timeout))
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
            return decision_adapter.validate_json(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise ModelAdapterError(f"Qwen response failed {schema_name} validation") from exc
        except OSError as exc:
            raise ModelAdapterError(f"Qwen-vLLM request failed: {exc}") from exc


def model_adapter_from_env() -> AgentDecisionAdapter:
    return GatewayDecisionAdapter(gateway_from_settings(model_settings_from_env()))


def material_audit_model_adapter_from_env() -> MaterialAuditDecisionAdapter:
    return GatewayDecisionAdapter(gateway_from_settings(model_settings_from_env()))
