from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Epc, LatestLocation, Project, RawReport, Trolley
from ..services.cache import get_cache, lookup_epc, lookup_reader
from ..services.normalize import norm_epc, pretty_epc
from ..services.project_svc import write_log
from .customer import movement_status
from .hooks import ParseContext, after_location_updated, after_parse


def _epc_from_bb_frame(frame: str) -> str:
    """从一帧 BB..7E 里按 PC 字长取出 EPC。RSSI(1) + PC(2) + EPC + CRC(2)。"""
    if len(frame) < 24:
        return ""
    try:
        pc = int(frame[12:16], 16)
    except ValueError:
        return ""
    words = (pc >> 11) & 0x1F
    nbytes = words * 2
    if nbytes < 4 or nbytes > 32:
        nbytes = 12
    start = 16
    end = start + nbytes * 2
    if end + 8 > len(frame):
        return ""
    epc = frame[start:end]
    if any(c not in "0123456789ABCDEF" for c in epc):
        return ""
    return epc


def _epcs_from_bb_frames(compact: str) -> list[str]:
    """现场报文常把多帧 BB0222..7E 直接拼在同一个 hex 里，每帧一个标签。"""
    found: list[str] = []
    i = 0
    n = len(compact)
    while i + 12 <= n:
        if compact[i : i + 2] != "BB":
            i += 2
            continue
        try:
            payload_len = int(compact[i + 6 : i + 10], 16)
        except ValueError:
            i += 2
            continue
        if payload_len < 5 or payload_len > 80:
            i += 2
            continue
        hex_len = (5 + payload_len + 2) * 2
        if i + hex_len > n or compact[i + hex_len - 2 : i + hex_len] != "7E":
            i += 2
            continue
        epc = _epc_from_bb_frame(compact[i : i + hex_len])
        if epc and epc not in found:
            found.append(epc)
        i += hex_len
    return found


def _epcs_from_e2(compact: str) -> list[str]:
    found: list[str] = []
    start = 0
    while True:
        i = compact.find("E2", start)
        if i < 0 or len(compact) < i + 24:
            break
        chunk = compact[i : i + 24]
        if chunk not in found:
            found.append(chunk)
        start = i + 24
    return found


def extract_epcs(hex_data: str, epcs: list[str]) -> list[str]:
    vals = [norm_epc(x) for x in epcs if norm_epc(x)]
    if vals:
        return vals
    compact = norm_epc(hex_data)
    if not compact:
        return []
    framed = _epcs_from_bb_frames(compact)
    if framed:
        return framed
    found = _epcs_from_e2(compact)
    if found:
        return found
    # 常见 EPC 96bit = 24 hex chars；按 24 切分，剩余整段也保留
    if len(compact) >= 24 and len(compact) % 24 == 0:
        return [compact[i : i + 24] for i in range(0, len(compact), 24)]
    return [compact]


def should_write_raw(db: Session, project: Project, device_id: str, hex_data: str, when: datetime) -> bool:
    """同一台车（读卡器）窗口内重复相同 MQTT Hex 不写入；换 Hex 则重新计时。"""
    minutes = max(int(getattr(project, "hex_agg_min", 5) or 0), 0)
    current = hex_data or ""
    if minutes <= 0 or not current.strip():
        return True
    last = db.scalar(
        select(RawReport)
        .where(RawReport.project_id == project.id, RawReport.device_id == device_id)
        .order_by(RawReport.ts.desc(), RawReport.id.desc())
        .limit(1)
    )
    if not last:
        return True
    if (last.hex_data or "") != current:
        return True
    last_ts = last.ts
    if last_ts is None:
        return True
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    return (when - last_ts).total_seconds() > minutes * 60


def touch_report_meta(
    db: Session,
    project: Project,
    trolley_id: int | None,
    parsed_epcs: list[str],
    hex_data: str,
    when: datetime,
) -> None:
    if trolley_id:
        row = db.get(Trolley, trolley_id)
        if row:
            row.last_report_time = when
    norms = [norm_epc(e) for e in parsed_epcs if norm_epc(e)]
    if not norms:
        return
    for epc_row in db.scalars(select(Epc).where(Epc.project_id == project.id, Epc.epc_norm.in_(norms))).all():
        epc_row.last_read_time = when
        epc_row.last_hex = hex_data or ""


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
    parsed_epcs = extract_epcs(hex_data, epcs)
    hex_store = hex_data or " ".join(pretty_epc(x) for x in parsed_epcs)
    if should_write_raw(db, project, device_id, hex_store, when):
        db.add(
            RawReport(
                project_id=project.id,
                device_id=device_id,
                hex_data=hex_store,
                epcs=parsed_epcs,
                report_day=when.astimezone().strftime("%Y-%m-%d"),
                test=test,
                ts=when,
            )
        )
    touch_report_meta(db, project, getattr(trolley, "id", None), parsed_epcs, hex_store, when)

    if not trolley:
        return {"ok": False, "reason": "unknown_device", "deviceId": device_id}

    hit = None
    used = ""
    for e in parsed_epcs:
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
