"""Provider-neutral contracts for Agent-to-Tool interaction."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnyToolInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class EmptyToolInput(StrictModel):
    pass


class ToolSpec(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    provider_type: Literal["LOCAL", "MCP"]
    provider_name: str = Field(min_length=1)
    supported_intents: list[str] = Field(min_length=1)
    side_effect: Literal["READ_ONLY", "STATE_PROPOSAL"] = "READ_ONLY"
    preconditions: list[str] = Field(default_factory=list)
    cost_tier: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    latency_tier: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    idempotency_scope: str = "tool_call_id"
    timeout_ms: int = Field(default=10_000, ge=1)
    max_retries: int = Field(default=0, ge=0, le=5)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(StrictModel):
    tool_call_id: str = Field(default_factory=lambda: f"TC-{uuid4().hex[:12].upper()}")
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: str = Field(min_length=1)
    task_intent: str = Field(min_length=1)
    allowed_tools: list[str]


class ToolObservation(StrictModel):
    status: Literal["SUCCESS", "FAILED", "TIMEOUT", "REJECTED"] = "SUCCESS"
    result: str
    normalized_value: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider_type: Literal["LOCAL", "MCP"] = "LOCAL"
    provider_name: str = "local"
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class ToolRuntimeContext:
    """Runtime-only values are deliberately excluded from model-visible schema."""

    case_id: str = ""
    task_id: str = ""
    task_intent: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    subject: Any = None
