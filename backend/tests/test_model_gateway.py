from __future__ import annotations

import http.client
import json
import os
import unittest
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

from app.bootstrap.settings import ModelEndpointSettings, ModelSettings, model_settings_from_env
from app.providers.contracts import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderCallError,
)
from app.providers.gateway import ModelGateway
from app.providers.openai_compatible import OpenAICompatibleProvider


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str


class ScriptedProvider:
    def __init__(self, endpoint: ModelEndpointSettings, scripts: dict[str, list[object]]) -> None:
        self.name = endpoint.name
        self.model = endpoint.model
        self.provider = endpoint.provider
        self.scripts = scripts

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.last_request = request
        item = self.scripts[self.name].pop(0)
        if isinstance(item, Exception):
            raise item
        return CompletionResponse(
            text=str(item),
            provider=self.provider,
            model=self.model,
        )


def endpoint(name: str, *, retries: int = 0) -> ModelEndpointSettings:
    return ModelEndpointSettings(
        name=name,
        provider="test-provider",
        base_url="http://model.invalid/v1",
        model=f"model-{name}",
        max_retries=retries,
        retry_base_seconds=0,
        structured_mode="json_object",
    )


class ModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    def gateway(
        self,
        scripts: dict[str, list[object]],
        *,
        primary_retries: int = 0,
        schema_retries: int = 1,
    ) -> ModelGateway:
        settings = ModelSettings(
            endpoints=(endpoint("primary", retries=primary_retries), endpoint("fallback")),
            routes={
                "default": ("primary", "fallback"),
                "audit": ("primary", "fallback"),
            },
            schema_retries=schema_retries,
        )
        return ModelGateway(
            settings,
            provider_factory=lambda item: ScriptedProvider(item, scripts),
        )

    async def test_role_specific_token_budget_is_applied(self) -> None:
        scripts = {"primary": ['{"action":"CONFIRM"}'], "fallback": []}
        settings = ModelSettings(
            endpoints=(endpoint("primary"), endpoint("fallback")),
            routes={"default": ("primary",), "association": ("primary",)},
            max_tokens=1200,
            role_max_tokens={"association": 8192},
        )
        gateway = ModelGateway(
            settings,
            provider_factory=lambda item: ScriptedProvider(item, scripts),
        )

        await gateway.structured(
            role="association",
            messages=[Message(role="user", content="decide")],
            schema=Decision,
            schema_name="decision",
        )

        self.assertEqual(gateway.providers["primary"].last_request.max_tokens, 8192)

    async def test_transient_error_retries_same_endpoint_before_success(self) -> None:
        scripts = {
            "primary": [
                ProviderCallError("rate limited", code="HTTP_429", transient=True),
                '{"action":"CONFIRM"}',
            ],
            "fallback": [],
        }
        result = await self.gateway(scripts, primary_retries=1).structured(
            role="audit",
            messages=[Message(role="user", content="decide")],
            schema=Decision,
            schema_name="decision",
        )
        self.assertEqual(result.value.action, "CONFIRM")
        self.assertEqual(result.trace.selected_endpoint, "primary")
        self.assertEqual(
            [item.status for item in result.trace.attempts],
            ["TRANSIENT_ERROR", "SUCCESS"],
        )

    async def test_permanent_error_switches_to_configured_fallback_without_retry(self) -> None:
        scripts = {
            "primary": [ProviderCallError("unauthorized", code="HTTP_401", transient=False)],
            "fallback": ['{"action":"ESCALATE"}'],
        }
        result = await self.gateway(scripts, primary_retries=3).structured(
            role="audit",
            messages=[Message(role="user", content="decide")],
            schema=Decision,
            schema_name="decision",
        )
        self.assertEqual(result.trace.selected_endpoint, "fallback")
        self.assertEqual(len([item for item in result.trace.attempts if item.endpoint == "primary"]), 1)

    async def test_schema_repair_is_bounded_then_falls_back(self) -> None:
        scripts = {
            "primary": ["not-json", '{"wrong":"field"}'],
            "fallback": ['{"action":"REQUEST_HUMAN"}'],
        }
        result = await self.gateway(scripts, schema_retries=1).structured(
            role="audit",
            messages=[Message(role="user", content="decide")],
            schema=Decision,
            schema_name="decision",
        )
        self.assertEqual(result.value.action, "REQUEST_HUMAN")
        self.assertEqual(result.trace.selected_endpoint, "fallback")
        public_trace = result.trace.to_public_dict()
        self.assertNotIn("decide", str(public_trace))
        self.assertNotIn("api_key", str(public_trace).lower())

    async def test_endpoint_thinking_mode_is_adapter_configuration(self) -> None:
        captured: dict[str, object] = {}

        def transport(http_request, _timeout):
            captured["body"] = json.loads(http_request.data)
            return json.dumps({
                "choices": [{
                    "message": {"content": '{"action":"CONFIRM"}'},
                    "finish_reason": "stop",
                }],
            }).encode()

        provider = OpenAICompatibleProvider(
            name="primary",
            provider="deepseek",
            base_url="https://model.invalid/v1",
            model="configured-model",
            api_key="test-only",
            timeout_seconds=1,
            structured_mode="json_object",
            thinking_mode="disabled",
            transport=transport,
        )
        await provider.complete(CompletionRequest(
            messages=[Message(role="user", content="请输出 JSON")],
            response_schema=Decision.model_json_schema(),
            schema_name="decision",
        ))

        self.assertEqual(captured["body"]["thinking"], {"type": "disabled"})
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})

    async def test_structured_max_tokens_can_be_omitted_per_endpoint(self) -> None:
        captured: dict[str, object] = {}

        def transport(http_request, _timeout):
            captured["body"] = json.loads(http_request.data)
            return json.dumps({
                "choices": [{
                    "message": {"content": '{"action":"CONFIRM"}'},
                    "finish_reason": "stop",
                }],
            }).encode()

        provider = OpenAICompatibleProvider(
            name="qwen-fallback",
            provider="aliyun-qwen",
            base_url="https://model.invalid/v1",
            model="configured-model",
            api_key="test-only",
            timeout_seconds=1,
            structured_mode="json_object",
            omit_max_tokens_for_structured=True,
            transport=transport,
        )
        await provider.complete(CompletionRequest(
            messages=[Message(role="user", content="请输出 JSON")],
            response_schema=Decision.model_json_schema(),
            schema_name="decision",
        ))

        self.assertNotIn("max_tokens", captured["body"])

    async def test_incomplete_http_read_is_a_retryable_transport_error(self) -> None:
        def transport(_http_request, _timeout):
            raise http.client.IncompleteRead(b"")

        provider = OpenAICompatibleProvider(
            name="primary",
            provider="test-provider",
            base_url="https://model.invalid/v1",
            model="configured-model",
            api_key="test-only",
            timeout_seconds=1,
            structured_mode="json_object",
            transport=transport,
        )
        with self.assertRaises(ProviderCallError) as caught:
            await provider.complete(CompletionRequest(
                messages=[Message(role="user", content="ping")],
            ))

        self.assertTrue(caught.exception.transient)
        self.assertEqual(caught.exception.code, "TRANSPORT_ERROR")

    def test_environment_registry_resolves_mimo_fallback_without_business_code_changes(self) -> None:
        fallback = json.dumps([{
            "name": "mimo-fallback",
            "provider": "xiaomi-mimo",
            "base_url_env": "MIMO_LLM_BASE_URL",
            "model_env": "MIMO_LLM_MODEL_ID",
            "api_key_env": "MIMO_LLM_API_KEY",
            "structured_mode": "json_object",
            "thinking_mode": "omit",
            "omit_max_tokens_for_structured": False,
            "max_retries": 1,
        }])
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "deepseek",
            "LLM_BASE_URL": "https://primary.invalid/v1",
            "LLM_MODEL": "primary-model",
            "LLM_API_KEY": "primary-test-key",
            "LLM_THINKING_MODE": "disabled",
            "LLM_MAX_RETRIES": "1",
            "LLM_FALLBACKS_JSON": fallback,
            "MIMO_LLM_BASE_URL": "https://mimo.invalid/v1",
            "MIMO_LLM_MODEL_ID": "mimo-test-model",
            "MIMO_LLM_API_KEY": "mimo-test-key",
        }, clear=True):
            settings = model_settings_from_env()

        self.assertEqual(settings.routes["association"], ("primary", "mimo-fallback"))
        self.assertEqual(settings.endpoints[0].thinking_mode, "disabled")
        self.assertEqual(settings.endpoints[0].max_retries, 1)
        self.assertEqual(settings.endpoints[1].provider, "xiaomi-mimo")
        self.assertEqual(settings.endpoints[1].model, "mimo-test-model")
        self.assertFalse(settings.endpoints[1].omit_max_tokens_for_structured)
        self.assertEqual(settings.endpoints[1].api_key.get_secret_value(), "mimo-test-key")


if __name__ == "__main__":
    unittest.main()
