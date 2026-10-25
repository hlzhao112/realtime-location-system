"""把 MQTT 报文按 dev 映射到项目，再进入现有解析 / 原始数据流程。"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import SessionLocal
from ..models import Project
from ..services.project_svc import write_log
from ..services.queue import enqueue, parse_ts
from .mqtt_codec import parse_mqtt_payload
from .parse import apply_report

_INDEX: dict[str, int] = {}
_INDEX_TS = 0.0
_INDEX_TTL = 5.0
_UNKNOWN_AT: dict[str, float] = {}


def invalidate_dev_index() -> None:
    global _INDEX_TS
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
    global _INDEX, _INDEX_TS
    now = time.monotonic()
    if _INDEX and now - _INDEX_TS < _INDEX_TTL:
        return _INDEX
    rows = db.execute(select(Project).options(selectinload(Project.trolleys))).scalars().all()
    _INDEX = build_dev_index(list(rows))
    _INDEX_TS = now
    return _INDEX


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
    if not project_id:
        return None
    return db.get(Project, project_id)


def _warn_unknown(db: Session, dev: str) -> None:
    now = time.monotonic()
    if now - _UNKNOWN_AT.get(dev, 0) < 60:
        return
    _UNKNOWN_AT[dev] = now
    write_log(db, None, "警告", f"MQTT 报文 dev={dev} 未匹配到项目（请核对 PID / 项目名称 / 读卡器编号）")


def ingest_mqtt_message(topic: str, payload: bytes | str | None) -> dict:
    from .mqtt_client import note_result
    from .runtime import LOCK

    parsed = parse_mqtt_payload(topic, payload)
    if not parsed:
        note_result(skipped=True)
        return {"ok": False, "reason": "ignored"}

    db = SessionLocal()
    try:
        project = resolve_project_by_dev(db, parsed["dev"])
        if not project:
            _warn_unknown(db, parsed["dev"])
            db.commit()
            note_result(skipped=True, last_dev=parsed["dev"])
            return {"ok": False, "reason": "unknown_project", "dev": parsed["dev"]}

        when = parse_ts(parsed.get("ts")) or datetime.now(timezone.utc)
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
        note_result(ingested=True, last_dev=parsed["dev"])
        return {"ok": True, "dev": parsed["dev"], "pid": project.pid, "queued": queued}
    except Exception as exc:
        db.rollback()
        note_result(error=str(exc), last_dev=parsed.get("dev") or "")
        raise
    finally:
        db.close()
