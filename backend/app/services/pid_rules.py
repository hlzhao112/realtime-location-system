"""项目 PID 唯一性，以及 MQTT 设备号按「包含 PID」匹配。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Project


def normalize_pid(value: str | None) -> str:
    return (value or "").strip()


def match_contained_pid(device_id: str, pids: list[str]) -> str:
    """Device ID 中包含的已有项目 PID；多条命中时取最长。"""
    dev = (device_id or "").strip().lower()
    if not dev:
        return ""
    hits = [p for p in pids if p and p.lower() in dev]
    if not hits:
        return ""
    return max(hits, key=lambda p: (len(p), p.lower()))


def pid_conflict(new_pid: str, existing: list[str]) -> str:
    pid = normalize_pid(new_pid)
    if not pid:
        return "PID 不能为空"
    for old in existing:
        cur = normalize_pid(old)
        if not cur:
            continue
        if pid.lower() == cur.lower():
            return "PID 已存在，请更换"
        if cur.lower() in pid.lower():
            return f"PID 不能包含已有项目 PID「{cur}」"
    return ""


def existing_pids(db: Session, exclude: str | None = None) -> list[str]:
    rows = db.execute(select(Project.pid)).scalars().all()
    skip = (exclude or "").strip().lower()
    out = []
    for pid in rows:
        text = normalize_pid(pid)
        if not text:
            continue
        if skip and text.lower() == skip:
            continue
        out.append(text)
    return out


def assert_pid_allowed(db: Session, pid: str, exclude: str | None = None) -> str:
    pid = normalize_pid(pid)
    err = pid_conflict(pid, existing_pids(db, exclude=exclude))
    if err:
        raise ValueError(err)
    return pid
