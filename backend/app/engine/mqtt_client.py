"""后台订阅现场 MQTT，把原始定位报文交给解析引擎。"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any

from ..config import get_settings

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

_STATE: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "broker": "",
    "port": 0,
    "topic": "",
    "received": 0,
    "ingested": 0,
    "skipped": 0,
    "last_dev": "",
    "last_error": "",
    "last_at": None,
}
_LOCK = threading.Lock()
_CLIENT = None


def mqtt_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def note_result(
    ingested: bool = False,
    skipped: bool = False,
    last_dev: str = "",
    error: str | None = None,
) -> None:
    with _LOCK:
        if ingested:
            _STATE["ingested"] = int(_STATE["ingested"] or 0) + 1
        if skipped:
            _STATE["skipped"] = int(_STATE["skipped"] or 0) + 1
        if last_dev:
            _STATE["last_dev"] = last_dev
            _STATE["last_at"] = datetime.now(timezone.utc).isoformat()
        if error:
            _STATE["last_error"] = error


def start_mqtt() -> None:
    global _CLIENT
    settings = get_settings()
    with _LOCK:
        _STATE["enabled"] = bool(settings.mqtt_enabled)
        _STATE["broker"] = settings.mqtt_broker
        _STATE["port"] = int(settings.mqtt_port)
        _STATE["topic"] = settings.mqtt_topic
        _STATE["connected"] = False
        _STATE["last_error"] = ""
    if not settings.mqtt_enabled:
        return
    if mqtt is None:
        with _LOCK:
            _STATE["last_error"] = "未安装 paho-mqtt"
        return
    if _CLIENT is not None:
        return

    client_id = f"{settings.mqtt_client_id}-{os.getpid()}"
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(cli, _userdata, _flags, reason_code, _properties):
        ok = not getattr(reason_code, "is_failure", reason_code != 0)
        with _LOCK:
            _STATE["connected"] = ok
            _STATE["last_error"] = "" if ok else f"MQTT 连接失败 {reason_code}"
        if ok:
            cli.subscribe(settings.mqtt_topic)

    def on_disconnect(_cli, _userdata, _flags, reason_code, _properties):
        failed = getattr(reason_code, "is_failure", bool(reason_code))
        with _LOCK:
            _STATE["connected"] = False
            if failed:
                _STATE["last_error"] = f"MQTT 断开 {reason_code}"

    def on_message(_cli, _userdata, msg):
        with _LOCK:
            _STATE["received"] = int(_STATE["received"] or 0) + 1
        try:
            from .mqtt_ingest import ingest_mqtt_message

            ingest_mqtt_message(msg.topic, msg.payload)
        except Exception as exc:
            note_result(error=str(exc))

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    _CLIENT = client
    try:
        client.connect_async(settings.mqtt_broker, int(settings.mqtt_port), 60)
        client.loop_start()
    except Exception as exc:
        with _LOCK:
            _STATE["last_error"] = str(exc)
