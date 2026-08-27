"""模型能力边界。

业务模块只依赖这里导出的协议与 ModelGateway，不感知 DeepSeek、Qwen、
vLLM 或其他厂商 SDK。模型与故障切换顺序全部由启动配置决定。
"""

from .contracts import (
    CompletionRequest,
    CompletionResponse,
    GatewayAttempt,
    GatewayTrace,
    LLMProvider,
    Message,
    StructuredResult,
    Usage,
)
from .gateway import ModelGateway, gateway_from_settings

__all__ = [
    "CompletionRequest",
    "CompletionResponse",
    "GatewayAttempt",
    "GatewayTrace",
    "LLMProvider",
    "Message",
    "ModelGateway",
    "StructuredResult",
    "Usage",
    "gateway_from_settings",
]
