"""解析现场 MQTT 原始报文：{"dev":"OF2","hex":"..."}。"""

from __future__ import annotations

import json
from typing import Any, Optional


def topic_dev(topic: str) -> str:
    parts = [p for p in str(topic or "").strip().split("/") if p]
    return parts[-1] if parts else ""


def _as_dict(data: Any) -> Optional[dict]:
    if isinstance(data, dict):
        inner = data.get("data")
        if "dev" not in data and "hex" not in data and isinstance(inner, dict):
            return inner
        return data
    return None


def parse_mqtt_payload(topic: str, payload: bytes | str | None) -> Optional[dict]:
    """抽出 RFID 上报。传感器数组（sdata）等非定位报文返回 None。"""
    if payload is None:
        return None
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace").strip()
    else:
        text = str(payload).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    obj = _as_dict(data)
    if not obj:
        return None
    hex_data = str(obj.get("hex") or obj.get("HEX") or "").strip()
    if not hex_data:
        return None
    dev = str(obj.get("dev") or obj.get("deviceId") or obj.get("device_id") or "").strip()
    if not dev:
        dev = topic_dev(topic)
    if not dev:
        return None
    ts = obj.get("timestamp") or obj.get("time") or obj.get("ts")
    return {
        "dev": dev,
        "deviceId": dev,
        "hex": hex_data,
        "ts": ts,
        "topic": topic,
    }
