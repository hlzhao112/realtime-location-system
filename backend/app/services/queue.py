"""上报入队。Redis 可用走 Stream；否则同步解析（降级）。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import ulid

from ..config import get_settings

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

_r = None
_ok = None


def redis_client():
    global _r, _ok
    if _ok is False:
        return None
    if _r is not None:
        return _r
    if redis is None:
        _ok = False
        return None
    try:
        cli = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        cli.ping()
        _r = cli
        _ok = True
        return cli
    except Exception:
        _ok = False
        return None


def dedup_key(project_id: int, device_id: str, ts: str, hex_data: str) -> str:
    raw = f"{project_id}|{device_id}|{ts}|{hex_data}"
    return "dedup:" + hashlib.sha1(raw.encode()).hexdigest()


def enqueue(project_id: int, payload: dict) -> tuple[str, bool]:
    """返回 (id, queued_async)。"""
    mid = str(ulid.new())
    settings = get_settings()
    payload = {**payload, "id": mid, "project_id": project_id}
    r = redis_client()
    if not r:
        return mid, False
    key = dedup_key(project_id, payload.get("deviceId", ""), str(payload.get("ts") or ""), payload.get("hex") or "")
    if not r.set(key, "1", nx=True, ex=settings.ingest_dedup_ttl):
        return payload.get("id") or mid, True
    r.xadd(
        settings.ingest_stream,
        {"data": json.dumps(payload, default=str, ensure_ascii=False)},
        maxlen=settings.ingest_stream_maxlen,
        approximate=True,
    )
    return mid, True


def consume_one(handler) -> bool:
    settings = get_settings()
    r = redis_client()
    if not r:
        return False
    group = "parsers"
    try:
        r.xgroup_create(settings.ingest_stream, group, id="0", mkstream=True)
    except Exception:
        pass
    msgs = r.xreadgroup(group, "w1", {settings.ingest_stream: ">"}, count=20, block=500)
    if not msgs:
        return False
    for _stream, items in msgs:
        for msg_id, fields in items:
            try:
                handler(json.loads(fields["data"]))
                r.xack(settings.ingest_stream, group, msg_id)
            except Exception:
                # 留在 PEL，后续可做死信；本版不让异常打断循环
                continue
    return True


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
