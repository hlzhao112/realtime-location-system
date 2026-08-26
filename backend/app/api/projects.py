from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..deps import get_current_user
from ..models import Line, Project, User
from ..schemas import (
    ALL_FIELDS,
    DEFAULT_FIELDS,
    DEFAULT_TPL,
    AreaPatchIn,
    CfgPatchIn,
    CopyIn,
    LinePatchIn,
    ProcOrderIn,
    ProcPatchIn,
    ProjectOut,
    ProjectSaveIn,
    TemplatePatchIn,
    TrolleyPatchIn,
)
from ..services.cache import rebuild_project_cache
from ..services.project_svc import (
    apply_save,
    is_ready,
    load_project,
    new_ingest_token,
    replace_mapping,
    replace_trolleys,
    to_list_item,
    to_out,
    write_log,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"], dependencies=[Depends(get_current_user)])


def _must(db: Session, pid: str) -> Project:
    p = load_project(db, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Project).options(selectinload(Project.lines).selectinload(Line.procedures), selectinload(Project.trolleys))
        .order_by(Project.created_at.desc())
    ).scalars().all()
    items = [to_list_item(db, p) for p in rows]
    return {
        "items": items,
        "stats": {
            "total": len(items),
            "running": sum(1 for x in items if x.push and x.ready),
            "lines": sum(x.lines for x in items),
            "trolleys": sum(x.trolleys for x in items),
        },
    }


@router.get("/{pid}", response_model=ProjectOut)
def get_project(pid: str, db: Session = Depends(get_db)):
    return to_out(db, _must(db, pid))


@router.post("", response_model=ProjectOut)
def create_project(body: ProjectSaveIn, db: Session = Depends(get_db)):
    if db.execute(select(Project).where(Project.pid == body.pid.strip())).scalar_one_or_none():
        raise HTTPException(400, "PID 已存在，请更换")
    if not body.pid.strip() or not body.name.strip() or not body.folder:
        raise HTTPException(400, "Step1 为必填：PID、项目名称、监控文件夹")
    token = new_ingest_token()
    p = Project(
        pid=body.pid.strip(),
        name=body.name.strip(),
        ingest_token=token,
        tpl_fields=DEFAULT_FIELDS[:],
        tpl_json=DEFAULT_TPL,
    )
    db.add(p)
    db.flush()
    apply_save(db, p, body)
    db.flush()
    p = load_project(db, p.pid)
    if not is_ready(p):
        raise HTTPException(400, "Step2 为必填：至少一条产线映射且台车映射不能为空")
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", f"新建项目 {p.pid} / {p.name}")
    return to_out(db, p, ingest_once=token)


@router.put("/{pid}", response_model=ProjectOut)
def update_project(pid: str, body: ProjectSaveIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    if body.pid.strip() != pid:
        raise HTTPException(400, "项目 PID 创建后不可修改")
    apply_save(db, p, body)
    db.flush()
    p = load_project(db, pid)
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", f"整体流程编辑保存：{p.name}")
    return to_out(db, p)


@router.post("/{pid}/copy", response_model=ProjectOut)
def copy_project(pid: str, body: CopyIn, db: Session = Depends(get_db)):
    src = _must(db, pid)
    if db.execute(select(Project).where(Project.pid == body.pid.strip())).scalar_one_or_none():
        raise HTTPException(400, "PID 已存在，请更换")
    token = new_ingest_token()
    dst = Project(
        pid=body.pid.strip(),
        name=body.name.strip(),
        folder=src.folder,
        backup=src.backup,
        scan=src.scan,
        stable=src.stable,
        offline=src.offline,
        batch=src.batch,
        resend_max=src.resend_max,
        retry=src.retry,
        log_clean=src.log_clean,
        app_id="",
        app_secret="",
        token_url=src.token_url,
        push_url=src.push_url,
        ingest_token=token,
        tpl_fields=src.tpl_fields,
        tpl_json=src.tpl_json,
        tpl_custom=src.tpl_custom,
        push_enabled=False,
        monitor_enabled=False,
    )
    db.add(dst)
    db.flush()
    from ..services.project_svc import project_to_tree

    lines, tag_nos = project_to_tree(src)
    replace_mapping(db, dst, [x.model_dump() for x in lines], tag_nos)
    replace_trolleys(db, dst, [{"tz": t.tz, "name": t.name, "reader": t.reader} for t in src.trolleys])
    db.flush()
    dst = load_project(db, dst.pid)
    rebuild_project_cache(db, dst.id)
    write_log(db, dst.id, "信息", f"由 {src.pid} 复制项目 {dst.pid}")
    return to_out(db, dst, ingest_once=token)


@router.delete("/{pid}")
def delete_project(pid: str, db: Session = Depends(get_db)):
    p = _must(db, pid)
    db.delete(p)
    write_log(db, None, "信息", f"删除项目 {pid}（映射与运行数据一并删除）")
    return {"ok": True}


@router.patch("/{pid}/cfg", response_model=ProjectOut)
def patch_cfg(pid: str, body: CfgPatchIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    mapping = {
        "name": "name",
        "folder": "folder",
        "backup": "backup",
        "scan": "scan",
        "stable": "stable",
        "offline": "offline",
        "batch": "batch",
        "resendMax": "resend_max",
        "retry": "retry",
        "logClean": "log_clean",
        "appId": "app_id",
        "appSecret": "app_secret",
        "tokenUrl": "token_url",
        "pushUrl": "push_url",
    }
    n = 0
    for k, v in body.values.items():
        if k == "pid":
            continue
        if k == "appSecret" and (not v or str(v).startswith("•")):
            continue
        attr = mapping.get(k)
        if not attr:
            continue
        setattr(p, attr, v)
        n += 1
        if k == "name":
            p.name = str(v)
    write_log(db, p.id, "信息", f"项目信息单项编辑：{body.group} 更新 {n} 个字段")
    return to_out(db, load_project(db, pid))


@router.patch("/{pid}/template", response_model=ProjectOut)
def patch_tpl(pid: str, body: TemplatePatchIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    if not body.fields:
        raise HTTPException(400, "至少勾选 1 个推送字段")
    import json
    import re

    try:
        json.loads(re.sub(r"\$\{[^}]+\}", "x", body.json))
    except Exception as e:
        raise HTTPException(400, f"模板不是合法 JSON：{e}") from e
    p.tpl_fields = [f for f in body.fields if f in ALL_FIELDS]
    p.tpl_json = body.json
    p.tpl_custom = True
    write_log(db, p.id, "信息", f"报文模板单项编辑：字段 {len(p.tpl_fields)} 个")
    return to_out(db, load_project(db, pid))


@router.post("/{pid}/token/reset")
def reset_token(pid: str, db: Session = Depends(get_db)):
    p = _must(db, pid)
    p.ingest_token = new_ingest_token()
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", "重置设备上报 Token")
    return {"ingestToken": p.ingest_token}


@router.post("/{pid}/test-api")
def test_api(pid: str, db: Session = Depends(get_db)):
    p = _must(db, pid)
    if not p.token_url and not p.push_url:
        raise HTTPException(400, "请先填写 Token / 推送接口 URL")
    import httpx

    results = []
    for label, url in (("Token 接口", p.token_url), ("推送接口", p.push_url)):
        if not url:
            results.append(f"{label} 未配置")
            continue
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(url)
            results.append(f"{label} {r.status_code}")
        except Exception as e:
            results.append(f"{label} 失败：{e}")
    return {"ok": True, "detail": " · ".join(results)}


@router.patch("/{pid}/lines/{code}", response_model=ProjectOut)
def patch_line(pid: str, code: str, body: LinePatchIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    line = next((x for x in p.lines if x.code == code), None)
    if not line:
        raise HTTPException(404, "产线不存在")
    if not body.name.strip() or not body.id.strip():
        raise HTTPException(400, "产线名称与产线 ID 均为必填")
    if any(x.code == body.id.strip() and x.id != line.id for x in p.lines):
        raise HTTPException(400, f"产线 ID {body.id} 已存在")
    old = f"{line.code} / {line.name}"
    line.name = body.name.strip()
    line.code = body.id.strip()
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", f"编辑产线：{old} → {line.code} / {line.name}")
    return to_out(db, load_project(db, pid))


@router.patch("/{pid}/lines/{line_code}/procedures/{code}", response_model=ProjectOut)
def patch_proc(pid: str, line_code: str, code: str, body: ProcPatchIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    line = next((x for x in p.lines if x.code == line_code), None)
    if not line:
        raise HTTPException(404, "产线不存在")
    proc = next((x for x in line.procedures if x.code == code), None)
    if not proc:
        raise HTTPException(404, "工序不存在")
    if not body.name.strip() or not body.code.strip():
        raise HTTPException(400, "工序名称与工序编码均为必填")
    if any(x.code == body.code.strip() and x.id != proc.id for x in line.procedures):
        raise HTTPException(400, f"同一产线内工序编码 {body.code} 重复")
    old = f"{proc.code} / {proc.name}"
    proc.name = body.name.strip()
    proc.code = body.code.strip()
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", f"编辑工序（{line.name}）：{old} → {proc.code} / {proc.name}")
    return to_out(db, load_project(db, pid))


@router.put("/{pid}/lines/{line_code}/procedures/order", response_model=ProjectOut)
def order_procs(pid: str, line_code: str, body: ProcOrderIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    line = next((x for x in p.lines if x.code == line_code), None)
    if not line:
        raise HTTPException(404, "产线不存在")
    ix = {x.code: x for x in line.procedures}
    if set(body.codes) != set(ix):
        raise HTTPException(400, "工序列表与当前不一致")
    for i, c in enumerate(body.codes, start=1):
        ix[c].sort_order = i
    db.flush()
    names = " → ".join(ix[c].name for c in body.codes)
    write_log(db, p.id, "信息", f"调整工序顺序（{line.name}）：{names}")
    rebuild_project_cache(db, p.id)
    return to_out(db, load_project(db, pid))


@router.patch("/{pid}/areas/{line_code}/{proc_code}/{area_id}", response_model=ProjectOut)
def patch_area(pid: str, line_code: str, proc_code: str, area_id: str, body: AreaPatchIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    line = next((x for x in p.lines if x.code == line_code), None)
    proc = next((x for x in (line.procedures if line else []) if x.code == proc_code), None)
    area = next((x for x in (proc.areas if proc else []) if x.code == area_id), None)
    if not area:
        raise HTTPException(404, "区域不存在")
    area.code = body.id.strip()
    area.name = body.name.strip()
    write_log(db, p.id, "信息", f"编辑区域 {area.code}")
    rebuild_project_cache(db, p.id)
    return to_out(db, load_project(db, pid))


@router.patch("/{pid}/trolleys/{tz}", response_model=ProjectOut)
def patch_trolley(pid: str, tz: str, body: TrolleyPatchIn, db: Session = Depends(get_db)):
    p = _must(db, pid)
    t = next((x for x in p.trolleys if x.tz == tz), None)
    if not t:
        raise HTTPException(404, "台车不存在")
    if any(x.tz == body.tz and x.id != t.id for x in p.trolleys):
        raise HTTPException(400, "台车 ID 重复")
    if any(x.reader == body.reader and x.id != t.id for x in p.trolleys):
        raise HTTPException(400, "读卡器 ID 重复")
    t.tz = body.tz.strip()
    t.name = body.name.strip()
    t.reader = body.reader.strip()
    rebuild_project_cache(db, p.id)
    write_log(db, p.id, "信息", f"编辑台车 {t.tz}")
    return to_out(db, load_project(db, pid))
