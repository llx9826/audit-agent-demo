"""集中解析环境配置，不让业务 Node 直接读取环境变量。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


DEFAULT_MODEL_ROLES = (
    "default",
    "association",
    "audit",
    "exception",
    "knowledge_intent",
    "knowledge_grounding",
    "query_rewrite",
    "offline_contextualization",
    "judge",
)


class StrictSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelEndpointSettings(StrictSettings):
    """一个可调用模型 Endpoint；模型名称只存在于配置层。"""

    name: str = Field(min_length=1)
    adapter: Literal["openai_compatible"] = "openai_compatible"
    provider: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=8)
    retry_base_seconds: float = Field(default=0.5, ge=0, le=30)
    structured_mode: Literal["json_schema", "json_object", "prompt_only"] = "json_object"
    # 不同 OpenAI-compatible Endpoint 对“思考模式”的默认值不同。
    # 通过 Endpoint 配置注入厂商差异，业务 Agent 不感知具体模型厂商。
    thinking_mode: Literal["omit", "enabled", "disabled"] = "omit"
    omit_max_tokens_for_structured: bool = False


class ModelSettings(StrictSettings):
    """Endpoint Registry 与按任务角色定义的故障切换链。"""

    endpoints: tuple[ModelEndpointSettings, ...]
    routes: dict[str, tuple[str, ...]]
    schema_retries: int = Field(default=1, ge=0, le=3)
    max_tokens: int = Field(default=1200, ge=64, le=32768)
    role_max_tokens: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_routes(self) -> "ModelSettings":
        names = {item.name for item in self.endpoints}
        if len(names) != len(self.endpoints):
            raise ValueError("model endpoint names must be unique")
        for role, chain in self.routes.items():
            if not chain:
                raise ValueError(f"model route {role!r} cannot be empty")
            unknown = [name for name in chain if name not in names]
            if unknown:
                raise ValueError(f"model route {role!r} references unknown endpoint(s): {unknown}")
        invalid_budgets = {
            role: budget for role, budget in self.role_max_tokens.items()
            if budget < 64 or budget > 32768
        }
        if invalid_budgets:
            raise ValueError(f"model role token budget out of range: {invalid_budgets}")
        return self

    def endpoint_map(self) -> dict[str, ModelEndpointSettings]:
        return {item.name: item for item in self.endpoints}

    def max_tokens_for(self, role: str, override: int | None = None) -> int:
        """优先使用调用方显式预算，其次使用角色预算，最后回退全局值。"""

        return override if override is not None else self.role_max_tokens.get(role, self.max_tokens)


class AppSettings(StrictSettings):
    profile: Literal["demo", "real", "production"] = "demo"
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    model: ModelSettings | None = None
    rag_cache_backend: Literal["none", "memory", "redis"] = "memory"
    rag_cache_redis_url: SecretStr = Field(default_factory=lambda: SecretStr(""))
    rag_cache_ttl_seconds: int = Field(default=900, ge=1, le=86400)
    rag_cache_verify_write: bool = True
    rag_cache_version: str = "requirements-v2-zh-bm25"
    task_worker_max_concurrency: int = Field(default=4, ge=1, le=32)
    audit_graph_recursion_limit: int = Field(default=96, ge=32, le=256)


def _load_project_env() -> None:
    """从项目根目录加载本地 .env；已有进程环境变量始终优先。"""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    project_root = Path(__file__).resolve().parents[3]
    load_dotenv(project_root / ".env", override=False)


def _json_list(name: str) -> list[dict[str, Any]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{name} must be a JSON array of objects")
    return value


def _json_object(name: str) -> dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _endpoint_from_mapping(item: dict[str, Any], *, index: int) -> ModelEndpointSettings:
    api_key = str(item.get("api_key") or "")
    api_key_env = str(item.get("api_key_env") or "").strip()
    if api_key_env:
        api_key = os.getenv(api_key_env, "")
    base_url_env = str(item.get("base_url_env") or "").strip()
    model_env = str(item.get("model_env") or "").strip()
    base_url = os.getenv(base_url_env, "") if base_url_env else str(item.get("base_url") or "")
    model = os.getenv(model_env, "") if model_env else str(item.get("model") or "")
    return ModelEndpointSettings(
        name=str(item.get("name") or f"fallback-{index}"),
        adapter=str(item.get("adapter") or "openai_compatible"),
        provider=str(item.get("provider") or "openai_compatible"),
        base_url=base_url,
        model=model,
        api_key=SecretStr(api_key),
        timeout_seconds=float(item.get("timeout_seconds") or os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        max_retries=int(item.get("max_retries") if item.get("max_retries") is not None else os.getenv("LLM_MAX_RETRIES", "2")),
        retry_base_seconds=float(item.get("retry_base_seconds") or os.getenv("LLM_RETRY_BASE_SECONDS", "0.5")),
        structured_mode=str(item.get("structured_mode") or os.getenv("LLM_JSON_MODE", "json_object")),
        thinking_mode=str(item.get("thinking_mode") or "omit"),
        omit_max_tokens_for_structured=bool(item.get("omit_max_tokens_for_structured", False)),
    )


def model_settings_from_env() -> ModelSettings:
    """构建主模型 + 任意数量 Fallback，不把厂商名称写入业务逻辑。"""

    primary = ModelEndpointSettings(
        name="primary",
        adapter=os.getenv("LLM_ADAPTER", "openai_compatible").strip(),
        provider=os.getenv("LLM_PROVIDER", "openai_compatible").strip(),
        base_url=os.getenv("LLM_BASE_URL", "").strip(),
        model=os.getenv("LLM_MODEL", "").strip(),
        api_key=SecretStr(os.getenv("LLM_API_KEY", "")),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        retry_base_seconds=float(os.getenv("LLM_RETRY_BASE_SECONDS", "0.5")),
        structured_mode=os.getenv("LLM_JSON_MODE", "json_object").strip(),
        thinking_mode=os.getenv("LLM_THINKING_MODE", "omit").strip(),
        omit_max_tokens_for_structured=os.getenv(
            "LLM_OMIT_MAX_TOKENS_FOR_STRUCTURED", "false"
        ).lower() == "true",
    )
    fallbacks = tuple(
        _endpoint_from_mapping(item, index=index)
        for index, item in enumerate(_json_list("LLM_FALLBACKS_JSON"), start=1)
    )
    endpoints = (primary, *fallbacks)
    default_chain = tuple(item.name for item in endpoints)
    configured_routes = _json_object("LLM_ROUTES_JSON")
    routes: dict[str, tuple[str, ...]] = {}
    for role in DEFAULT_MODEL_ROLES:
        env_name = f"LLM_ROUTE_{role.upper()}"
        raw = os.getenv(env_name, "").strip()
        configured = configured_routes.get(role)
        if raw:
            chain = tuple(item.strip() for item in raw.split(",") if item.strip())
        elif isinstance(configured, list):
            chain = tuple(str(item) for item in configured)
        elif isinstance(configured, str):
            chain = tuple(item.strip() for item in configured.split(",") if item.strip())
        else:
            chain = default_chain
        routes[role] = chain
    return ModelSettings(
        endpoints=endpoints,
        routes=routes,
        schema_retries=int(os.getenv("LLM_SCHEMA_RETRIES", "1")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1200")),
        role_max_tokens={
            str(role): int(budget)
            for role, budget in _json_object("LLM_ROLE_MAX_TOKENS_JSON").items()
        },
    )


def settings_from_env(*, profile: str | None = None) -> AppSettings:
    _load_project_env()
    active_profile = (profile or os.getenv("APP_PROFILE", "demo")).strip().lower()
    origins = tuple(
        item.strip()
        for item in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if item.strip()
    )
    # Demo 只固定 Case 和 Tool Observation；RAG 仍使用真实模型。
    # 因此是否组装 ModelGateway 只由 Endpoint 配置决定，不再由
    # APP_PROFILE 偷偷切换成脚本化模型。
    has_model_endpoint = bool(
        os.getenv("LLM_BASE_URL", "").strip()
        and os.getenv("LLM_MODEL", "").strip()
    )
    model = model_settings_from_env() if has_model_endpoint else None
    return AppSettings(
        profile=active_profile,
        cors_origins=origins,
        model=model,
        rag_cache_backend=os.getenv("RAG_CACHE_BACKEND", "memory").strip().lower(),
        rag_cache_redis_url=SecretStr(os.getenv("RAG_CACHE_REDIS_URL", "")),
        rag_cache_ttl_seconds=int(os.getenv("RAG_CACHE_TTL_SECONDS", "900")),
        rag_cache_verify_write=os.getenv("RAG_CACHE_VERIFY_WRITE", "true").lower() == "true",
        rag_cache_version=os.getenv(
            "RAG_CACHE_VERSION",
            os.getenv("REQUIREMENT_RAG_INDEX_VERSION", "requirements-v2-zh-bm25"),
        ),
        task_worker_max_concurrency=int(os.getenv("TASK_WORKER_MAX_CONCURRENCY", "4")),
        audit_graph_recursion_limit=int(os.getenv("AUDIT_GRAPH_RECURSION_LIMIT", "96")),
    )
