

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from redis import Redis

from app.config import settings


logger = logging.getLogger(__name__)


class CacheStore(Protocol):
    def get_json(self, key: str) -> Any | None: ...

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> bool: ...

    def delete(self, key: str) -> bool: ...


class NullCache:
    """No-op implementation used when caching is disabled or unavailable."""

    def get_json(self, key: str) -> Any | None:
        return None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> bool:
        return False

    def delete(self, key: str) -> bool:
        return False


class RedisJsonCache:
    def __init__(self, client: Redis, *, prefix: str) -> None:
        self.client = client
        self.prefix = prefix.strip(":") or "jm:v1"

    def get_json(self, key: str) -> Any | None:
        try:
            raw = self.client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:  # Redis is an acceleration layer, never a hard dependency.
            logger.warning("Redis cache read failed: %s", _safe_error(exc))
            if settings.cache_fail_open:
                return None
            raise RuntimeError("Redis cache read failed") from exc

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> bool:
        try:
            self.client.set(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), ex=max(1, ttl_seconds))
            return True
        except Exception as exc:
            logger.warning("Redis cache write failed: %s", _safe_error(exc))
            if settings.cache_fail_open:
                return False
            raise RuntimeError("Redis cache write failed") from exc

    def delete(self, key: str) -> bool:
        try:
            self.client.delete(key)
            return True
        except Exception as exc:
            logger.warning("Redis cache delete failed: %s", _safe_error(exc))
            if settings.cache_fail_open:
                return False
            raise RuntimeError("Redis cache delete failed") from exc


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {' '.join(str(error).split())[:160]}"


def build_cache_key(namespace: str, identity: Mapping[str, Any] | None = None) -> str:
    """Build a versioned key without placing user text in Redis keys."""
    payload = json.dumps(identity or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    normalized_namespace = namespace.strip(":")
    return f"{settings.cache_prefix.strip(':')}:{normalized_namespace}:{digest}"


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_cache: CacheStore | None = None


def get_cache() -> CacheStore:
    """Return a lazily-created cache; never fail application startup."""
    global _cache
    if not settings.cache_enabled:
        return NullCache()
    if _cache is not None:
        return _cache
    try:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        _cache = RedisJsonCache(client, prefix=settings.cache_prefix)
        return _cache
    except Exception as exc:
        logger.warning("Redis cache initialization failed: %s", _safe_error(exc))
        if settings.cache_fail_open:
            return NullCache()
        raise RuntimeError("Redis cache initialization failed") from exc


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
