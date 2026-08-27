"""OpenAI-compatible Chat Completions Adapter。

DeepSeek、Qwen/vLLM、OpenAI-compatible Gateway 都可使用该 Wire Protocol。
厂商差异只通过 Endpoint 的 structured_mode 配置处理。
"""
from __future__ import annotations

import asyncio
import http.client
import json
from typing import Any, Callable
from urllib import error, request

from .contracts import (
    CompletionRequest,
    CompletionResponse,
    ProviderCallError,
    Usage,
)


Transport = Callable[[request.Request, float], bytes]


class OpenAICompatibleProvider:
    """一个 Endpoint 的无重试 Adapter；策略由 ModelGateway 统一控制。"""

    def __init__(
        self,
        *,
        name: str,
        provider: str,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        structured_mode: str,
        thinking_mode: str = "omit",
        omit_max_tokens_for_structured: bool = False,
        transport: Transport | None = None,
    ) -> None:
        self.name = name
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.structured_mode = structured_mode
        self.thinking_mode = thinking_mode
        self.omit_max_tokens_for_structured = omit_max_tokens_for_structured
        self._transport = transport or self._urlopen

    @staticmethod
    def _urlopen(http_request: request.Request, timeout: float) -> bytes:
        with request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310 - deployment-owned endpoint
            return response.read()

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _body(self, req: CompletionRequest) -> dict[str, Any]:
        messages = [message.model_dump(exclude_none=True) for message in req.messages]
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": req.temperature,
            "messages": messages,
        }
        if not (req.response_schema and self.omit_max_tokens_for_structured):
            body["max_tokens"] = req.max_tokens
        if self.thinking_mode != "omit":
            body["thinking"] = {"type": self.thinking_mode}
        if req.response_schema:
            if self.structured_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": req.schema_name or "structured_output",
                        "strict": True,
                        "schema": req.response_schema,
                    },
                }
            elif self.structured_mode == "json_object":
                body["response_format"] = {"type": "json_object"}
                messages.append({
                    "role": "user",
                    "content": (
                        "必须只输出一个 JSON 对象，并严格符合以下 JSON Schema；"
                        "不要添加 Markdown 代码块或解释：\n"
                        + json.dumps(req.response_schema, ensure_ascii=False, sort_keys=True)
                    ),
                })
            elif self.structured_mode == "prompt_only":
                messages.append({
                    "role": "user",
                    "content": (
                        "只输出符合以下 JSON Schema 的 JSON 对象：\n"
                        + json.dumps(req.response_schema, ensure_ascii=False, sort_keys=True)
                    ),
                })
            else:
                raise ProviderCallError(
                    "unsupported structured output mode",
                    code="UNSUPPORTED_STRUCTURED_MODE",
                    transient=False,
                )
        return body

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        http_request = request.Request(
            self._endpoint(),
            data=json.dumps(self._body(req), ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            raw = await asyncio.to_thread(self._transport, http_request, self.timeout_seconds)
            payload = json.loads(raw)
            content = payload["choices"][0]["message"].get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                )
            finish_reason = str(payload["choices"][0].get("finish_reason") or "stop")
            if req.response_schema and not str(content).strip():
                raise ProviderCallError(
                    "model endpoint returned empty structured output",
                    code="EMPTY_STRUCTURED_OUTPUT",
                    transient=True,
                )
            if req.response_schema and finish_reason == "length":
                raise ProviderCallError(
                    "model endpoint truncated structured output",
                    code="STRUCTURED_OUTPUT_TRUNCATED",
                    transient=True,
                )
            usage = payload.get("usage") or {}
            return CompletionResponse(
                text=str(content),
                finish_reason=finish_reason,
                usage=Usage(
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    cached_input_tokens=int(
                        (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                    ),
                ),
                provider=self.provider,
                model=self.model,
            )
        except error.HTTPError as exc:
            status = int(exc.code)
            transient = status in {408, 409, 425, 429} or status >= 500
            raise ProviderCallError(
                f"model endpoint returned HTTP {status}",
                code=f"HTTP_{status}",
                transient=transient,
                status_code=status,
            ) from exc
        except (TimeoutError, ConnectionError, error.URLError, OSError, http.client.HTTPException) as exc:
            raise ProviderCallError(
                "model endpoint transport failed",
                code="TRANSPORT_ERROR",
                transient=True,
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderCallError(
                "model endpoint returned an invalid response envelope",
                code="INVALID_RESPONSE_ENVELOPE",
                transient=False,
            ) from exc
