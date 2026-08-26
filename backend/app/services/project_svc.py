from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Area, Epc, LatestLocation, Line, Procedure, Project, SysLog, TagNo, Trolley
from ..schemas import (
    ALL_FIELDS,
    DEFAULT_FIELDS,
    DEFAULT_TPL,
    AreaOut,
    CfgOut,
    LineOut,
    ProcOut,
    ProjectListItem,
    ProjectOut,
    TemplateOut,
    TrolleyOut,
)
from .normalize import norm_epc, pretty_epc


def write_log(db: Session, project_id: int | None, level: str, message: str) -> None:
    db.add(SysLog(project_id=project_id, level=level, message=message))


def new_ingest_token() -> str:
    return secrets.token_urlsafe(24)


def _mask_token(token: str) -> str:
    if not token or len(token) < 8:
        return "••••"
    return token[:4] + "••••" + token[-4:]


def load_project(db: Session, pid: str) -> Project | None:
    return (
        db.execute(
            select(Project)
            .options(
                selectinload(Project.lines)
                .selectinload(Line.procedures)
                .selectinload(Procedure.areas)
                .selectinload(Area.tag_nos)
                .selectinload(TagNo.epcs),
                selectinload(Project.trolleys),
                selectinload(Project.epcs),
            )
            .where(Project.pid == pid)
        )
        .scalar_one_or_none()
    )


def project_to_tree(p: Project) -> tuple[list[LineOut], dict[str, list[str]]]:
    tag_nos: dict[str, list[str]] = {}
    lines: list[LineOut] = []
    for line in sorted(p.lines, key=lambda x: x.sort_order):
        procs = []
        for proc in sorted(line.procedures, key=lambda x: x.sort_order):
            areas = []
            for area in sorted(proc.areas, key=lambda x: x.sort_order):
                nos = []
                epc_display = []
                for tag in sorted(area.tag_nos, key=lambda x: x.sort_order):
                    nos.append(tag.no)
                    vals = [e.epc_raw or pretty_epc(e.epc_norm) for e in tag.epcs]
                    tag_nos[tag.no] = vals
                    epc_display.extend(vals)
                areas.append(AreaOut(id=area.code, name=area.name, nos=nos, epcs=epc_display))
            procs.append(ProcOut(code=proc.code, name=proc.name, order=proc.sort_order, areas=areas))
        lines.append(LineOut(id=line.code, name=line.name, procs=procs))
    return lines, tag_nos


def count_online(db: Session, project_id: int, offline_hours: float) -> int:
    minutes = max(offline_hours, 0) * 60
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(minutes=minutes or 30)
    n = db.scalar(
        select(func.count(LatestLocation.id)).where(
            LatestLocation.project_id == project_id,
            LatestLocation.identify_time >= since,
        )
    )
    return int(n or 0)


def is_ready(p: Project) -> bool:
    if not p.pid or not p.name or not p.folder:
        return False
    if not p.trolleys:
        return False
    has_area = any(a for line in p.lines for proc in line.procedures for a in proc.areas)
    has_epc = bool(p.epcs)
    return has_area and has_epc


def last_push_text(db: Session, project_id: int) -> str:
    loc = db.scalar(
        select(LatestLocation)
        .where(LatestLocation.project_id == project_id)
        .order_by(LatestLocation.updated_at.desc())
        .limit(1)
    )
    if not loc or not loc.updated_at:
        return "—"
    return loc.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")


def to_out(db: Session, p: Project, ingest_once: str | None = None) -> ProjectOut:
    lines, tag_nos = project_to_tree(p)
    cfg = CfgOut(
        pid=p.pid,
        name=p.name,
        folder=p.folder,
        backup=p.backup,
        scan=p.scan,
        stable=p.stable,
        offline=p.offline,
        batch=p.batch,
        resendMax=p.resend_max,
        retry=p.retry,
        logClean=p.log_clean,
        appId=p.app_id,
        appSecret="••••••••" if p.app_secret else "",
        tokenUrl=p.token_url,
        pushUrl=p.push_url,
        ingestTokenMasked=_mask_token(p.ingest_token),
    )
    return ProjectOut(
        pid=p.pid,
        name=p.name,
        cfg=cfg,
        lines=lines,
        trolleys=[TrolleyOut(tz=t.tz, name=t.name, reader=t.reader) for t in p.trolleys],
        tagNos=tag_nos,
        tpl=TemplateOut(fields=p.tpl_fields or DEFAULT_FIELDS, json=p.tpl_json or DEFAULT_TPL, custom=p.tpl_custom),
        push=p.push_enabled,
        monitor=p.monitor_enabled,
        lastPush=last_push_text(db, p.id),
        online=count_online(db, p.id, p.offline),
        ready=is_ready(p),
        ingestTokenOnce=ingest_once,
    )


def to_list_item(db: Session, p: Project) -> ProjectListItem:
    areas = sum(len(proc.areas) for line in p.lines for proc in line.procedures)
    return ProjectListItem(
        pid=p.pid,
        name=p.name,
        lines=len(p.lines),
        areas=areas,
        trolleys=len(p.trolleys),
        online=count_online(db, p.id, p.offline),
        lastPush=last_push_text(db, p.id),
        push=p.push_enabled,
        ready=is_ready(p),
    )


def replace_mapping(db: Session, p: Project, lines: list[dict], tag_nos: dict[str, list[str]]) -> None:
    db.query(Epc).filter(Epc.project_id == p.id).delete()
    for line in list(p.lines):
        db.delete(line)
    db.flush()
    for i, line in enumerate(lines):
        ln = Line(code=line["id"], name=line["name"], sort_order=i + 1)
        p.lines.append(ln)
        db.flush()
        for j, proc in enumerate(line.get("procs") or []):
            pr = Procedure(code=proc["code"], name=proc["name"], sort_order=j + 1)
            ln.procedures.append(pr)
            db.flush()
            for k, area in enumerate(proc.get("areas") or []):
                ar = Area(code=area["id"], name=area["name"], sort_order=k + 1)
                pr.areas.append(ar)
                db.flush()
                for n, no in enumerate(area.get("nos") or []):
                    if not no:
                        continue
                    tag = TagNo(no=no, sort_order=n + 1)
                    ar.tag_nos.append(tag)
                    db.flush()
                    for epc in tag_nos.get(no, area.get("epcs") or []):
                        key = norm_epc(epc)
                        if not key:
                            continue
                        tag.epcs.append(
                            Epc(
                                project=p,
                                epc_raw=pretty_epc(epc),
                                epc_norm=key,
                            )
                        )
    db.expire(p)


def replace_trolleys(db: Session, p: Project, trolleys: list[dict]) -> None:
    for t in list(p.trolleys):
        db.delete(t)
    db.flush()
    for t in trolleys:
        p.trolleys.append(Trolley(tz=t["tz"], name=t["name"], reader=t["reader"]))
    db.flush()
    db.expire(p)


def apply_save(db: Session, p: Project, data) -> None:
    p.pid = data.pid.strip()
    p.name = data.name.strip()
    p.folder = data.folder
    p.backup = data.backup
    p.scan = data.scan
    p.stable = data.stable
    p.offline = data.offline
    p.batch = data.batch
    p.resend_max = data.resendMax
    p.retry = data.retry
    p.log_clean = data.logClean
    p.app_id = data.appId
    if data.appSecret and not data.appSecret.startswith("•"):
        p.app_secret = data.appSecret
    p.token_url = data.tokenUrl
    p.push_url = data.pushUrl
    if data.tpl:
        p.tpl_fields = [f for f in data.tpl.fields if f in ALL_FIELDS] or DEFAULT_FIELDS
        p.tpl_json = data.tpl.json or DEFAULT_TPL
        p.tpl_custom = data.tpl.custom
    if data.push is not None:
        p.push_enabled = data.push
    lines = [ln.model_dump() for ln in data.lines]
    tag_nos = data.tagNos or {}
    if not tag_nos:
        for ln in lines:
            for proc in ln.get("procs") or []:
                for area in proc.get("areas") or []:
                    for no, epc_list in zip(area.get("nos") or [], [area.get("epcs") or []]):
                        if no:
                            tag_nos.setdefault(no, epc_list if isinstance(epc_list, list) else [])
        # also flatten area.epcs onto first no
        for ln in lines:
            for proc in ln.get("procs") or []:
                for area in proc.get("areas") or []:
                    for no in area.get("nos") or []:
                        if no and no not in tag_nos:
                            tag_nos[no] = area.get("epcs") or []
    replace_mapping(db, p, lines, tag_nos)
    replace_trolleys(db, p, [t.model_dump() for t in data.trolleys])


def current_stats(p: Project) -> dict[str, int]:
    procs = sum(len(l.procedures) for l in p.lines)
    areas = sum(len(pr.areas) for l in p.lines for pr in l.procedures)
    nos = sum(len(a.tag_nos) for l in p.lines for pr in l.procedures for a in pr.areas)
    return {
        "line": len(p.lines),
        "proc": procs,
        "area": areas,
        "no": nos,
        "epc": len(p.epcs),
        "trolley": len(p.trolleys),
    }


def diff_stats(cur: dict[str, int], nxt: dict[str, int], keys: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for label, key in keys:
        a, b = cur.get(key, 0), nxt.get(key, 0)
        rows.append(
            {
                "object": label,
                "current": a,
                "incoming": b,
                "added": max(0, b - a),
                "changed": min(a, b),
                "removed": max(0, a - b),
            }
        )
    return rows
