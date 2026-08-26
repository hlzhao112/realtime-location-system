"""映射热缓存：EPC / 读卡器 → 位置，供解析引擎 O(1) 命中。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Area, Epc, Line, Procedure, Project, TagNo, Trolley
from .normalize import norm_epc

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None


@dataclass
class Hit:
    tag_no: str
    line_code: str
    line_name: str
    proc_code: str
    proc_name: str
    area_code: str
    area_name: str


@dataclass
class TrolleyHit:
    id: int
    tz: str
    name: str
    reader: str


_LOCAL: dict[int, dict] = {}


def _redis():
    from ..config import get_settings

    if redis is None:
        return None
    try:
        r = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        r.ping()
        return r
    except Exception:
        return None


def rebuild_project_cache(db: Session, project_id: int) -> dict:
    p = db.execute(
        select(Project)
        .options(
            selectinload(Project.epcs).selectinload(Epc.tag_no_row).selectinload(TagNo.area).selectinload(Area.procedure).selectinload(Procedure.line),
            selectinload(Project.trolleys),
        )
        .where(Project.id == project_id)
    ).scalar_one()
    epc_map: dict[str, dict] = {}
    for e in p.epcs:
        tag = e.tag_no_row
        area = tag.area
        proc = area.procedure
        line = proc.line
        epc_map[e.epc_norm] = {
            "tag_no": tag.no,
            "line_code": line.code,
            "line_name": line.name,
            "proc_code": proc.code,
            "proc_name": proc.name,
            "area_code": area.code,
            "area_name": area.name,
        }
    reader_map = {
        t.reader: {"id": t.id, "tz": t.tz, "name": t.name, "reader": t.reader} for t in p.trolleys
    }
    blob = {"pid": p.pid, "epc": epc_map, "reader": reader_map}
    _LOCAL[project_id] = blob
    r = _redis()
    if r:
        r.set(f"map:{p.pid}", json.dumps(blob, ensure_ascii=False))
        r.set(f"ingest:{p.ingest_token}", json.dumps({"id": p.id, "pid": p.pid}))
    return blob


def get_cache(db: Session, project: Project) -> dict:
    blob = _LOCAL.get(project.id)
    if blob:
        return blob
    r = _redis()
    if r:
        raw = r.get(f"map:{project.pid}")
        if raw:
            blob = json.loads(raw)
            _LOCAL[project.id] = blob
            return blob
    return rebuild_project_cache(db, project.id)


def resolve_token(db: Session, token: str) -> Project | None:
    r = _redis()
    if r:
        raw = r.get(f"ingest:{token}")
        if raw:
            info = json.loads(raw)
            return db.get(Project, info["id"])
    return db.execute(select(Project).where(Project.ingest_token == token)).scalar_one_or_none()


def lookup_epc(cache: dict, epc: str) -> Hit | None:
    item = cache.get("epc", {}).get(norm_epc(epc))
    if not item:
        return None
    return Hit(**item)


def lookup_reader(cache: dict, device_id: str) -> TrolleyHit | None:
    item = cache.get("reader", {}).get(device_id)
    if not item:
        return None
    return TrolleyHit(**item)
