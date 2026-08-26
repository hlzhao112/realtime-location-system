from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LatestLocation, Project, RawReport
from ..services.cache import get_cache, lookup_epc, lookup_reader
from ..services.normalize import norm_epc, pretty_epc
from .hooks import ParseContext, after_location_updated, after_parse


def extract_epcs(hex_data: str, epcs: list[str]) -> list[str]:
    vals = [norm_epc(x) for x in epcs if norm_epc(x)]
    if vals:
        return vals
    compact = norm_epc(hex_data)
    if not compact:
        return []
    # 常见 EPC 96bit = 24 hex chars；按 24 切分，剩余整段也保留
    if len(compact) >= 24 and len(compact) % 24 == 0:
        return [compact[i : i + 24] for i in range(0, len(compact), 24)]
    return [compact]


def apply_report(
    db: Session,
    project: Project,
    device_id: str,
    hex_data: str,
    epcs: list[str],
    ts: datetime | None,
    sport_state: str | None,
    test: bool = False,
) -> dict:
    cache = get_cache(db, project)
    trolley = lookup_reader(cache, device_id)
    when = ts or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    db.add(
        RawReport(
            project_id=project.id,
            device_id=device_id,
            hex_data=hex_data or " ".join(pretty_epc(x) for x in epcs),
            epcs=epcs,
            report_day=when.astimezone().strftime("%Y-%m-%d"),
            test=test,
            ts=when,
        )
    )

    if not trolley:
        return {"ok": False, "reason": "unknown_device", "deviceId": device_id}

    hit = None
    used = ""
    for e in extract_epcs(hex_data, epcs):
        found = lookup_epc(cache, e)
        if found:
            hit = found
            used = e
    if not hit:
        return {"ok": False, "reason": "unknown_epc", "deviceId": device_id, "tz": trolley.tz}

    state = sport_state or "still"
    if state not in {"moving", "still", "运动", "静止"}:
        state = "still"
    if state == "运动":
        state = "moving"
    if state == "静止":
        state = "still"

    ctx = after_parse(
        ParseContext(
            project_id=project.id,
            pid=project.pid,
            device_id=device_id,
            trolley_tz=trolley.tz,
            line_code=hit.line_code,
            proc_code=hit.proc_code,
            area_code=hit.area_code,
            identify_time=when,
            sport_state=state,
            source="auto",
            kind="实时上报",
            test=test,
        )
    )

    loc = db.scalar(
        select(LatestLocation).where(
            LatestLocation.project_id == project.id,
            LatestLocation.trolley_id == trolley.id,
        )
    )
    if not loc:
        loc = LatestLocation(project_id=project.id, trolley_id=trolley.id)
        db.add(loc)
        db.flush()
    loc.line_code = ctx.line_code
    loc.line_name = hit.line_name
    loc.proc_code = ctx.proc_code
    loc.proc_name = hit.proc_name
    loc.area_code = ctx.area_code
    loc.area_name = hit.area_name
    loc.tag_no = hit.tag_no
    loc.epc = pretty_epc(used)
    loc.sport_state = ctx.sport_state
    loc.source = ctx.source
    loc.kind = ctx.kind
    loc.test = ctx.test
    loc.unassigned = False
    loc.identify_time = ctx.identify_time
    db.flush()
    after_location_updated(db, project, trolley, loc, ctx)
    return {
        "ok": True,
        "tz": trolley.tz,
        "line": ctx.line_code,
        "proc": ctx.proc_code,
        "area": ctx.area_code,
    }
