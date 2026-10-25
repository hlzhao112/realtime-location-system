"""把 MQTT 报文按 dev 映射到项目，再进入现有解析 / 原始数据流程。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import SessionLocal
from ..models import MqttEvent, Project
from ..services.pid_rules import match_contained_pid
from ..services.project_svc import write_log
from ..services.queue import enqueue, parse_ts
from .mqtt_codec import parse_mqtt_payload
from .parse import apply_report

_INDEX: dict[str, int] = {}
_PIDS: list[str] = []
_INDEX_TS = 0.0
_INDEX_TTL = 5.0
_UNKNOWN_AT: dict[str, float] = {}
_SAVE_N = 0
_MAX_EVENTS = 50000


def invalidate_dev_index() -> None:
    global _INDEX, _PIDS, _INDEX_TS
    _INDEX = {}
    _PIDS = []
    _INDEX_TS = 0.0


def build_dev_index(projects: list[Project]) -> dict[str, int]:
    """PID > 项目名称 > 台车读卡器编号。先写入的键不会被后续覆盖。"""
    index: dict[str, int] = {}
    for p in projects:
        key = (p.pid or "").strip().lower()
        if key:
            index[key] = p.id
    for p in projects:
        key = (p.name or "").strip().lower()
        if key and key not in index:
            index[key] = p.id
    for p in projects:
        for t in p.trolleys or []:
            key = (t.reader or "").strip().lower()
            if key and key not in index:
                index[key] = p.id
    return index


def _index(db: Session) -> dict[str, int]:
    global _INDEX, _PIDS, _INDEX_TS
    now = time.monotonic()
    if _INDEX and now - _INDEX_TS < _INDEX_TTL:
        return _INDEX
    rows = list(db.execute(select(Project).options(selectinload(Project.trolleys))).scalars().all())
    _INDEX = build_dev_index(rows)
    _PIDS = [(p.pid or "").strip() for p in rows if (p.pid or "").strip()]
    _INDEX_TS = now
    return _INDEX


def listed_pids(db: Session) -> list[str]:
    _index(db)
    return list(_PIDS)


def project_dev_keys(project: Project) -> set[str]:
    keys: set[str] = set()
    for value in [project.pid, project.name, *[t.reader for t in (project.trolleys or [])]]:
        key = (value or "").strip().lower()
        if key:
            keys.add(key)
    return keys


def resolve_project_by_dev(db: Session, dev: str) -> Project | None:
    key = (dev or "").strip().lower()
    if not key:
        return None
    project_id = _index(db).get(key)
    if project_id:
        return db.get(Project, project_id)
    contained = match_contained_pid(dev, listed_pids(db))
    if not contained:
        return None
    return db.execute(select(Project).where(Project.pid == contained)).scalar_one_or_none()


def _warn_unknown(db: Session, dev: str) -> None:
    now = time.monotonic()
    if now - _UNKNOWN_AT.get(dev, 0) < 60:
        return
    _UNKNOWN_AT[dev] = now
    write_log(db, None, "警告", f"MQTT 报文 dev={dev} 未匹配到项目（请核对 PID / 项目名称 / 读卡器编号）")


def _prune_events(db: Session) -> None:
    total = db.scalar(select(func.count(MqttEvent.id))) or 0
    if total <= _MAX_EVENTS:
        return
    extra = int(total) - (_MAX_EVENTS * 4 // 5)
    ids = db.scalars(select(MqttEvent.id).order_by(MqttEvent.id.asc()).limit(extra)).all()
    if ids:
        db.query(MqttEvent).filter(MqttEvent.id.in_(ids)).delete(synchronize_session=False)


def persist_mqtt_event(db: Session, parsed: dict, when: datetime) -> None:
    global _SAVE_N
    cutoff = when - timedelta(seconds=3)
    dup = db.scalar(
        select(MqttEvent.id)
        .where(
            MqttEvent.device_id == parsed["dev"],
            MqttEvent.hex_data == parsed["hex"],
            MqttEvent.ts >= cutoff,
        )
        .limit(1)
    )
    if dup:
        return
    db.add(
        MqttEvent(
            device_id=parsed["dev"],
            hex_data=parsed["hex"],
            topic=parsed.get("topic") or "",
            ts=when,
        )
    )
    _SAVE_N += 1
    if _SAVE_N % 200 == 0:
        _prune_events(db)


def _fmt_event_ts(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S") + f".{local.microsecond // 1000:03d}"


def list_mqtt_events(db: Session, pid: str = "", limit: int = 1000) -> dict:
    limit = max(1, min(int(limit or 1000), 5000))
    fetch_n = min(5000, max(limit * 20, 2000)) if pid else limit
    rows = db.scalars(select(MqttEvent).order_by(MqttEvent.ts.desc(), MqttEvent.id.desc()).limit(fetch_n)).all()
    pids = listed_pids(db)
    projects = [
        {"pid": p.pid, "name": p.name}
        for p in db.execute(select(Project).order_by(Project.pid.asc())).scalars().all()
        if (p.pid or "").strip()
    ]
    items = []
    for r in rows:
        matched = match_contained_pid(r.device_id, pids)
        if pid == "__none__" and matched:
            continue
        if pid and pid not in {"__none__", "all"} and matched.lower() != pid.strip().lower():
            continue
        items.append(
            {
                "id": r.id,
                "deviceId": r.device_id,
                "projectPid": matched,
                "hex": r.hex_data,
                "topic": r.topic,
                "ts": _fmt_event_ts(r.ts),
            }
        )
        if len(items) >= limit:
            break
    return {"items": items, "total": len(items), "pids": pids, "projects": projects}


def ingest_mqtt_message(topic: str, payload: bytes | str | None) -> dict:
    from .mqtt_client import note_result
    from .runtime import LOCK

    parsed = parse_mqtt_payload(topic, payload)
    if not parsed:
        note_result(skipped=True)
        return {"ok": False, "reason": "ignored"}

    db = SessionLocal()
    try:
        when = parse_ts(parsed.get("ts")) or datetime.now(timezone.utc)
        persist_mqtt_event(db, parsed, when)
        project = resolve_project_by_dev(db, parsed["dev"])
        if not project:
            _warn_unknown(db, parsed["dev"])
            db.commit()
            note_result(skipped=True, last_dev=parsed["dev"])
            return {"ok": False, "reason": "unknown_project", "dev": parsed["dev"]}

        body = {
            "deviceId": parsed["deviceId"],
            "hex": parsed["hex"],
            "epcs": [],
            "ts": when.isoformat(),
            "sportState": None,
        }
        _mid, queued = enqueue(project.id, body)
        if not queued:
            with LOCK:
                apply_report(
                    db,
                    project,
                    parsed["deviceId"],
                    parsed["hex"],
                    [],
                    when,
                    None,
                    test=False,
                )
            db.commit()
        else:
            db.commit()
        note_result(ingested=True, last_dev=parsed["dev"])
        return {"ok": True, "dev": parsed["dev"], "pid": project.pid, "queued": queued}
    except Exception as exc:
        db.rollback()
        note_result(error=str(exc), last_dev=parsed.get("dev") or "")
        raise
    finally:
        db.close()


def refresh_mqtt_feed(db: Session, timeout_sec: float = 5.0) -> dict:
    from .mqtt_history import collect_emqx_retained, collect_mqtt_burst

    burst = collect_mqtt_burst(timeout_sec=timeout_sec)
    retained = collect_emqx_retained()
    collected = 0
    for topic, payload in list(retained) + list(burst):
        ingest_mqtt_message(topic, payload)
        collected += 1
    data = list_mqtt_events(db)
    data["collected"] = collected
    return data
