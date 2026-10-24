from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LatestLocation, Project, RawReport
from ..services.cache import get_cache, lookup_epc, lookup_reader
from ..services.normalize import norm_epc, pretty_epc
from ..services.project_svc import write_log
from .customer import movement_status
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
    from .runtime import (
        create_push,
        find_line,
        find_proc_named,
        mark_real_proc_visit,
        proc_index,
        reissue_missing_procs,
        seq_guard_blocks,
        update_seq_guard,
    )

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

    loc = db.scalar(
        select(LatestLocation).where(
            LatestLocation.project_id == project.id,
            LatestLocation.trolley_id == trolley.id,
        )
    )
    blocked, reason = seq_guard_blocks(
        project,
        loc,
        hit.line_code,
        hit.proc_code,
        hit.proc_name,
    )
    if blocked:
        # 本产线历史工序的真实识别：不跳转，但记真实访问并清零该工序连续补发
        same_line = bool(loc and (hit.line_code or "") == (loc.line_code or ""))
        hist_line = find_line(project, loc.line_code) if loc and same_line else None
        hist_proc = (
            find_proc_named(hist_line, hit.proc_code, hit.proc_name) if hist_line else None
        )
        cur_idx = proc_index(hist_line, loc.proc_code, loc.proc_name) if loc and hist_line else -1
        tgt_idx = proc_index(hist_line, hit.proc_code, hit.proc_name) if hist_line else -1
        if same_line and hist_proc is not None and cur_idx >= 0 and 0 <= tgt_idx < cur_idx:
            mark_real_proc_visit(loc, hist_proc)
            snap = SimpleNamespace(
                line_code=hit.line_code,
                line_name=hit.line_name,
                proc_code=hit.proc_code,
                proc_name=hit.proc_name,
                area_code=hit.area_code,
                area_name=hit.area_name,
            )
            cn = movement_status(when)
            create_push(
                db,
                project,
                trolley,
                snap,
                "实时上报",
                "auto",
                test,
                "moving" if cn == "运动" else "still",
                when,
            )
            write_log(
                db,
                project.id,
                "信息",
                f"台车 {trolley.tz} 历史工序 {hit.proc_name} 收到真实数据：已清零连续补发，位置保持在 {loc.proc_name}",
            )
            db.flush()
            return {
                "ok": True,
                "filled": True,
                "reason": "seq_guard_real_fill",
                "tz": trolley.tz,
                "line": loc.line_code,
                "proc": loc.proc_code,
                "area": loc.area_code,
            }
        write_log(db, project.id, "信息", f"台车 {trolley.tz} {reason}，保持当前位置不变")
        return {
            "ok": True,
            "filtered": True,
            "reason": "seq_guard",
            "detail": reason,
            "tz": trolley.tz,
            "line": loc.line_code if loc else "",
            "proc": loc.proc_code if loc else "",
            "area": loc.area_code if loc else "",
        }

    cn = movement_status(when)
    state = "moving" if cn == "运动" else "still"

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

    if not loc:
        loc = LatestLocation(project_id=project.id, trolley_id=trolley.id)
        db.add(loc)
        db.flush()

    line = find_line(project, ctx.line_code)
    proc = find_proc_named(line, ctx.proc_code, hit.proc_name) if line else None
    tgt_idx = proc_index(line, ctx.proc_code, hit.proc_name) if line else -1
    cur_idx = proc_index(line, loc.proc_code, loc.proc_name) if line and loc.proc_code else -1
    advancing = tgt_idx >= 0 and (cur_idx < 0 or tgt_idx > cur_idx)

    # 进入第 2 道及以后的新工序前，对缺失前置工序自动补发
    if advancing and tgt_idx >= 1 and line:
        reissue_missing_procs(db, project, trolley, loc, line, tgt_idx, when, test=test)

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
    mark_real_proc_visit(loc, proc)
    update_seq_guard(loc, line, proc)
    db.flush()
    after_location_updated(db, project, trolley, loc, ctx)
    return {
        "ok": True,
        "tz": trolley.tz,
        "line": ctx.line_code,
        "proc": ctx.proc_code,
        "area": ctx.area_code,
        "seqGuard": bool(loc.seq_guard),
        "reissue": int(loc.reissue or 0),
    }
