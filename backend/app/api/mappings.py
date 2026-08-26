from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..schemas import CheckItem, DiffRow, PreviewRow, TagImportPreview, TrolleyImportPreview, TrolleyOut
from ..services.cache import rebuild_project_cache
from ..services.excel_io import (
    build_lines_from_pos,
    build_template,
    export_nos,
    export_position,
    export_trolleys,
    parse_no_file,
    parse_position_file,
    parse_trolley_file,
)
from ..services.project_svc import (
    current_stats,
    diff_stats,
    load_project,
    project_to_tree,
    replace_mapping,
    replace_trolleys,
    write_log,
)

router = APIRouter(prefix="/api/v1", tags=["mappings"], dependencies=[Depends(get_current_user)])


def _tag_preview(pos_bytes: bytes, no_bytes: bytes, current=None) -> TagImportPreview:
    pos_rows, pos_err = parse_position_file(pos_bytes)
    no_map, no_err = parse_no_file(no_bytes)
    checks = [CheckItem(**e) for e in pos_err + no_err]
    unused = []
    payload = None
    stats = {"line": 0, "proc": 0, "area": 0, "no": 0, "epc": 0}
    preview: list[PreviewRow] = []
    ok = not any(c.level == "err" for c in checks)
    if ok and pos_rows:
        lines, used, undefined = build_lines_from_pos(pos_rows, no_map)
        for n in undefined:
            checks.append(
                CheckItem(
                    level="err",
                    title="标签编号未定义",
                    detail=f"位置表引用的编号 {n} 未在编号表中定义",
                    file="位置表",
                    column="蓝牙标签编号",
                )
            )
        unused = [k for k in no_map if k not in used]
        if unused:
            checks.append(
                CheckItem(
                    level="warn",
                    title="编号表存在未被引用的编号",
                    detail=f"{', '.join(unused[:12])}{'…' if len(unused) > 12 else ''}（{len(unused)} 个）作为备用标签不导入",
                )
            )
        ok = not any(c.level == "err" for c in checks)
        stats = {
            "line": len(lines),
            "proc": sum(len(l["procs"]) for l in lines),
            "area": sum(len(g["areas"]) for l in lines for g in l["procs"]),
            "no": sum(len(a["nos"]) for l in lines for g in l["procs"] for a in g["areas"]),
            "epc": sum(len(used.get(no, [])) for l in lines for g in l["procs"] for a in g["areas"] for no in a["nos"]),
        }
        n = 0
        for l in lines:
            for g in l["procs"]:
                for a in g["areas"]:
                    for no_id in a["nos"]:
                        if n >= 8:
                            break
                        preview.append(
                            PreviewRow(
                                lineName=l["name"],
                                lineId=l["id"],
                                order=g["order"],
                                procName=g["name"],
                                procCode=g["code"],
                                areaName=a["name"],
                                areaId=a["id"],
                                no=no_id,
                                epcs=used.get(no_id, []),
                            )
                        )
                        n += 1
        if ok:
            payload = {"lines": lines, "tagNos": used}
            checks.insert(0, CheckItem(level="ok", title="位置-编号映射表表头", detail=f"7 列齐全，{len(pos_rows)} 行有效数据"))
            checks.insert(1, CheckItem(level="ok", title="编号-映射表表头", detail=f"2 列齐全，{len(no_map)} 个编号"))
            checks.insert(2, CheckItem(level="ok", title="标签编号均已定义", detail="位置表引用的编号都能在编号表中找到 EPC"))
    cur = current or {"line": 0, "proc": 0, "area": 0, "no": 0, "epc": 0, "trolley": 0}
    diff = [
        DiffRow(**x)
        for x in diff_stats(cur, stats, [("产线", "line"), ("工序", "proc"), ("区域", "area"), ("标签编号", "no")])
    ]
    return TagImportPreview(ok=ok, stats=stats, preview=preview, checks=checks, diff=diff, unusedNos=unused, payload=payload)


def _tz_preview(data: bytes, current_n: int = 0) -> TrolleyImportPreview:
    rows, errs = parse_trolley_file(data)
    checks = [CheckItem(**e) for e in errs]
    ok = not any(c.level == "err" for c in checks)
    items = [TrolleyOut(tz=r["tz"], name=r["name"], reader=r["reader"]) for r in rows]
    if ok and items:
        checks.insert(0, CheckItem(level="ok", title="表头与列数", detail=f"3 列齐全，{len(items)} 行有效数据"))
        checks.insert(1, CheckItem(level="ok", title="台座编号唯一", detail="编号无重复"))
        checks.insert(2, CheckItem(level="ok", title="读卡器 ID 唯一", detail="读卡器无重复"))
    diff = [DiffRow(**x) for x in diff_stats({"trolley": current_n}, {"trolley": len(items)}, [("台车", "trolley")])]
    return TrolleyImportPreview(
        ok=ok,
        stats={"trolley": len(items)},
        preview=items[:8],
        checks=checks,
        diff=diff,
        payload=items if ok else None,
    )


@router.post("/imports/tags/preview", response_model=TagImportPreview)
async def preview_tags_draft(pos: UploadFile = File(...), no: UploadFile = File(...)):
    return _tag_preview(await pos.read(), await no.read())


@router.post("/imports/trolleys/preview", response_model=TrolleyImportPreview)
async def preview_trolleys_draft(file: UploadFile = File(...)):
    return _tz_preview(await file.read())


@router.get("/templates/{kind}")
def download_template(kind: str):
    names = {
        "pos": ("位置-编号映射表.xlsx", "pos"),
        "no": ("编号-映射表.xlsx", "no"),
        "tz": ("港台座编号映射.xlsx", "tz"),
    }
    if kind not in names:
        raise HTTPException(404, "未知模板")
    fname, k = names[kind]
    return Response(
        build_template(k),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/projects/{pid}/imports/tags/preview", response_model=TagImportPreview)
async def preview_tags(
    pid: str,
    pos: UploadFile = File(...),
    no: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    return _tag_preview(await pos.read(), await no.read(), current_stats(p))


@router.post("/projects/{pid}/imports/tags/commit")
def commit_tags(pid: str, body: dict, db: Session = Depends(get_db)):
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    lines = body.get("lines") or []
    tag_nos = body.get("tagNos") or {}
    if not lines:
        raise HTTPException(400, "导入内容为空")
    replace_mapping(db, p, lines, tag_nos)
    db.flush()
    rebuild_project_cache(db, p.id)
    write_log(
        db,
        p.id,
        "信息",
        f"映射表一覆盖导入：{len(lines)} 产线 / {sum(len(l.get('procs') or []) for l in lines)} 工序",
    )
    return {"ok": True}


@router.post("/projects/{pid}/imports/trolleys/preview", response_model=TrolleyImportPreview)
async def preview_trolleys(pid: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    return _tz_preview(await file.read(), current_stats(p)["trolley"])


@router.post("/projects/{pid}/imports/trolleys/commit")
def commit_trolleys(pid: str, body: dict, db: Session = Depends(get_db)):
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    items = body.get("trolleys") or body.get("payload") or []
    if not items:
        raise HTTPException(400, "导入内容为空")
    replace_trolleys(db, p, items)
    db.flush()
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", f"映射表二覆盖导入：{len(items)} 条台座")
    return {"ok": True}


@router.get("/projects/{pid}/exports/{kind}")
def export_map(pid: str, kind: str, db: Session = Depends(get_db)):
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    lines, tag_nos = project_to_tree(p)
    if kind == "pos":
        data, fname = export_position([x.model_dump() for x in lines]), "位置-编号映射表.xlsx"
    elif kind == "no":
        data, fname = export_nos(tag_nos), "编号-映射表.xlsx"
    elif kind == "tz":
        data, fname = export_trolleys([{"tz": t.tz, "name": t.name, "reader": t.reader} for t in p.trolleys]), "港台座编号映射.xlsx"
    else:
        raise HTTPException(404, "未知导出类型")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
