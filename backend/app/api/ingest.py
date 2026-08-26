from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, get_db
from ..deps import get_current_user
from ..engine.parse import apply_report
from ..models import LatestLocation, Project, Trolley, User
from ..schemas import IngestIn, IngestOut, LocationOut
from ..services.cache import resolve_token
from ..services.queue import enqueue, parse_ts

router = APIRouter(prefix="/api/v1", tags=["ingest"])


def _project_by_token(db: Session, token: str | None) -> Project:
    if not token:
        raise HTTPException(401, "缺少 X-Ingest-Token")
    p = resolve_token(db, token)
    if not p:
        raise HTTPException(401, "上报 Token 无效")
    return p


@router.post("/ingest/report", response_model=IngestOut, status_code=202)
def ingest_report(
    body: IngestIn,
    db: Session = Depends(get_db),
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
    authorization: str | None = Header(default=None),
):
    token = x_ingest_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    project = _project_by_token(db, token)
    settings = get_settings()
    epcs = body.epcs or []
    if len(epcs) > settings.ingest_max_epcs:
        raise HTTPException(400, f"单次 EPC 数不能超过 {settings.ingest_max_epcs}")
    if not body.hex and not epcs:
        raise HTTPException(400, "hex 与 epcs 至少提供一个")
    payload = {
        "deviceId": body.deviceId,
        "hex": body.hex,
        "epcs": epcs,
        "ts": (body.ts or datetime.now(timezone.utc)).isoformat(),
        "sportState": body.sportState,
    }
    mid, queued = enqueue(project.id, payload)
    if not queued:
        from ..engine.runtime import LOCK

        with LOCK:
            apply_report(db, project, body.deviceId, body.hex, epcs, body.ts, body.sportState)
    return IngestOut(accepted=True, queued=1, id=mid)


def process_queued(item: dict) -> None:
    from ..engine.runtime import LOCK

    db = SessionLocal()
    try:
        project = db.get(Project, item["project_id"])
        if not project:
            return
        with LOCK:
            apply_report(
                db,
                project,
                item.get("deviceId") or "",
                item.get("hex") or "",
                item.get("epcs") or [],
                parse_ts(item.get("ts")),
                item.get("sportState"),
            )
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/projects/{pid}/locations", response_model=list[LocationOut])
def list_locations(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    """内部接口，供后续主看板使用。本版管理端不展示。"""
    from sqlalchemy import select

    p = db.execute(select(Project).where(Project.pid == pid)).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "项目不存在")
    rows = (
        db.execute(
            select(LatestLocation, Trolley)
            .join(Trolley, Trolley.id == LatestLocation.trolley_id)
            .where(LatestLocation.project_id == p.id)
        )
        .all()
    )
    out = []
    for loc, tz in rows:
        out.append(
            LocationOut(
                tz=tz.tz,
                name=tz.name,
                reader=tz.reader,
                lineCode=loc.line_code,
                lineName=loc.line_name,
                procCode=loc.proc_code,
                procName=loc.proc_name,
                areaCode=loc.area_code,
                areaName=loc.area_name,
                tagNo=loc.tag_no,
                epc=loc.epc,
                sportState=loc.sport_state,
                source=loc.source,
                kind=loc.kind or "实时上报",
                unassigned=bool(loc.unassigned),
                test=bool(loc.test),
                reissue=loc.reissue or 0,
                identifyTime=loc.identify_time,
                updatedAt=loc.updated_at,
            )
        )
    return out
