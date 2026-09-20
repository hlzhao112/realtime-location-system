"""主看板、推送记录、测试模式、保持推送、原始数据与日志。"""

from __future__ import annotations

import queue
import random
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .customer import build_customer_payload, movement_status, post_customer, replace_identify_time, replace_sport_state
from ..db import SessionLocal
from ..models import LatestLocation, Line, Project, PushRecord, RawReport, SysLog, Trolley
from ..services.normalize import norm_epc, pretty_epc
from ..services.project_svc import load_project, write_log

LOCK = threading.Lock()
_DEFER_HTTP = False
PENDING_OUTBOUND: list[int] = []
OUTBOUND_Q: queue.Queue[int] = queue.Queue()


def _dbg(message: str, data: dict, hid: str, loc: str) -> None:
    # #region agent log
    import json
    import time
    from pathlib import Path

    try:
        p = Path(__file__).resolve().parents[3] / "debug-537a1e.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "537a1e",
                        "runId": "post-fix",
                        "hypothesisId": hid,
                        "location": loc,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


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


def parse_query_dt(value: Optional[str], *, end: bool = False) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    text = text.replace("T", " ")
    naive = None
    matched = ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(text, fmt)
            matched = fmt
            break
        except ValueError:
            continue
    if naive is None:
        raise ValueError("时间格式无效，请使用 YYYY-MM-DD HH:MM")
    if end and matched == "%Y-%m-%d %H:%M":
        naive = naive.replace(second=59)
    tz = datetime.now().astimezone().tzinfo
    return naive.replace(tzinfo=tz)


def sport_cn(state: str) -> str:
    return "运动" if state in {"moving", "运动", "1"} else "静止"


def sport_db(state: str) -> str:
    return "moving" if state in {"moving", "运动", "1"} else "still"


def sport_code(state: str) -> str:
    return "1" if state in {"moving", "运动", "1"} else "0"


def src_cn(source: str) -> str:
    return "手动" if source == "manual" else "自动"


def adv_mode(p: Project) -> str:
    mode = str(getattr(p, "test_adv_mode", None) or "").strip().lower()
    if mode in {"fast", "real", "hour"}:
        return mode
    return "fast" if getattr(p, "test_fast", True) else "real"


def adv_sec(p: Project) -> int:
    return {"fast": 6, "real": 120, "hour": 3600}.get(adv_mode(p), 6)


def keep_sec(p: Project) -> int:
    if p.test_on:
        return 600
    return max(int(getattr(p, "keep_sec", None) or 600), 1)


def format_keep_interval(seconds: int) -> str:
    seconds = max(int(seconds or 0), 1)
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def adv_label(p: Project) -> str:
    return {
        "fast": "6 秒（演示加速，对应现场 2 分钟）",
        "real": "2 分钟",
        "hour": "1 小时",
    }.get(adv_mode(p), "6 秒（演示加速，对应现场 2 分钟）")


def keep_label(p: Project) -> str:
    if p.test_on:
        return "10 分钟"
    return format_keep_interval(keep_sec(p))


def normalize_test_adv_mode(mode: Optional[str] = None, fast: Optional[bool] = None) -> str:
    m = str(mode or "").strip().lower()
    if m in {"fast", "real", "hour"}:
        return m
    if fast is not None:
        return "fast" if fast else "real"
    return "fast"

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


def find_proc_named(line: Optional[Line], code: str, name: str = ""):
    """同一产线允许工序编码重复，需用名称区分。"""
    if not line:
        return None
    name = (name or "").strip()
    if name:
        for proc in line.procedures:
            if proc.code == code and proc.name == name:
                return proc
    return find_proc(line, code)


def next_proc(line: Optional[Line], code: str, name: str = ""):
    procs = list(line.procedures) if line else []
    if not procs:
        return None
    current = find_proc_named(line, code, name)
    if current is None:
        return procs[0]
    return procs[(procs.index(current) + 1) % len(procs)]


def pick_free_on_line(db: Session, project: Project, line, except_tz: str, start_proc=None):
    """未分配台车只进入目标工序，不跳过中间工序；无空位则挤占该工序。"""
    procs = list(line.procedures) if line else []
    if not procs:
        return None, None, None
    proc = start_proc if start_proc in procs else procs[0]
    area, evict = pick_area(db, project, line, proc, except_tz)
    return proc, area, evict


def find_area(proc, code: str):
    if not proc:
        return None
    for area in proc.areas:
        if area.code == code:
            return area
    return None


def resolve_area(project: Project, line_code: str, proc_code: str, area_code: str, proc_name: str = ""):
    line = find_line(project, line_code)
    proc = find_proc_named(line, proc_code, proc_name) if line else None
    return find_area(proc, area_code) if proc and area_code else None


def touch_area_seen(area, when: Optional[datetime], tz: str = "") -> None:
    """记录区域最后一次识别时间，台车离开后仍可在看板上展示间隔。"""
    if not area or not when:
        return
    area.last_identify_time = when
    if tz:
        area.last_tz = tz


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


def last_push(db: Session, project_id: int, tz: str, test: Optional[bool] = None) -> Optional[PushRecord]:
    stmt = select(PushRecord).where(PushRecord.project_id == project_id, PushRecord.trolley_tz == tz)
    if test is not None:
        stmt = stmt.where(PushRecord.test.is_(test))
    return db.scalar(stmt.order_by(PushRecord.id.desc()).limit(1))


def area_epc(area) -> str:
    for tag in area.tag_nos:
        for epc in tag.epcs:
            if epc.epc_norm or epc.epc_raw:
                return epc.epc_raw or pretty_epc(epc.epc_norm)
    return ""


@contextmanager
def defer_outbound():
    global _DEFER_HTTP
    prev = _DEFER_HTTP
    _DEFER_HTTP = True
    try:
        yield
    finally:
        _DEFER_HTTP = prev


def take_outbound() -> list[int]:
    ids = PENDING_OUTBOUND[:]
    PENDING_OUTBOUND.clear()
    return ids


def project_snap(project: Project) -> SimpleNamespace:
    return SimpleNamespace(
        pid=project.pid,
        name=project.name,
        app_id=project.app_id,
        app_secret=project.app_secret,
        token_url=project.token_url,
        push_url=project.push_url,
        retry=project.retry,
    )


def apply_deliver_result(db: Session, project: Project, rec: PushRecord, ok: bool, result: dict) -> tuple[bool, dict]:
    attempts = max(1, int(getattr(project, "retry", None) or 3))
    detail = result.get("msg") or "接口超时"
    if result.get("code") is not None:
        detail = f"code={result.get('code')} msg={detail}"
    if ok:
        rec.status = "success"
        test_mark = "，测试记录" if rec.test else ""
        write_log(db, project.id, "成功", f"台车 {rec.trolley_tz} 推送成功（来源:{rec.kind}{test_mark}，{detail}）")
        return True, result
    rec.status = "failed"
    write_log(
        db,
        project.id,
        "失败",
        f"台车 {rec.trolley_tz} 推送失败（已重试{attempts}次）: {detail}",
    )
    return False, result


def send_record(rec_id: int) -> bool:
    """在全局锁外请求客户接口，避免测试批量推送卡住页面上的单条推送。"""
    db = SessionLocal()
    try:
        with LOCK:
            rec = db.get(PushRecord, rec_id)
            if not rec or rec.status not in {"pending", "failed"}:
                return False
            project = db.get(Project, rec.project_id)
            if not project:
                return False
            snap = project_snap(project)
            payload = dict(rec.payload or {})
            if rec.source != "manual" and not project.push_enabled:
                rec.status = "pending"
                db.commit()
                return False
            if not project.push_url:
                rec.status = "success"
                write_log(db, project.id, "成功", f"台车 {rec.trolley_tz} 推送成功（本地记成功，未配置客户推送 URL，来源:{rec.kind}）")
                db.commit()
                return True
            if not project.token_url or not project.app_id or not project.app_secret:
                rec.status = "failed"
                write_log(db, project.id, "失败", f"台车 {rec.trolley_tz} 推送失败：Token配置不完整（需 AppID、AppSecret、Token URL）")
                db.commit()
                return False
        ok, result = post_customer(snap, payload, attempts=2)
        with LOCK:
            rec = db.get(PushRecord, rec_id)
            project = db.get(Project, rec.project_id) if rec else None
            if not rec or not project:
                return False
            apply_deliver_result(db, project, rec, ok, result)
            db.commit()
            return ok
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def enqueue_outbound(ids: Optional[list[int]] = None) -> None:
    for rec_id in ids or []:
        OUTBOUND_Q.put(rec_id)


def flush_pending_outbound(ids: Optional[list[int]] = None) -> None:
    enqueue_outbound(ids)


def outbound_worker_loop() -> None:
    while True:
        rec_id = OUTBOUND_Q.get()
        try:
            send_record(rec_id)
        except Exception:
            pass


def deliver(db: Session, project: Project, rec: PushRecord, force: bool = False) -> tuple[bool, dict]:
    auto = rec.source != "manual"
    if auto and not project.push_enabled and not force:
        rec.status = "pending"
        write_log(
            db,
            project.id,
            "信息",
            f"台车 {rec.trolley_tz} 识别到 {rec.proc_name}·{rec.area_code}，自动推送已停止，仅生成记录待补推（来源:{rec.kind}）",
        )
        return False, {"code": None, "msg": "自动推送已停止，仅生成记录待补推"}
    if not project.push_url:
        rec.status = "success"
        write_log(db, project.id, "成功", f"台车 {rec.trolley_tz} 推送成功（本地记成功，未配置客户推送 URL，来源:{rec.kind}）")
        return True, {"code": None, "msg": "未配置客户推送 URL，仅本地记成功"}
    if not project.token_url or not project.app_id or not project.app_secret:
        rec.status = "failed"
        write_log(db, project.id, "失败", f"台车 {rec.trolley_tz} 推送失败：Token配置不完整（需 AppID、AppSecret、Token URL）")
        return False, {"code": None, "msg": "Token配置不完整（需 AppID、AppSecret、Token URL）"}
    if _DEFER_HTTP and not force:
        rec.status = "pending"
        PENDING_OUTBOUND.append(rec.id)
        return False, {"code": None, "msg": "已排队外发"}
    ok, result = post_customer(project, rec.payload or {}, attempts=2)
    return apply_deliver_result(db, project, rec, ok, result)


def build_payload(project: Project, trolley: Trolley, loc_like, identify_time: datetime, sport_state: str) -> dict:
    return build_customer_payload(project, trolley, loc_like, identify_time, sport_state=sport_state)


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
                LatestLocation.proc_name == loc.proc_name,
                LatestLocation.area_code == loc.area_code,
                LatestLocation.trolley_id != trolley.id,
            )
        )
        .all()
    )
    line = find_line(project, loc.line_code)
    nxt_p = next_proc(line, loc.proc_code, loc.proc_name)
    nxt = nxt_p.code if nxt_p else ""
    nxt_name = nxt_p.name if nxt_p else ""
    for other, ot in others:
        other.unassigned = True
        other.retry_proc = nxt
        other.proc_code = nxt or other.proc_code
        other.proc_name = nxt_name or other.proc_name
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
    make_push: bool = True,
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
    touch_area_seen(area, when, trolley.tz)
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
    if make_push:
        create_push(db, project, trolley, loc, kind, source, test, sport_state, when)
    return loc


def on_location_updated(db: Session, project: Project, trolley: Trolley, loc: LatestLocation, test: bool = False) -> None:
    loc.unassigned = False
    loc.kind = loc.kind or "实时上报"
    loc.source = loc.source or "auto"
    loc.test = test
    when = loc.identify_time or now_utc()
    area = resolve_area(project, loc.line_code, loc.proc_code, loc.area_code, loc.proc_name)
    touch_area_seen(area, when, trolley.tz)
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
        when,
    )


def assigned_on(
    db: Session,
    project: Project,
    line_code: str,
    proc_code: str,
    area_code: str,
    except_tz: str,
    proc_name: str = "",
):
    stmt = (
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
    )
    if proc_name:
        stmt = stmt.where(LatestLocation.proc_name == proc_name)
    return db.execute(stmt).all()


def pick_area(db: Session, project: Project, line, proc, except_tz: str):
    """返回 (area, evict_loc_trolley_or_None)。无空闲时挤出停留最久者（与 demo 一致）。"""
    if not proc or not proc.areas:
        return None, None
    free = []
    occupied = []
    for area in proc.areas:
        rows = assigned_on(db, project, line.code, proc.code, area.code, except_tz, proc.name)
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


def mark_unassigned(loc: LatestLocation, line, retry_proc: str, skip_tick: int, retry_name: str = "") -> None:
    loc.unassigned = True
    loc.area_code = ""
    loc.area_name = ""
    loc.line_code = line.code if line else loc.line_code
    loc.line_name = line.name if line else loc.line_name
    loc.retry_proc = retry_proc
    if retry_proc:
        loc.proc_code = retry_proc
        if retry_name:
            loc.proc_name = retry_name
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
            loc.retry_proc = ""
            touch_area_seen(area, loc.identify_time, t.tz)
            det.append(f"{t.tz} → {line.name}·{first.name}·{area.code}")
        else:
            loc.unassigned = True
            loc.retry_proc = first.code if first else ""
            loc.proc_code = first.code if first else ""
            loc.proc_name = first.name if first else ""
            det.append(f"{t.tz} → {line.name}·未分配（首工序区域已占满，按序等待进入）")
        db.flush()

    project.test_on = True
    project.test_tick = 0
    project.push_enabled = True
    now = now_utc()
    project.test_next_adv = now + timedelta(seconds=adv_sec(project))
    project.test_next_keep = now
    write_log(
        db,
        project.id,
        "信息",
        f"测试模式启动：已打开自动推送，测试数据将上传客户接口（工序推进 {adv_label(project)}，台座保持推送每 10 分钟）；为 {len(trolleys)} 台车分配初始位置（按台车顺序平均分配到各产线）—— "
        + "；".join(det),
    )
    _dbg("test-start", {"pid": project.pid, "trolleys": len(trolleys), "mode": adv_mode(project)}, "A,E", "runtime.py:start_test")
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


def set_test_speed(db: Session, project: Project, mode: Optional[str] = None, fast: Optional[bool] = None) -> None:
    resolved = normalize_test_adv_mode(mode, fast)
    project.test_adv_mode = resolved
    project.test_fast = resolved == "fast"
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
    n = 0
    for t in trolleys:
        loc = loc_of(db, project, t)
        if loc.skip_tick == tick:
            continue
        line = find_line(project, loc.line_code) or (project.lines[0] if project.lines else None)
        if not line or not line.procedures:
            continue
        procs = list(line.procedures)
        was_assigned = not loc.unassigned
        if loc.unassigned:
            start = find_proc_named(line, loc.retry_proc or loc.proc_code, loc.proc_name) or procs[0]
            proc, area, evict = pick_free_on_line(db, project, line, t.tz, start)
        else:
            proc = next_proc(line, loc.proc_code, loc.proc_name)
            area, evict = pick_area(db, project, line, proc, t.tz) if proc else (None, None)
        if not proc or not area:
            continue
        if evict:
            eloc, et = evict
            nxt = next_proc(line, proc.code, proc.name)
            mark_unassigned(
                eloc,
                line,
                nxt.code if nxt else "",
                tick,
                retry_name=nxt.name if nxt else "",
            )
            write_log(
                db,
                project.id,
                "信息",
                f"区域独占冲突：{t.tz} 进入 {proc.name}·{area.code}，原台车 {et.tz} 被挤出至「未分配」（下一轮重试进入下一工序）",
            )
            db.flush()
        loop = was_assigned and proc is procs[0] and len(procs) > 1
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
            make_push=True,
        )
        n += 1
        if loop:
            write_log(
                db,
                project.id,
                "信息",
                f"台车 {t.tz} 已走完 {line.name} 全部工序，回到首工序 {procs[0].name} 重新开始（不跨产线）",
            )
    if n:
        dest = "并排队上传客户接口" if project.push_enabled else "仅保存在本地（自动推送已停止）"
        write_log(db, project.id, "信息", f"测试推进第 {tick} 轮：更新 {n} 台车位置，{dest}")
        _dbg("test-tick", {"pid": project.pid, "tick": tick, "n": n}, "F", "runtime.py:test_tick")


def keep_tick(db: Session, project: Project, test: bool) -> None:
    n = 0
    now = now_utc()
    for t in project.trolleys:
        loc = loc_of(db, project, t)
        if loc.unassigned or not loc.area_code:
            continue
        last = last_push(db, project.id, t.tz, test=bool(test))
        state_cn = movement_status(loc.identify_time)
        area = resolve_area(project, loc.line_code, loc.proc_code, loc.area_code, loc.proc_name)
        touch_area_seen(area, now, t.tz)
        if test:
            create_push(db, project, t, loc, "保持推送", "auto", True, state_cn, now)
            n += 1
            continue
        if not last:
            continue
        rec = PushRecord(
            project_id=project.id,
            trolley_tz=last.trolley_tz,
            seat_name=last.seat_name,
            line_code=last.line_code,
            proc_code=last.proc_code,
            proc_name=last.proc_name,
            area_code=last.area_code,
            area_name=last.area_name or "",
            sport_state=sport_db(state_cn),
            source="auto",
            kind="保持推送",
            test=bool(test),
            payload=replace_sport_state(replace_identify_time(last.payload, now), state_cn),
            status="pending",
            identify_time=now,
            ref_id=last.id,
        )
        db.add(rec)
        db.flush()
        if test:
            project.test_made = int(project.test_made or 0) + 1
        deliver(db, project, rec, force=False)
        n += 1
    if n:
        write_log(
            db,
            project.id,
            "成功",
            (
                f"测试台座推送：按当前看板位置向客户接口上报 {n} 条（间隔 {keep_label(project)}）"
                if test
                else f"定时保持推送：位置未变，复制上一条记录、更新识别时间与运动状态续推 {n} 条（间隔 {keep_label(project)}）"
            ),
        )
        _dbg("keep-tick", {"pid": project.pid, "n": n, "test": bool(test)}, "F", "runtime.py:keep_tick")


def place_trolley(db: Session, project: Project, tz: str, line_code: str, proc_code: str, area_code: str = "") -> dict:
    trolley = next((x for x in project.trolleys if x.tz == tz), None)
    if not trolley:
        raise ValueError("台车不存在")
    line = find_line(project, line_code)
    if not line:
        raise ValueError("产线或工序不存在")
    proc = None
    area = None
    if area_code:
        for p in line.procedures:
            if proc_code and p.code != proc_code:
                continue
            hit = find_area(p, area_code)
            if hit:
                proc, area = p, hit
                break
    if not proc:
        proc = find_proc(line, proc_code)
    if not proc:
        raise ValueError("产线或工序不存在")
    if not area:
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
        f"台车 {trolley.tz} 手动标位 → {line.name}·{proc.name}·{area.name}，已触发推送（来源:manual）",
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


def push_one_prepare(db: Session, project: Project, rec_id: int) -> dict:
    rec = db.get(PushRecord, rec_id)
    if not rec or rec.project_id != project.id:
        raise ValueError("推送记录不存在")
    trolley = next((x for x in project.trolleys if x.tz == rec.trolley_tz), None)
    if not trolley:
        raise ValueError("台车不存在")
    loc = loc_of(db, project, trolley)
    now = now_utc()
    if rec.source == "manual":
        state_cn = sport_cn(rec.sport_state)
    else:
        state_cn = movement_status(loc.identify_time or rec.identify_time)
    newest = PushRecord(
        project_id=project.id,
        trolley_tz=rec.trolley_tz,
        seat_name=rec.seat_name,
        line_code=rec.line_code,
        proc_code=rec.proc_code,
        proc_name=rec.proc_name,
        area_code=rec.area_code,
        area_name=rec.area_name or "",
        sport_state=sport_db(state_cn),
        source="manual",
        kind="手动推送",
        test=bool(rec.test),
        ref_id=rec.id,
        payload=build_payload(project, trolley, rec, now, state_cn),
        status="pending",
        identify_time=now,
    )
    db.add(newest)
    db.flush()
    return {
        "id": newest.id,
        "src_id": rec.id,
        "state_cn": state_cn,
        "payload": dict(newest.payload or {}),
        "snap": project_snap(project),
    }


def push_one_finish(db: Session, project: Project, prepared: dict, ok: bool, result: dict) -> dict:
    newest = db.get(PushRecord, prepared["id"])
    if not newest:
        raise ValueError("推送记录不存在")
    apply_deliver_result(db, project, newest, ok, result)
    write_log(
        db,
        project.id,
        "成功" if ok else "失败",
        f"手动推送记录 {prepared['src_id']}：新增序号 {newest.id}（{prepared['state_cn']}）",
    )
    return {
        "ok": ok,
        "id": newest.id,
        "status": newest.status,
        "code": result.get("code"),
        "msg": result.get("msg") or "",
        "httpStatus": result.get("httpStatus"),
    }


def push_one(db: Session, project: Project, rec_id: int) -> dict:
    prepared = push_one_prepare(db, project, rec_id)
    ok, result = post_customer(prepared["snap"], prepared["payload"], attempts=2)
    return push_one_finish(db, project, prepared, ok, result)


def batch_pending_ids(db: Session, project: Project) -> list[int]:
    limit = max(int(project.batch or 20), 1)
    rows = (
        db.scalars(
            select(PushRecord)
            .where(PushRecord.project_id == project.id, PushRecord.status == "pending")
            .order_by(PushRecord.id.asc())
            .limit(limit)
        )
        .all()
    )
    return [r.id for r in rows]


def batch_push(db: Session, project: Project) -> dict:
    ids = batch_pending_ids(db, project)
    ok = 0
    for rec_id in ids:
        rec = db.get(PushRecord, rec_id)
        if not rec:
            continue
        success, _ = deliver(db, project, rec, force=True)
        if success:
            ok += 1
    write_log(db, project.id, "成功" if ok == len(ids) else "信息", f"批量推送完成，成功 {ok}/{len(ids)} 台，来源:manual")
    return {"ok": True, "success": ok, "total": len(ids)}


def set_flags(db: Session, project: Project, push: Optional[bool] = None, monitor: Optional[bool] = None) -> None:
    if push is not None:
        project.push_enabled = push
        write_log(db, project.id, "信息", "自动推送已开启，测试/正式记录将上传客户接口" if push else "自动推送已停止，期间数据将积压待批量补推")
        if push:
            pending = (
                db.scalars(
                    select(PushRecord.id)
                    .where(PushRecord.project_id == project.id, PushRecord.status == "pending")
                    .order_by(PushRecord.id.asc())
                    .limit(200)
                )
                .all()
            )
            enqueue_outbound(list(pending))
    if monitor is not None:
        project.monitor_enabled = monitor
        write_log(db, project.id, "信息", "文件夹监测已启动" if monitor else "文件夹监测已停止")


def merge_rows(rows: list[PushRecord]) -> list[PushRecord]:
    """同台车同位置连续相同记录合并为一条（保留每段连续中最新的那条）。"""
    out: list[PushRecord] = []
    prev: Optional[str] = None
    for r in rows:
        key = f"{r.trolley_tz}|{r.line_code}|{r.proc_code}|{r.area_code}"
        if key == prev:
            continue
        prev = key
        out.append(r)
    return out


def list_pushes(
    db: Session,
    project: Project,
    q: str = "",
    merge: bool = False,
    tz: str = "",
    line: str = "",
    area: str = "",
    pc: str = "",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 200,
    paginate: bool = True,
) -> tuple[list[PushRecord], int]:
    stmt = select(PushRecord).where(PushRecord.project_id == project.id)
    if tz:
        stmt = stmt.where(PushRecord.trolley_tz == tz)
    if line:
        stmt = stmt.where(PushRecord.line_code == line)
    if area:
        stmt = stmt.where(PushRecord.area_code == area)
    if pc:
        stmt = stmt.where(PushRecord.proc_code == pc)
    if start is not None:
        stmt = stmt.where(PushRecord.identify_time >= start)
    if end is not None:
        stmt = stmt.where(PushRecord.identify_time <= end)
    rows = db.scalars(stmt.order_by(PushRecord.id.desc())).all()
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
                    r.area_name or "",
                    sport_cn(r.sport_state),
                    src_cn(r.source),
                    r.kind or "",
                ]
            ).lower()
        ]
    if merge:
        rows = merge_rows(rows)
    total = len(rows)
    if not paginate:
        return rows, total
    size = min(200, max(1, int(page_size or 200)))
    pg = max(1, int(page or 1))
    start_i = (pg - 1) * size
    return rows[start_i : start_i + size], total


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
                "agoSec": (
                    max(0, int((now - aware(loc.identify_time)).total_seconds()))
                    if loc and loc.identify_time
                    else None
                ),
                "online": on,
                "unassigned": unassigned,
                "test": bool(loc.test) if loc else False,
                "reissue": loc.reissue if loc else 0,
                "src": "手动" if loc and loc.source == "manual" else ("待标位" if unassigned else "自动"),
                "kind": loc.kind if loc else "",
                "last": fmt_local(loc.identify_time) if loc else "—",
            }
        )
    area_seen = {}
    for line in project.lines:
        for proc in line.procedures:
            for area in proc.areas:
                when = aware(getattr(area, "last_identify_time", None))
                if not when:
                    continue
                key = f"{line.code}|{proc.code}|{proc.name}|{area.code}"
                area_seen[key] = {
                    "last": fmt_local(when),
                    "agoSec": max(0, int((now - when).total_seconds())),
                    "tz": getattr(area, "last_tz", None) or "",
                }
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
        "areaSeen": area_seen,
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
            "fast": adv_mode(project) == "fast",
            "mode": adv_mode(project),
            "tick": project.test_tick or 0,
            "made": project.test_made or 0,
            "recs": int(test_recs),
            "advLabel": adv_label(project),
            "keepLabel": keep_label(project),
        },
    }


def resolve_raw_place(project: Project, db: Session, hex_data: str = "", epcs: Optional[list] = None) -> dict:
    """用映射表把原始 HEX / EPC 解析为产线·工序·区域。"""
    from ..services.cache import get_cache, lookup_epc
    from .parse import extract_epcs

    cache = get_cache(db, project)
    candidates = extract_epcs(hex_data or "", [str(x) for x in (epcs or []) if x])
    display = ""
    hit = None
    for e in candidates:
        pretty = pretty_epc(e) or e
        if not display:
            display = pretty
        found = lookup_epc(cache, e)
        if found:
            hit = found
            display = pretty
            break
    if not display and epcs:
        display = pretty_epc(str(epcs[-1])) or str(epcs[-1])
    if not hit:
        return {
            "epc": display or "",
            "lineCode": "",
            "lineName": "",
            "procCode": "",
            "procName": "",
            "areaCode": "",
            "areaName": "",
        }
    return {
        "epc": display or "",
        "lineCode": hit.line_code,
        "lineName": hit.line_name,
        "procCode": hit.proc_code,
        "procName": hit.proc_name,
        "areaCode": hit.area_code,
        "areaName": hit.area_name,
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
        place = resolve_raw_place(project, db, hex_data, last.epcs if last else None) if last else {
            "epc": "",
            "lineCode": "",
            "lineName": "",
            "procCode": "",
            "procName": "",
            "areaCode": "",
            "areaName": "",
        }
        epc = place.get("epc") or ""
        item = {
            "id": did,
            "tz": meta["tz"],
            "seat": meta["seat"],
            "on": active,
            "hex": hex_data,
            "epc": epc,
            "lineCode": place.get("lineCode") or "",
            "lineName": place.get("lineName") or "",
            "procCode": place.get("procCode") or "",
            "procName": place.get("procName") or "",
            "areaCode": place.get("areaCode") or "",
            "areaName": place.get("areaName") or "",
            "n": counts.get(did, 0),
            "last": fmt_local(ts, ms=True) if ts else "—",
            "test": bool(last.test) if last else False,
        }
        hay = " ".join(
            [
                did,
                epc or "",
                hex_data,
                item["lineCode"],
                item["lineName"],
                item["procCode"],
                item["procName"],
                item["areaCode"],
                item["areaName"],
                meta["tz"],
                meta["seat"],
            ]
        ).upper()
        if qn and qn not in hay:
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
        place = resolve_raw_place(project, db, r.hex_data or "", r.epcs)
        items.append(
            {
                "no": i,
                "ts": fmt_local(r.ts, ms=True),
                "hex": r.hex_data,
                "epc": place.get("epc") or "",
                "lineCode": place.get("lineCode") or "",
                "lineName": place.get("lineName") or "",
                "procCode": place.get("procCode") or "",
                "procName": place.get("procName") or "",
                "areaCode": place.get("areaCode") or "",
                "areaName": place.get("areaName") or "",
                "test": bool(r.test),
            }
        )
    last = rows[0] if rows else None
    last_place = resolve_raw_place(project, db, last.hex_data or "", last.epcs) if last else {}
    epc = last_place.get("epc") or ""
    return {
        "id": device_id,
        "tz": trolley.tz if trolley else "—",
        "seat": trolley.name if trolley else "未绑定台车",
        "on": bool(last and (now_utc() - aware(last.ts)) <= timedelta(minutes=offline_minutes(project))),
        "epc": epc,
        "lineCode": last_place.get("lineCode") or "",
        "lineName": last_place.get("lineName") or "",
        "procCode": last_place.get("procCode") or "",
        "procName": last_place.get("procName") or "",
        "areaCode": last_place.get("areaCode") or "",
        "areaName": last_place.get("areaName") or "",
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


def tick_all(db: Session) -> bool:
    now = now_utc()
    t0 = now
    did = 0
    dirty = False
    rows = db.scalars(select(Project)).all()
    for stub in rows:
        due_adv = bool(stub.test_on and stub.test_next_adv and now >= aware(stub.test_next_adv))
        due_keep_test = bool(stub.test_on and stub.test_next_keep and now >= aware(stub.test_next_keep))
        due_keep_prod = bool(not stub.test_on and stub.keep_next and now >= aware(stub.keep_next))
        if not stub.test_on and not stub.keep_next:
            stub.keep_next = now + timedelta(seconds=keep_sec(stub))
            dirty = True
            continue
        if not (due_adv or due_keep_test or due_keep_prod):
            continue
        p = load_project(db, stub.pid)
        if not p:
            continue
        did += 1
        dirty = True
        with defer_outbound():
            if due_adv:
                test_tick(db, p)
                p.test_next_adv = now_utc() + timedelta(seconds=adv_sec(p))
            if due_keep_test:
                keep_tick(db, p, test=True)
                p.test_next_keep = now_utc() + timedelta(seconds=keep_sec(p))
            if due_keep_prod:
                keep_tick(db, p, test=False)
                p.keep_next = now_utc() + timedelta(seconds=keep_sec(p))
        _dbg(
            "tick-work",
            {
                "pid": stub.pid,
                "dueAdv": due_adv,
                "dueKeepTest": due_keep_test,
                "dueKeepProd": due_keep_prod,
                "testOn": bool(stub.test_on),
                "ms": int((now_utc() - t0).total_seconds() * 1000),
                "jobs": did,
            },
            "F",
            "runtime.py:tick_all",
        )
    return dirty
