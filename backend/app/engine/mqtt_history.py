"""按项目回放 MQTT 当前 retain / 在线路由报文，写入原始数据。"""

from __future__ import annotations

import os
import threading
import time
from typing import Iterable

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Project
from ..services.project_svc import write_log
from .mqtt_codec import parse_mqtt_payload
from .mqtt_ingest import ingest_mqtt_message, project_dev_keys

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

_REFETCH_LOCK = threading.Lock()


def belongs_to_project(dev: str, keys: set[str]) -> bool:
    return (dev or "").strip().lower() in keys


def filter_project_messages(
    messages: Iterable[tuple[str, bytes | str | None]], keys: set[str]
) -> list[dict]:
    matched: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for topic, payload in messages:
        parsed = parse_mqtt_payload(topic, payload)
        if not parsed or not belongs_to_project(parsed["dev"], keys):
            continue
        fingerprint = (parsed["dev"], parsed["hex"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        matched.append({"topic": topic, "payload": payload, **parsed})
    return matched


def collect_mqtt_burst(timeout_sec: float | None = None) -> list[tuple[str, bytes]]:
    settings = get_settings()
    wait = max(2.0, float(timeout_sec if timeout_sec is not None else settings.mqtt_refetch_sec))
    if mqtt is None:
        raise RuntimeError("未安装 paho-mqtt")
    items: list[tuple[str, bytes]] = []
    lock = threading.Lock()
    connected = threading.Event()
    error: list[str] = []

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{settings.mqtt_client_id}-refetch-{os.getpid()}-{time.time_ns()}",
        protocol=mqtt.MQTTv311,
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(cli, _userdata, _flags, reason_code, _properties):
        ok = not getattr(reason_code, "is_failure", reason_code != 0)
        if not ok:
            error.append(f"MQTT 连接失败 {reason_code}")
            connected.set()
            return
        cli.subscribe(settings.mqtt_topic, qos=1)
        connected.set()

    def on_message(_cli, _userdata, msg):
        with lock:
            items.append((msg.topic, msg.payload))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings.mqtt_broker, int(settings.mqtt_port), 20)
    client.loop_start()
    try:
        if not connected.wait(10):
            raise RuntimeError("MQTT 回放连接超时")
        if error:
            raise RuntimeError(error[0])
        time.sleep(wait)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
    return items


def collect_emqx_retained() -> list[tuple[str, bytes]]:
    """尽力从 EMQX HTTP API 拉 retain（失败则忽略）。"""
    settings = get_settings()
    base = (settings.mqtt_api_url or "").rstrip("/")
    if not base:
        return []
    try:
        import httpx
    except ImportError:
        return []
    items: list[tuple[str, bytes]] = []
    try:
        import json

        with httpx.Client(timeout=8.0) as client:
            login = client.post(
                f"{base}/api/v5/login",
                json={"username": settings.mqtt_username, "password": settings.mqtt_password},
            )
            if login.status_code >= 400:
                return []
            token = (login.json() or {}).get("token") or ""
            if not token:
                return []
            headers = {"Authorization": f"Bearer {token}"}
            cursor = None
            for _ in range(20):
                params: dict = {"limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                resp = client.get(f"{base}/api/v5/mqtt/retainer/messages", headers=headers, params=params)
                if resp.status_code >= 400:
                    resp = client.get(f"{base}/api/v5/retainer/messages", headers=headers, params=params)
                if resp.status_code >= 400:
                    break
                data = resp.json() or {}
                rows = data.get("data") or data.get("messages") or []
                if isinstance(data, list):
                    rows = data
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    topic = str(row.get("topic") or "")
                    payload = row.get("payload") or row.get("message") or ""
                    if isinstance(payload, dict):
                        payload = json.dumps(payload, ensure_ascii=False)
                    if isinstance(payload, str):
                        payload = payload.encode("utf-8")
                    if topic and payload:
                        items.append((topic, payload))
                cursor = (data.get("meta") or {}).get("cursor") or data.get("cursor")
                if not cursor or not rows:
                    break
    except Exception:
        return items
    return items


def refetch_project_history(db: Session, project: Project) -> dict:
    if not _REFETCH_LOCK.acquire(blocking=False):
        raise RuntimeError("已有历史回放任务在执行，请稍后再试")
    started = time.monotonic()
    try:
        keys = project_dev_keys(project)
        if not keys:
            raise RuntimeError("项目没有可匹配的 PID / 名称 / 读卡器编号")
        burst = collect_mqtt_burst()
        retained = collect_emqx_retained()
        matched = filter_project_messages(list(retained) + list(burst), keys)
        ingested = 0
        for item in matched:
            try:
                result = ingest_mqtt_message(item["topic"], item["payload"])
                if result.get("ok"):
                    ingested += 1
            except Exception:
                continue
        elapsed = round(time.monotonic() - started, 2)
        write_log(
            db,
            project.id,
            "信息",
            f"重新获取 MQTT 历史：收到 {len(burst) + len(retained)} 条，本项目匹配 {len(matched)} 条，写入 {ingested} 条，耗时 {elapsed}s",
        )
        return {
            "ok": True,
            "collected": len(burst) + len(retained),
            "matched": len(matched),
            "ingested": ingested,
            "skipped": max(0, len(burst) + len(retained) - len(matched)),
            "seconds": elapsed,
            "devs": sorted(keys),
        }
    finally:
        _REFETCH_LOCK.release()
