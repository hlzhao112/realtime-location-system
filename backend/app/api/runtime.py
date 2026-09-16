from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..engine import runtime as rt
from ..engine.customer import post_customer
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
            rt.set_test_speed(db, p, fast=body.testFast)
        return rt.board_payload(db, p)

    return _locked(db, _do)


@router.post("/{pid}/trolleys/{tz}/place")
def place(pid: str, tz: str, body: PlaceIn, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    return _locked(db, lambda: rt.place_trolley(db, p, tz, body.lineCode, body.procCode, body.areaCode))


def _push_filters(
    startTime: str = "",
    endTime: str = "",
) -> tuple:
    try:
        start = rt.parse_query_dt(startTime)
        end = rt.parse_query_dt(endTime, end=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if start and end and end < start:
        raise HTTPException(400, "结束时间不能小于开始时间")
    return start, end


@router.get("/{pid}/pushes")
def list_pushes(
    pid: str,
    q: str = "",
    merge: bool = False,
    page: int = 1,
    pageSize: int = 200,
    startTime: str = "",
    endTime: str = "",
    tz: str = "",
    line: str = "",
    area: str = "",
    pc: str = "",
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    p = _must(db, pid)
    start, end = _push_filters(startTime, endTime)
    size = min(200, max(1, pageSize or 200))
    pg = max(1, page or 1)
    rows, total = rt.list_pushes(
        db,
        p,
        q=q,
        merge=merge,
        tz=tz,
        line=line,
        area=area,
        pc=pc,
        start=start,
        end=end,
        page=pg,
        page_size=size,
    )
    return {"items": [rt.push_out(r) for r in rows], "total": total, "page": pg, "pageSize": size}


@router.get("/{pid}/pushes/export")
def export_pushes(
    pid: str,
    q: str = "",
    merge: bool = False,
    startTime: str = "",
    endTime: str = "",
    tz: str = "",
    line: str = "",
    area: str = "",
    pc: str = "",
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    p = _must(db, pid)
    start, end = _push_filters(startTime, endTime)
    rows, _total = rt.list_pushes(
        db,
        p,
        q=q,
        merge=merge,
        tz=tz,
        line=line,
        area=area,
        pc=pc,
        start=start,
        end=end,
        paginate=False,
    )
    data = export_table(
        "推送记录",
        ["序号", "识别工序", "工序编码", "关联台座", "台座名称", "关联生产线", "区域", "区域ID", "状态", "识别时间", "数据来源", "来源细分", "测试", "已修改", "推送状态"],
        [
            [
                r.id,
                r.proc_name,
                r.proc_code,
                r.trolley_tz,
                r.seat_name,
                r.line_code,
                (r.area_name or "").strip() or "-",
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
        [10, 18, 14, 14, 16, 14, 16, 12, 10, 20, 10, 12, 8, 8, 10],
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
    # #region agent log
    import json as _json
    import time as _time
    from pathlib import Path as _Path

    try:
        _p = _Path(__file__).resolve().parents[3] / "debug-7c60c2.log"
        with _p.open("a", encoding="utf-8") as _f:
            _f.write(
                _json.dumps(
                    {
                        "sessionId": "7c60c2",
                        "runId": "pre-fix",
                        "hypothesisId": "E,F",
                        "location": "api/runtime.py:push_one",
                        "message": "click-enter",
                        "data": {"pid": pid, "rid": rid},
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    try:
        with rt.LOCK:
            prepared = rt.push_one_prepare(db, p, rid)
            db.commit()
        # #region agent log
        import json as _json
        import time as _time
        from pathlib import Path as _Path

        _pl = prepared.get("payload") or {}
        try:
            _p = _Path(__file__).resolve().parents[3] / "debug-7c60c2.log"
            with _p.open("a", encoding="utf-8") as _f:
                _f.write(
                    _json.dumps(
                        {
                            "sessionId": "7c60c2",
                            "runId": "pre-fix",
                            "hypothesisId": "A,B",
                            "location": "api/runtime.py:push_one",
                            "message": "click-prepare",
                            "data": {
                                "rid": rid,
                                "newId": prepared.get("id"),
                                "pushUrlSet": bool(getattr(prepared.get("snap"), "push_url", "")),
                                "isAutoImported": _pl.get("isAutoImported") if isinstance(_pl, dict) else None,
                                "dataSource": _pl.get("dataSource") if isinstance(_pl, dict) else None,
                                "dataIndex": _pl.get("dataIndex") if isinstance(_pl, dict) else None,
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        try:
            ok, result = post_customer(prepared["snap"], prepared["payload"], attempts=1)
        except Exception as exc:  # noqa: BLE001
            ok, result = False, {"code": None, "msg": str(exc) or "客户接口请求失败"}
        with rt.LOCK:
            out = rt.push_one_finish(db, p, prepared, ok, result)
            db.flush()
        # #region agent log
        try:
            _p = _Path(__file__).resolve().parents[3] / "debug-7c60c2.log"
            with _p.open("a", encoding="utf-8") as _f:
                _f.write(
                    _json.dumps(
                        {
                            "sessionId": "7c60c2",
                            "runId": "post-fix",
                            "hypothesisId": "D,E",
                            "location": "api/runtime.py:push_one",
                            "message": "click-result",
                            "data": {
                                "rid": rid,
                                "ok": ok,
                                "code": (result or {}).get("code"),
                                "msg": str((result or {}).get("msg") or "")[:200],
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        return out
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"客户接口请求失败：{e}") from e


@router.post("/{pid}/pushes/{rid}/edit")
def edit_push(pid: str, rid: int, body: PushEditIn, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    return _locked(db, lambda: rt.edit_push(db, p, rid, body.procCode, body.lineCode, body.areaCode, body.sportState))


@router.post("/{pid}/pushes/batch")
def batch_push(pid: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    p = _must(db, pid)
    try:
        with rt.LOCK:
            ids = rt.batch_pending_ids(db, p)
            db.flush()
        ok = 0
        for rec_id in ids:
            if rt.send_record(rec_id):
                ok += 1
        with rt.LOCK:
            from ..services.project_svc import write_log

            write_log(db, p.id, "成功" if ok == len(ids) else "信息", f"批量推送完成，成功 {ok}/{len(ids)} 台，来源:manual")
            db.flush()
        return {"ok": True, "success": ok, "total": len(ids)}
    except Exception as e:
        raise HTTPException(502, f"批量推送失败：{e}") from e


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
        rt.set_test_speed(db, p, mode=body.mode, fast=body.fast)
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
