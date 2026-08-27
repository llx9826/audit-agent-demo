"""Optional OFFLINE contextual retrieval enrichment.

The contextualizer creates search-only text.  It never replaces the immutable
source chunk used for evidence and citations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import os
from threading import RLock
from typing import Protocol
from urllib.request import Request, urlopen

from ...bootstrap.settings import settings_from_env
from ...providers import Message, ModelGateway, gateway_from_settings


@dataclass(frozen=True, slots=True)
class ContextRequest:
    source_id: str
    source_title: str
    publisher: str
    product: str
    jurisdiction: str
    parent_heading: str
    parent_text: str
    chunk_text: str
    semantic_title: str

    @property
    def cache_key(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        return sha256(payload.encode()).hexdigest()


class RetrievalContextualizer(Protocol):
    name: str

    def contextualize(self, request: ContextRequest) -> str: ...


@dataclass(frozen=True, slots=True)
class OfflinePromptAsset:
    prompt_id: str
    version: str
    system: str
    user_template: str
    sha256: str


def load_contextualization_prompt(
    *,
    version: str = "v1",
    prompt_root: str | Path | None = None,
) -> OfflinePromptAsset:
    """Load a versioned prompt without coupling the offline job to Agent runtime."""

    root = (
        Path(prompt_root)
        if prompt_root is not None
        else Path(__file__).resolve().parents[2] / "prompts"
    )
    directory = root / "offline_contextualization" / version
    manifest_path = directory / "prompt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {"prompt_id", "version", "system_file", "user_template_file"}
    if set(manifest) != expected:
        raise ValueError("offline contextualization prompt manifest has invalid fields")
    system_path = directory / str(manifest["system_file"])
    user_template_path = directory / str(manifest["user_template_file"])
    system = system_path.read_text(encoding="utf-8").strip()
    user_template = user_template_path.read_text(encoding="utf-8").strip()
    digest = sha256(
        manifest_path.read_bytes()
        + b"\0"
        + system.encode("utf-8")
        + b"\0"
        + user_template.encode("utf-8")
    ).hexdigest()
    return OfflinePromptAsset(
        prompt_id=str(manifest["prompt_id"]),
        version=str(manifest["version"]),
        system=system,
        user_template=user_template,
        sha256=digest,
    )


class DeterministicContextualizer:
    """Reproducible baseline for tests and dependency-light demos."""

    name = "deterministic-structure-context-v1"

    def contextualize(self, request: ContextRequest) -> str:
        return (
            f"该材料要求来自{request.publisher}发布的《{request.source_title}》，"
            f"适用于{request.jurisdiction}范围的{request.product}，"
            f"位于“{request.parent_heading}”中的“{request.semantic_title}”。"
        )


class JsonContextCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._values = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {}
        )
        self._lock = RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._values.get(key)
        return str(value) if value else None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._values[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )


class QwenVllmContextualizer:
    """OpenAI-compatible Qwen/vLLM adapter used only by the offline build job."""

    name = "qwen-vllm-contextual-retrieval-v1"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        cache: JsonContextCache,
        timeout_seconds: float = 30,
        prompt: OfflinePromptAsset | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.prompt = prompt or load_contextualization_prompt()

    @property
    def prompt_metadata(self) -> dict[str, str]:
        return {
            "prompt_id": self.prompt.prompt_id,
            "version": self.prompt.version,
            "sha256": self.prompt.sha256,
        }

    def contextualize(self, request: ContextRequest) -> str:
        if cached := self.cache.get(request.cache_key):
            return cached
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 160,
            "messages": [
                {
                    "role": "system",
                    "content": self.prompt.system,
                },
                {
                    "role": "user",
                    "content": self.prompt.user_template.format(
                        document_title=request.source_title,
                        publisher=request.publisher,
                        product=request.product,
                        jurisdiction=request.jurisdiction,
                        section=request.parent_heading,
                        parent_text=request.parent_text,
                        chunk=request.chunk_text,
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        http_request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers=headers,
            method="POST",
        )
        with urlopen(http_request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        context = str(body["choices"][0]["message"]["content"]).strip()
        if not context or len(context) > 600 or "```" in context:
            raise ValueError("Qwen contextualizer returned invalid search context")
        self.cache.set(request.cache_key, context)
        return context


class GatewayContextualizer:
    """模型无关的离线上下文增强；与在线 Agent 共享 Gateway 配置和故障切换。"""

    name = "model-gateway-contextual-retrieval-v1"

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        cache: JsonContextCache,
        prompt: OfflinePromptAsset | None = None,
    ) -> None:
        self.gateway = gateway
        self.cache = cache
        self.prompt = prompt or load_contextualization_prompt()
        self.last_trace: dict[str, object] | None = None

    @property
    def prompt_metadata(self) -> dict[str, str]:
        return {
            "prompt_id": self.prompt.prompt_id,
            "version": self.prompt.version,
            "sha256": self.prompt.sha256,
        }

    def contextualize(self, request: ContextRequest) -> str:
        if cached := self.cache.get(request.cache_key):
            return cached
        user = self.prompt.user_template.format(
            document_title=request.source_title,
            publisher=request.publisher,
            product=request.product,
            jurisdiction=request.jurisdiction,
            section=request.parent_heading,
            parent_text=request.parent_text,
            chunk=request.chunk_text,
        )
        response, trace = self.gateway.complete_sync(
            role="offline_contextualization",
            messages=[
                Message(role="system", content=self.prompt.system),
                Message(role="user", content=user),
            ],
            # Reasoning-capable OpenAI-compatible models may count hidden reasoning
            # against max_tokens; 160 can finish before emitting visible content.
            max_tokens=int(os.getenv("OFFLINE_CONTEXT_MAX_TOKENS", "384")),
        )
        self.last_trace = trace.to_public_dict()
        context = response.text.strip()
        if not context or len(context) > 600 or "```" in context:
            raise ValueError("offline contextualizer returned invalid search context")
        self.cache.set(request.cache_key, context)
        return context


def configured_contextualizer(
    profile: str,
    *,
    cache_path: str | Path,
) -> RetrievalContextualizer:
    if profile == "deterministic":
        return DeterministicContextualizer()
    if profile not in {"model", "qwen"}:
        raise ValueError(f"unsupported contextualizer profile: {profile}")
    settings = settings_from_env(profile="real")
    if settings.model is None:  # pragma: no cover - defensive composition guard
        raise ValueError("model settings are required for model contextualization")
    return GatewayContextualizer(
        gateway=gateway_from_settings(settings.model),
        cache=JsonContextCache(cache_path),
    )
