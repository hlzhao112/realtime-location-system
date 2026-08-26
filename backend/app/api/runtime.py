from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..engine import runtime as rt
from ..models import User
from ..schemas import PlaceIn, PushEditIn, RuntimePatchIn, TestSpeedIn
from ..services.excel_io import export_table
from ..services.project_svc import load_project

router = APIRouter(prefix="/api/v1/projects", tags=["runtime"], dependencies=[Depends(get_current_user)])


def _must(db: Session, pid: str):
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


def _locked(db: Session, fn):
    with rt.LOCK:
        try:
            out = fn()
            db.flush()
            return out
        except ValueError as e:
            raise HTTPException(400, str(e)) from e


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/{pid}/board")
def get_board(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return rt.board_payload(db, _must(db, pid))


@router.patch("/{pid}/runtime")
def patch_runtime(pid: str, body: RuntimePatchIn, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)

    def _do():
        if body.push is not None or body.monitor is not None:
            rt.set_flags(db, p, push=body.push, monitor=body.monitor)
        if body.testFast is not None:
            rt.set_test_speed(db, p, body.testFast)
        return rt.board_payload(db, p)

    return _locked(db, _do)


@router.post("/{pid}/trolleys/{tz}/place")
def place(pid: str, tz: str, body: PlaceIn, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    return _locked(db, lambda: rt.place_trolley(db, p, tz, body.lineCode, body.procCode, body.areaCode))


@router.get("/{pid}/pushes")
def list_pushes(
    pid: str,
    q: str = "",
    merge: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    p = _must(db, pid)
    rows = rt.list_pushes(db, p, q=q, merge=merge)
    return {"items": [rt.push_out(r) for r in rows]}


@router.get("/{pid}/pushes/export")
def export_pushes(
    pid: str,
    q: str = "",
    merge: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    p = _must(db, pid)
    rows = rt.list_pushes(db, p, q=q, merge=merge)
    data = export_table(
        "推送记录",
        ["序号", "识别工序", "工序编码", "关联台座", "台座名称", "关联生产线", "区域", "状态", "识别时间", "数据来源", "来源细分", "测试", "已修改", "推送状态"],
        [
            [
                r.id,
                r.proc_name,
                r.proc_code,
                r.trolley_tz,
                r.seat_name,
                r.line_code,
                r.area_code,
                rt.sport_cn(r.sport_state),
                rt.fmt_local(r.identify_time),
                rt.src_cn(r.source),
                r.kind,
                "是" if r.test else "",
                "是" if r.edited else "",
                r.status,
            ]
            for r in rows
        ],
        [10, 18, 14, 14, 16, 14, 12, 10, 20, 10, 12, 8, 8, 10],
    )
    return _xlsx(data, f"{pid}-push-records.xlsx")


@router.get("/{pid}/pushes/{rid}/payload")
def get_payload(pid: str, rid: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    from ..models import PushRecord

    p = _must(db, pid)
    rec = db.get(PushRecord, rid)
    if not rec or rec.project_id != p.id:
        raise HTTPException(404, "推送记录不存在")
    return {"id": rec.id, "payload": rec.payload or {}}


@router.post("/{pid}/pushes/{rid}/push")
def push_one(pid: str, rid: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    return _locked(db, lambda: rt.push_one(db, p, rid))


@router.post("/{pid}/pushes/{rid}/edit")
def edit_push(pid: str, rid: int, body: PushEditIn, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    return _locked(db, lambda: rt.edit_push(db, p, rid, body.procCode, body.lineCode, body.areaCode, body.sportState))


@router.post("/{pid}/pushes/batch")
def batch_push(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return _locked(db, lambda: rt.batch_push(db, _must(db, pid)))


@router.post("/{pid}/test/start")
def test_start(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    return _locked(db, lambda: rt.start_test(db, p))


@router.post("/{pid}/test/stop")
def test_stop(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return _locked(db, lambda: rt.stop_test(db, _must(db, pid)))


@router.post("/{pid}/test/clear")
def test_clear(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return _locked(db, lambda: rt.clear_test_data(db, _must(db, pid)))


@router.post("/{pid}/test/speed")
def test_speed(pid: str, body: TestSpeedIn, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)

    def _do():
        rt.set_test_speed(db, p, body.fast)
        return rt.board_payload(db, p)

    return _locked(db, _do)


@router.get("/{pid}/raw")
def get_raw(
    pid: str,
    day: str = "",
    q: str = "",
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return rt.raw_payload(db, _must(db, pid), day=day, q=q)


@router.get("/{pid}/raw/devices/{device_id}")
def get_raw_device(
    pid: str,
    device_id: str,
    day: str = "",
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return rt.raw_device(db, _must(db, pid), device_id, day=day)


@router.get("/{pid}/logs")
def get_logs(pid: str, q: str = "", db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return rt.list_logs(db, _must(db, pid), q=q)


@router.get("/{pid}/logs/export")
def export_logs(pid: str, q: str = "", db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    data_out = rt.list_logs(db, p, q=q)
    xlsx = export_table(
        "系统日志",
        ["序号", "时间", "类型", "内容"],
        [[r["no"], r["time"], r["type"], r["msg"]] for r in data_out["items"]],
        [10, 22, 10, 80],
    )
    return _xlsx(xlsx, f"{pid}-system-logs.xlsx")


@router.delete("/{pid}/logs")
def delete_logs(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return _locked(db, lambda: rt.clear_logs(db, _must(db, pid)) or {"ok": True})
