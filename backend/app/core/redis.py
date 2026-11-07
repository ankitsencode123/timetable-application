"""Redis connection and distributed caching utilities.

Provides resilient L2 caching for timetable snapshots, preloading bundles,
AI chat answers, schedule contexts, and advanced actions plans.
Supports TLS (rediss://) for cloud Redis providers like Upstash.
All operations fail-open: if Redis is unavailable or times out, the application
continues uninterrupted using in-memory caches and database queries.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from loguru import logger
import redis
import redis.asyncio as aioredis


def normalize_redis_url(raw_url: Optional[str]) -> str:
    """Normalize Redis URL strings, handling redis-cli commands, TLS, and quotes."""
    if not raw_url:
        return ""
    url = raw_url.strip()
    # Strip command wrapper if user pasted full redis-cli invocation
    if "-u " in url:
        url = url.split("-u ", 1)[1].strip()
    # Strip quotes
    url = url.strip("'\"")
    # Clean any trailing parameters from CLI command
    url = url.split()[0] if url else ""

    # Ensure TLS scheme (rediss://) for Upstash or if TLS flag is present
    if url.startswith("redis://"):
        if "upstash.io" in url or os.getenv("REDIS_USE_TLS", "true").lower() in ("1", "true", "yes"):
            url = "rediss://" + url[len("redis://"):]
    return url


def mask_redis_url(url: str) -> str:
    """Return URL with password masked for safe logging and telemetry."""
    if not url:
        return ""
    # Mask password pattern ://<user>:<password>@ -> ://<user>:***@
    return re.sub(r"(://[^:]+):([^@]+)@", r"\1:***@", url)


# Config
DEFAULT_REDIS_URL = (
    "rediss://default:gQAAAAAAAegbAAIgcDI0MWYwZjNlNzExNWQ0YmEwODRmYmYxM2VhYTBlNjcxMw"
    "@pure-oyster-124955.upstash.io:6379"
)

REDIS_URL = normalize_redis_url(os.getenv("REDIS_URL", DEFAULT_REDIS_URL))
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() in ("1", "true", "yes", "on")

_sync_client: Optional[redis.Redis] = None
_async_client: Optional[aioredis.Redis] = None
_client_lock = asyncio.Lock() if hasattr(asyncio, "Lock") else None

_last_error_log = 0.0
_ERROR_LOG_COOLDOWN = 30.0  # seconds between error log bursts


def _log_redis_error(msg: str, exc: Exception) -> None:
    global _last_error_log
    now = time.monotonic()
    if now - _last_error_log > _ERROR_LOG_COOLDOWN:
        _last_error_log = now
        logger.warning(f"[redis] {msg}: {exc}")


def get_redis_client() -> Optional[redis.Redis]:
    """Return a thread-safe synchronous Redis client instance, or None if disabled/failing."""
    global _sync_client
    if not REDIS_ENABLED or not REDIS_URL:
        return None
    if _sync_client is None:
        try:
            pool = redis.ConnectionPool.from_url(
                REDIS_URL,
                max_connections=20,
                socket_timeout=2.5,
                socket_connect_timeout=3.0,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            _sync_client = redis.Redis(connection_pool=pool)
            logger.info(f"[redis] initialized sync client -> {mask_redis_url(REDIS_URL)}")
        except Exception as exc:
            _log_redis_error("failed to initialize sync client", exc)
            return None
    return _sync_client


_async_client_loop: Optional[asyncio.AbstractEventLoop] = None


def get_async_redis_client() -> Optional[aioredis.Redis]:
    """Return an asynchronous Redis client instance, or None if disabled/failing.
    Automatically re-initializes connection pool if current event loop changes (e.g. in test suites).
    """
    global _async_client, _async_client_loop
    if not REDIS_ENABLED or not REDIS_URL:
        return None
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _async_client is not None and _async_client_loop is not current_loop:
        _async_client = None

    if _async_client is None:
        try:
            pool = aioredis.ConnectionPool.from_url(
                REDIS_URL,
                max_connections=20,
                socket_timeout=2.5,
                socket_connect_timeout=3.0,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            _async_client = aioredis.Redis(connection_pool=pool)
            _async_client_loop = current_loop
            logger.info(f"[redis] initialized async client -> {mask_redis_url(REDIS_URL)}")
        except Exception as exc:
            _log_redis_error("failed to initialize async client", exc)
            return None
    return _async_client


# ============================================================================
# Synchronous Redis Operations (Fail-open)
# ============================================================================
def redis_get(key: str) -> Optional[bytes]:
    """Retrieve raw bytes for key, or None on miss or error."""
    client = get_redis_client()
    if client is None:
        return None
    try:
        val = client.get(key)
        if isinstance(val, str):
            return val.encode("utf-8")
        return val
    except Exception as exc:
        _log_redis_error(f"get failed for key {key}", exc)
        return None


def redis_set(key: str, value: bytes | str, ex: Optional[int] = None) -> bool:
    """Store value with optional expiration in seconds. Returns True if stored."""
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.set(key, value, ex=ex)
        return True
    except Exception as exc:
        _log_redis_error(f"set failed for key {key}", exc)
        return False


def redis_delete(*keys: str) -> int:
    """Delete one or more keys. Returns number of keys removed."""
    if not keys:
        return 0
    client = get_redis_client()
    if client is None:
        return 0
    try:
        return int(client.delete(*keys))
    except Exception as exc:
        _log_redis_error(f"delete failed for {len(keys)} keys", exc)
        return 0


def redis_delete_pattern(pattern: str) -> int:
    """Delete all keys matching pattern using non-blocking SCAN. Returns deleted count."""
    client = get_redis_client()
    if client is None:
        return 0
    try:
        cursor = 0
        total_deleted = 0
        while True:
            cursor, keys = client.scan(cursor, match=pattern, count=100)
            if keys:
                total_deleted += client.delete(*keys)
            if cursor == 0:
                break
        return total_deleted
    except Exception as exc:
        _log_redis_error(f"delete_pattern failed for {pattern}", exc)
        return 0


def redis_get_json(key: str) -> Optional[Any]:
    """Retrieve and deserialize JSON data."""
    raw = redis_get(key)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        _log_redis_error(f"json decode failed for key {key}", exc)
        return None


def redis_set_json(key: str, data: Any, ex: Optional[int] = None) -> bool:
    """Serialize and store JSON data with optional TTL."""
    try:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return redis_set(key, payload, ex=ex)
    except Exception as exc:
        _log_redis_error(f"json serialize failed for key {key}", exc)
        return False


# ============================================================================
# Asynchronous Redis Operations (Fail-open)
# ============================================================================
async def async_redis_get(key: str) -> Optional[bytes]:
    """Asynchronously retrieve raw bytes for key."""
    client = get_async_redis_client()
    if client is None:
        return None
    try:
        val = await client.get(key)
        if isinstance(val, str):
            return val.encode("utf-8")
        return val
    except Exception as exc:
        _log_redis_error(f"async get failed for key {key}", exc)
        return None


async def async_redis_set(key: str, value: bytes | str, ex: Optional[int] = None) -> bool:
    """Asynchronously store value with optional TTL."""
    client = get_async_redis_client()
    if client is None:
        return False
    try:
        await client.set(key, value, ex=ex)
        return True
    except Exception as exc:
        _log_redis_error(f"async set failed for key {key}", exc)
        return False


async def async_redis_delete(*keys: str) -> int:
    """Asynchronously delete keys."""
    if not keys:
        return 0
    client = get_async_redis_client()
    if client is None:
        return 0
    try:
        return int(await client.delete(*keys))
    except Exception as exc:
        _log_redis_error(f"async delete failed for {len(keys)} keys", exc)
        return 0


async def async_redis_delete_pattern(pattern: str) -> int:
    """Asynchronously delete keys matching pattern using non-blocking SCAN."""
    client = get_async_redis_client()
    if client is None:
        return 0
    try:
        cursor = 0
        total_deleted = 0
        while True:
            cursor, keys = await client.scan(cursor, match=pattern, count=100)
            if keys:
                total_deleted += await client.delete(*keys)
            if cursor == 0:
                break
        return total_deleted
    except Exception as exc:
        _log_redis_error(f"async delete_pattern failed for {pattern}", exc)
        return 0


async def async_redis_get_json(key: str) -> Optional[Any]:
    """Asynchronously retrieve and deserialize JSON data."""
    raw = await async_redis_get(key)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        _log_redis_error(f"async json decode failed for key {key}", exc)
        return None


async def async_redis_set_json(key: str, data: Any, ex: Optional[int] = None) -> bool:
    """Asynchronously serialize and store JSON data with optional TTL."""
    try:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return await async_redis_set(key, payload, ex=ex)
    except Exception as exc:
        _log_redis_error(f"async json serialize failed for key {key}", exc)
        return False


# ============================================================================
# Telemetry & Diagnostics
# ============================================================================
def get_redis_stats() -> Dict[str, Any]:
    """Collect health status and stats for /api/admin/cache/stats."""
    client = get_redis_client()
    if client is None:
        return {
            "enabled": REDIS_ENABLED,
            "connected": False,
            "url": mask_redis_url(REDIS_URL),
            "reason": "disabled_or_uninitialized",
        }
    t0 = time.monotonic()
    try:
        client.ping()
        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        info = client.info("memory")
        used_memory = info.get("used_memory_human", "unknown")
        
        # Count keys in timetable namespace
        cursor = 0
        key_count = 0
        while True:
            cursor, keys = client.scan(cursor, match="tt:*", count=200)
            key_count += len(keys)
            if cursor == 0 or key_count >= 500:
                break

        return {
            "enabled": True,
            "connected": True,
            "ping_ms": latency_ms,
            "used_memory_human": used_memory,
            "app_keys_count": key_count,
            "url": mask_redis_url(REDIS_URL),
        }
    except Exception as exc:
        return {
            "enabled": True,
            "connected": False,
            "url": mask_redis_url(REDIS_URL),
            "error": str(exc),
        }
