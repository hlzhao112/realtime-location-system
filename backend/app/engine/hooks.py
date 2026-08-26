"""解析后钩子：纠偏/补发仍占位；位置更新后写入推送记录与系统日志。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import LatestLocation, Project, Trolley


@dataclass
class ParseContext:
    project_id: int
    pid: str
    device_id: str
    trolley_tz: str
    line_code: str
    proc_code: str
    area_code: str
    identify_time: datetime
    sport_state: str
    source: str = "auto"
    kind: str = "实时上报"
    test: bool = False


def after_parse(ctx: ParseContext) -> ParseContext:
    """预留：纠偏 / 补发 / 连续补发计数。"""
    return ctx


def after_location_updated(
    db: Session,
    project: Project,
    trolley: Trolley,
    loc: LatestLocation,
    ctx: ParseContext,
) -> None:
    from .runtime import on_location_updated

    on_location_updated(db, project, trolley, loc, test=ctx.test)


def persist_raw_extra(ctx: ParseContext) -> None:
    return
