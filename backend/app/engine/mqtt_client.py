"""后台订阅现场 MQTT，把原始定位报文交给解析引擎。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..config import get_settings

try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties
except ImportError:  # pragma: no cover
    mqtt = None
    PacketTypes = None
    Properties = None

_STATE: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "session_present": False,
    "broker": "",
    "port": 0,
    "topic": "",
    "received": 0,
    "ingested": 0,
    "skipped": 0,
    "last_dev": "",
    "last_error": "",
    "last_at": None,
    "catchup_collected": 0,
    "catchup_at": None,
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
        _STATE["session_present"] = False
        _STATE["last_error"] = ""
    if not settings.mqtt_enabled:
        return
    if mqtt is None:
        with _LOCK:
            _STATE["last_error"] = "未安装 paho-mqtt"
        return
    if _CLIENT is not None:
        return

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.mqtt_client_id,
        protocol=mqtt.MQTTv5,
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(cli, _userdata, flags, reason_code, _properties):
        ok = not getattr(reason_code, "is_failure", reason_code != 0)
        session_present = bool(getattr(flags, "session_present", False))
        with _LOCK:
            _STATE["connected"] = ok
            _STATE["session_present"] = session_present
            if ok:
                _STATE["last_error"] = ""
                if session_present:
                    _STATE["last_error"] = ""
            else:
                _STATE["last_error"] = f"MQTT 连接失败 {reason_code}"
        if ok:
            # QoS 1 + 持久会话：中断期间 broker 会补发未确认报文
            cli.subscribe(settings.mqtt_topic, qos=1)

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
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    _CLIENT = client
    props = None
    if Properties is not None and PacketTypes is not None:
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = int(settings.mqtt_session_expiry or 604800)
    try:
        client.connect_async(
            settings.mqtt_broker,
            int(settings.mqtt_port),
            60,
            clean_start=False,
            properties=props,
        )
        client.loop_start()
        threading.Thread(target=_recover_missed, name="mqtt-catchup", daemon=True).start()
    except Exception as exc:
        with _LOCK:
            _STATE["last_error"] = str(exc)


def stop_mqtt() -> None:
    """优雅断开时保留 MQTT 会话，便于下次上线补发中断期间的报文。"""
    global _CLIENT
    client = _CLIENT
    if client is None:
        return
    try:
        props = None
        if Properties is not None and PacketTypes is not None:
            props = Properties(PacketTypes.DISCONNECT)
            props.SessionExpiryInterval = int(get_settings().mqtt_session_expiry or 604800)
        if props is not None:
            client.disconnect(reasoncode=0, properties=props)
        else:
            client.disconnect()
    except Exception:
        pass
    try:
        client.loop_stop()
    except Exception:
        pass
    _CLIENT = None
    with _LOCK:
        _STATE["connected"] = False


def _recover_missed() -> None:
    """持久会话补发之后，再拉 retain / 短时在线报文，尽量补齐中断期间遗漏值。"""
    time.sleep(2.0)
    try:
        from ..db import SessionLocal
        from .mqtt_ingest import refresh_mqtt_feed

        db = SessionLocal()
        try:
            data = refresh_mqtt_feed(db, timeout_sec=get_settings().mqtt_refetch_sec)
        finally:
            db.close()
        with _LOCK:
            _STATE["catchup_collected"] = int((data or {}).get("collected") or 0)
            _STATE["catchup_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        note_result(error=f"中断补报失败 {exc}")
