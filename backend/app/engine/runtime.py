"""主看板、推送记录、测试模式、保持推送、原始数据与日志。"""

from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import LatestLocation, Line, Project, PushRecord, RawReport, SysLog, Trolley
from ..schemas import DEFAULT_FIELDS
from ..services.normalize import norm_epc, pretty_epc
from ..services.project_svc import load_project, write_log

LOCK = threading.Lock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_local(dt: Optional[datetime], ms: bool = False) -> str:
    dt = aware(dt)
    if not dt:
        return "—"
    local = dt.astimezone()
    base = local.strftime("%Y-%m-%d %H:%M:%S")
    if ms:
        return f"{base}.{local.microsecond // 1000:03d}"
    return base


def sport_cn(state: str) -> str:
    return "运动" if state in {"moving", "运动", "1"} else "静止"


def sport_db(state: str) -> str:
    return "moving" if state in {"moving", "运动", "1"} else "still"


def sport_code(state: str) -> str:
    return "1" if state in {"moving", "运动", "1"} else "0"


def src_cn(source: str) -> str:
    return "手动" if source == "manual" else "自动"


def adv_sec(p: Project) -> int:
    return 6 if p.test_fast else 120


def keep_sec(p: Project) -> int:
    if p.test_on:
        return 3 if p.test_fast else 60
    return 600


def adv_label(p: Project) -> str:
    return "6 秒（演示加速，对应现场 2 分钟）" if p.test_fast else "2 分钟"


def keep_label(p: Project) -> str:
    if p.test_on:
        return "3 秒（演示加速，对应现场 1 分钟）" if p.test_fast else "1 分钟"
    return "10 分钟"


def offline_minutes(p: Project) -> float:
    return max(float(p.offline or 0.5), 0) * 60 or 30


def stay_minutes(loc: Optional[LatestLocation], now: Optional[datetime] = None) -> int:
    if not loc or not loc.identify_time:
        return 999
    now = now or now_utc()
    delta = now - aware(loc.identify_time)
    return max(0, int(delta.total_seconds() // 60))


def is_online(loc: Optional[LatestLocation], p: Project, now: Optional[datetime] = None) -> bool:
    if not loc or not loc.identify_time:
        return False
    now = now or now_utc()
    return (now - aware(loc.identify_time)).total_seconds() <= offline_minutes(p) * 60


def find_line(project: Project, code: str) -> Optional[Line]:
    for line in project.lines:
        if line.code == code:
            return line
    return None


def find_proc(line: Optional[Line], code: str):
    if not line:
        return None
    for proc in line.procedures:
        if proc.code == code:
            return proc
    return None


def find_area(proc, code: str):
    if not proc:
        return None
    for area in proc.areas:
        if area.code == code:
            return area
    return None


def loc_of(db: Session, project: Project, trolley: Trolley) -> LatestLocation:
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
    return loc


def last_push(db: Session, project_id: int, tz: str) -> Optional[PushRecord]:
    return db.scalar(
        select(PushRecord)
        .where(PushRecord.project_id == project_id, PushRecord.trolley_tz == tz)
        .order_by(PushRecord.id.desc())
        .limit(1)
    )


def area_epc(area) -> str:
    for tag in area.tag_nos:
        for epc in tag.epcs:
            if epc.epc_norm or epc.epc_raw:
                return epc.epc_raw or pretty_epc(epc.epc_norm)
    return ""


def build_payload(project: Project, trolley: Trolley, loc_like, identify_time: datetime, sport_state: str) -> dict:
    mapping = {
        "areaCode": getattr(loc_like, "area_code", "") or "",
        "beamAssetsCode": trolley.tz,
        "beamLineCode": getattr(loc_like, "line_code", "") or "",
        "identifyTime": fmt_local(identify_time),
        "procedureCode": getattr(loc_like, "proc_code", "") or "",
        "sportState": sport_code(sport_state),
        "deviceCode": trolley.reader or "",
        "projectCode": project.pid,
    }
    fields = project.tpl_fields or DEFAULT_FIELDS
    return {k: mapping.get(k, "") for k in fields}


def deliver(db: Session, project: Project, rec: PushRecord, force: bool = False) -> bool:
    auto = rec.source != "manual"
    if rec.test:
        rec.status = "success"
        write_log(
            db,
            project.id,
            "信息",
            f"台车 {rec.trolley_tz} 测试记录已生成本地（来源:auto·{rec.kind}·测试，默认不外发客户接口）",
        )
        return True
    if auto and not project.push_enabled and not force:
        rec.status = "pending"
        write_log(
            db,
            project.id,
            "信息",
            f"台车 {rec.trolley_tz} 识别到 {rec.proc_name}·{rec.area_code}，自动推送已停止，仅生成记录待补推（来源:{rec.kind}）",
        )
        return False
    if not project.push_url:
        rec.status = "success"
        write_log(db, project.id, "成功", f"台车 {rec.trolley_tz} 推送成功（本地记成功，未配置客户推送 URL，来源:{rec.kind}）")
        return True
    retries = max(int(project.retry or 0), 0)
    last_err = ""
    for i in range(retries + 1):
        try:
            httpx.post(project.push_url, json=rec.payload, timeout=8.0).raise_for_status()
            rec.status = "success"
            write_log(db, project.id, "成功", f"台车 {rec.trolley_tz} 推送成功（来源:{rec.kind}）")
            return True
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
    rec.status = "failed"
    write_log(
        db,
        project.id,
        "失败",
        f"台车 {rec.trolley_tz} 推送失败：{last_err or '接口超时'}，已重试 {retries}/{retries or 1}",
    )
    return False


def create_push(
    db: Session,
    project: Project,
    trolley: Trolley,
    loc,
    kind: str,
    source: str,
    test: bool,
    sport_state: str,
    identify_time: datetime,
    ref_id: Optional[int] = None,
    force: bool = False,
) -> PushRecord:
    rec = PushRecord(
        project_id=project.id,
        trolley_tz=trolley.tz,
        seat_name=trolley.name,
        line_code=loc.line_code,
        proc_code=loc.proc_code,
        proc_name=loc.proc_name,
        area_code=loc.area_code,
        area_name=getattr(loc, "area_name", "") or "",
        sport_state=sport_db(sport_state),
        source=source,
        kind=kind,
        test=test,
        ref_id=ref_id,
        payload=build_payload(project, trolley, loc, identify_time, sport_state),
        status="pending",
        identify_time=identify_time,
    )
    db.add(rec)
    db.flush()
    if test:
        project.test_made = int(project.test_made or 0) + 1
    deliver(db, project, rec, force=force or source == "manual")
    return rec


def occupy_area(db: Session, project: Project, trolley: Trolley, loc: LatestLocation) -> None:
    if loc.unassigned or not loc.area_code or not loc.line_code:
        return
    others = (
        db.execute(
            select(LatestLocation, Trolley)
            .join(Trolley, Trolley.id == LatestLocation.trolley_id)
            .where(
                LatestLocation.project_id == project.id,
                LatestLocation.unassigned.is_(False),
                LatestLocation.line_code == loc.line_code,
                LatestLocation.proc_code == loc.proc_code,
                LatestLocation.area_code == loc.area_code,
                LatestLocation.trolley_id != trolley.id,
            )
        )
        .all()
    )
    line = find_line(project, loc.line_code)
    procs = list(line.procedures) if line else []
    codes = [x.code for x in procs]
    nxt = ""
    if loc.proc_code in codes:
        nxt = codes[(codes.index(loc.proc_code) + 1) % len(codes)]
    elif codes:
        nxt = codes[0]
    for other, ot in others:
        other.unassigned = True
        other.retry_proc = nxt
        other.skip_tick = int(project.test_tick or 0)
        write_log(
            db,
            project.id,
            "信息",
            f"区域独占冲突：{trolley.tz} 进入 {loc.proc_name}·{loc.area_code}，原台车 {ot.tz} 被挤出至「未分配」",
        )


def apply_place(
    db: Session,
    project: Project,
    trolley: Trolley,
    line,
    proc,
    area,
    source: str,
    kind: str,
    test: bool,
    sport_state: str,
    identify_time: Optional[datetime] = None,
    write_raw: bool = False,
    hex_data: str = "",
    epc: str = "",
) -> LatestLocation:
    when = identify_time or now_utc()
    loc = loc_of(db, project, trolley)
    loc.line_code = line.code
    loc.line_name = line.name
    loc.proc_code = proc.code
    loc.proc_name = proc.name
    loc.area_code = area.code if area else ""
    loc.area_name = area.name if area else ""
    loc.source = source
    loc.kind = kind
    loc.test = test
    loc.unassigned = False
    loc.retry_proc = ""
    loc.sport_state = sport_db(sport_state)
    loc.identify_time = when
    if epc:
        loc.epc = pretty_epc(epc)
    db.flush()
    occupy_area(db, project, trolley, loc)
    if write_raw:
        db.add(
            RawReport(
                project_id=project.id,
                device_id=trolley.reader,
                hex_data=hex_data or pretty_epc(epc),
                epcs=[epc] if epc else [],
                report_day=when.astimezone().strftime("%Y-%m-%d"),
                test=test,
                ts=when,
            )
        )
    create_push(db, project, trolley, loc, kind, source, test, sport_state, when)
    return loc


def on_location_updated(db: Session, project: Project, trolley: Trolley, loc: LatestLocation, test: bool = False) -> None:
    loc.unassigned = False
    loc.kind = loc.kind or "实时上报"
    loc.source = loc.source or "auto"
    loc.test = test
    occupy_area(db, project, trolley, loc)
    create_push(
        db,
        project,
        trolley,
        loc,
        loc.kind or "实时上报",
        loc.source or "auto",
        test,
        loc.sport_state,
        loc.identify_time or now_utc(),
    )


def assigned_on(db: Session, project: Project, line_code: str, proc_code: str, area_code: str, except_tz: str):
    rows = db.execute(
        select(LatestLocation, Trolley)
        .join(Trolley, Trolley.id == LatestLocation.trolley_id)
        .where(
            LatestLocation.project_id == project.id,
            LatestLocation.unassigned.is_(False),
            LatestLocation.line_code == line_code,
            LatestLocation.proc_code == proc_code,
            LatestLocation.area_code == area_code,
            Trolley.tz != except_tz,
        )
    ).all()
    return rows


def pick_area(db: Session, project: Project, line, proc, except_tz: str):
    """返回 (area, evict_loc_trolley_or_None)。无空闲时挤出停留最久者（与 demo 一致）。"""
    if not proc or not proc.areas:
        return None, None
    free = []
    occupied = []
    for area in proc.areas:
        rows = assigned_on(db, project, line.code, proc.code, area.code, except_tz)
        if not rows:
            free.append(area)
        else:
            loc, t = rows[0]
            occupied.append((area, loc, t, stay_minutes(loc)))
    if free:
        return random.choice(free), None
    if not occupied:
        return None, None
    occupied.sort(key=lambda x: -x[3])
    area, loc, t, _ = occupied[0]
    return area, (loc, t)


def mark_unassigned(loc: LatestLocation, line, retry_proc: str, skip_tick: int) -> None:
    loc.unassigned = True
    loc.area_code = ""
    loc.area_name = ""
    loc.line_code = line.code if line else loc.line_code
    loc.line_name = line.name if line else loc.line_name
    loc.retry_proc = retry_proc
    loc.skip_tick = skip_tick
    loc.source = "auto"
    loc.kind = "实时上报"


def start_test(db: Session, project: Project) -> dict:
    if not project.lines or not project.trolleys:
        raise ValueError("请先完成映射表配置，再启动测试模式")
    n_lines = len(project.lines)
    det = []
    trolleys = list(project.trolleys)
    for t in trolleys:
        loc = loc_of(db, project, t)
        loc.unassigned = True
        loc.area_code = ""
        loc.proc_code = ""
        loc.skip_tick = -1
        loc.test = True
        loc.retry_proc = ""
    db.flush()
    for i, t in enumerate(trolleys):
        line = project.lines[i % n_lines]
        loc = loc_of(db, project, t)
        loc.line_code = line.code
        loc.line_name = line.name
        loc.test = True
        first = line.procedures[0] if line.procedures else None
        area, evict = pick_area(db, project, line, first, t.tz) if first else (None, None)
        if first and area and not evict:
            loc.unassigned = False
            loc.proc_code = first.code
            loc.proc_name = first.name
            loc.area_code = area.code
            loc.area_name = area.name
            loc.source = "auto"
            loc.kind = "实时上报"
            loc.identify_time = now_utc()
            det.append(f"{t.tz} → {line.name}·{first.name}·{area.code}")
        else:
            loc.unassigned = True
            loc.retry_proc = first.code if first else ""
            loc.proc_code = first.code if first else ""
            loc.proc_name = first.name if first else ""
            det.append(f"{t.tz} → {line.name}·未分配（首工序区域已占满）")
        db.flush()
    project.test_on = True
    project.test_tick = 0
    now = now_utc()
    project.test_next_adv = now + timedelta(seconds=adv_sec(project))
    project.test_next_keep = now + timedelta(seconds=keep_sec(project))
    write_log(
        db,
        project.id,
        "信息",
        f"测试模式启动：为 {len(trolleys)} 台车分配初始位置（按台车顺序平均分配到各产线）—— " + "；".join(det),
    )
    return {"ok": True, "detail": det}


def stop_test(db: Session, project: Project) -> dict:
    project.test_on = False
    project.test_next_adv = None
    project.test_next_keep = None
    project.keep_next = now_utc() + timedelta(seconds=keep_sec(project))
    write_log(
        db,
        project.id,
        "信息",
        f"测试模式停止：本次共生成 {project.test_made or 0} 条测试推送记录，数据保留可继续核对",
    )
    return {"ok": True, "made": project.test_made or 0}


def clear_test_data(db: Session, project: Project) -> dict:
    if project.test_on:
        stop_test(db, project)
    n_push = db.query(PushRecord).filter(PushRecord.project_id == project.id, PushRecord.test.is_(True)).delete()
    n_raw = db.query(RawReport).filter(RawReport.project_id == project.id, RawReport.test.is_(True)).delete()
    for loc in db.scalars(select(LatestLocation).where(LatestLocation.project_id == project.id)):
        loc.test = False
    project.test_made = 0
    project.test_tick = 0
    write_log(db, project.id, "信息", f"清除测试数据：按「测试」标识删除推送记录 {n_push} 条、原始数据 {n_raw} 条")
    return {"ok": True, "pushes": n_push, "raw": n_raw}


def set_test_speed(db: Session, project: Project, fast: bool) -> None:
    project.test_fast = fast
    now = now_utc()
    if project.test_on:
        project.test_next_adv = now + timedelta(seconds=adv_sec(project))
        project.test_next_keep = now + timedelta(seconds=keep_sec(project))
    write_log(db, project.id, "信息", f"测试模式节奏：推进 {adv_label(project)} / 保持推送 {keep_label(project)}")


def _simulate_hex(tick: int, epc: str) -> str:
    compact = norm_epc(epc)
    return f"3400{tick % 100:02d}{compact}8A2F00"


def test_tick(db: Session, project: Project) -> None:
    project.test_tick = int(project.test_tick or 0) + 1
    tick = project.test_tick
    trolleys = list(project.trolleys)
    for t in trolleys:
        loc = loc_of(db, project, t)
        if loc.skip_tick == tick:
            continue
        line = find_line(project, loc.line_code) or (project.lines[0] if project.lines else None)
        if not line or not line.procedures:
            continue
        procs = list(line.procedures)
        codes = [p.code for p in procs]
        if loc.unassigned:
            target_code = loc.retry_proc or (codes[0] if codes else "")
        else:
            if loc.proc_code in codes:
                target_code = codes[(codes.index(loc.proc_code) + 1) % len(codes)]
            else:
                target_code = codes[0]
        proc = find_proc(line, target_code)
        if not proc:
            continue
        area, evict = pick_area(db, project, line, proc, t.tz)
        if not area:
            continue
        if evict:
            eloc, et = evict
            nxt = codes[(codes.index(target_code) + 1) % len(codes)] if target_code in codes else (codes[0] if codes else "")
            mark_unassigned(eloc, line, nxt, tick)
            write_log(
                db,
                project.id,
                "信息",
                f"区域独占冲突：{t.tz} 进入 {proc.name}·{area.code}，原台车 {et.tz} 被挤出至「未分配」（下一轮重试进入下一工序）",
            )
            db.flush()
        loop = (not loc.unassigned) and loc.proc_code in codes and target_code == codes[0] and len(codes) > 1
        epc = area_epc(area)
        apply_place(
            db,
            project,
            t,
            line,
            proc,
            area,
            source="auto",
            kind="实时上报",
            test=True,
            sport_state="moving",
            write_raw=True,
            hex_data=_simulate_hex(tick, epc),
            epc=epc,
        )
        if loop:
            write_log(
                db,
                project.id,
                "信息",
                f"台车 {t.tz} 已走完 {line.name} 全部工序，回到首工序 {procs[0].name} 重新开始（不跨产线）",
            )


def keep_tick(db: Session, project: Project, test: bool) -> None:
    n = 0
    now = now_utc()
    for t in project.trolleys:
        loc = loc_of(db, project, t)
        if loc.unassigned or not loc.area_code:
            continue
        last = last_push(db, project.id, t.tz)
        if not last:
            continue
        loc.identify_time = now
        loc.kind = "保持推送"
        loc.source = "auto"
        create_push(
            db,
            project,
            t,
            loc,
            "保持推送",
            "auto",
            bool(test),
            last.sport_state or "still",
            now,
        )
        n += 1
    if n:
        write_log(
            db,
            project.id,
            "成功",
            f"定时保持推送：位置未变，复制上一条记录、仅更新识别时间续推 {n} 条（间隔 {keep_label(project)}）",
        )


def place_trolley(db: Session, project: Project, tz: str, line_code: str, proc_code: str, area_code: str = "") -> dict:
    trolley = next((x for x in project.trolleys if x.tz == tz), None)
    if not trolley:
        raise ValueError("台车不存在")
    line = find_line(project, line_code)
    proc = find_proc(line, proc_code)
    if not line or not proc:
        raise ValueError("产线或工序不存在")
    area = find_area(proc, area_code) if area_code else (proc.areas[0] if proc.areas else None)
    if not area:
        raise ValueError("该工序没有可投放区域")
    apply_place(
        db,
        project,
        trolley,
        line,
        proc,
        area,
        source="manual",
        kind="手动拖拽",
        test=False,
        sport_state="still",
    )
    write_log(
        db,
        project.id,
        "成功",
        f"台车 {trolley.tz} 手动标位 → {line.name}·{proc.name}，已触发推送（来源:manual）",
    )
    return {"ok": True, "tz": trolley.tz, "line": line.code, "proc": proc.code, "area": area.code}


def edit_push(db: Session, project: Project, rec_id: int, proc_code: str, line_code: str, area_code: str, sport_state: str) -> dict:
    rec = db.get(PushRecord, rec_id)
    if not rec or rec.project_id != project.id:
        raise ValueError("推送记录不存在")
    rec.edited = True
    line = find_line(project, line_code) or find_line(project, rec.line_code)
    proc = find_proc(line, proc_code)
    if not line or not proc:
        raise ValueError("产线或工序不存在")
    area = find_area(proc, area_code) if area_code else find_area(proc, rec.area_code)
    if not area and proc.areas:
        area = proc.areas[0]
    trolley = next((x for x in project.trolleys if x.tz == rec.trolley_tz), None)
    if not trolley:
        raise ValueError("台车不存在")
    loc = apply_place(
        db,
        project,
        trolley,
        line,
        proc,
        area,
        source="manual",
        kind="手动编辑",
        test=False,
        sport_state=sport_state,
    )
    # apply_place already created a new push; point ref to original
    newest = last_push(db, project.id, trolley.tz)
    if newest:
        newest.ref_id = rec.id
    write_log(
        db,
        project.id,
        "成功",
        f"推送记录 {rec.id} 人工修改：原记录保留并标记已修改，新增一条手动记录并推送（来源:manual·手动编辑）",
    )
    return {"ok": True, "id": newest.id if newest else None}


def push_one(db: Session, project: Project, rec_id: int) -> dict:
    rec = db.get(PushRecord, rec_id)
    if not rec or rec.project_id != project.id:
        raise ValueError("推送记录不存在")
    ok = deliver(db, project, rec, force=True)
    return {"ok": ok, "status": rec.status}


def batch_push(db: Session, project: Project) -> dict:
    limit = max(int(project.batch or 20), 1)
    rows = (
        db.scalars(
            select(PushRecord)
            .where(PushRecord.project_id == project.id, PushRecord.status == "pending", PushRecord.test.is_(False))
            .order_by(PushRecord.id.asc())
            .limit(limit)
        )
        .all()
    )
    ok = 0
    for rec in rows:
        if deliver(db, project, rec, force=True):
            ok += 1
    write_log(db, project.id, "成功" if ok == len(rows) else "信息", f"批量推送完成，成功 {ok}/{len(rows)} 台，来源:manual")
    return {"ok": True, "success": ok, "total": len(rows)}


def set_flags(db: Session, project: Project, push: Optional[bool] = None, monitor: Optional[bool] = None) -> None:
    if push is not None:
        project.push_enabled = push
        write_log(db, project.id, "信息", "自动推送已开启" if push else "自动推送已停止，期间数据将积压待批量补推")
    if monitor is not None:
        project.monitor_enabled = monitor
        write_log(db, project.id, "信息", "文件夹监测已启动" if monitor else "文件夹监测已停止")


def merge_rows(rows: list[PushRecord]) -> list[PushRecord]:
    seen = set()
    out = []
    for r in rows:
        key = f"{r.trolley_tz}|{r.area_code}|{r.proc_code}"
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def list_pushes(db: Session, project: Project, q: str = "", merge: bool = False) -> list[PushRecord]:
    rows = db.scalars(select(PushRecord).where(PushRecord.project_id == project.id).order_by(PushRecord.id.desc()).limit(800)).all()
    if q:
        key = q.lower()
        rows = [
            r
            for r in rows
            if key
            in " ".join(
                [
                    r.proc_name or "",
                    r.proc_code or "",
                    r.trolley_tz or "",
                    r.seat_name or "",
                    r.line_code or "",
                    r.area_code or "",
                    sport_cn(r.sport_state),
                    src_cn(r.source),
                    r.kind or "",
                ]
            ).lower()
        ]
    if merge:
        rows = merge_rows(rows)
    return rows


def push_out(r: PushRecord) -> dict:
    return {
        "id": r.id,
        "no": r.id,
        "pc": r.proc_code,
        "pn": r.proc_name,
        "tz": r.trolley_tz,
        "seat": r.seat_name,
        "line": r.line_code,
        "area": r.area_code,
        "areaName": r.area_name,
        "state": sport_cn(r.sport_state),
        "time": fmt_local(r.identify_time),
        "src": src_cn(r.source),
        "k": r.kind or ("手动编辑" if r.source == "manual" else "实时上报"),
        "test": bool(r.test),
        "edited": bool(r.edited),
        "status": r.status,
        "payload": r.payload or {},
        "refId": r.ref_id,
    }


def board_payload(db: Session, project: Project) -> dict:
    now = now_utc()
    locs = {
        loc.trolley_id: loc
        for loc in db.scalars(select(LatestLocation).where(LatestLocation.project_id == project.id)).all()
    }
    cards = []
    online = 0
    for t in project.trolleys:
        loc = locs.get(t.id)
        mins = stay_minutes(loc, now) if loc else 999
        on = bool(loc and loc.identify_time and (now - aware(loc.identify_time)).total_seconds() <= offline_minutes(project) * 60)
        if on:
            online += 1
        unassigned = True
        if loc and not loc.unassigned and loc.area_code and loc.proc_code:
            unassigned = False
        line_code = (loc.line_code if loc and loc.line_code else "") or (project.lines[0].code if project.lines else "")
        cards.append(
            {
                "tz": t.tz,
                "name": t.name,
                "reader": t.reader,
                "lineCode": line_code,
                "lineName": loc.line_name if loc else "",
                "procCode": "" if unassigned else (loc.proc_code if loc else ""),
                "procName": loc.proc_name if loc else "",
                "areaCode": "" if unassigned else (loc.area_code if loc else ""),
                "areaName": loc.area_name if loc else "",
                "mins": mins if loc and loc.identify_time else None,
                "online": on,
                "unassigned": unassigned,
                "test": bool(loc.test) if loc else False,
                "reissue": loc.reissue if loc else 0,
                "src": "手动" if loc and loc.source == "manual" else ("待标位" if unassigned else "自动"),
                "kind": loc.kind if loc else "",
                "last": fmt_local(loc.identify_time) if loc else "—",
            }
        )
    procs = sum(len(l.procedures) for l in project.lines)
    areas = sum(len(pr.areas) for l in project.lines for pr in l.procedures)
    test_recs = db.scalar(
        select(func.count(PushRecord.id)).where(PushRecord.project_id == project.id, PushRecord.test.is_(True))
    ) or 0
    return {
        "push": project.push_enabled,
        "monitor": project.monitor_enabled,
        "offlineMin": int(offline_minutes(project)),
        "ready": bool(project.lines and project.trolleys),
        "tplCustom": project.tpl_custom,
        "trolleys": cards,
        "stats": {
            "lines": len(project.lines),
            "procs": procs,
            "areas": areas,
            "online": online,
            "stale": len(cards) - online,
            "total": len(cards),
        },
        "test": {
            "on": project.test_on,
            "fast": project.test_fast,
            "tick": project.test_tick or 0,
            "made": project.test_made or 0,
            "recs": int(test_recs),
            "advLabel": adv_label(project),
            "keepLabel": keep_label(project),
        },
    }


def raw_payload(db: Session, project: Project, day: str = "", q: str = "") -> dict:
    now = now_utc()
    today = now.astimezone().strftime("%Y-%m-%d")
    qn = (q or "").upper()
    devices = {}
    for t in project.trolleys:
        devices[t.reader] = {"id": t.reader, "tz": t.tz, "seat": t.name}
    stmt = select(RawReport).where(RawReport.project_id == project.id)
    if day:
        stmt = stmt.where(RawReport.report_day == day)
    rows = db.scalars(stmt.order_by(RawReport.ts.desc()).limit(5000)).all()
    latest = {}
    counts = {}
    last_ts = {}
    for r in rows:
        counts[r.device_id] = counts.get(r.device_id, 0) + 1
        if r.device_id not in latest:
            latest[r.device_id] = r
            last_ts[r.device_id] = r.ts
        if r.device_id not in devices:
            devices[r.device_id] = {"id": r.device_id, "tz": "—", "seat": "未绑定台车"}
    window = timedelta(minutes=offline_minutes(project))
    cards = []
    act = 0
    for did, meta in devices.items():
        last = latest.get(did)
        ts = last_ts.get(did)
        active = bool(ts and (now - aware(ts)) <= window)
        if active:
            act += 1
        hex_data = last.hex_data if last else ""
        epc = ""
        if last and last.epcs:
            epc = last.epcs[-1]
        elif hex_data:
            compact = norm_epc(hex_data)
            epc = pretty_epc(compact[-24:] if len(compact) >= 24 else compact)
        item = {
            "id": did,
            "tz": meta["tz"],
            "seat": meta["seat"],
            "on": active,
            "hex": hex_data,
            "epc": epc,
            "n": counts.get(did, 0),
            "last": fmt_local(ts, ms=True) if ts else "—",
            "test": bool(last.test) if last else False,
        }
        if qn and qn not in (did + " " + (epc or "") + " " + hex_data).upper():
            continue
        cards.append(item)
    total = db.scalar(select(func.count(RawReport.id)).where(RawReport.project_id == project.id)) or 0
    today_n = db.scalar(
        select(func.count(RawReport.id)).where(RawReport.project_id == project.id, RawReport.report_day == today)
    ) or 0
    day_n = total
    if day:
        day_n = db.scalar(
            select(func.count(RawReport.id)).where(RawReport.project_id == project.id, RawReport.report_day == day)
        ) or 0
    return {
        "day": day or today,
        "today": today,
        "stats": {
            "active": act,
            "inactive": max(0, len(devices) - act),
            "total": int(day_n if day else total),
            "today": int(today_n),
        },
        "devices": cards,
    }


def raw_device(db: Session, project: Project, device_id: str, day: str = "") -> dict:
    stmt = select(RawReport).where(RawReport.project_id == project.id, RawReport.device_id == device_id)
    if day:
        stmt = stmt.where(RawReport.report_day == day)
    rows = db.scalars(stmt.order_by(RawReport.ts.desc()).limit(800)).all()
    trolley = next((t for t in project.trolleys if t.reader == device_id), None)
    items = []
    for i, r in enumerate(reversed(rows), start=1):
        items.append({"no": i, "ts": fmt_local(r.ts, ms=True), "hex": r.hex_data, "test": bool(r.test)})
    last = rows[0] if rows else None
    epc = ""
    if last and last.epcs:
        epc = last.epcs[-1]
    return {
        "id": device_id,
        "tz": trolley.tz if trolley else "—",
        "seat": trolley.name if trolley else "未绑定台车",
        "on": bool(last and (now_utc() - aware(last.ts)) <= timedelta(minutes=offline_minutes(project))),
        "epc": epc,
        "n": len(items),
        "items": items,
    }


def purge_logs(db: Session, project: Project) -> None:
    days = max(int(project.log_clean or 30), 1)
    cutoff = now_utc() - timedelta(days=days)
    db.query(SysLog).filter(SysLog.project_id == project.id, SysLog.created_at < cutoff).delete()


def list_logs(db: Session, project: Project, q: str = "") -> dict:
    purge_logs(db, project)
    rows = db.scalars(select(SysLog).where(SysLog.project_id == project.id).order_by(SysLog.id.desc()).limit(800)).all()
    if q:
        key = q.lower()
        rows = [r for r in rows if key in f"{r.level} {r.message} {fmt_local(r.created_at)}".lower()]
    fail = db.scalar(
        select(func.count(SysLog.id)).where(SysLog.project_id == project.id, SysLog.level == "失败")
    ) or 0
    total = db.scalar(select(func.count(SysLog.id)).where(SysLog.project_id == project.id)) or 0
    return {
        "stats": {"total": int(total), "ok": int(total) - int(fail), "fail": int(fail), "logClean": project.log_clean or 30},
        "items": [
            {"id": r.id, "no": r.id, "time": fmt_local(r.created_at), "type": r.level, "msg": r.message} for r in rows
        ],
    }


def clear_logs(db: Session, project: Project) -> None:
    db.query(SysLog).filter(SysLog.project_id == project.id).delete()


def tick_all(db: Session) -> None:
    now = now_utc()
    rows = db.scalars(select(Project)).all()
    for stub in rows:
        due_adv = bool(stub.test_on and stub.test_next_adv and now >= aware(stub.test_next_adv))
        due_keep_test = bool(stub.test_on and stub.test_next_keep and now >= aware(stub.test_next_keep))
        due_keep_prod = bool(not stub.test_on and stub.keep_next and now >= aware(stub.keep_next))
        if not stub.test_on and not stub.keep_next:
            stub.keep_next = now + timedelta(seconds=keep_sec(stub))
            continue
        if not (due_adv or due_keep_test or due_keep_prod):
            continue
        p = load_project(db, stub.pid)
        if not p:
            continue
        if due_adv:
            test_tick(db, p)
            p.test_next_adv = now_utc() + timedelta(seconds=adv_sec(p))
        if due_keep_test:
            keep_tick(db, p, test=True)
            p.test_next_keep = now_utc() + timedelta(seconds=keep_sec(p))
        if due_keep_prod:
            keep_tick(db, p, test=False)
            p.keep_next = now_utc() + timedelta(seconds=keep_sec(p))
