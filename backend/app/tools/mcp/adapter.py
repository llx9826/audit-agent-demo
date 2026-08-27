"""Lazy, optional MCP boundary mapped into the provider-neutral ToolRegistry."""
from __future__ import annotations

from typing import Any, Callable, Protocol

from pydantic import BaseModel

from ..contracts import AnyToolInput, ToolObservation, ToolRuntimeContext, ToolSpec
from ..registry import ToolRegistry


class McpUnavailableError(RuntimeError):
    pass


class McpClient(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


McpClientFactory = Callable[[], McpClient]


class LazyMcpToolAdapter:
    """Register MCP capabilities without importing or connecting at startup.

    Production supplies a factory backed by the installed MCP SDK. Tests can
    inject a tiny fake client; the base backend therefore has no hard MCP
    dependency and performs no network access during import or registration.
    """

    def __init__(self, *, server_name: str, client_factory: McpClientFactory | None = None) -> None:
        self.server_name = server_name
        self._client_factory = client_factory
        self._client: McpClient | None = None

    def _get_client(self) -> McpClient:
        if self._client is not None:
            return self._client
        if self._client_factory is None:
            try:
                import mcp  # noqa: F401  # optional dependency probe, intentionally lazy
            except ImportError as exc:
                raise McpUnavailableError(
                    "MCP SDK is optional; install it and provide client_factory to enable this server"
                ) from exc
            raise McpUnavailableError("provide client_factory for the configured MCP transport/session")
        self._client = self._client_factory()
        return self._client

    def register(
        self,
        registry: ToolRegistry,
        *,
        name: str,
        remote_name: str,
        description: str,
        supported_intents: list[str],
        timeout_ms: int = 10_000,
        max_retries: int = 1,
    ) -> None:
        spec = ToolSpec(
            name=name,
            version="1.0.0",
            description=description,
            provider_type="MCP",
            provider_name=self.server_name,
            supported_intents=supported_intents,
            timeout_ms=timeout_ms,
            max_retries=max_retries,
        )

        def invoke(arguments: BaseModel, _runtime: ToolRuntimeContext) -> ToolObservation:
            client = self._get_client()
            payload = client.call_tool(remote_name, arguments.model_dump())
            if isinstance(payload, ToolObservation):
                return payload
            if isinstance(payload, dict):
                if "result" in payload:
                    return ToolObservation.model_validate(payload)
                return ToolObservation(result=str(payload), metadata={"raw": payload})
            return ToolObservation(result=str(payload))

        registry.register(spec, invoke, input_model=AnyToolInput)
