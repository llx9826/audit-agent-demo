"""RAG 精确作用域缓存 Port 与本地/Redis Adapter。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
import time
from typing import Any, Protocol


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_cache_key(namespace: str, material: dict[str, Any]) -> str:
    """只缓存完全一致的作用域；地区/分行/产品/版本都进入 Key。"""

    digest = sha256(_canonical(material).encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


@dataclass(frozen=True, slots=True)
class CacheRead:
    hit: bool
    backend: str
    key_digest: str
    value: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CacheWriteReceipt:
    stored: bool
    verified: bool
    backend: str
    key_digest: str
    reason: str | None = None


class RagCache(Protocol):
    backend_name: str

    def get(self, key: str) -> CacheRead: ...

    def set(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> CacheWriteReceipt: ...

    def delete(self, key: str) -> None: ...

    def healthcheck(self) -> bool: ...


def _envelope(key: str, value: dict[str, Any], ttl_seconds: int) -> str:
    created_at = time.time()
    payload_json = _canonical(value)
    return _canonical({
        "schema_version": "1.0",
        "key_digest": sha256(key.encode("utf-8")).hexdigest()[:16],
        "created_at": created_at,
        "expires_at": created_at + ttl_seconds,
        "payload_checksum": sha256(payload_json.encode("utf-8")).hexdigest(),
        "payload": value,
    })


def _decode(key: str, raw: str | bytes | None, backend: str) -> CacheRead:
    digest = sha256(key.encode("utf-8")).hexdigest()[:16]
    if raw is None:
        return CacheRead(False, backend, digest, reason="MISS")
    try:
        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        envelope = json.loads(decoded)
        if float(envelope["expires_at"]) <= time.time():
            return CacheRead(False, backend, digest, reason="EXPIRED")
        payload = envelope["payload"]
        checksum = sha256(_canonical(payload).encode("utf-8")).hexdigest()
        if checksum != envelope["payload_checksum"]:
            return CacheRead(False, backend, digest, reason="CHECKSUM_MISMATCH")
        if not isinstance(payload, dict):
            return CacheRead(False, backend, digest, reason="INVALID_PAYLOAD")
        return CacheRead(True, backend, digest, value=payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return CacheRead(False, backend, digest, reason="CORRUPT")


class NullRagCache:
    backend_name = "NONE"

    def get(self, key: str) -> CacheRead:
        return CacheRead(False, self.backend_name, sha256(key.encode()).hexdigest()[:16], reason="DISABLED")

    def set(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> CacheWriteReceipt:
        del value, ttl_seconds
        return CacheWriteReceipt(False, False, self.backend_name, sha256(key.encode()).hexdigest()[:16], "DISABLED")

    def delete(self, key: str) -> None:
        del key

    def healthcheck(self) -> bool:
        return True


class MemoryRagCache:
    """单进程 TTL 缓存，用于本地测试与面试演示。"""

    backend_name = "MEMORY"

    def __init__(self, *, verify_write: bool = True) -> None:
        self.verify_write = verify_write
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def get(self, key: str) -> CacheRead:
        with self._lock:
            result = _decode(key, self._values.get(key), self.backend_name)
            if not result.hit and result.reason in {"EXPIRED", "CORRUPT", "CHECKSUM_MISMATCH", "INVALID_PAYLOAD"}:
                self._values.pop(key, None)
            return result

    def set(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> CacheWriteReceipt:
        with self._lock:
            self._values[key] = _envelope(key, value, ttl_seconds)
            verified = self.get(key).hit if self.verify_write else True
        return CacheWriteReceipt(True, verified, self.backend_name, sha256(key.encode()).hexdigest()[:16])

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)

    def healthcheck(self) -> bool:
        return True


class RedisRagCache:
    """Redis Adapter；配置它时连接失败必须显式失败，不伪装成 Memory。"""

    backend_name = "REDIS"

    def __init__(self, url: str, *, verify_write: bool = True, client: Any | None = None) -> None:
        if client is None:
            try:
                from redis import Redis
            except ImportError as exc:  # pragma: no cover - optional integration
                raise RuntimeError("Redis RAG cache requires the redis package") from exc
            client = Redis.from_url(url, decode_responses=False)
        self.client = client
        self.verify_write = verify_write

    def get(self, key: str) -> CacheRead:
        return _decode(key, self.client.get(key), self.backend_name)

    def set(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> CacheWriteReceipt:
        stored = bool(self.client.set(key, _envelope(key, value, ttl_seconds), ex=ttl_seconds))
        verified = self.get(key).hit if stored and self.verify_write else stored
        return CacheWriteReceipt(stored, verified, self.backend_name, sha256(key.encode()).hexdigest()[:16])

    def delete(self, key: str) -> None:
        self.client.delete(key)

    def healthcheck(self) -> bool:
        return bool(self.client.ping())


def build_rag_cache(*, backend: str, redis_url: str = "", verify_write: bool = True) -> RagCache:
    normalized = backend.strip().lower()
    if normalized == "memory":
        return MemoryRagCache(verify_write=verify_write)
    if normalized == "redis":
        if not redis_url:
            raise ValueError("RAG_CACHE_REDIS_URL is required when backend=redis")
        cache = RedisRagCache(redis_url, verify_write=verify_write)
        if not cache.healthcheck():
            raise RuntimeError("configured Redis RAG cache failed readiness probe")
        return cache
    if normalized == "none":
        return NullRagCache()
    raise ValueError(f"unsupported RAG cache backend: {backend}")
